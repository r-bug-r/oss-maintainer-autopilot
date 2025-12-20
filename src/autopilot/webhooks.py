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


