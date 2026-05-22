"""Local fixture adapter behavior, including pull-request action targets."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from autopilot.adapters import AdapterNotFound, LocalFixtureAdapter
from autopilot.config import Settings
from autopilot.models import Issue, PullRequest, Repository
from autopilot.runtime import Runtime, build_runtime, register_repository

LocalAdapterFixture = tuple[LocalFixtureAdapter, Repository, Runtime]


@pytest.fixture
def local_adapter(tmp_path: Path) -> Iterator[LocalAdapterFixture]:
    runtime = build_runtime(Settings(db_path=tmp_path / "adapter.db", provider="fake"))
    try:
        repository = register_repository(
            runtime.store, full_name="acme/widget", owner_login="owner-login"
        )
        runtime.store.save_issue(
            Issue(
                repository_id=repository.id,
                number=7,
                title="Crash on startup",
                body="traceback",
                author_login="reporter",
                state="open",
            )
        )
        # GitHub numbers issues and pull requests in one sequence, so the same
        # adapter must resolve either kind by number.
        runtime.store.save_pull_request(
            PullRequest(
                repository_id=repository.id,
                number=9,
                title="Validate config",
                body="patch",
                author_login="contributor",
                state="open",
            )
        )
        yield LocalFixtureAdapter(runtime.store), repository, runtime
    finally:
        runtime.close()


