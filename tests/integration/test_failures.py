"""Failure paths: provider errors, forge errors, cost attribution, deletion."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from autopilot.adapters import AdapterTransient, PostedComment, RepositoryAdapter
from autopilot.config import Settings
from autopilot.gateway import BudgetExceeded
from autopilot.logging_setup import configure_logging
from autopilot.models import Issue, Repository
from autopilot.providers import FakeProvider, ModelProvider, ProviderTimeout
from autopilot.runtime import Runtime, build_runtime, register_repository
from autopilot.workflows import WorkflowEngine, WorkflowError


def failing_runtime(tmp_path: Path) -> Iterator[Runtime]:
    runtime = _runtime(tmp_path, FakeProvider(scripted={"issue_triage": ProviderTimeout()}))
    try:
        yield runtime
    finally:
        runtime.close()


class _BrokenAdapter:
    """Adapter whose writes always fail, to prove the recoverable state."""

    def __init__(self, inner: RepositoryAdapter) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def post_comment(self, repository: Repository, number: int, body: str) -> PostedComment:
        raise AdapterTransient("forge is unavailable")

    def add_labels(self, repository: Repository, number: int, labels: Sequence[str]) -> None:
        raise AdapterTransient("forge is unavailable")


def test_failed_generation_does_not_consume_the_cache(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, FakeProvider(scripted={"issue_triage": ProviderTimeout()}))
    try:
        repository = register_repository(
            runtime.store, full_name="acme/widget", owner_login="maintainer"
        )
        issue = _issue(repository)
        runtime.store.save_issue(issue)
        with pytest.raises(WorkflowError, match="provider failed: timeout"):
            runtime.engine.triage_issue(issue)
        assert runtime.store.get_cached_response("anything") is None
        cached = runtime.store.conn.execute("SELECT COUNT(*) AS n FROM model_cache").fetchone()
        assert cached is not None and cached["n"] == 0
    finally:
        runtime.close()


def test_forge_failure_produces_recoverable_state(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, FakeProvider())
    try:
        repository = register_repository(
            runtime.store, full_name="acme/widget", owner_login="maintainer"
        )
        issue = _issue(repository)
        runtime.store.save_issue(issue)
        runtime.engine.adapter = _BrokenAdapter(runtime.engine.adapter)
        result = runtime.engine.triage_issue(issue)
        assert result.proposal.state == "pending_approval"
        runtime.engine.approve(result.proposal.id, "maintainer")
        failed = runtime.engine.execute(result.proposal.id, "maintainer")
        assert failed.state == "action_failed"
        assert failed.error_class == "transport"
        assert failed.attempt_count == 1
        # Retry is explicit: the same proposal may be claimed again.
        retried = runtime.engine.execute(result.proposal.id, "maintainer")
        assert retried.state == "action_failed"
        assert retried.attempt_count == 2
        assert runtime.store.list_comments(repository.id, 7) == []
    finally:
        runtime.close()


def test_cost_is_attributed_to_repository_and_workflow(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, FakeProvider())
    try:
        first = register_repository(runtime.store, full_name="acme/one", owner_login="maintainer")
        second = register_repository(runtime.store, full_name="acme/two", owner_login="maintainer")
        for repository, number in ((first, 1), (second, 2)):
            issue = _issue(repository, number=number, title=f"Crash {number}")
            runtime.store.save_issue(issue)
            runtime.engine.triage_issue(issue)

        rows = runtime.store.conn.execute(
            "SELECT repository_id, workflow, input_tokens, output_tokens FROM model_calls"
        ).fetchall()
        assert {row["repository_id"] for row in rows} == {first.id, second.id}
        assert {row["workflow"] for row in rows} == {"issue_triage"}
        assert all(row["input_tokens"] > 0 and row["output_tokens"] > 0 for row in rows)

        summary = runtime.store.cost_summary(since="2000-01-01T00:00:00.000Z")
        assert summary.requests == 2
        assert summary.input_tokens > 0
    finally:
        runtime.close()


def test_budget_exceeded_blocks_before_the_provider_is_called(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, FakeProvider())
    try:
        repository = register_repository(
            runtime.store, full_name="acme/widget", owner_login="maintainer"
        )
        settings = runtime.store.get_settings(repository.id)
        runtime.store.save_settings(
            type(settings)(
                repository_id=settings.repository_id,
                workflows=settings.workflows,
                label_taxonomy=settings.label_taxonomy,
                daily_request_limit=1,
                daily_cost_limit_usd=settings.daily_cost_limit_usd,
                proposal_ttl_minutes=settings.proposal_ttl_minutes,
            )
        )
        first = _issue(repository, number=1, title="One")
        runtime.store.save_issue(first)
        runtime.engine.triage_issue(first)

        second = _issue(repository, number=2, title="Two")
        runtime.store.save_issue(second)
        with pytest.raises(BudgetExceeded):
            runtime.engine.triage_issue(second)
        calls = runtime.store.conn.execute("SELECT COUNT(*) AS n FROM model_calls").fetchone()
        assert calls is not None and calls["n"] == 1
    finally:
        runtime.close()


def test_deletion_removes_content_and_keeps_audit_shape(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, FakeProvider())
    try:
        repository = register_repository(
            runtime.store, full_name="acme/widget", owner_login="maintainer"
        )
        issue = _issue(repository, title="Secret-bearing issue")
        runtime.store.save_issue(issue)
        runtime.engine.triage_issue(issue)

        counts = runtime.store.delete_repository_data(repository.id)
        assert counts["repositories"] == 1
        assert runtime.store.get_repository(repository.id) is None
        assert runtime.store.list_issues(repository.id) == []
        assert runtime.store.list_proposals(repository_id=repository.id) == []
        remaining = runtime.store.conn.execute(
            "SELECT COUNT(*) AS n FROM issues WHERE repository_id = ?", (repository.id,)
        ).fetchone()
        assert remaining is not None and remaining["n"] == 0
        calls = runtime.store.conn.execute(
            "SELECT COUNT(*) AS n FROM model_calls WHERE repository_id = ?", (repository.id,)
        ).fetchone()
        assert calls is not None and calls["n"] == 0

        events = runtime.store.list_audit(repository_id=repository.id, limit=100)
        assert events
        assert events[0].event_type == "repository.deleted"
        assert events[0].detail["deleted"]["repositories"] == 1
        # Audit details never carry issue or pull-request content.
        for event in events:
            serialized = json.dumps(event.detail)
            assert "Secret-bearing issue" not in serialized
            assert "Traceback." not in serialized
        ok, _, _ = runtime.store.verify_audit_chain()
        assert ok is True
    finally:
        runtime.close()


def test_log_lines_are_redacted() -> None:
    stream = io.StringIO()
    configure_logging(level=logging.INFO, stream=stream)
    logger = logging.getLogger("autopilot.test")
    logger.info("token ghp_abcdefghijklmnopqrstuvwxyz0123 leaked from %s", "alice@example.com")
    rendered = stream.getvalue()
    assert "ghp_" not in rendered
    assert "alice@example.com" not in rendered
    assert "REDACTED:github_token" in rendered
    assert "REDACTED:email" in rendered
