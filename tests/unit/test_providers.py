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


