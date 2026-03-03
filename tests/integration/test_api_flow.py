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


