"""HTTP surface: webhook intake, JSON API, and the local dashboard.

The request router is a pure function of `(method, path, query, headers, body)`
so the whole API is testable without sockets. The `http.server` handler below is
a thin adapter over it. Authentication is a local-first model:

* loopback requests are trusted by default, because the dashboard is bound to
  127.0.0.1 and there is no browser session to steal;
* a remote bind requires `AUTOPILOT_ADMIN_TOKEN` and an `Authorization: Bearer`
  header, and never trusts `X-Forwarded-For` unless explicitly configured;
* every mutating route additionally resolves a *named actor* and checks the
  repository role, so authentication and authorization stay separate concerns.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .adapters import AdapterError, AdapterNotFound
from .canonical import constant_time_equals, sha256_hex
from .gateway import BudgetExceeded
from .models import Issue, PullRequest, Repository, RepositorySettings, ValidationError
from .runtime import Runtime
from .store import Conflict, Expired, NotFound, StoreError, Unauthorized
from .webhooks import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    InvalidSignature,
    MalformedPayload,
    MissingSignature,
    PayloadTooLarge,
    UnsupportedEvent,
    WebhookError,
    parse_delivery,
)
from .workflows import WorkflowDisabled, WorkflowError, proposal_detail, proposal_summary

JSON = dict[str, Any]
MAX_JSON_BODY = 64 * 1024
ACTOR_HEADER = "X-Autopilot-Actor"
STATIC_DIR = Path(__file__).parent / "ui"


@dataclass(frozen=True, slots=True)
