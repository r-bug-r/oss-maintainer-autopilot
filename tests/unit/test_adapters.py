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
