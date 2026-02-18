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
