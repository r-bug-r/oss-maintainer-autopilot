"""Model provider contract plus deterministic and live implementations.

The contract is intentionally narrow: `complete()` and `count_tokens()`. Every
implementation records provider, model, token counts, latency and cost so the
caller can write an auditable ledger row.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from .canonical import sha256_hex

RETRYABLE_ERRORS = frozenset({"timeout", "rate_limited", "server_error", "transport"})
PERMANENT_ERRORS = frozenset({"auth", "invalid_request", "schema_invalid"})


