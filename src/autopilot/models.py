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


