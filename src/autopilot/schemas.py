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


def _text_list(value: object, where: str, *, max_items: int = MAX_LIST_ITEMS) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValidationError(f"{where} must be a list")
    if len(value) > max_items:
        raise ValidationError(f"{where} exceeds {max_items} items")
    items = tuple(
        _text(item, f"{where}[{index}]", max_chars=MAX_ITEM_CHARS)
        for index, item in enumerate(value)
    )
    return tuple(dict.fromkeys(items))


def _optional_text(value: object, where: str, *, max_chars: int) -> str | None:
    if value is None:
        return None
    return _text(value, where, max_chars=max_chars)


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("confidence must be a number")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValidationError("confidence must be between 0 and 1")
    return number


def _risk(value: object) -> Risk:
    if value == "low":
        return "low"
    if value == "medium":
        return "medium"
    if value == "high":
        return "high"
    raise ValidationError("risk must be one of low, medium, high")


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


def _parse_triage(
    obj: dict[str, Any], *, label_taxonomy: tuple[str, ...], candidate_numbers: tuple[int, ...]
) -> IssueTriageOutput:
    _reject_unknown(obj, _TRIAGE_FIELDS, "issue_triage output")
    labels = _text_list(obj["labels"], "labels", max_items=5)
    unknown_labels = [label for label in labels if label not in label_taxonomy]
    if unknown_labels:
        raise ValidationError(
            "labels outside the configured taxonomy: " + ", ".join(unknown_labels)
        )

    raw_duplicates = obj["duplicate_explanations"]
    if not isinstance(raw_duplicates, list):
        raise ValidationError("duplicate_explanations must be a list")
    if len(raw_duplicates) > 5:
        raise ValidationError("duplicate_explanations exceeds 5 items")
    duplicates: list[tuple[int, str]] = []
    for index, item in enumerate(raw_duplicates):
        entry = _require_object(item, f"duplicate_explanations[{index}]")
        _reject_unknown(
            entry, {"candidate_number", "explanation"}, f"duplicate_explanations[{index}]"
        )
        number = entry["candidate_number"]
        if isinstance(number, bool) or not isinstance(number, int):
            raise ValidationError(
                f"duplicate_explanations[{index}].candidate_number must be an integer"
            )
        if number not in candidate_numbers:
            raise ValidationError(
                f"duplicate_explanations[{index}].candidate_number {number} "
                "was not an offered candidate"
            )
        duplicates.append((number, _text(entry["explanation"], "explanation", max_chars=500)))

    return IssueTriageOutput(
        summary=_text(obj["summary"], "summary", max_chars=MAX_SUMMARY_CHARS),
        labels=labels,
        missing_reproduction_questions=_text_list(
            obj["missing_reproduction_questions"], "missing_reproduction_questions", max_items=5
        ),
        duplicate_explanations=tuple(duplicates),
        proposed_comment=_optional_text(
            obj["proposed_comment"], "proposed_comment", max_chars=MAX_COMMENT_CHARS
        ),
        confidence=_confidence(obj["confidence"]),
        risk=_risk(obj["risk"]),
    )


def _parse_pr(obj: dict[str, Any]) -> PullRequestReviewOutput:
    _reject_unknown(obj, _PR_FIELDS, "pr_review output")
    return PullRequestReviewOutput(
        summary=_text(obj["summary"], "summary", max_chars=MAX_SUMMARY_CHARS),
        review_notes=_text_list(obj["review_notes"], "review_notes"),
        risk_notes=_text_list(obj["risk_notes"], "risk_notes"),
        proposed_comment=_optional_text(
            obj["proposed_comment"], "proposed_comment", max_chars=MAX_COMMENT_CHARS
        ),
        confidence=_confidence(obj["confidence"]),
        risk=_risk(obj["risk"]),
    )


def _parse_release(obj: dict[str, Any]) -> ReleaseNotesOutput:
    _reject_unknown(obj, _RELEASE_FIELDS, "release_notes output")
    raw_sections = obj["sections"]
    if not isinstance(raw_sections, list):
        raise ValidationError("sections must be a list")
    if len(raw_sections) > 10:
        raise ValidationError("sections exceeds 10 items")
    sections: list[tuple[str, tuple[str, ...]]] = []
    for index, item in enumerate(raw_sections):
        entry = _require_object(item, f"sections[{index}]")
        _reject_unknown(entry, {"heading", "items"}, f"sections[{index}]")
        sections.append(
            (
                _text(entry["heading"], "heading", max_chars=120),
                _text_list(entry["items"], f"sections[{index}].items", max_items=50),
            )
        )
    return ReleaseNotesOutput(
        summary=_text(obj["summary"], "summary", max_chars=MAX_SUMMARY_CHARS),
        sections=tuple(sections),
        proposed_comment=_optional_text(
            obj["proposed_comment"], "proposed_comment", max_chars=MAX_COMMENT_CHARS
        ),
        confidence=_confidence(obj["confidence"]),
        risk=_risk(obj["risk"]),
    )


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
