from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest

from autopilot.models import Issue, PullRequest, ValidationError
from autopilot.webhooks import (
    InvalidSignature,
    MalformedPayload,
    MissingSignature,
    PayloadTooLarge,
    UnsupportedEvent,
    compute_signature,
    parse_delivery,
    parse_repository,
    verify_signature,
)
from tests.fixtures.payloads import issue_opened_payload

SECRET = "unit-test-secret-0123456789"


def test_signature_rejects_missing_or_wrong_scheme() -> None:
    body = _body(_payload())
    with pytest.raises(MissingSignature):
        verify_signature(SECRET, body, None)
    with pytest.raises(InvalidSignature):
        verify_signature(SECRET, body, "sha1=deadbeef")
    with pytest.raises(MissingSignature):
        verify_signature("", body, compute_signature(SECRET, body))


def test_signature_is_hmac_sha256() -> None:
    body = b"{}"
    expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert compute_signature(SECRET, body) == f"sha256={expected}"


def test_delivery_parses_issue_and_repository() -> None:
    body = _body(_payload())
    delivery = parse_delivery(
        secret=SECRET,
        body=body,
        signature=compute_signature(SECRET, body),
        delivery_id="delivery-1",
        event="issues",
    )
    assert delivery.repository.full_name == "acme/widget"
    assert delivery.repository.id == "github:acme/widget"
    event_object = delivery.event_object
    assert isinstance(event_object, Issue)
    assert event_object.number == 7
    assert event_object.labels == ("bug",)
    assert delivery.payload_sha256 == hashlib.sha256(body).hexdigest()


def test_oversized_body_is_rejected_before_parsing() -> None:
    body = b"x" * 2048
    with pytest.raises(PayloadTooLarge):
        parse_delivery(
            secret=SECRET,
            body=body,
            signature=compute_signature(SECRET, body),
            delivery_id="d",
            event="issues",
            max_body_bytes=1024,
        )


def test_unsupported_event_is_rejected() -> None:
    body = _body(_payload())
    with pytest.raises(UnsupportedEvent):
        parse_delivery(
            secret=SECRET,
            body=body,
            signature=compute_signature(SECRET, body),
            delivery_id="d",
            event="deployment",
        )


def test_unsupported_action_is_rejected() -> None:
    body = _body(_payload(action="transferred"))
    with pytest.raises(UnsupportedEvent):
        parse_delivery(
            secret=SECRET,
            body=body,
            signature=compute_signature(SECRET, body),
            delivery_id="d",
            event="issues",
        )


def test_missing_delivery_id_is_rejected() -> None:
    body = _body(_payload())
    with pytest.raises(MalformedPayload, match="delivery id"):
        parse_delivery(
            secret=SECRET,
            body=body,
            signature=compute_signature(SECRET, body),
            delivery_id="  ",
            event="issues",
        )


def test_missing_repository_owner_is_rejected() -> None:
    payload = _payload()
    repository = payload["repository"]
    assert isinstance(repository, dict)
    repository.pop("owner")
    with pytest.raises(MalformedPayload, match="owner"):
        parse_repository(payload)


def test_issue_without_title_is_rejected() -> None:
    payload = _payload()
    issue = payload["issue"]
    assert isinstance(issue, dict)
    issue.pop("title")
    body = _body(payload)
    with pytest.raises(MalformedPayload, match="title"):
        parse_delivery(
            secret=SECRET,
            body=body,
            signature=compute_signature(SECRET, body),
            delivery_id="d",
            event="issues",
        )


def test_invalid_json_is_rejected() -> None:
    body = b"{not json"
    with pytest.raises(MalformedPayload, match="valid JSON"):
        parse_delivery(
            secret=SECRET,
            body=body,
            signature=compute_signature(SECRET, body),
            delivery_id="d",
            event="issues",
        )


def test_ping_delivery_needs_no_action() -> None:
    payload = {"repository": _payload()["repository"]}
    body = _body(payload)
    delivery = parse_delivery(
        secret=SECRET,
        body=body,
        signature=compute_signature(SECRET, body),
        delivery_id="d",
        event="ping",
    )
    assert delivery.event_object is None
    assert delivery.action == "ping"


def test_pull_request_files_are_normalized() -> None:
    payload = {
        "action": "synchronize",
        "repository": _payload()["repository"],
        "pull_request": {
            "id": 1,
            "number": 12,
            "title": "Add validation",
            "body": "Adds validation.",
            "user": {"login": "contributor"},
            "state": "open",
            "base": {"ref": "main"},
            "head": {"ref": "feature"},
            "files": [{"filename": "src/app.py", "additions": 10, "deletions": 2, "patch": "+x"}],
        },
    }
    body = _body(payload)
    delivery = parse_delivery(
        secret=SECRET,
        body=body,
        signature=compute_signature(SECRET, body),
        delivery_id="d",
        event="pull_request",
    )
    pull_request = delivery.event_object
    assert isinstance(pull_request, PullRequest)
    assert pull_request.base_ref == "main"
    assert pull_request.files[0].path == "src/app.py"
    assert pull_request.files[0].additions == 10


def test_repository_id_is_derived_from_provider_and_name() -> None:
    repository = parse_repository(_payload())
    assert repository.id == "github:acme/widget"
    assert repository.owner_login == "acme"
    assert repository.external_id == "4242"


def test_repository_requires_a_slash_in_full_name() -> None:
    payload = _payload()
    repository = payload["repository"]
    assert isinstance(repository, dict)
    repository["full_name"] = "widget"
    with pytest.raises(MalformedPayload):
        parse_repository(payload)


def test_validation_error_is_wrapped() -> None:
    with pytest.raises((MalformedPayload, ValidationError)):
        parse_repository({"repository": {"full_name": "acme/widget", "owner": {}}})
