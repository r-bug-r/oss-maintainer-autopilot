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


