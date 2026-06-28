"""Role model and schema migration behavior."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from autopilot.config import Settings
from autopilot.db import _SCHEMA_V1, init_db
from autopilot.models import Role
from autopilot.runtime import build_runtime, register_repository
from autopilot.store import Unauthorized


def test_all_advertised_roles_can_be_granted(tmp_path: Path) -> None:
    runtime = build_runtime(Settings(db_path=tmp_path / "roles.db", provider="fake"))
    try:
        repository = register_repository(
            runtime.store, full_name="acme/widget", owner_login="owner-login"
        )
        grants: tuple[tuple[str, Role], ...] = (
            ("second-owner", "owner"),
            ("maintainer-login", "maintainer"),
            ("approver-login", "approver"),
        )
        for login, role in grants:
            runtime.store.grant_role(repository.id, login, role, granted_by="test")
            assert runtime.store.role_of(repository.id, login) == role

        runtime.store.authorize(repository.id, "approver-login")
        runtime.store.authorize(repository.id, "maintainer-login")
        runtime.store.authorize(repository.id, "second-owner", require_owner=True)
        with pytest.raises(Unauthorized):
            runtime.store.authorize(repository.id, "approver-login", require_owner=True)
        with pytest.raises(Unauthorized):
            runtime.store.authorize(repository.id, "stranger")
    finally:
        runtime.close()


def test_existing_v1_database_is_upgraded_to_accept_approvers(tmp_path: Path) -> None:
    db_path = tmp_path / "v1.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA_V1)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_migrations VALUES (1, '2026-01-01T00:00:00.000Z')")
        conn.execute(
            "INSERT INTO repositories "
            "(id, provider, full_name, owner_login, created_at, updated_at) "
            "VALUES ('github:acme/widget', 'github', 'acme/widget', 'owner-login', "
            "'2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z')"
        )
        conn.execute(
            "INSERT INTO actors (login, created_at) VALUES ('owner-login', "
            "'2026-01-01T00:00:00.000Z')"
        )
        conn.execute(
            "INSERT INTO repository_actors (repository_id, actor_login, role, granted_at) "
            "VALUES ('github:acme/widget', 'owner-login', 'owner', "
            "'2026-01-01T00:00:00.000Z')"
        )

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations")]
        assert versions == [1, 2]
        conn.execute(
            "INSERT INTO actors (login, created_at) VALUES ('approver-login', "
            "'2026-02-01T00:00:00.000Z')"
        )
        conn.execute(
            "INSERT INTO repository_actors (repository_id, actor_login, role, granted_at) "
            "VALUES ('github:acme/widget', 'approver-login', 'approver', "
            "'2026-02-01T00:00:00.000Z')"
        )
        preserved = conn.execute(
            "SELECT role FROM repository_actors WHERE actor_login = 'owner-login'"
        ).fetchone()
        assert preserved is not None and preserved[0] == "owner"
