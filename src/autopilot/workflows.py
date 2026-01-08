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


