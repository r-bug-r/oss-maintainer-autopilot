"""Persistence layer: repositories, proposals, audit chain, and cost ledger.

All state transitions that gate external side effects are single SQL statements
with a WHERE clause encoding the precondition, so two concurrent approvals or a
double-clicked execute cannot both win.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .canonical import canonical_json, hash_canonical, hash_text, sha256_hex
from .db import connect, transaction, utcnow
from .models import (
    Citation,
    Issue,
    Proposal,
    PullRequest,
    Repository,
    RepositorySettings,
    Role,
)

JsonObject = dict[str, Any]

GENESIS_HASH = "0" * 64


