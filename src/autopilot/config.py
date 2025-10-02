"""Runtime configuration.

Configuration is environment-driven, validated at startup, and fails closed
when a live provider is requested without an explicit opt-in.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path


class ConfigError(ValueError):
    """Raised when the runtime configuration is unsafe or inconsistent."""


