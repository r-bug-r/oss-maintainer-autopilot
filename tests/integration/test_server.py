"""One real-socket test: the handler must translate HTTP into the router correctly.

The rest of the suite calls `Api.handle` directly. This test proves the thin
adapter (headers, Content-Length, query string, loopback detection, security
headers) does not silently change request semantics.
"""

from __future__ import annotations

# ruff: noqa: S310 - every URL is built from a loopback test server address.
import json
import threading
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer

import pytest

from autopilot.api import serve
from autopilot.models import Issue, Repository
from autopilot.runtime import Runtime


@pytest.fixture
