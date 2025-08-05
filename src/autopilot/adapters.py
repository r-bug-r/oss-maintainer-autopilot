"""Repository adapters: the boundary between Autopilot and a forge.

GitHub is implemented. GitLab is a declared, unimplemented boundary: calling it
raises rather than silently degrading, because a half-working adapter would be
a security and correctness risk.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .models import Comment, Issue, PullRequest, PullRequestFile, Repository
from .store import Store


