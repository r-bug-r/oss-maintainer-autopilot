"""Shared fixtures: a real SQLite database, a fake provider, and a local forge.

Tests never reach the network: the fake provider is deterministic and the
fixture adapter records writes locally. Anything that would call GitHub requires
the `live` marker and an explicit token.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from autopilot.api import Api
from autopilot.config import Settings
from autopilot.models import Issue, Repository
from autopilot.runtime import Runtime, build_runtime, register_repository

WEBHOOK_SECRET = "test-webhook-secret-0123456789"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "autopilot.db",
        webhook_secret=WEBHOOK_SECRET,
        provider="fake",
    )


@pytest.fixture
def runtime(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[Runtime]:
    monkeypatch.delenv("AUTOPILOT_GITHUB_TOKEN", raising=False)
    instance = build_runtime(settings)
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def repository(runtime: Runtime) -> Repository:
    return register_repository(
        runtime.store,
        full_name="acme/widget",
        owner_login="maintainer",
        actor="maintainer",
    )


@pytest.fixture
def issue(runtime: Runtime, repository: Repository) -> Issue:
    item = Issue(
        repository_id=repository.id,
        number=7,
        title="Crash when the config file is empty",
        body="Running with an empty config raises a traceback instead of a clear error.",
        author_login="reporter",
        state="open",
        labels=("bug",),
    )
    runtime.store.save_issue(item)
    return item


@pytest.fixture
def issue_two(runtime: Runtime, repository: Repository) -> Issue:
    item = Issue(
        repository_id=repository.id,
        number=8,
        title="Empty config file crashes on startup",
        body="Same traceback when the config has no keys at all.",
        author_login="reporter-two",
        state="open",
        labels=(),
    )
    runtime.store.save_issue(item)
    return item


@pytest.fixture
def api(runtime: Runtime) -> Api:
    return Api(runtime)
