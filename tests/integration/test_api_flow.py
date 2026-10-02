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


def _request(
    method: str,
    path: str,
    *,
    body: object | None = None,
    actor: str | None = ACTOR,
    loopback: bool = True,
    headers: dict[str, str] | None = None,
    query: str = "",
) -> Request:
    raw = b"" if body is None else json.dumps(body).encode("utf-8")
    merged = dict(headers or {})
    if actor is not None:
        merged["x-autopilot-actor"] = actor
    path, _, inline_query = path.partition("?")
    combined = inline_query or query
    return Request(
        method=method,
        path=path,
        query={key: [value] for key, value in _parse_query(combined).items()},
        headers={key.lower(): value for key, value in merged.items()},
        body=raw,
        remote_is_loopback=loopback,
    )


def _repo(repository: Repository) -> str:
    return quote(repository.id, safe="")


def _parse_query(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    parsed: dict[str, str] = {}
    for chunk in raw.split("&"):
        key, _, value = chunk.partition("=")
        parsed[unquote(key)] = unquote(value)
    return parsed


def _json(response: Response) -> dict[str, Any]:
    return json.loads(response.body.decode("utf-8"))


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


def test_reads_report_a_passed_ttl_as_expired(
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

    listed = _json(
        api.handle(_request("GET", f"/api/proposals?repository_id={_repo(repository)}"))
    )["proposals"]
    assert [item["state"] for item in listed] == ["expired"]

    detail = _json(api.handle(_request("GET", f"/api/proposals/{proposal['id']}")))["proposal"]
    assert detail["state"] == "expired"


def test_disabled_workflow_is_refused(api: Api, repository: Repository, issue: Issue) -> None:
    current = api.store.get_settings(repository.id)
    api.store.save_settings(
        type(current)(
            repository_id=repository.id,
            workflows={**current.workflows, "issue_triage": False},
            label_taxonomy=current.label_taxonomy,
            daily_request_limit=current.daily_request_limit,
            daily_cost_limit_usd=current.daily_cost_limit_usd,
            proposal_ttl_minutes=current.proposal_ttl_minutes,
        )
    )
    response = api.handle(
        _request(
            "POST",
            f"/api/repositories/{_repo(repository)}/workflows/issue_triage",
            body={"number": 7},
        )
    )
    assert response.status == 409
    assert _json(response)["error"]["code"] == "workflow_disabled"


def test_budget_limit_blocks_generation(api: Api, repository: Repository, issue: Issue) -> None:
    current = api.store.get_settings(repository.id)
    api.store.save_settings(
        type(current)(
            repository_id=repository.id,
            workflows=current.workflows,
            label_taxonomy=current.label_taxonomy,
            daily_request_limit=1,
            daily_cost_limit_usd=current.daily_cost_limit_usd,
            proposal_ttl_minutes=current.proposal_ttl_minutes,
        )
    )
    first = api.handle(
        _request(
            "POST",
            f"/api/repositories/{_repo(repository)}/workflows/issue_triage",
            body={"number": 7},
        )
    )
    assert first.status == 201
    second = api.handle(
        _request(
            "POST",
            f"/api/repositories/{_repo(repository)}/workflows/issue_triage",
            body={"number": 7},
        )
    )
    assert second.status == 200  # cached response does not consume budget
    api.store.save_issue(
        Issue(
            repository_id=repository.id,
            number=9,
            title="Different issue",
            body="Something else entirely.",
            author_login="someone",
            state="open",
            labels=(),
        )
    )
    third = api.handle(
        _request(
            "POST",
            f"/api/repositories/{_repo(repository)}/workflows/issue_triage",
            body={"number": 9},
        )
    )
    assert third.status == 429


def test_settings_update_is_validated_and_audited(api: Api, repository: Repository) -> None:
    response = api.handle(
        _request(
            "PUT",
            f"/api/repositories/{_repo(repository)}/settings",
            body={"proposal_ttl_minutes": 0},
        )
    )
    assert response.status == 400

    ok = api.handle(
        _request(
            "PUT",
            f"/api/repositories/{_repo(repository)}/settings",
            body={"proposal_ttl_minutes": 30, "daily_request_limit": 10},
        )
    )
    assert ok.status == 200
    assert _json(ok)["settings"]["proposal_ttl_minutes"] == 30

    audit = _json(api.handle(_request("GET", "/api/audit")))["events"]
    assert any(event["event_type"] == "settings.updated" for event in audit)


def test_audit_verify_reports_healthy_chain(api: Api, repository: Repository, issue: Issue) -> None:
    api.handle(
        _request(
            "POST",
            f"/api/repositories/{_repo(repository)}/workflows/issue_triage",
            body={"number": 7},
        )
    )
    response = api.handle(_request("GET", "/api/audit/verify"))
    assert response.status == 200
    assert _json(response)["ok"] is True


def test_metrics_reports_budget(api: Api, repository: Repository, issue: Issue) -> None:
    api.handle(
        _request(
            "POST",
            f"/api/repositories/{_repo(repository)}/workflows/issue_triage",
            body={"number": 7},
        )
    )
    response = api.handle(
        _request("GET", f"/api/metrics?repository_id={quote(repository.id, safe='')}")
    )
    assert response.status == 200
    payload = _json(response)
    assert payload["model_calls"]["requests"] >= 1
    assert payload["budget"]["requests_today"] >= 1


def test_webhook_creates_proposal_and_deduplicates(api: Api, repository: Repository) -> None:
    body = json.dumps(
        {
            "action": "opened",
            "repository": {
                "id": 4242,
                "full_name": repository.full_name,
                "owner": {"login": "acme"},
            },
            "issue": {
                "id": 9001,
                "number": 11,
                "title": "Crash when config is empty",
                "body": "Traceback on empty config.",
                "user": {"login": "reporter"},
                "state": "open",
                "labels": [{"name": "bug"}],
            },
        }
    ).encode("utf-8")
    headers = {
        "x-github-delivery": "delivery-abc",
        "x-github-event": "issues",
        "x-hub-signature-256": compute_signature(WEBHOOK_SECRET, body),
    }
    first = api.handle(
        Request("POST", "/webhooks/github", {}, headers, body, remote_is_loopback=False)
    )
    assert first.status == 202, first.body
    assert _json(first)["status"] == "proposal_created"

    second = api.handle(
        Request("POST", "/webhooks/github", {}, headers, body, remote_is_loopback=False)
    )
    assert _json(second)["status"] == "duplicate"
    assert len(api.store.list_proposals(repository_id=repository.id)) == 1


def test_webhook_rejects_bad_signature(api: Api, repository: Repository) -> None:
    body = b'{"action":"opened"}'
    headers = {
        "x-github-delivery": "delivery-bad",
        "x-github-event": "issues",
        "x-hub-signature-256": "sha256=" + "0" * 64,
    }
    response = api.handle(
        Request("POST", "/webhooks/github", {}, headers, body, remote_is_loopback=False)
    )
    assert response.status == 401


def test_webhook_does_not_grant_roles_on_discovery(api: Api) -> None:
    body = json.dumps(
        {
            "action": "opened",
            "repository": {
                "id": 555,
                "full_name": "unknown/project",
                "owner": {"login": "unknown"},
            },
            "issue": {
                "id": 1,
                "number": 1,
                "title": "Hello",
                "body": "Body",
                "user": {"login": "someone"},
                "state": "open",
                "labels": [],
            },
        }
    ).encode("utf-8")
    headers = {
        "x-github-delivery": "delivery-new",
        "x-github-event": "issues",
        "x-hub-signature-256": compute_signature(WEBHOOK_SECRET, body),
    }
    response = api.handle(
        Request("POST", "/webhooks/github", {}, headers, body, remote_is_loopback=False)
    )
    assert response.status == 202
    discovered = api.store.get_repository("github:unknown/project")
    assert discovered is not None
    assert api.store.role_of(discovered.id, "unknown") is None
    # A verified delivery may generate a proposal, but without a granted role
    # nobody can approve it, so no action can reach the forge.
    assert _json(response)["status"] == "proposal_created"
    proposals = api.store.list_proposals(repository_id=discovered.id)
    assert proposals and proposals[0].state == "pending_approval"


def test_webhook_payload_hash_matches_body(api: Api, repository: Repository) -> None:
    body = (
        b'{"action":"ping","repository":'
        b'{"id":1,"full_name":"acme/widget","owner":{"login":"acme"}}}'
    )
    headers = {
        "x-github-delivery": "delivery-ping",
        "x-github-event": "ping",
        "x-hub-signature-256": compute_signature(WEBHOOK_SECRET, body),
    }
    response = api.handle(
        Request("POST", "/webhooks/github", {}, headers, body, remote_is_loopback=False)
    )
    assert response.status == 202
    events = api.store.list_audit(limit=10)
    received = [event for event in events if event.event_type == "webhook.received"]
    assert received[0].detail["payload_sha256"] == sha256_hex(body)


def test_proposal_detail_includes_citations(api: Api, repository: Repository, issue: Issue) -> None:
    proposal = _json(
        api.handle(
            _request(
                "POST",
                f"/api/repositories/{_repo(repository)}/workflows/issue_triage",
                body={"number": 7},
            )
        )
    )["proposal"]
    detail = _json(api.handle(_request("GET", f"/api/proposals/{proposal['id']}")))["proposal"]
    assert detail["citations"]
    assert detail["citations"][0]["kind"] == "issue"


def test_list_proposals_filters_by_state(api: Api, repository: Repository, issue: Issue) -> None:
    api.handle(
        _request(
            "POST",
            f"/api/repositories/{_repo(repository)}/workflows/issue_triage",
            body={"number": 7},
        )
    )
    pending = _json(api.handle(_request("GET", "/api/proposals?state=pending_approval")))[
        "proposals"
    ]
    approved = _json(api.handle(_request("GET", "/api/proposals?state=approved")))["proposals"]
    assert len(pending) == 1
    assert approved == []


def test_large_json_body_is_rejected(api: Api, repository: Repository) -> None:
    request = Request(
        "PUT",
        f"/api/repositories/{_repo(repository)}/settings",
        {},
        {"x-autopilot-actor": ACTOR},
        b"{" + b" " * 70_000,
    )
    response = api.handle(request)
    assert response.status == 400
    assert _json(response)["error"]["code"] == "invalid_request"


def test_repository_delete_requires_confirmation(
    api: Api, repository: Repository, issue: Issue
) -> None:
    path = f"/api/repositories/{_repo(repository)}"
    response = api.handle(_request("DELETE", path, body={}))
    assert response.status == 400
    assert _json(response)["error"]["code"] == "confirmation_required"
    assert api.store.get_repository(repository.id) is not None

    response = api.handle(_request("DELETE", path, body={"confirm": True}))
    assert response.status == 200, response.body
    assert _json(response)["deleted"]["issues"] == 1
    assert api.store.get_repository(repository.id) is None
    assert api.store.list_issues(repository.id) == []


def test_repository_delete_requires_owner(api: Api, repository: Repository, issue: Issue) -> None:
    api.store.grant_role(repository.id, "approver-only", "approver", granted_by="test")
    response = api.handle(
        _request(
            "DELETE",
            f"/api/repositories/{_repo(repository)}",
            body={"confirm": True},
            actor="approver-only",
        )
    )
    assert response.status == 403
    assert api.store.get_repository(repository.id) is not None


def test_webhook_acknowledges_an_unsupported_action_without_retry(
    api: Api, repository: Repository
) -> None:
    from tests.fixtures.payloads import issue_opened_payload

    body = json.dumps(issue_opened_payload(action="transferred")).encode("utf-8")
    headers = {
        "x-github-delivery": "delivery-unsupported",
        "x-github-event": "issues",
        "x-hub-signature-256": compute_signature(WEBHOOK_SECRET, body),
    }
    response = api.handle(
        Request("POST", "/webhooks/github", {}, headers, body, remote_is_loopback=False)
    )
    assert response.status == 202
    assert _json(response)["status"] == "ignored"
    assert api.store.list_proposals(repository_id=repository.id) == []


def test_system_endpoint_reports_boundaries_without_secrets(api: Api) -> None:
    response = api.handle(_request("GET", "/api/system"))
    assert response.status == 200
    payload = _json(response)
    assert payload["provider"] == "fake"
    assert payload["live_provider_enabled"] is False
    assert payload["webhook_secret_configured"] is True
    assert payload["retention_days"] >= 1
    assert "webhook_secret" not in payload
    assert "admin_token" not in payload


def test_system_endpoint_requires_an_actor(api: Api) -> None:
    response = api.handle(_request("GET", "/api/system", actor=None, loopback=False))
    assert response.status == 403


def test_static_assets_revalidate_and_304_on_match(api: Api) -> None:
    first = api.handle(_request("GET", "/app.js", actor=None))
    assert first.status == 200
    etag = first.headers["ETag"]
    assert first.headers["Cache-Control"] == "no-cache"

    again = api.handle(
        Request("GET", "/app.js", {}, {"if-none-match": etag}, b"", remote_is_loopback=True)
    )
    assert again.status == 304
    assert again.body == b""

    changed = api.handle(
        Request("GET", "/app.js", {}, {"if-none-match": '"stale"'}, b"", remote_is_loopback=True)
    )
    assert changed.status == 200


def test_static_asset_path_traversal_is_rejected(api: Api) -> None:
    response = api.handle(_request("GET", "/../api.py", actor=None))
    assert response.status == 404
