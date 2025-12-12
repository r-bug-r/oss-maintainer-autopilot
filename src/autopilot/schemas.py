"""Strict validation of model output before it can reach business logic.

The validator is hand-written rather than schema-library-driven so that the
exact allowed fields, bounds, and cross-references are visible in one place.
Unknown fields are rejected: a model that invents a new action cannot smuggle
it into the proposal store.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .models import Citation, Risk, ValidationError, WorkflowName

MAX_SUMMARY_CHARS = 2000
MAX_COMMENT_CHARS = 4000
MAX_LIST_ITEMS = 10
MAX_ITEM_CHARS = 400


@dataclass(frozen=True, slots=True)
