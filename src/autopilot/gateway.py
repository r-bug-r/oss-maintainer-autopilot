"""Budget enforcement, response caching, and the model-call ledger.

Every provider call goes through `ModelGateway`. The gateway enforces the
request limit and spend caps *before* calling the provider, records a ledger
row for every attempt (including failures and cache hits), and never retries
automatically: retry policy belongs to the caller, which knows whether the
follow-on side effect is idempotent.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .db import utcnow
from .models import RepositorySettings
from .providers import ModelProvider, ModelRequest, ModelResponse, ProviderError
from .store import Store


