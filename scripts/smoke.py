"""End-to-end smoke scenario for the Phase A exit gate.

Runs the real HTTP server with a signed GitHub `issues.opened` fixture, checks
that a question-only proposal is produced for an issue with no reproduction
details, approves and executes it, and proves that exactly one comment exists
and that the audit chain still verifies.

Usage: `python scripts/smoke.py`
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autopilot.config import Settings  # noqa: E402
from autopilot.runtime import build_runtime, register_repository  # noqa: E402

SECRET = "smoke-secret-not-a-credential"
REPOSITORY = "demo/autopilot-sandbox"
REPOSITORY_ID = "github:demo/autopilot-sandbox"
ACTOR = "demo-maintainer"

Json = dict[str, Any]


