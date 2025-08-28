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


