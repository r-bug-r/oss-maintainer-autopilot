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


def test_triage_and_approval_over_a_real_socket(
    server: ThreadingHTTPServer,
    runtime: Runtime,
    repository: Repository,
    issue: Issue,
) -> None:
    from urllib.parse import quote

    base = _base(server)
    triage = urllib.request.Request(
        f"{base}/api/repositories/{quote(repository.id, safe='')}/workflows/issue_triage",
        data=json.dumps({"number": 7}).encode(),
        headers={"Content-Type": "application/json", "X-Autopilot-Actor": "maintainer"},
        method="POST",
    )
    with urllib.request.urlopen(triage, timeout=10) as response:
        assert response.status == 201
        proposal = json.loads(response.read())["proposal"]

    approve = urllib.request.Request(
        f"{base}/api/proposals/{quote(proposal['id'], safe='')}/approve",
        data=b"{}",
        headers={"Content-Type": "application/json", "X-Autopilot-Actor": "maintainer"},
        method="POST",
    )
    with urllib.request.urlopen(approve, timeout=10) as response:
        assert response.status == 200
        assert json.loads(response.read())["proposal"]["state"] == "approved"

    audit = urllib.request.Request(
        f"{base}/api/audit/verify", headers={"X-Autopilot-Actor": "maintainer"}
    )
    with urllib.request.urlopen(audit, timeout=10) as response:
        assert json.loads(response.read())["ok"] is True


def test_dashboard_is_served(server: ThreadingHTTPServer) -> None:
    with urllib.request.urlopen(f"{_base(server)}/", timeout=5) as response:
        body = response.read().decode("utf-8")
        assert "<title>" in body
        assert response.headers["Content-Type"].startswith("text/html")
