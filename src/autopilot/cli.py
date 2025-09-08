"""Command line interface.

The CLI and the HTTP server share `build_runtime`, so both get the same
provider, budget, adapter, and authorization posture. Generation, approval, and
application are separate subcommands on purpose: an approval is a durable record
that a named human made a decision, and applying it is a second, auditable step.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from .adapters import AdapterError, AdapterNotFound, AdapterRateLimited
from .api import serve
from .config import ConfigError, Settings
from .models import Repository, ValidationError
from .runtime import (
    build_runtime,
    default_db_path,
    purge_expired,
    register_repository,
    seed_demo,
)
from .store import Conflict, Expired, NotFound, Store, StoreError, Unauthorized
from .workflows import WorkflowError, proposal_detail, proposal_summary

EXIT_OK = 0
EXIT_ERROR = 2


