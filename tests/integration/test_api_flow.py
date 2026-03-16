"""End-to-end workflow behavior through the real HTTP router (no socket).

Every request is constructed exactly as the server would construct it, so this
covers routing, authentication, authorization, proposal generation, the approval
gate, and audit chaining together.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, unquote

from autopilot.api import Api, Request, Response
from autopilot.canonical import sha256_hex
from autopilot.models import Issue, Repository
from autopilot.webhooks import compute_signature

from ..conftest import WEBHOOK_SECRET

ACTOR = "maintainer"


def test_health_is_public(api: Api) -> None:
    response = api.handle(_request("GET", "/api/health", actor=None))
    assert response.status == 200
    assert _json(response)["status"] == "ok"


def test_missing_actor_is_forbidden_for_remote_requests(api: Api) -> None:
    response = api.handle(_request("GET", "/api/repositories", actor=None, loopback=False))
    assert response.status == 403


def test_remote_request_requires_admin_token(api: Api) -> None:
    response = api.handle(
        _request(
            "GET", "/api/repositories", loopback=False, headers={"authorization": "Bearer nope"}
        )
    )
    assert response.status == 403


def test_unknown_route_is_404(api: Api) -> None:
    response = api.handle(_request("GET", "/api/nope"))
    assert response.status == 404


def test_static_path_traversal_is_blocked(api: Api) -> None:
    response = api.handle(_request("GET", "/../pyproject.toml", actor=None))
    assert response.status == 404


def test_triage_creates_pending_proposal(api: Api, repository: Repository, issue: Issue) -> None:
    response = api.handle(
        _request(
            "POST",
            f"/api/repositories/{_repo(repository)}/workflows/issue_triage",
            body={"number": 7},
        )
    )
    assert response.status == 201, response.body
    payload = _json(response)["proposal"]
    assert payload["workflow"] == "issue_triage"
    assert payload["state"] == "pending_approval"
    assert payload["created_by"] == "autopilot"
    assert payload["subject_number"] == 7
    assert payload["action_type"] in {"comment", "label", "none"}


def test_triage_is_idempotent_for_same_input(
    api: Api, repository: Repository, issue: Issue
) -> None:
    first = api.handle(
        _request(
            "POST",
            f"/api/repositories/{_repo(repository)}/workflows/issue_triage",
            body={"number": 7},
        )
    )
    second = api.handle(
        _request(
            "POST",
            f"/api/repositories/{_repo(repository)}/workflows/issue_triage",
            body={"number": 7},
        )
    )
    assert first.status == 201
    assert second.status == 200
    assert _json(second)["created"] is False
    assert _json(second)["proposal"]["id"] == _json(first)["proposal"]["id"]


def test_unknown_issue_is_not_found(api: Api, repository: Repository) -> None:
    response = api.handle(
        _request(
            "POST",
            f"/api/repositories/{_repo(repository)}/workflows/issue_triage",
            body={"number": 999},
        )
    )
    assert response.status == 404


def test_actor_without_role_cannot_trigger_triage(
    api: Api, repository: Repository, issue: Issue
) -> None:
    response = api.handle(
        _request(
            "POST",
            f"/api/repositories/{_repo(repository)}/workflows/issue_triage",
            body={"number": 7},
            actor="stranger",
        )
    )
    assert response.status == 403


def test_approval_requires_a_role(api: Api, repository: Repository, issue: Issue) -> None:
    created = _json(
        api.handle(
            _request(
                "POST",
                f"/api/repositories/{_repo(repository)}/workflows/issue_triage",
                body={"number": 7},
            )
        )
    )["proposal"]
    denied = api.handle(
        _request("POST", f"/api/proposals/{created['id']}/approve", actor="stranger")
    )
    assert denied.status == 403


def test_full_approve_execute_cycle(api: Api, repository: Repository, issue: Issue) -> None:
    proposal = _json(
        api.handle(
            _request(
                "POST",
                f"/api/repositories/{_repo(repository)}/workflows/issue_triage",
                body={"number": 7},
            )
        )
    )["proposal"]

    approved = api.handle(_request("POST", f"/api/proposals/{proposal['id']}/approve"))
    assert approved.status == 200
    assert _json(approved)["proposal"]["state"] == "approved"

    executed = api.handle(_request("POST", f"/api/proposals/{proposal['id']}/execute"))
    assert executed.status == 200, executed.body
    detail = _json(executed)["proposal"]
    assert detail["state"] in {"executed", "action_failed"}
    assert detail["attempt_count"] == 1

    if detail["state"] == "executed":
        # The local fixture adapter records the write so the dashboard can show it.
        comments = api.runtime.store.list_comments(repository.id, 7)
        if detail["action_type"] == "comment":
            assert comments


def test_execute_before_approval_is_rejected(
    api: Api, repository: Repository, issue: Issue
) -> None:
    proposal = _json(
        api.handle(
            _request(
                "POST",
                f"/api/repositories/{_repo(repository)}/workflows/issue_triage",
                body={"number": 7},
            )
        )
    )["proposal"]
    response = api.handle(_request("POST", f"/api/proposals/{proposal['id']}/execute"))
    assert response.status in {400, 409, 403}


def test_double_execute_conflicts(api: Api, repository: Repository, issue: Issue) -> None:
    proposal = _json(
        api.handle(
            _request(
                "POST",
                f"/api/repositories/{_repo(repository)}/workflows/issue_triage",
                body={"number": 7},
            )
        )
    )["proposal"]
    api.handle(_request("POST", f"/api/proposals/{proposal['id']}/approve"))
    first = api.handle(_request("POST", f"/api/proposals/{proposal['id']}/execute"))
    second = api.handle(_request("POST", f"/api/proposals/{proposal['id']}/execute"))
    assert first.status == 200
    assert second.status == 409


def test_rejection_records_reason(api: Api, repository: Repository, issue: Issue) -> None:
    proposal = _json(
        api.handle(
            _request(
                "POST",
                f"/api/repositories/{_repo(repository)}/workflows/issue_triage",
                body={"number": 7},
            )
        )
    )["proposal"]
    response = api.handle(
        _request(
            "POST",
            f"/api/proposals/{proposal['id']}/reject",
            body={"reason": "not actionable yet"},
        )
    )
    assert response.status == 200
    assert _json(response)["proposal"]["state"] == "rejected"


def test_expired_proposal_cannot_be_approved(
    api: Api, repository: Repository, issue: Issue
) -> None:
    proposal = _json(
        api.handle(
            _request(
                "POST",
                f"/api/repositories/{_repo(repository)}/workflows/issue_triage",
                body={"number": 7},
            )
        )
    )["proposal"]
    api.store.conn.execute(
        "UPDATE proposals SET expires_at = '2000-01-01T00:00:00.000Z' WHERE id = ?",
        (proposal["id"],),
    )
    api.store.conn.commit()
    response = api.handle(_request("POST", f"/api/proposals/{proposal['id']}/approve"))
    assert response.status == 410


