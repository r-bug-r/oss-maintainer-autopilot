"""Process assembly: settings, database, provider, adapter, engine.

One place constructs the object graph so the CLI and the HTTP server cannot
drift into different security postures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .adapters import GitHubAdapter, LocalFixtureAdapter, RepositoryAdapter
from .config import Settings
from .db import connect, init_db, utcnow
from .gateway import ModelGateway
from .models import Provider, Repository, RepositorySettings
from .providers import FakeProvider, ModelProvider, OpenAICompatibleProvider
from .store import Store
from .workflows import WorkflowEngine


@dataclass(slots=True)
class Runtime:
    settings: Settings
    store: Store
    provider: ModelProvider
    gateway: ModelGateway
    adapter: RepositoryAdapter
    engine: WorkflowEngine

    def close(self) -> None:
        self.store.close()


def build_provider(settings: Settings) -> ModelProvider:
    if settings.provider == "fake":
        return FakeProvider()
    return OpenAICompatibleProvider(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        model=settings.model,
        timeout_seconds=settings.model_timeout_seconds,
    )


def build_runtime(settings: Settings, *, adapter: RepositoryAdapter | None = None) -> Runtime:
    settings.validate()
    init_db(settings.db_path)
    connection = connect(settings.db_path)
    store = Store(connection)
    provider = build_provider(settings)
    gateway = ModelGateway(
        store=store,
        provider=provider,
        daily_cost_limit_usd=settings.daily_cost_limit_usd,
        monthly_cost_limit_usd=settings.monthly_cost_limit_usd,
        default_request_limit=settings.daily_request_limit,
        cache_enabled=settings.cache_model_outputs,
    )
    resolved_adapter = adapter if adapter is not None else build_adapter(store)
    engine = WorkflowEngine(
        store=store,
        gateway=gateway,
        adapter=resolved_adapter,
        proposal_ttl_minutes=settings.proposal_ttl_minutes,
    )
    return Runtime(
        settings=settings,
        store=store,
        provider=provider,
        gateway=gateway,
        adapter=resolved_adapter,
        engine=engine,
    )


def build_adapter(store: Store) -> RepositoryAdapter:
    """GitHub only when an explicit token exists; otherwise the local fixture."""

    token = _github_token()
    if token:
        return GitHubAdapter(token=token)
    return LocalFixtureAdapter(store)


def _github_token() -> str:
    import os

    return os.environ.get("AUTOPILOT_GITHUB_TOKEN", "").strip()


def register_repository(
    store: Store,
    *,
    full_name: str,
    owner_login: str,
    provider: Provider = "github",
    actor: str | None = None,
) -> Repository:
    """Register a repository and grant the registrant the owner role."""

    if "/" not in full_name:
        raise ValueError("repository full name must look like owner/name")
    repository = Repository.build(
        provider=provider,
        full_name=full_name,
        owner_login=owner_login,
    )
    store.upsert_repository(repository)
    store.grant_role(repository.id, actor or owner_login, "owner", granted_by="cli")
    store.append_audit(
        event_type="repository.registered",
        repository_id=repository.id,
        actor_login=actor or owner_login,
        subject_type="repository",
        subject_id=repository.id,
        detail={"full_name": full_name, "provider": provider},
    )
    return repository


def purge_expired(*, store: Store, retention_days: int) -> dict[str, int]:
    """Delete content past retention while keeping the audit chain intact."""

    cutoff = (
        (datetime.now(UTC) - timedelta(days=retention_days))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    counts = {"proposals": 0, "model_calls": 0, "deliveries": 0}
    with store.conn:
        counts["proposals"] = store.conn.execute(
            "DELETE FROM proposals WHERE state IN ('executed', 'rejected', 'expired') "
            "AND created_at < ?",
            (cutoff,),
        ).rowcount
        counts["model_calls"] = store.conn.execute(
            "DELETE FROM model_calls WHERE started_at < ?", (cutoff,)
        ).rowcount
        counts["deliveries"] = store.conn.execute(
            "DELETE FROM webhook_deliveries WHERE received_at < ?", (cutoff,)
        ).rowcount
        store.conn.execute("DELETE FROM model_cache WHERE created_at < ?", (cutoff,))
    store.append_audit(
        event_type="retention.purged",
        detail={"cutoff": cutoff, **counts},
    )
    return counts


def seed_demo(store: Store, *, repository_full_name: str = "demo/autopilot-sandbox") -> Repository:
    """Create a deterministic local repository with issues and a pull request.

    Demo data is local-only: it exists so the dashboard and the approval flow can
    be exercised without a token, and it is clearly labeled as such.
    """

    from .models import Issue, PullRequest, PullRequestFile

    repository = register_repository(
        store,
        full_name=repository_full_name,
        owner_login="demo-maintainer",
        actor="demo-maintainer",
    )
    now = utcnow()
    issues = [
        Issue(
            repository_id=repository.id,
            number=41,
            title="Crash on startup with empty config file",
            body=(
                "Running `autopilot --config empty.yml` raises a traceback. "
                "Expected a clear error message instead."
            ),
            author_login="reporter-a",
            state="open",
            labels=("bug",),
            created_at=now,
            updated_at=now,
        ),
        Issue(
            repository_id=repository.id,
            number=42,
            title="Crash when config file is empty at startup",
            body="Same traceback as before when the config file has no keys. Version 0.1.0.",
            author_login="reporter-b",
            state="open",
            labels=(),
            created_at=now,
            updated_at=now,
        ),
        Issue(
            repository_id=repository.id,
            number=43,
            title="Document the webhook secret rotation procedure",
            body=(
                "The docs mention rotation but do not describe the steps or the expected downtime."
            ),
            author_login="reporter-c",
            state="open",
            labels=("documentation",),
            created_at=now,
            updated_at=now,
        ),
        Issue(
            repository_id=repository.id,
            number=44,
            title="Would be nice to add a Slack notification option",
            body="Feature request: send a summary to Slack when a proposal is approved.",
            author_login="reporter-d",
            state="open",
            labels=("enhancement",),
            created_at=now,
            updated_at=now,
        ),
    ]
    for issue in issues:
        store.save_issue(issue)
    store.save_pull_request(
        PullRequest(
            repository_id=repository.id,
            number=57,
            title="Validate configuration before loading providers",
            body="Adds an explicit validation pass so an empty config fails loudly.",
            author_login="contributor-a",
            state="open",
            base_ref="main",
            head_ref="validate-config",
            files=(
                PullRequestFile(
                    path="src/autopilot/config.py",
                    additions=42,
                    deletions=3,
                    patch_summary="validate() now rejects empty provider blocks",
                ),
                PullRequestFile(
                    path="tests/unit/test_config.py",
                    additions=61,
                    deletions=0,
                    patch_summary="covers the empty-config case",
                ),
            ),
        )
    )
    settings = store.get_settings(repository.id)
    store.save_settings(
        RepositorySettings(
            repository_id=repository.id,
            workflows=dict(settings.workflows),
            label_taxonomy=settings.label_taxonomy,
            daily_request_limit=settings.daily_request_limit,
            daily_cost_limit_usd=settings.daily_cost_limit_usd,
            proposal_ttl_minutes=settings.proposal_ttl_minutes,
        )
    )
    return repository


def default_db_path() -> Path:
    return Path.cwd() / "var" / "autopilot.db"
