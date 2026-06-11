from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from autopilot.api import Api
from autopilot.canonical import sha256_hex
from autopilot.config import ConfigError, Settings
from autopilot.models import RepositorySettings, ValidationError
from autopilot.runtime import build_runtime


def test_defaults_are_offline_and_local(tmp_path: Path) -> None:
    settings = Settings(db_path=tmp_path / "db.sqlite")
    assert settings.provider == "fake"
    assert settings.host == "127.0.0.1"
    assert settings.allow_remote is False
    assert settings.allow_live_provider is False


def test_live_provider_requires_explicit_opt_in(tmp_path: Path) -> None:
    settings = Settings(
        db_path=tmp_path / "db.sqlite",
        provider="openai_compatible",
        openai_base_url="https://example.invalid/v1",
        openai_api_key="key",
        daily_cost_limit_usd=1.0,
        monthly_cost_limit_usd=10.0,
    )
    with pytest.raises(ConfigError, match="AUTOPILOT_ALLOW_LIVE_PROVIDER"):
        settings.validate()


def test_live_provider_requires_cost_caps(tmp_path: Path) -> None:
    settings = Settings(
        db_path=tmp_path / "db.sqlite",
        provider="openai_compatible",
        allow_live_provider=True,
        openai_base_url="https://example.invalid/v1",
        openai_api_key="key",
    )
    with pytest.raises(ConfigError, match="cost caps"):
        settings.validate()


def test_remote_binding_requires_admin_token(tmp_path: Path) -> None:
    settings = Settings(db_path=tmp_path / "db.sqlite", allow_remote=True)
    with pytest.raises(ConfigError, match="AUTOPILOT_ADMIN_TOKEN"):
        settings.validate()


def test_webhook_secret_must_be_long_enough(tmp_path: Path) -> None:
    settings = Settings(db_path=tmp_path / "db.sqlite", webhook_secret="short")
    with pytest.raises(ConfigError, match="at least 16 characters"):
        settings.require_webhook_secret()


def test_retention_and_ttl_bounds(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        Settings(db_path=tmp_path / "db.sqlite", retention_days=0).validate()
    with pytest.raises(ConfigError):
        Settings(db_path=tmp_path / "db.sqlite", proposal_ttl_minutes=0).validate()
    with pytest.raises(ConfigError):
        Settings(db_path=tmp_path / "db.sqlite", max_body_bytes=10).validate()


def test_settings_read_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOPILOT_HOME", str(tmp_path))
    monkeypatch.setenv("AUTOPILOT_PROVIDER", "fake")
    monkeypatch.setenv("AUTOPILOT_PORT", "9123")
    monkeypatch.setenv("AUTOPILOT_DAILY_REQUEST_LIMIT", "17")
    settings = Settings.from_env()
    assert settings.port == 9123
    assert settings.daily_request_limit == 17
    assert settings.db_path == (tmp_path / "var" / "autopilot.db").resolve()


def test_schema_enforces_append_only_audit(tmp_path: Path) -> None:
    runtime = build_runtime(Settings(db_path=tmp_path / "db.sqlite"))
    try:
        runtime.store.append_audit(event_type="test.event", detail={"a": 1})
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            runtime.store.conn.execute("UPDATE audit_events SET event_type = 'tampered'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            runtime.store.conn.execute("DELETE FROM audit_events")
    finally:
        runtime.close()


def test_settings_round_trip_and_defaults(tmp_path: Path) -> None:
    runtime = build_runtime(Settings(db_path=tmp_path / "db.sqlite"))
    try:
        from autopilot.runtime import register_repository

        repository = register_repository(
            runtime.store, full_name="acme/widget", owner_login="maintainer"
        )
        default = runtime.store.get_settings(repository.id)
        assert default.workflows["issue_triage"] is True
        assert default.workflows["release_notes"] is False
        assert "bug" in default.label_taxonomy

        updated = RepositorySettings(
            repository_id=repository.id,
            workflows={"issue_triage": False, "pr_review": True, "release_notes": True},
            label_taxonomy=("bug", "duplicate"),
            daily_request_limit=5,
            daily_cost_limit_usd=1.5,
            proposal_ttl_minutes=30,
        )
        runtime.store.save_settings(updated)
        stored = runtime.store.get_settings(repository.id)
        assert stored.as_dict() == updated.as_dict()
    finally:
        runtime.close()


def test_audit_chain_detects_tampering(tmp_path: Path) -> None:
    runtime = build_runtime(Settings(db_path=tmp_path / "db.sqlite"))
    try:
        for index in range(3):
            runtime.store.append_audit(event_type=f"test.{index}", detail={"n": index})
        ok, checked, broken = runtime.store.verify_audit_chain()
        assert (ok, checked, broken) == (True, 3, None)

        # The trigger blocks UPDATE, so simulate an out-of-band edit to prove the
        # hash chain catches a rewritten row even when the trigger is bypassed.
        runtime.store.conn.execute("DROP TRIGGER IF EXISTS audit_events_no_update")
        runtime.store.conn.execute(
            "UPDATE audit_events SET detail_json = ? WHERE seq = 2",
            (json.dumps({"n": 99}),),
        )
        runtime.store.conn.commit()
        ok, checked, broken = runtime.store.verify_audit_chain()
        assert ok is False
        assert broken is not None
    finally:
        runtime.close()


def test_audit_hash_covers_previous_hash(tmp_path: Path) -> None:
    runtime = build_runtime(Settings(db_path=tmp_path / "db.sqlite"))
    try:
        first = runtime.store.append_audit(event_type="a", detail={})
        second = runtime.store.append_audit(event_type="b", detail={})
        assert first.prev_hash == "0" * 64
        assert second.prev_hash == first.hash
        assert second.hash != sha256_hex("b")
    finally:
        runtime.close()


def test_settings_validation_rejects_nonsense() -> None:
    with pytest.raises(ValidationError):
        Api.validate_settings(RepositorySettings(repository_id="x", daily_request_limit=0))
    with pytest.raises(ValidationError):
        Api.validate_settings(RepositorySettings(repository_id="x", label_taxonomy=()))
