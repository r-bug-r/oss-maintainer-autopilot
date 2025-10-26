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


class BudgetExceeded(Exception):
    """Raised before a provider call when a hard limit would be crossed."""


@dataclass(frozen=True, slots=True)
class BudgetState:
    requests_today: int
    request_limit: int
    cost_today_usd: float
    daily_cost_limit_usd: float
    cost_month_usd: float
    monthly_cost_limit_usd: float

    @property
    def requests_remaining(self) -> int:
        return max(0, self.request_limit - self.requests_today)

    def as_dict(self) -> dict[str, object]:
        return {
            "requests_today": self.requests_today,
            "request_limit": self.request_limit,
            "requests_remaining": self.requests_remaining,
            "cost_today_usd": round(self.cost_today_usd, 6),
            "daily_cost_limit_usd": self.daily_cost_limit_usd,
            "cost_month_usd": round(self.cost_month_usd, 6),
            "monthly_cost_limit_usd": self.monthly_cost_limit_usd,
        }


def retention_cutoff(days: int, now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    return (moment - timedelta(days=days)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
