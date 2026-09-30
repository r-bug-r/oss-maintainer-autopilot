"""Webhook verification, replay protection, and event normalization.

The verifier is deliberately strict: size, then signature, then event
allowlist, then delivery idempotency. Nothing is parsed into domain objects
until the signature has been verified.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Literal

from .canonical import constant_time_equals, sha256_hex
from .models import Issue, PullRequest, PullRequestFile, Repository, ValidationError

SIGNATURE_HEADER = "X-Hub-Signature-256"
DELIVERY_HEADER = "X-GitHub-Delivery"
EVENT_HEADER = "X-GitHub-Event"

SUPPORTED_EVENTS = frozenset({"issues", "issue_comment", "pull_request", "ping", "repository"})

# GitHub sends these actions for issues and pull requests. Anything else is
# acknowledged but not processed, instead of guessing at semantics.
SUPPORTED_ACTIONS = frozenset(
    {"opened", "edited", "reopened", "synchronize", "closed", "created", "deleted"}
)

JsonObject = dict[str, Any]


class WebhookError(Exception):
    """Base class for rejected webhook deliveries."""

    status_code: int = 400
    code: str = "webhook_rejected"


class MissingSignature(WebhookError):
    status_code = 401
    code = "missing_signature"


class InvalidSignature(WebhookError):
    status_code = 401
    code = "invalid_signature"


class PayloadTooLarge(WebhookError):
    status_code = 413
    code = "payload_too_large"


class MalformedPayload(WebhookError):
    status_code = 400
    code = "malformed_payload"


class UnsupportedEvent(WebhookError):
    status_code = 202
    code = "unsupported_event"


class DuplicateDelivery(WebhookError):
    status_code = 200
    code = "duplicate_delivery"


def compute_signature(secret: str, body: bytes) -> str:
    """Return the `sha256=`-prefixed signature for a raw body."""

    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, body: bytes, header_value: str | None) -> None:
    """Verify the GitHub HMAC header, failing closed on every anomaly."""

    if not secret:
        raise MissingSignature("webhook secret is not configured")
    if not header_value:
        raise MissingSignature("missing X-Hub-Signature-256 header")
    if not header_value.startswith("sha256="):
        raise InvalidSignature("unsupported signature algorithm")
    if not constant_time_equals(compute_signature(secret, body), header_value.strip()):
        raise InvalidSignature("signature mismatch")


@dataclass(frozen=True, slots=True)
class VerifiedDelivery:
    delivery_id: str
    event: str
    action: str
    payload_sha256: str
    repository: Repository
    payload: JsonObject
    event_object: Issue | PullRequest | None


def _as_dict(value: object) -> JsonObject:
    return value if isinstance(value, dict) else {}


def _require_str(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MalformedPayload(f"missing string field: {key}")
    return value


def _require_dict(payload: JsonObject, key: str) -> JsonObject:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise MalformedPayload(f"missing object field: {key}")
    return value


def _optional_str(payload: JsonObject, key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _optional_int(payload: JsonObject, key: str, default: int = 0) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default


def _login(payload: JsonObject) -> str | None:
    login = _as_dict(payload.get("user")).get("login")
    return login if isinstance(login, str) and login else None


def _labels(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    names: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and name:
                names.append(name)
        elif isinstance(item, str) and item:
            names.append(item)
    return tuple(dict.fromkeys(names))


def parse_repository(payload: JsonObject) -> Repository:
    repo = _require_dict(payload, "repository")
    owner_login = _require_str(_require_dict(repo, "owner"), "login")
    external_id = repo.get("id")
    provider: Literal["github"] = "github"
    try:
        return Repository.build(
            provider=provider,
            full_name=_require_str(repo, "full_name"),
            owner_login=owner_login,
            external_id=str(external_id) if external_id is not None else None,
        )
    except ValidationError as exc:
        raise MalformedPayload(str(exc)) from exc


def parse_issue(payload: JsonObject, repository: Repository) -> Issue:
    raw = _require_dict(payload, "issue")
    external_id = raw.get("id")
    try:
        return Issue(
            repository_id=repository.id,
            number=_optional_int(raw, "number"),
            title=_require_str(raw, "title"),
            body=_optional_str(raw, "body") or "",
            author_login=_login(raw),
            state=str(raw.get("state", "open")),
            labels=_labels(raw.get("labels")),
            url=_optional_str(raw, "html_url"),
            created_at=_optional_str(raw, "created_at"),
            updated_at=_optional_str(raw, "updated_at"),
            external_id=str(external_id) if external_id is not None else None,
        )
    except ValidationError as exc:
        raise MalformedPayload(str(exc)) from exc


def parse_pull_request(payload: JsonObject, repository: Repository) -> PullRequest:
    raw = _require_dict(payload, "pull_request")
    files: list[PullRequestFile] = []
    for item in raw.get("files") or []:
        if isinstance(item, dict) and isinstance(item.get("filename"), str):
            files.append(
                PullRequestFile(
                    path=item["filename"],
                    additions=_optional_int(item, "additions"),
                    deletions=_optional_int(item, "deletions"),
                    patch_summary=_optional_str(item, "patch") or "",
                )
            )
    external_id = raw.get("id")
    try:
        return PullRequest(
            repository_id=repository.id,
            number=_optional_int(raw, "number"),
            title=_require_str(raw, "title"),
            body=_optional_str(raw, "body") or "",
            author_login=_login(raw),
            state=str(raw.get("state", "open")),
            base_ref=str(_as_dict(raw.get("base")).get("ref", "")),
            head_ref=str(_as_dict(raw.get("head")).get("ref", "")),
            files=tuple(files),
            external_id=str(external_id) if external_id is not None else None,
        )
    except ValidationError as exc:
        raise MalformedPayload(str(exc)) from exc


def parse_delivery(
    *,
    secret: str,
    body: bytes,
    signature: str | None,
    delivery_id: str | None,
    event: str | None,
    max_body_bytes: int = 1_048_576,
) -> VerifiedDelivery:
    """Verify and normalize one webhook delivery.

    Raises a `WebhookError` subclass on every rejection path so callers can map
    errors to HTTP status codes without inspecting message text.
    """

    if len(body) > max_body_bytes:
        raise PayloadTooLarge(f"payload exceeds {max_body_bytes} bytes")
    verify_signature(secret, body, signature)
    if not delivery_id or not delivery_id.strip():
        raise MalformedPayload("missing delivery id")
    if not event or event.strip() not in SUPPORTED_EVENTS:
        raise UnsupportedEvent(f"unsupported event: {event}")
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MalformedPayload("body is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise MalformedPayload("body must be a JSON object")
    payload: JsonObject = decoded

    raw_action = payload.get("action", "ping" if event.strip() == "ping" else "")
    if not isinstance(raw_action, str):
        raise MalformedPayload("action must be a string")
    action = raw_action
    if event.strip() != "ping" and action and action not in SUPPORTED_ACTIONS:
        raise UnsupportedEvent(f"unsupported action: {action}")

    repository = parse_repository(payload)
    event_object: Issue | PullRequest | None = None
    if event.strip() in {"issues", "issue_comment"} and "issue" in payload:
        event_object = parse_issue(payload, repository)
    elif event.strip() == "pull_request":
        event_object = parse_pull_request(payload, repository)

    return VerifiedDelivery(
        delivery_id=delivery_id.strip(),
        event=event.strip(),
        action=action,
        payload_sha256=sha256_hex(body),
        repository=repository,
        payload=payload,
        event_object=event_object,
    )
