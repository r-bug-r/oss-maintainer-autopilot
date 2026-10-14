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


def _triage(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "summary": "Startup crashes when config is empty.",
        "labels": ["bug"],
        "missing_reproduction_questions": ["Which Python version?"],
        "duplicate_explanations": [{"candidate_number": 8, "explanation": "Same traceback."}],
        "proposed_comment": "Thanks for the report. Which Python version are you on?",
        "confidence": 0.8,
        "risk": "low",
    }
    payload.update(overrides)
    return payload


def _parse(payload: dict[str, object], **kwargs: object) -> WorkflowOutput:
    return parse_workflow_output(
        "issue_triage",
        json.dumps(payload),
        label_taxonomy=TAXONOMY,
        candidate_numbers=(8,),
        **kwargs,
    )


def test_valid_triage_output_is_accepted() -> None:
    output = _parse(_triage())
    assert isinstance(output, IssueTriageOutput)
    assert output.labels == ("bug",)
    assert output.duplicate_explanations == ((8, "Same traceback."),)
    assert output.confidence == 0.8


def test_unknown_top_level_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unsupported field"):
        _parse(_triage(extra="sneaky"))


def test_unknown_label_is_rejected() -> None:
    with pytest.raises(ValidationError, match="taxonomy"):
        _parse(_triage(labels=["bug", "invented-label"]))


def test_duplicate_candidate_must_have_been_offered() -> None:
    with pytest.raises(ValidationError, match="not an offered candidate"):
        _parse(_triage(duplicate_explanations=[{"candidate_number": 99, "explanation": "x"}]))


def test_confidence_out_of_range_is_rejected() -> None:
    with pytest.raises(ValidationError, match="between 0 and 1"):
        _parse(_triage(confidence=1.5))
    with pytest.raises(ValidationError, match="must be a number"):
        _parse(_triage(confidence=True))


def test_comment_length_is_bounded() -> None:
    with pytest.raises(ValidationError, match="exceeds"):
        _parse(_triage(proposed_comment="x" * 4001))


def test_missing_field_is_reported() -> None:
    payload = _triage()
    payload.pop("risk")
    with pytest.raises(ValidationError, match="missing field"):
        _parse(payload)


def test_non_json_output_is_rejected() -> None:
    with pytest.raises(ValidationError, match="valid JSON"):
        parse_workflow_output("issue_triage", "not json", label_taxonomy=TAXONOMY)


def test_json_array_output_is_rejected() -> None:
    with pytest.raises(ValidationError, match="JSON object"):
        parse_workflow_output("issue_triage", "[]", label_taxonomy=TAXONOMY)


def test_pr_output_rejects_unknown_fields() -> None:
    payload = {
        "summary": "Adds validation.",
        "review_notes": ["Checks empty input."],
        "risk_notes": ["Touches error handling."],
        "proposed_comment": None,
        "confidence": 0.6,
        "risk": "medium",
        "approve": True,
    }
    with pytest.raises(ValidationError, match="unsupported field"):
        parse_workflow_output("pr_review", json.dumps(payload))


def test_pr_output_accepts_null_comment() -> None:
    payload = {
        "summary": "Adds validation.",
        "review_notes": ["Checks empty input."],
        "risk_notes": [],
        "proposed_comment": None,
        "confidence": 0.6,
        "risk": "medium",
    }
    output = parse_workflow_output("pr_review", json.dumps(payload))
    assert isinstance(output, PullRequestReviewOutput)
    assert output.proposed_comment is None


def test_release_sections_are_normalized() -> None:
    payload = {
        "summary": "Two fixes.",
        "sections": [
            {"heading": "Fixed", "items": ["Empty config no longer crashes."]},
            {"heading": "Changed", "items": []},
        ],
        "proposed_comment": "Draft notes.",
        "confidence": 0.7,
        "risk": "low",
    }
    output = parse_workflow_output("release_notes", json.dumps(payload))
    assert isinstance(output, ReleaseNotesOutput)
    assert output.sections[0][0] == "Fixed"
    assert output.sections[0][1] == ("Empty config no longer crashes.",)


def test_release_section_heading_is_bounded() -> None:
    payload = {
        "summary": "x",
        "sections": [{"heading": "h" * 200, "items": ["a"]}],
        "proposed_comment": None,
        "confidence": 0.5,
        "risk": "low",
    }
    with pytest.raises(ValidationError, match="exceeds"):
        parse_workflow_output("release_notes", json.dumps(payload))


def test_payload_round_trips_to_plain_json() -> None:
    output = _parse(_triage())
    assert isinstance(output, IssueTriageOutput)
    payload = output_as_payload(output)
    assert payload["labels"] == ["bug"]
    assert payload["duplicate_explanations"] == [
        {"candidate_number": 8, "explanation": "Same traceback."}
    ]
    json.dumps(payload)


def test_citations_reference_real_issue_numbers() -> None:
    output = _parse(_triage())
    citations = citations_for(output, subject_number=7)
    assert [(c.kind, c.number) for c in citations] == [("issue", 7), ("issue", 8)]
    assert all(c.label for c in citations)
