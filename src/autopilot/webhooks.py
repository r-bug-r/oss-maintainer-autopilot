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


