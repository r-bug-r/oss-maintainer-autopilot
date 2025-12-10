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


