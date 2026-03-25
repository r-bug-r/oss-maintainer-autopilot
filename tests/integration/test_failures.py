"""Failure paths: provider errors, forge errors, cost attribution, deletion."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from autopilot.adapters import AdapterTransient, PostedComment, RepositoryAdapter
from autopilot.config import Settings
from autopilot.gateway import BudgetExceeded
from autopilot.logging_setup import configure_logging
from autopilot.models import Issue, Repository
from autopilot.providers import FakeProvider, ModelProvider, ProviderTimeout
from autopilot.runtime import Runtime, build_runtime, register_repository
from autopilot.workflows import WorkflowEngine, WorkflowError


def failing_runtime(tmp_path: Path) -> Iterator[Runtime]:
    runtime = _runtime(tmp_path, FakeProvider(scripted={"issue_triage": ProviderTimeout()}))
    try:
        yield runtime
    finally:
        runtime.close()


class _BrokenAdapter:
    """Adapter whose writes always fail, to prove the recoverable state."""

    def __init__(self, inner: RepositoryAdapter) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def post_comment(self, repository: Repository, number: int, body: str) -> PostedComment:
        raise AdapterTransient("forge is unavailable")

    def add_labels(self, repository: Repository, number: int, labels: Sequence[str]) -> None:
        raise AdapterTransient("forge is unavailable")


