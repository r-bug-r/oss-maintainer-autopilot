"""Model provider contract plus deterministic and live implementations.

The contract is intentionally narrow: `complete()` and `count_tokens()`. Every
implementation records provider, model, token counts, latency and cost so the
caller can write an auditable ledger row.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from .canonical import sha256_hex

RETRYABLE_ERRORS = frozenset({"timeout", "rate_limited", "server_error", "transport"})
PERMANENT_ERRORS = frozenset({"auth", "invalid_request", "schema_invalid"})


class ProviderError(Exception):
    """Provider failure with an explicit retry classification."""

    def __init__(self, message: str, *, error_class: str, retryable: bool | None = None) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.retryable = error_class in RETRYABLE_ERRORS if retryable is None else retryable


class ProviderTimeout(ProviderError):
    def __init__(self, message: str = "provider timed out") -> None:
        super().__init__(message, error_class="timeout", retryable=True)


@dataclass(frozen=True, slots=True)
class ModelRequest:
    workflow: str
    prompt_version: str
    system_prompt: str
    user_prompt: str
    max_output_tokens: int = 700
    temperature: float = 0.0
    json_schema: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 20.0

    @property
    def input_sha256(self) -> str:
        return sha256_hex(f"{self.prompt_version}\n{self.system_prompt}\n{self.user_prompt}")

    @property
    def cache_key(self) -> str:
        return sha256_hex(
            json.dumps(
                {
                    "prompt_version": self.prompt_version,
                    "input": self.input_sha256,
                    "max_output_tokens": self.max_output_tokens,
                    "temperature": self.temperature,
                },
                sort_keys=True,
            )
        )


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str
    provider: str
    model: str
    request_id: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    estimated_cost_usd: float
    cached: bool = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ModelProvider(Protocol):
    name: str
    model: str

    def complete(self, request: ModelRequest) -> ModelResponse: ...

    def count_tokens(self, text: str) -> int: ...


def estimate_tokens(text: str) -> int:
    """Conservative token estimate without a tokenizer dependency.

    Four characters per token plus one for the boundary; this over-estimates
    slightly, which is the safe direction for budget enforcement.
    """

    if not text:
        return 0
    return max(1, len(text) // 4 + 1)


class FakeProvider:
    """Deterministic provider used by tests, CI, and the default local path.

    Responses are a pure function of the request's canonical hash, so identical
    fixtures always yield identical proposals. A `scripted` map lets tests force
    specific failures without touching production code.
    """

    name = "fake"

    def __init__(
        self,
        model: str = "fake-maintainer-v1",
        *,
        scripted: dict[str, str | Exception] | None = None,
        latency_ms: int = 0,
    ) -> None:
        self.model = model
        self._scripted = scripted or {}
        self._latency_ms = latency_ms

    def count_tokens(self, text: str) -> int:
        return estimate_tokens(text)

    def complete(self, request: ModelRequest) -> ModelResponse:
        started = time.monotonic()
        if self._latency_ms:
            time.sleep(self._latency_ms / 1000)
        key = request.cache_key
        scripted = self._scripted.get(key) or self._scripted.get(request.workflow)
        if isinstance(scripted, Exception):
            raise scripted
        if isinstance(scripted, str):
            text = scripted
        else:
            text = json.dumps(self._render(request), sort_keys=True)
        latency = int((time.monotonic() - started) * 1000)
        input_tokens = estimate_tokens(request.system_prompt + request.user_prompt)
        output_tokens = estimate_tokens(text)
        return ModelResponse(
            text=text,
            provider=self.name,
            model=self.model,
            request_id=f"fake-{key[:16]}",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency,
            estimated_cost_usd=0.0,
        )

    def _render(self, request: ModelRequest) -> dict[str, Any]:
        context = _extract_context(request.user_prompt)
        workflow = request.workflow
        if workflow == "issue_triage":
            return _fake_issue_triage(context)
        if workflow == "pr_review":
            return _fake_pr_review(context)
        if workflow == "release_notes":
            return _fake_release_notes(context)
        raise ProviderError(
            f"fake provider has no template for workflow {workflow}",
            error_class="invalid_request",
        )


