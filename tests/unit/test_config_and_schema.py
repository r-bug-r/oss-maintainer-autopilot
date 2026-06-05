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


