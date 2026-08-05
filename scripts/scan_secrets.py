"""Local secret scan used by `make security` and CI.

Scans repository text files for credential shapes and fails when one appears
outside an explicitly allowlisted fixture path. This is a guard against
accidental commits, not a replacement for a full history scanner.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "var",
}

SKIP_SUFFIXES = {".db", ".db-wal", ".db-shm", ".pyc", ".png", ".jpg", ".ico", ".zip"}

# Fixtures deliberately contain synthetic token shapes. Each allowlisted path
# must be a test or smoke fixture; production code is never allowlisted.
ALLOWLIST = {
    "tests/conftest.py",
    "tests/unit/test_redaction.py",
    "tests/unit/test_webhooks.py",
    "tests/integration/test_privacy.py",
    "tests/integration/test_failures.py",
    "tests/fixtures/payloads.py",
    "scripts/smoke.py",
}

TOKEN_SHAPES = (
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("github fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("gitlab token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{16,}")),
    ("openai key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{20,}")),
    ("aws access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")),
    ("slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("private key block", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
)

ASSIGNMENT = re.compile(
    r"""(?ix)
    \b(?:api[_-]?key|secret|password|passwd|token|credential)\b
    \s*[:=]\s*
    ["']([^"'\s]{16,200})["']
    """
)


