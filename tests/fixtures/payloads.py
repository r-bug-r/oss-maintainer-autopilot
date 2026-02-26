"""Synthetic forge payloads shared by tests.

All values are invented. Nothing here refers to a real repository, person, or
credential. Payloads mirror the subset of the GitHub webhook shape that
`autopilot.webhooks` normalizes.
"""

from __future__ import annotations

from typing import Any

REPOSITORY_PAYLOAD: dict[str, Any] = {
    "id": 4242,
    "name": "widget",
    "full_name": "acme/widget",
    "owner": {"login": "acme", "id": 4000, "type": "Organization"},
    "private": False,
}

ISSUE_PAYLOAD: dict[str, Any] = {
    "id": 9001,
    "number": 7,
    "title": "Crash on startup",
    "body": "Steps: run it.",
    "user": {"login": "reporter", "id": 4100},
    "state": "open",
    "labels": [{"name": "bug"}],
    "html_url": "https://example.invalid/acme/widget/issues/7",
    "created_at": "2026-01-05T09:00:00Z",
    "updated_at": "2026-01-05T09:00:00Z",
}

PULL_REQUEST_PAYLOAD: dict[str, Any] = {
    "id": 7100,
    "number": 12,
    "title": "Handle an empty config file",
    "body": "Fixes the traceback by validating the parsed document.",
    "user": {"login": "contributor", "id": 4200},
    "state": "open",
    "base": {"ref": "main"},
    "head": {"ref": "empty-config"},
    "additions": 24,
    "deletions": 3,
    "changed_files": 2,
    "html_url": "https://example.invalid/acme/widget/pull/12",
}


def issue_opened_payload(**overrides: Any) -> dict[str, Any]:
    """Return a full `issues` webhook payload with optional top-level overrides."""

    payload: dict[str, Any] = {
        "action": "opened",
        "repository": dict(REPOSITORY_PAYLOAD),
        "issue": dict(ISSUE_PAYLOAD),
    }
    payload.update(overrides)
    return payload


def pull_request_opened_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "opened",
        "repository": dict(REPOSITORY_PAYLOAD),
        "pull_request": dict(PULL_REQUEST_PAYLOAD),
    }
    payload.update(overrides)
    return payload
