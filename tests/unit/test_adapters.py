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


def test_post_comment_targets_a_pull_request(local_adapter: LocalAdapterFixture) -> None:
    adapter, repository, runtime = local_adapter

    posted = adapter.post_comment(repository, 9, "Thanks, reviewing now.")

    assert posted.external_id.startswith("local-")
    runtime.store.save_comment(
        comment_id=posted.external_id,
        repository_id=repository.id,
        issue_number=9,
        body=posted.body,
        author_login="autopilot",
        source="posted",
        idempotency_key="prop_test",
    )
    stored = runtime.store.list_comments(repository.id, 9)
    assert [row["body"] for row in stored] == ["Thanks, reviewing now."]
    assert stored[0]["source"] == "posted"


def test_add_labels_targets_a_pull_request(local_adapter: LocalAdapterFixture) -> None:
    adapter, repository, _runtime = local_adapter

    adapter.add_labels(repository, 9, ("needs-tests",))

    assert adapter.applied_labels == [(repository.id, 9, ("needs-tests",))]


def test_unknown_target_is_rejected(local_adapter: LocalAdapterFixture) -> None:
    adapter, repository, _runtime = local_adapter

    with pytest.raises(AdapterNotFound, match="not an issue or pull request"):
        adapter.post_comment(repository, 404, "hello")
    with pytest.raises(AdapterNotFound, match="not an issue or pull request"):
        adapter.add_labels(repository, 404, ("bug",))
