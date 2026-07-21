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


