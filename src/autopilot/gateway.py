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


def _start_of_day(now: datetime) -> str:
    return (
        now.replace(hour=0, minute=0, second=0, microsecond=0)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _start_of_month(now: datetime) -> str:
    return (
        now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class ModelGateway:
    def __init__(
        self,
        *,
        store: Store,
        provider: ModelProvider,
        daily_cost_limit_usd: float,
        monthly_cost_limit_usd: float,
        default_request_limit: int,
        cache_enabled: bool = True,
    ) -> None:
        self.store = store
        self.provider = provider
        self.daily_cost_limit_usd = daily_cost_limit_usd
        self.monthly_cost_limit_usd = monthly_cost_limit_usd
        self.default_request_limit = default_request_limit
        self.cache_enabled = cache_enabled

    def budget_state(
        self, repository_id: str, settings: RepositorySettings | None = None
    ) -> BudgetState:
        now = datetime.now(UTC)
        day = self.store.cost_summary(since=_start_of_day(now), repository_id=repository_id)
        month = self.store.cost_summary(since=_start_of_month(now), repository_id=repository_id)
        request_limit = (
            settings.daily_request_limit if settings is not None else self.default_request_limit
        )
        daily_cost_limit = (
            settings.daily_cost_limit_usd if settings is not None else self.daily_cost_limit_usd
        )
        return BudgetState(
            requests_today=day.requests,
            request_limit=request_limit,
            cost_today_usd=day.cost_usd,
            daily_cost_limit_usd=daily_cost_limit,
            cost_month_usd=month.cost_usd,
            monthly_cost_limit_usd=self.monthly_cost_limit_usd,
        )

    def check_budget(
        self, repository_id: str, settings: RepositorySettings | None = None
    ) -> BudgetState:
        state = self.budget_state(repository_id, settings)
        if state.requests_today >= state.request_limit:
            raise BudgetExceeded(
                f"daily request limit reached for {repository_id} "
                f"({state.requests_today}/{state.request_limit})"
            )
        if state.daily_cost_limit_usd > 0 and state.cost_today_usd >= state.daily_cost_limit_usd:
            raise BudgetExceeded(
                f"daily spend cap reached for {repository_id} "
                f"(${state.cost_today_usd:.4f}/${state.daily_cost_limit_usd:.2f})"
            )
        if (
            state.monthly_cost_limit_usd > 0
            and state.cost_month_usd >= state.monthly_cost_limit_usd
        ):
            raise BudgetExceeded(
                f"monthly spend cap reached (${state.cost_month_usd:.4f}/"
                f"${state.monthly_cost_limit_usd:.2f})"
            )
        return state

    def complete(
        self,
        request: ModelRequest,
        *,
        repository_id: str,
        settings: RepositorySettings | None = None,
    ) -> ModelResponse:
        """Check budget, serve from cache when safe, else call the provider."""

        self.check_budget(repository_id, settings)
        call_id = f"call_{uuid.uuid4().hex[:16]}"
        started_at = utcnow()

        if self.cache_enabled:
            cached = self.store.get_cached_response(request.cache_key)
            if cached is not None:
                self.store.record_model_call(
                    call_id=call_id,
                    repository_id=repository_id,
                    workflow=request.workflow,
                    provider=self.provider.name,
                    model=self.provider.model,
                    started_at=started_at,
                    finished_at=utcnow(),
                    input_tokens=0,
                    output_tokens=0,
                    estimated_cost_usd=0.0,
                    cache_hit=True,
                    success=True,
                    error_class=None,
                    prompt_version=request.prompt_version,
                    input_sha256=request.input_sha256,
                )
                return ModelResponse(
                    text=cached,
                    provider=self.provider.name,
                    model=self.provider.model,
                    request_id=f"cache-{request.cache_key[:16]}",
                    input_tokens=0,
                    output_tokens=0,
                    latency_ms=0,
                    estimated_cost_usd=0.0,
                    cached=True,
                )

        try:
            response = self.provider.complete(request)
        except ProviderError as exc:
            self.store.record_model_call(
                call_id=call_id,
                repository_id=repository_id,
                workflow=request.workflow,
                provider=self.provider.name,
                model=self.provider.model,
                started_at=started_at,
                finished_at=utcnow(),
                input_tokens=0,
                output_tokens=0,
                estimated_cost_usd=0.0,
                cache_hit=False,
                success=False,
                error_class=exc.error_class,
                prompt_version=request.prompt_version,
                input_sha256=request.input_sha256,
            )
            raise

        self.store.record_model_call(
            call_id=call_id,
            repository_id=repository_id,
            workflow=request.workflow,
            provider=response.provider,
            model=response.model,
            started_at=started_at,
            finished_at=utcnow(),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            estimated_cost_usd=response.estimated_cost_usd,
            cache_hit=False,
            success=True,
            error_class=None,
            prompt_version=request.prompt_version,
            input_sha256=request.input_sha256,
        )
        if self.cache_enabled and response.text:
            self.store.put_cached_response(
                cache_key=request.cache_key,
                provider=response.provider,
                model=response.model,
                prompt_version=request.prompt_version,
                response_text=response.text,
                input_sha256=request.input_sha256,
            )
        return response


def retention_cutoff(days: int, now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    return (moment - timedelta(days=days)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
