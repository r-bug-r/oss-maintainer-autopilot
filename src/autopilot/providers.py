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


def _extract_context(user_prompt: str) -> dict[str, Any]:
    """Read the canonical context block delimited by the prompt builder."""

    marker = "CONTEXT_JSON:"
    index = user_prompt.find(marker)
    if index < 0:
        return {}
    raw = user_prompt[index + len(marker) :]
    end = raw.find("END_CONTEXT_JSON")
    if end >= 0:
        raw = raw[:end]
    try:
        parsed = json.loads(raw.strip())
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _fake_issue_triage(context: dict[str, Any]) -> dict[str, Any]:
    title = str(context.get("title", "")).strip()
    body = str(context.get("body", ""))
    labels = [label for label in context.get("label_taxonomy", []) if isinstance(label, str)]
    suggested: list[str] = []
    lowered = f"{title}\n{body}".lower()
    if any(word in lowered for word in ("crash", "error", "traceback", "bug", "broken")):
        suggested.append("bug")
    if any(word in lowered for word in ("docs", "documentation", "readme", "typo")):
        suggested.append("documentation")
    if any(word in lowered for word in ("would be nice", "feature", "support for", "add ")):
        suggested.append("enhancement")
    if not suggested:
        suggested.append("question")
    if "reproduc" not in lowered and "steps" not in lowered:
        suggested.append("needs-reproduction")
    suggested = [label for label in dict.fromkeys(suggested) if label in labels]

    questions: list[str] = []
    if "version" not in lowered:
        questions.append("Which version of the project are you running?")
    if "reproduc" not in lowered and "steps" not in lowered:
        questions.append("What are the exact steps that reproduce the problem?")
    if "expected" not in lowered:
        questions.append("What did you expect to happen instead?")

    duplicates = [
        {
            "candidate_number": int(item["number"]),
            "explanation": (
                "Similar wording in the title and overlapping keywords; "
                "verify the referenced issue before closing this one."
            ),
        }
        for item in context.get("duplicate_candidates", [])
        if isinstance(item, dict) and isinstance(item.get("number"), int)
    ]

    summary = (
        f"Issue #{context.get('number', '?')} reports: {title}. "
        "The report needs maintainer review before any repository change."
    )
    comment_parts = []
    if questions:
        comment_parts.append("Thanks for the report. To help us reproduce this, could you share:")
        comment_parts.extend(f"- {question}" for question in questions)
    if duplicates:
        comment_parts.append("This may overlap with an existing report:")
        comment_parts.extend(
            f"- #{item['candidate_number']} (candidate match, please confirm)"
            for item in duplicates
        )
    proposed_comment = "\n".join(comment_parts) if comment_parts else None

    return {
        "summary": summary,
        "labels": suggested,
        "missing_reproduction_questions": questions,
        "duplicate_explanations": duplicates,
        "proposed_comment": proposed_comment,
        "confidence": 0.82 if questions else 0.64,
        "risk": "low" if not duplicates else "medium",
    }


def _fake_pr_review(context: dict[str, Any]) -> dict[str, Any]:
    files = [item for item in context.get("files", []) if isinstance(item, dict)]
    touched = [str(item.get("path", "")) for item in files][:10]
    additions = sum(int(item.get("additions", 0) or 0) for item in files)
    deletions = sum(int(item.get("deletions", 0) or 0) for item in files)
    notes = [
        f"Changes touch {len(touched)} file(s) with +{additions}/-{deletions} lines.",
        "Confirm the change includes tests for the new behavior.",
        "Check that public documentation is updated when behavior changes.",
    ]
    if any(path.endswith((".env", ".pem", ".key")) for path in touched):
        notes.append(
            "A file with a credential-like extension is included; verify no secret is committed."
        )
    return {
        "summary": (
            f"PR #{context.get('number', '?')} {context.get('title', '')!s}".strip()
            + " proposes a change that needs maintainer review before merge."
        ),
        "review_notes": notes,
        "risk_notes": [
            "Merge and release actions are out of scope; only a draft comment is proposed."
        ],
        "proposed_comment": "Draft review note: " + notes[0],
        "confidence": 0.7,
        "risk": "medium",
    }


def _fake_release_notes(context: dict[str, Any]) -> dict[str, Any]:
    pull_requests = [item for item in context.get("pull_requests", []) if isinstance(item, dict)]
    items = [f"#{item.get('number')} {item.get('title', '')}".strip() for item in pull_requests][
        :50
    ]
    return {
        "summary": f"Draft release notes covering {len(items)} merged pull request(s).",
        "sections": [{"heading": "Changes", "items": items}] if items else [],
        "proposed_comment": None,
        "confidence": 0.6,
        "risk": "low",
    }


class OpenAICompatibleProvider:
    """Live provider adapter, disabled unless configuration explicitly opts in.

    It uses only the standard library, enforces a timeout, and classifies
    failures so the caller never retries a non-idempotent side effect.
    """

    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 20.0,
        input_cost_per_million: float = 0.0,
        output_cost_per_million: float = 0.0,
    ) -> None:
        if not base_url or not api_key:
            raise ProviderError("live provider requires base URL and API key", error_class="auth")
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderError(
                "provider base URL must be an absolute http(s) URL", error_class="invalid_request"
            )
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model = model
        self._timeout = timeout_seconds
        self._input_cost = input_cost_per_million / 1_000_000
        self._output_cost = output_cost_per_million / 1_000_000

    def count_tokens(self, text: str) -> int:
        return estimate_tokens(text)

    def complete(self, request: ModelRequest) -> ModelResponse:
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": request.system_prompt},
                    {"role": "user", "content": request.user_prompt},
                ],
                "max_tokens": request.max_output_tokens,
                "temperature": request.temperature,
            }
        ).encode("utf-8")
        # Scheme is validated in __init__, so only http(s) URLs reach this call.
        http_request = urllib.request.Request(  # noqa: S310
            f"{self._base_url}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
        )
        started = time.monotonic()
        try:
            # Scheme is validated in __init__, so only http(s) URLs reach this call.
            opener = urllib.request.build_opener()
            with opener.open(http_request, timeout=request.timeout_seconds) as response:
                raw = response.read()
                request_id = response.headers.get("x-request-id", "")
        except urllib.error.HTTPError as exc:
            error_class = "rate_limited" if exc.code == 429 else "server_error"
            if exc.code in {401, 403}:
                error_class = "auth"
            elif exc.code == 400:
                error_class = "invalid_request"
            raise ProviderError(
                f"provider returned HTTP {exc.code}", error_class=error_class
            ) from exc
        except TimeoutError as exc:
            raise ProviderTimeout() from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ProviderTimeout() from exc
            raise ProviderError("provider transport failure", error_class="transport") from exc
        latency = int((time.monotonic() - started) * 1000)

        try:
            payload = json.loads(raw.decode("utf-8"))
            text = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage", {})
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "provider returned an unparsable body", error_class="server_error"
            ) from exc
        if not isinstance(text, str):
            raise ProviderError("provider returned non-text content", error_class="server_error")
        input_tokens = int(usage.get("prompt_tokens", estimate_tokens(request.user_prompt)))
        output_tokens = int(usage.get("completion_tokens", estimate_tokens(text)))
        return ModelResponse(
            text=text,
            provider=self.name,
            model=self.model,
            request_id=str(payload.get("id", request_id) or ""),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency,
            estimated_cost_usd=input_tokens * self._input_cost + output_tokens * self._output_cost,
        )
