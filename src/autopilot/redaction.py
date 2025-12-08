"""Redaction of secrets and personal data before logging or persistence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Order matters: broad token patterns run before generic assignments so the
# replacement marker is stable and idempotent.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}")),
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b(password|passwd|secret|token|api[_\-]?key)\b"
            r"(\s*[:=]\s*)([\"']?)([^\s\"',;]{6,})\3"
        ),
    ),
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    ),
)

_SECRET_MARKERS = tuple(marker for marker, _ in _PATTERNS if marker not in {"email"})


@dataclass(frozen=True, slots=True)
class Redaction:
    text: str
    hits: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.hits)

    @property
    def contained_secret(self) -> bool:
        return any(hit in _SECRET_MARKERS for hit in self.hits)


def _apply(name: str, pattern: re.Pattern[str], text: str, hits: list[str]) -> str:
    """Apply one redaction pattern, recording each category that matched."""

    if name == "assigned_secret":

        def _replace_assignment(match: re.Match[str]) -> str:
            hits.append(name)
            return f"{match.group(1)}{match.group(2)}[REDACTED:{name}]"

        return pattern.sub(_replace_assignment, text)

    def _replace(match: re.Match[str]) -> str:
        hits.append(name)
        return f"[REDACTED:{name}]"

    return pattern.sub(_replace, text)


def redact(text: str) -> Redaction:
    """Redact secrets and email addresses from text.

    The replacement includes the category, which keeps logs useful while
    removing the sensitive value. Running redaction twice yields the same text.
    """

    if not text:
        return Redaction(text="")
    hits: list[str] = []
    result = text
    for name, pattern in _PATTERNS:
        result = _apply(name, pattern, result, hits)
    return Redaction(text=result, hits=tuple(dict.fromkeys(hits)))


def redact_value(value: Any) -> Any:
    """Recursively redact strings inside JSON-like values."""

    if isinstance(value, str):
        return redact(value).text
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    return value


def contains_secret(text: str) -> bool:
    return redact(text).contained_secret
