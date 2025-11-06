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
class PullRequestFile:
    path: str
    additions: int = 0
    deletions: int = 0
    patch_summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "additions": self.additions,
            "deletions": self.deletions,
            "patch_summary": _redacted_text(self.patch_summary, limit=400),
        }


@dataclass(frozen=True, slots=True)
class PullRequest:
    repository_id: str
    number: int
    title: str
    body: str
    author_login: str | None
    state: str
    base_ref: str = ""
    head_ref: str = ""
    files: tuple[PullRequestFile, ...] = ()
    external_id: str | None = None

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValidationError("pull request number must be positive")

    def normalized_dict(self) -> dict[str, Any]:
        return {
            "repository_id": self.repository_id,
            "number": self.number,
            "title": _redacted_text(self.title, limit=300),
            "body": _redacted_text(self.body, limit=20_000),
            "author_login": self.author_login,
            "state": self.state,
            "base_ref": self.base_ref,
            "head_ref": self.head_ref,
            "files": [item.as_dict() for item in self.files[:200]],
        }

    def input_sha256(self) -> str:
        return hash_canonical(self.normalized_dict())


@dataclass(frozen=True, slots=True)
class Comment:
    id: str
    repository_id: str
    issue_number: int
    body: str
    author_login: str
    source: Literal["fetched", "posted"]
    idempotency_key: str | None = None
    created_at: str = ""


@dataclass(frozen=True, slots=True)
class Citation:
    """A reference from generated text back to a concrete source."""

    kind: Literal["issue", "pull_request", "comment"]
    number: int
    label: str

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "number": self.number, "label": self.label}


@dataclass(frozen=True, slots=True)
class Proposal:
    id: str
    repository_id: str
    workflow: WorkflowName
    subject_type: Literal["issue", "pull_request", "repository"]
    subject_number: int | None
    state: ProposalState
    action_type: ActionType
    action_payload: dict[str, Any]
    payload: dict[str, Any]
    citations: tuple[Citation, ...]
    input_sha256: str
    input_context: dict[str, Any]
    prompt_version: str | None
    provider: str | None
    model: str | None
    model_request_id: str | None
    confidence: float | None
    risk: Risk | None
    cost_usd: float
    created_by: str
    created_at: str
    expires_at: str
    approved_by: str | None = None
    approved_at: str | None = None
    rejected_by: str | None = None
    rejected_at: str | None = None
    executed_at: str | None = None
    attempt_count: int = 0
    last_error: str | None = None
    error_class: str | None = None
    idempotency_key: str = ""

    @property
    def is_open(self) -> bool:
        return self.state in {"pending_approval", "approved", "action_failed"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "repository_id": self.repository_id,
            "workflow": self.workflow,
            "subject_type": self.subject_type,
            "subject_number": self.subject_number,
            "state": self.state,
            "action_type": self.action_type,
            "action_payload": self.action_payload,
            "payload": self.payload,
            "citations": [citation.as_dict() for citation in self.citations],
            "input_sha256": self.input_sha256,
            "prompt_version": self.prompt_version,
            "provider": self.provider,
            "model": self.model,
            "model_request_id": self.model_request_id,
            "confidence": self.confidence,
            "risk": self.risk,
            "cost_usd": self.cost_usd,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "rejected_by": self.rejected_by,
            "rejected_at": self.rejected_at,
            "executed_at": self.executed_at,
            "attempt_count": self.attempt_count,
            "last_error": self.last_error,
            "error_class": self.error_class,
        }


@dataclass(frozen=True, slots=True)
class RepositorySettings:
    repository_id: str
    workflows: dict[str, bool] = field(
        default_factory=lambda: {"issue_triage": True, "pr_review": True, "release_notes": False}
    )
    label_taxonomy: tuple[str, ...] = (
        "bug",
        "documentation",
        "enhancement",
        "question",
        "good first issue",
        "needs-reproduction",
        "duplicate",
    )
    daily_request_limit: int = 200
    daily_cost_limit_usd: float = 0.0
    proposal_ttl_minutes: int = 60

    def workflow_enabled(self, workflow: str) -> bool:
        return bool(self.workflows.get(workflow, False))

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository_id": self.repository_id,
            "workflows": dict(self.workflows),
            "label_taxonomy": list(self.label_taxonomy),
            "daily_request_limit": self.daily_request_limit,
            "daily_cost_limit_usd": self.daily_cost_limit_usd,
            "proposal_ttl_minutes": self.proposal_ttl_minutes,
        }
