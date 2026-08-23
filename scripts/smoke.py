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


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request(
    port: int,
    path: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, method=method, headers=headers or {}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        status = exc.code
    if raw.lstrip().startswith("{"):
        return status, json.loads(raw)
    return status, raw


def as_object(value: Any, what: str) -> Json:
    if not isinstance(value, dict):
        raise SystemExit(f"{what} is not a JSON object: {value!r}")
    return value


def wait_for_health(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"server exited early with code {process.returncode}")
        try:
            status, payload = request(port, "/api/health")
            if status == 200 and as_object(payload, "health").get("status") == "ok":
                return
        except OSError:
            pass
        time.sleep(0.1)
    raise SystemExit("server did not become healthy")


def issue_payload() -> bytes:
    return json.dumps(
        {
            "action": "opened",
            "repository": {
                "id": 900001,
                "name": "autopilot-sandbox",
                "full_name": REPOSITORY,
                "owner": {"login": "demo", "id": 900000, "type": "Organization"},
                "private": False,
            },
            "issue": {
                "id": 500001,
                "number": 41,
                "title": "Crash on startup with empty config file",
                "body": "Running `autopilot --config empty.yml` raises a traceback.",
                "state": "open",
                "user": {"login": "reporter-a", "id": 4242},
                "labels": [{"name": "bug"}],
                "created_at": "2026-10-20T12:00:00Z",
                "updated_at": "2026-10-20T12:00:00Z",
            },
        },
        sort_keys=True,
    ).encode("utf-8")


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="autopilot-smoke-"))
    db_path = home / "var" / "autopilot.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # A disposable local repository: registered, role granted, empty history.
    runtime = build_runtime(Settings(db_path=db_path, provider="fake"))
    register_repository(runtime.store, full_name=REPOSITORY, owner_login=ACTOR)
    runtime.close()

    port = free_port()
    env = {
        **os.environ,
        "AUTOPILOT_HOME": str(home),
        "AUTOPILOT_DB": str(db_path),
        "AUTOPILOT_WEBHOOK_SECRET": SECRET,
        "AUTOPILOT_PROVIDER": "fake",
        "PYTHONPATH": str(ROOT / "src"),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "autopilot", "serve", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    checks: list[str] = []
    try:
        wait_for_health(port, process)
        checks.append("GET /api/health -> 200")

        body = issue_payload()
        signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        status, payload = request(
            port,
            "/webhooks/github",
            method="POST",
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": signature,
                "X-GitHub-Delivery": "smoke-delivery-1",
                "X-GitHub-Event": "issues",
            },
        )
        assert status == 202 and payload == {
            "status": "proposal_created",
            "delivery_id": "smoke-delivery-1",
        }, (status, payload)
        checks.append("signed webhook -> 202 proposal_created")

        status, payload = request(port, "/api/proposals?state=pending_approval")
        assert status == 200, (status, payload)
        proposals = as_object(payload, "proposals")["proposals"]
        assert isinstance(proposals, list) and len(proposals) == 1, proposals
        proposal = as_object(proposals[0], "proposal")
        assert proposal["action_type"] == "comment", proposal
        proposal_id = str(proposal["id"])
        checks.append("one pending comment proposal")

        status, payload = request(port, f"/api/proposals/{proposal_id}")
        assert status == 200, (status, payload)
        detail = as_object(as_object(payload, "detail")["proposal"], "proposal detail")
        action_payload = as_object(detail["action_payload"], "action payload")
        comment = str(action_payload.get("comment") or "")
        assert "steps" in comment.lower(), comment
        assert detail["state"] == "pending_approval", detail["state"]
        checks.append("proposal asks for missing reproduction details")

        headers = {"Content-Type": "application/json", "X-Autopilot-Actor": ACTOR}
        status, payload = request(
            port,
            f"/api/proposals/{proposal_id}/approve",
            method="POST",
            body=b"{}",
            headers=headers,
        )
        assert status == 200, (status, payload)
        approved = as_object(as_object(payload, "approve")["proposal"], "approved proposal")
        assert approved["state"] == "approved", approved
        checks.append("approve -> approved")

        status, payload = request(
            port,
            f"/api/proposals/{proposal_id}/execute",
            method="POST",
            body=b"{}",
            headers=headers,
        )
        assert status == 200, (status, payload)
        executed = as_object(as_object(payload, "execute")["proposal"], "executed proposal")
        assert executed["state"] == "executed", executed
        checks.append("execute -> executed")

        status, payload = request(
            port,
            f"/api/proposals/{proposal_id}/execute",
            method="POST",
            body=b"{}",
            headers=headers,
        )
        assert status != 200, (status, payload)
        checks.append("second execute rejected")

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            comments = conn.execute(
                "SELECT body, source, idempotency_key FROM comments WHERE issue_number = 41"
            ).fetchall()
            assert len(comments) == 1, [dict(row) for row in comments]
            assert comments[0]["idempotency_key"] == proposal_id
            successful = conn.execute(
                "SELECT COUNT(*) FROM model_calls WHERE success = 1"
            ).fetchone()[0]
            assert successful == 1, successful
        checks.append("exactly one comment, one model call")

        status, payload = request(port, f"/api/audit?repository_id={REPOSITORY_ID}")
        assert status == 200, (status, payload)
        events = as_object(payload, "audit")["events"]
        assert isinstance(events, list), events
        event_types = [as_object(event, "event")["event_type"] for event in events]
        for expected in (
            "webhook.received",
            "proposal.created",
            "proposal.approved",
            "action.executed",
        ):
            assert expected in event_types, event_types
        status, payload = request(port, "/api/audit/verify")
        assert status == 200, (status, payload)
        assert as_object(payload, "verify").get("ok") is True, payload
        checks.append("audit trail verifies")

        status, html = request(port, "/")
        assert status == 200 and isinstance(html, str) and "<html" in html.lower(), status
        checks.append("dashboard served")

        status, payload = request(port, "/api/metrics")
        assert status == 200, (status, payload)
        calls = as_object(as_object(payload, "metrics")["model_calls"], "model calls")
        assert calls["requests"] == 1, calls
        assert calls["cost_usd"] == 0.0, calls
        checks.append("metrics report one $0 model call")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        stderr = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
        if process.returncode not in (0, 143, -15) and stderr:
            print(stderr, file=sys.stderr)

    print(json.dumps({"home": str(home), "checks": checks}, indent=2))
    return 0


