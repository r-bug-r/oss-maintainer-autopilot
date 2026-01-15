"""Workflow orchestration: proposal creation and gated execution.

The invariant this module protects: nothing leaves the process toward a forge
unless a stored proposal reached `approved` through an explicit human action.
Generation and execution are separate calls, and execution is claimed with a
single conditional UPDATE so a repeated click cannot post twice.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from .adapters import (
    AdapterAuthError,
    AdapterError,
    AdapterNotFound,
    AdapterRateLimited,
    AdapterTransient,
    RepositoryAdapter,
)
from .canonical import canonical_json, hash_canonical
from .duplicates import find_candidates
from .gateway import BudgetExceeded, ModelGateway
from .models import (
    ActionType,
    Citation,
    Issue,
    Proposal,
    PullRequest,
    Repository,
    RepositorySettings,
    ValidationError,
    WorkflowName,
)
from .prompts import build_prompt, injection_signals
from .providers import ModelRequest, ProviderError
from .redaction import redact, redact_value
from .schemas import (
    IssueTriageOutput,
    citations_for,
    output_as_payload,
    parse_workflow_output,
)
from .store import Conflict, Expired, NotFound, Store, Unauthorized, new_id

SubjectType = Literal["issue", "pull_request", "repository"]


class WorkflowError(Exception):
    """A workflow could not produce a proposal."""


class WorkflowDisabled(WorkflowError):
    pass


class _ExistingProposal(Exception):
    """Internal signal: this exact input already produced a proposal."""

    def __init__(self, proposal: Proposal) -> None:
        super().__init__(proposal.id)
        self.proposal = proposal


@dataclass(frozen=True, slots=True)
class GenerationResult:
    proposal: Proposal
    created: bool


def proposal_summary(proposal: Proposal) -> dict[str, Any]:
    payload = proposal.payload
    comment = proposal.action_payload.get("comment")
    return {
        "id": proposal.id,
        "repository_id": proposal.repository_id,
        "workflow": proposal.workflow,
        "subject_type": proposal.subject_type,
        "subject_number": proposal.subject_number,
        "state": proposal.state,
        "action_type": proposal.action_type,
        "confidence": proposal.confidence,
        "risk": proposal.risk,
        "summary": payload.get("summary", ""),
        "comment_preview": (comment[:280] + "…")
        if isinstance(comment, str) and len(comment) > 280
        else comment,
        "labels": proposal.action_payload.get("labels", []),
        "citations": [citation.as_dict() for citation in proposal.citations],
        "created_at": proposal.created_at,
        "expires_at": proposal.expires_at,
        "approved_by": proposal.approved_by,
        "executed_at": proposal.executed_at,
        "attempt_count": proposal.attempt_count,
        "last_error": proposal.last_error,
        "cost_usd": proposal.cost_usd,
        "input_sha256": proposal.input_sha256,
    }


def proposal_detail(proposal: Proposal) -> dict[str, Any]:
    detail = proposal.as_dict()
    detail["input_context"] = proposal.input_context
    detail["payload_canonical"] = canonical_json(proposal.payload)
    return detail


__all__ = [
    "Expired",
    "GenerationResult",
    "WorkflowDisabled",
    "WorkflowEngine",
    "WorkflowError",
    "proposal_detail",
    "proposal_summary",
]
