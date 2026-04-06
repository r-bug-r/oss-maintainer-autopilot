"""Privacy behavior: secret-bearing issue text must not persist verbatim."""

from __future__ import annotations

from pathlib import Path

from autopilot.config import Settings
from autopilot.models import Issue
from autopilot.runtime import build_runtime, register_repository

TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123"


def test_secret_in_issue_body_is_not_stored_in_the_proposal(tmp_path: Path) -> None:
    runtime = build_runtime(Settings(db_path=tmp_path / "privacy.db", provider="fake"))
    try:
        repository = register_repository(
            runtime.store, full_name="acme/widget", owner_login="maintainer"
        )
        issue = Issue(
            repository_id=repository.id,
            number=9,
            title="Crash with token",
            body=f"Pasted my token {TOKEN} into the config and it crashed.",
            author_login="reporter",
            state="open",
            labels=(),
        )
        runtime.store.save_issue(issue)
        result = runtime.engine.triage_issue(issue)

        stored = runtime.store.get_proposal(result.proposal.id)
        assert stored is not None
        serialized = repr(stored.input_context)
        assert TOKEN not in serialized
        assert "REDACTED:github_token" in serialized

        row = runtime.store.conn.execute(
            "SELECT input_context_json FROM proposals WHERE id = ?", (result.proposal.id,)
        ).fetchone()
        assert row is not None
        assert TOKEN not in row["input_context_json"]
    finally:
        runtime.close()
