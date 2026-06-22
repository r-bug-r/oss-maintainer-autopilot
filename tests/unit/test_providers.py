"""Contract tests for the deterministic fake provider.

The fake provider is the only provider exercised in CI, so its output must be
derived from the supplied context rather than from fixed strings; otherwise a
prompt-building regression would pass unnoticed.
"""

from __future__ import annotations

import json
from typing import Any

from autopilot.prompts import build_prompt
from autopilot.providers import FakeProvider, ModelRequest


def _request(context: dict[str, Any], workflow: str = "issue_triage") -> ModelRequest:
    bundle = build_prompt(workflow, context)  # type: ignore[arg-type]
    return ModelRequest(
        workflow=workflow,
        prompt_version=bundle.version,
        system_prompt=bundle.system_prompt,
        user_prompt=bundle.user_prompt,
    )


def test_issue_triage_uses_the_supplied_issue() -> None:
    context: dict[str, Any] = {
        "repository": "acme/widget",
        "number": 41,
        "title": "Crash on startup with empty config file",
        "body": "Running the tool raises a traceback.",
        "author": "reporter-a",
        "labels": ["bug"],
        "label_taxonomy": ["bug", "documentation", "needs-reproduction", "question"],
        "duplicate_candidates": [{"number": 42, "title": "Crash when config is empty"}],
    }
    response = FakeProvider().complete(_request(context))
    payload: dict[str, Any] = json.loads(response.text)

    assert "#41" in payload["summary"]
    assert "Crash on startup with empty config file" in payload["summary"]
    assert payload["labels"] == ["bug", "needs-reproduction"]
    assert payload["missing_reproduction_questions"]
    assert payload["duplicate_explanations"][0]["candidate_number"] == 42
    assert "#42" in payload["proposed_comment"]
    assert payload["risk"] == "medium"
    assert response.estimated_cost_usd == 0.0


def test_issue_triage_skips_questions_when_details_are_present() -> None:
    context: dict[str, Any] = {
        "number": 7,
        "title": "Documentation typo in README",
        "body": (
            "Version 0.1.0. Steps to reproduce: open the README. "
            "Expected the install command to work."
        ),
        "labels": ["documentation"],
        "label_taxonomy": ["bug", "documentation"],
        "duplicate_candidates": [],
    }
    payload: dict[str, Any] = json.loads(FakeProvider().complete(_request(context)).text)

    assert payload["labels"] == ["documentation"]
    assert payload["missing_reproduction_questions"] == []
    assert payload["proposed_comment"] is None
    assert payload["risk"] == "low"
