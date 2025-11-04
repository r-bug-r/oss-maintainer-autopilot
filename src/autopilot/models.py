"""Normalized domain models.

Every field that reaches a prompt, a proposal, or the audit log comes from one
of these types. Adapters convert provider payloads into these models; nothing
downstream parses raw webhook JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .canonical import hash_canonical
from .redaction import redact

Provider = Literal["github", "gitlab"]
Role = Literal["owner", "maintainer", "approver"]
WorkflowName = Literal["issue_triage", "pr_review", "release_notes"]
ActionType = Literal["comment", "label", "none"]
Risk = Literal["low", "medium", "high"]
ProposalState = Literal[
    "pending_approval",
    "approved",
    "executing",
    "executed",
    "rejected",
    "expired",
    "action_failed",
]


class ValidationError(ValueError):
    """Raised when untrusted input fails domain validation."""


def _clean_text(value: str, *, limit: int) -> str:
    """Normalize line endings and truncate, preserving a marker."""

    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "\u2026"


def _redacted_text(value: str, *, limit: int) -> str:
    return _clean_text(redact(value).text, limit=limit)


@dataclass(frozen=True, slots=True)
class Repository:
    id: str
    provider: Provider
    full_name: str
    owner_login: str
    external_id: str | None = None

    @classmethod
    def build(
        cls,
        *,
        provider: Provider,
        full_name: str,
        owner_login: str,
        external_id: str | None = None,
    ) -> Repository:
        normalized_name = full_name.strip()
        if normalized_name.count("/") != 1:
            raise ValidationError("repository full_name must be owner/name")
        if not owner_login.strip():
            raise ValidationError("repository owner is required")
        repo_id = f"{provider}:{normalized_name.lower()}"
        return cls(
            id=repo_id,
            provider=provider,
            full_name=normalized_name,
            owner_login=owner_login.strip(),
            external_id=external_id,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "full_name": self.full_name,
            "owner_login": self.owner_login,
            "external_id": self.external_id,
        }


@dataclass(frozen=True, slots=True)
class Issue:
    repository_id: str
    number: int
    title: str
    body: str
    author_login: str | None
    state: str
    labels: tuple[str, ...] = ()
    url: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    external_id: str | None = None

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValidationError("issue number must be positive")

    def normalized_dict(self) -> dict[str, Any]:
        return {
            "repository_id": self.repository_id,
            "number": self.number,
            "title": _redacted_text(self.title, limit=300),
            "body": _redacted_text(self.body, limit=20_000),
            "author_login": self.author_login,
            "state": self.state,
            "labels": list(self.labels),
            "url": self.url,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def input_sha256(self) -> str:
        return hash_canonical(self.normalized_dict())


@dataclass(frozen=True, slots=True)
