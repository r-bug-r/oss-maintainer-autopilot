from __future__ import annotations

import json

import pytest

from autopilot.models import ValidationError
from autopilot.schemas import (
    IssueTriageOutput,
    PullRequestReviewOutput,
    ReleaseNotesOutput,
    WorkflowOutput,
    citations_for,
    output_as_payload,
    parse_workflow_output,
)

TAXONOMY = ("bug", "enhancement", "documentation", "duplicate")


