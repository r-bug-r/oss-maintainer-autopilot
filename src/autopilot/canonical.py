"""Canonical serialization and hashing helpers.

Every persisted input, prompt, and audit record is hashed over a canonical
JSON encoding so identical fixture events always produce identical digests.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented as canonical JSON."""


def canonical_json(value: Any) -> str:
    """Return a deterministic JSON encoding for hashing and equality checks."""

    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise CanonicalizationError(str(exc)) from exc


def sha256_hex(value: str | bytes) -> str:
    """Hash a string (UTF-8) or bytes and return lowercase hex."""

    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def hash_canonical(value: Any) -> str:
    """Hash the canonical JSON encoding of a value."""

    return sha256_hex(canonical_json(value))


def hash_text(value: str) -> str:
    """Hash arbitrary text without canonicalization."""

    return sha256_hex(value)


def constant_time_equals(left: str, right: str) -> bool:
    """Compare two strings without leaking length-dependent timing."""

    return hmac.compare_digest(left, right)
