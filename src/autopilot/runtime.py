"""Process assembly: settings, database, provider, adapter, engine.

One place constructs the object graph so the CLI and the HTTP server cannot
drift into different security postures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .adapters import GitHubAdapter, LocalFixtureAdapter, RepositoryAdapter
from .config import Settings
from .db import connect, init_db, utcnow
from .gateway import ModelGateway
from .models import Provider, Repository, RepositorySettings
from .providers import FakeProvider, ModelProvider, OpenAICompatibleProvider
from .store import Store
from .workflows import WorkflowEngine


@dataclass(slots=True)
