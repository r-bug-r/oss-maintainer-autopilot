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


