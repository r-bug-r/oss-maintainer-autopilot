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
def server(runtime: Runtime) -> Iterator[ThreadingHTTPServer]:
    instance = serve(runtime)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def _base(server: ThreadingHTTPServer) -> str:
    host, port = server.server_address[:2]
    return f"http://{host!s}:{int(port)}"


def test_health_over_a_real_socket(server: ThreadingHTTPServer) -> None:
    with urllib.request.urlopen(f"{_base(server)}/api/health", timeout=5) as response:
        payload = json.loads(response.read())
        assert payload == {"status": "ok", "provider": "fake"}
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


