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
class IssueTriageOutput:
    summary: str
    labels: tuple[str, ...]
    missing_reproduction_questions: tuple[str, ...]
    duplicate_explanations: tuple[tuple[int, str], ...]
    proposed_comment: str | None
    confidence: float
    risk: Risk


@dataclass(frozen=True, slots=True)
class PullRequestReviewOutput:
    summary: str
    review_notes: tuple[str, ...]
    risk_notes: tuple[str, ...]
    proposed_comment: str | None
    confidence: float
    risk: Risk


@dataclass(frozen=True, slots=True)
class ReleaseNotesOutput:
    summary: str
    sections: tuple[tuple[str, tuple[str, ...]], ...]
    proposed_comment: str | None
    confidence: float
    risk: Risk


WorkflowOutput = IssueTriageOutput | PullRequestReviewOutput | ReleaseNotesOutput

_TRIAGE_FIELDS = {
    "summary",
    "labels",
    "missing_reproduction_questions",
    "duplicate_explanations",
    "proposed_comment",
    "confidence",
    "risk",
}
_PR_FIELDS = {"summary", "review_notes", "risk_notes", "proposed_comment", "confidence", "risk"}
_RELEASE_FIELDS = {"summary", "sections", "proposed_comment", "confidence", "risk"}


def _require_object(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{where} must be a JSON object")
    return value


def _reject_unknown(obj: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise ValidationError(f"{where} contains unsupported field(s): {', '.join(unknown)}")
    missing = sorted(allowed - set(obj))
    if missing:
        raise ValidationError(f"{where} is missing field(s): {', '.join(missing)}")


def _text(value: object, where: str, *, max_chars: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{where} must be a string")
    stripped = value.strip()
    if not stripped and not allow_empty:
        raise ValidationError(f"{where} must not be empty")
    if len(stripped) > max_chars:
        raise ValidationError(f"{where} exceeds {max_chars} characters")
    return stripped


def parse_workflow_output(
    workflow: WorkflowName,
    raw_text: str,
    *,
    label_taxonomy: tuple[str, ...] = (),
    candidate_numbers: tuple[int, ...] = (),
) -> WorkflowOutput:
    """Parse and validate a provider response for a workflow."""

    try:
        decoded = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValidationError("model output is not valid JSON") from exc
    obj = _require_object(decoded, "model output")

    if workflow == "issue_triage":
        return _parse_triage(
            obj, label_taxonomy=label_taxonomy, candidate_numbers=candidate_numbers
        )
    if workflow == "pr_review":
        return _parse_pr(obj)
    if workflow == "release_notes":
        return _parse_release(obj)
    raise ValidationError(f"unsupported workflow: {workflow}")


def output_as_payload(output: WorkflowOutput) -> dict[str, Any]:
    """Convert a validated output into a JSON-safe proposal payload."""

    if isinstance(output, IssueTriageOutput):
        return {
            "summary": output.summary,
            "labels": list(output.labels),
            "missing_reproduction_questions": list(output.missing_reproduction_questions),
            "duplicate_explanations": [
                {"candidate_number": number, "explanation": explanation}
                for number, explanation in output.duplicate_explanations
            ],
            "proposed_comment": output.proposed_comment,
            "confidence": output.confidence,
            "risk": output.risk,
        }
    if isinstance(output, PullRequestReviewOutput):
        return {
            "summary": output.summary,
            "review_notes": list(output.review_notes),
            "risk_notes": list(output.risk_notes),
            "proposed_comment": output.proposed_comment,
            "confidence": output.confidence,
            "risk": output.risk,
        }
    return {
        "summary": output.summary,
        "sections": [
            {"heading": heading, "items": list(items)} for heading, items in output.sections
        ],
        "proposed_comment": output.proposed_comment,
        "confidence": output.confidence,
        "risk": output.risk,
    }


def citations_for(output: WorkflowOutput, *, subject_number: int) -> tuple[Citation, ...]:
    """Attach source citations the UI can render next to generated text."""

    citations = [Citation(kind="issue", number=subject_number, label=f"#{subject_number}")]
    if isinstance(output, IssueTriageOutput):
        citations.extend(
            Citation(kind="issue", number=number, label=f"#{number} (duplicate candidate)")
            for number, _ in output.duplicate_explanations
        )
    return tuple(citations)
