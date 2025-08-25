"""HTTP surface: webhook intake, JSON API, and the local dashboard.

The request router is a pure function of `(method, path, query, headers, body)`
so the whole API is testable without sockets. The `http.server` handler below is
a thin adapter over it. Authentication is a local-first model:

* loopback requests are trusted by default, because the dashboard is bound to
  127.0.0.1 and there is no browser session to steal;
* a remote bind requires `AUTOPILOT_ADMIN_TOKEN` and an `Authorization: Bearer`
  header, and never trusts `X-Forwarded-For` unless explicitly configured;
* every mutating route additionally resolves a *named actor* and checks the
  repository role, so authentication and authorization stay separate concerns.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .adapters import AdapterError, AdapterNotFound
from .canonical import constant_time_equals, sha256_hex
from .gateway import BudgetExceeded
from .models import Issue, PullRequest, Repository, RepositorySettings, ValidationError
from .runtime import Runtime
from .store import Conflict, Expired, NotFound, StoreError, Unauthorized
from .webhooks import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    InvalidSignature,
    MalformedPayload,
    MissingSignature,
    PayloadTooLarge,
    UnsupportedEvent,
    WebhookError,
    parse_delivery,
)
from .workflows import WorkflowDisabled, WorkflowError, proposal_detail, proposal_summary

JSON = dict[str, Any]
MAX_JSON_BODY = 64 * 1024
ACTOR_HEADER = "X-Autopilot-Actor"
STATIC_DIR = Path(__file__).parent / "ui"


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


def _json_response(payload: Any, status: int = 200) -> Response:
    return Response(status=status, body=json.dumps(payload, sort_keys=True).encode("utf-8"))


def _error(status: int, code: str, message: str) -> Response:
    return _json_response({"error": {"code": code, "message": message}}, status=status)


@dataclass(frozen=True, slots=True)
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes
    remote_is_loopback: bool = True

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def query_one(self, name: str, default: str = "") -> str:
        values = self.query.get(name)
        return values[0] if values else default

    def json(self) -> JSON:
        if len(self.body) > MAX_JSON_BODY:
            raise ValidationError("request body is too large")
        if not self.body:
            return {}
        try:
            decoded = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("request body is not valid JSON") from exc
        if not isinstance(decoded, dict):
            raise ValidationError("request body must be a JSON object")
        return decoded


class Api:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.settings = runtime.settings
        self.store = runtime.store
        self.engine = runtime.engine

    # --------------------------------------------------------------- routing
    def handle(self, request: Request) -> Response:
        try:
            return self._dispatch(request)
        except Unauthorized as exc:
            return _error(403, "forbidden", str(exc))
        except NotFound as exc:
            return _error(404, "not_found", str(exc))
        except Conflict as exc:
            return _error(409, "conflict", str(exc))
        except Expired as exc:
            return _error(410, "expired", str(exc))
        except BudgetExceeded as exc:
            return _error(429, "budget_exceeded", str(exc))
        except WorkflowDisabled as exc:
            return _error(409, "workflow_disabled", str(exc))
        except ValidationError as exc:
            return _error(400, "invalid_request", str(exc))
        except StoreError as exc:
            return _error(400, "store_error", str(exc))
        except AdapterNotFound as exc:
            return _error(404, "not_found", str(exc))
        except (WorkflowError, AdapterError) as exc:
            return _error(502, "workflow_error", str(exc))

    def _dispatch(self, request: Request) -> Response:
        path = request.path.rstrip("/") or "/"
        method = request.method.upper()

        if path == "/api/health" and method == "GET":
            return _json_response({"status": "ok", "provider": self.settings.provider})

        if path == "/api/system" and method == "GET":
            # Non-secret runtime facts the console needs to describe its own
            # boundaries: which provider path is live, how long content is kept,
            # and whether anything can be reached from off-host. The webhook
            # secret and admin token are never returned, only their presence.
            self._authenticate(request)
            return _json_response(
                {
                    "version": __version__,
                    "provider": self.settings.provider,
                    "live_provider_enabled": self.settings.allow_live_provider,
                    "model": self.settings.model,
                    "retention_days": self.settings.retention_days,
                    "remote_access": self.settings.allow_remote,
                    "webhook_secret_configured": bool(self.settings.webhook_secret),
                    "max_body_bytes": self.settings.max_body_bytes,
                }
            )

        if path == "/webhooks/github" and method == "POST":
            return self._webhook(request)

        if not path.startswith("/api/"):
            if method != "GET":
                return _error(405, "method_not_allowed", "only GET is served for static assets")
            return self._static(path, request.header("if-none-match"))

        if path == "/api/repositories" and method == "GET":
            self._authenticate(request)
            return _json_response({"repositories": self.store.list_repositories()})

        if path == "/api/proposals" and method == "GET":
            self._authenticate(request)
            # Reads sweep expired rows first so the queue and the decision
            # endpoints cannot disagree about whether a proposal is actionable.
            self.store.expire_due_proposals()
            proposals = self.store.list_proposals(
                repository_id=request.query_one("repository_id") or None,
                states=self._states(request),
                limit=min(int(request.query_one("limit", "100") or 100), 500),
            )
            return _json_response({"proposals": [proposal_summary(item) for item in proposals]})

        match = re.fullmatch(r"/api/proposals/([^/]+)", path)
        if match and method == "GET":
            self._authenticate(request)
            proposal_id = unquote(match.group(1))
            self.store.expire_due_proposals()
            proposal = self.store.get_proposal(proposal_id)
            if proposal is None:
                raise NotFound(f"proposal {proposal_id} not found")
            return _json_response({"proposal": proposal_detail(proposal)})

        match = re.fullmatch(r"/api/proposals/([^/]+)/(approve|reject|execute)", path)
        if match and method == "POST":
            actor = self._authenticate(request)
            body = request.json()
            proposal_id, action = unquote(match.group(1)), match.group(2)
            if action == "approve":
                proposal = self.engine.approve(proposal_id, actor)
            elif action == "reject":
                proposal = self.engine.reject(proposal_id, actor, str(body.get("reason", "")))
            else:
                proposal = self.engine.execute(proposal_id, actor)
            return _json_response({"proposal": proposal_detail(proposal)})

        match = re.fullmatch(r"/api/repositories/([^/]+)", path)
        if match and method == "DELETE":
            actor = self._authenticate(request)
            repository_id = unquote(match.group(1))
            self.store.authorize(repository_id, actor, require_owner=True)
            body = request.json()
            if body.get("confirm") is not True:
                return _error(
                    400,
                    "confirmation_required",
                    'send {"confirm": true} to delete stored content; the audit trail is kept',
                )
            counts = self.store.delete_repository_data(repository_id)
            return _json_response({"deleted": counts, "repository_id": repository_id})

        match = re.fullmatch(r"/api/repositories/([^/]+)/settings", path)
        if match:
            actor = self._authenticate(request)
            repository_id = unquote(match.group(1))
            self.store.authorize(repository_id, actor)
            if method == "GET":
                return _json_response(
                    {"settings": self.store.get_settings(repository_id).as_dict()}
                )
            if method == "PUT":
                body = request.json()
                current = self.store.get_settings(repository_id)
                updated = RepositorySettings(
                    repository_id=repository_id,
                    workflows=(
                        {str(key): bool(value) for key, value in body["workflows"].items()}
                        if isinstance(body.get("workflows"), dict)
                        else current.workflows
                    ),
                    label_taxonomy=(
                        tuple(str(item) for item in body["label_taxonomy"])
                        if isinstance(body.get("label_taxonomy"), list)
                        else current.label_taxonomy
                    ),
                    daily_request_limit=int(
                        body.get("daily_request_limit", current.daily_request_limit)
                    ),
                    daily_cost_limit_usd=float(
                        body.get("daily_cost_limit_usd", current.daily_cost_limit_usd)
                    ),
                    proposal_ttl_minutes=int(
                        body.get("proposal_ttl_minutes", current.proposal_ttl_minutes)
                    ),
                )
                self.validate_settings(updated)
                self.store.save_settings(updated)
                self.store.append_audit(
                    event_type="settings.updated",
                    repository_id=repository_id,
                    actor_login=actor,
                    subject_type="repository",
                    subject_id=repository_id,
                    detail=updated.as_dict(),
                )
                return _json_response({"settings": updated.as_dict()})
            return _error(405, "method_not_allowed", "use GET or PUT")

        match = re.fullmatch(r"/api/repositories/([^/]+)/(issues|pull_requests|actors)", path)
        if match and method == "GET":
            self._authenticate(request)
            repository_id, kind = unquote(match.group(1)), match.group(2)
            if kind == "issues":
                issues = self.store.list_issues(repository_id)
                return _json_response(
                    {
                        "issues": [
                            {
                                "number": item.number,
                                "title": item.title,
                                "state": item.state,
                                "author_login": item.author_login,
                                "labels": list(item.labels),
                                "updated_at": item.updated_at,
                            }
                            for item in issues
                        ]
                    }
                )
            if kind == "pull_requests":
                pull_requests = self.store.list_merged_pull_requests(repository_id)
                return _json_response(
                    {
                        "pull_requests": [
                            {
                                "number": item.number,
                                "title": item.title,
                                "state": item.state,
                                "author_login": item.author_login,
                                "files": [file.path for file in item.files],
                            }
                            for item in pull_requests
                        ]
                    }
                )
            return _json_response({"actors": self.store.list_actors(repository_id)})

        match = re.fullmatch(r"/api/repositories/([^/]+)/workflows/(issue_triage|pr_review)", path)
        if match and method == "POST":
            actor = self._authenticate(request)
            repository_id, workflow = unquote(match.group(1)), match.group(2)
            self.store.authorize(repository_id, actor)
            body = request.json()
            repository = self._repository(repository_id)
            if workflow == "issue_triage":
                number = int(body.get("number", 0))
                issue = self.store.get_issue(repository_id, number)
                if issue is None:
                    issue = self.runtime.adapter.get_issue(repository, number)
                    self.store.save_issue(issue)
                result = self.engine.triage_issue(issue)
            else:
                number = int(body.get("number", 0))
                pull_request = self.store.get_pull_request(repository_id, number)
                if pull_request is None:
                    pull_request = self.runtime.adapter.get_pull_request(repository, number)
                    self.store.save_pull_request(pull_request)
                result = self.engine.review_pull_request(pull_request)
            return _json_response(
                {
                    "created": result.created,
                    "proposal": proposal_detail(result.proposal),
                },
                status=201 if result.created else 200,
            )

        if path == "/api/audit" and method == "GET":
            self._authenticate(request)
            entries = self.store.list_audit(
                repository_id=request.query_one("repository_id") or None,
                limit=min(int(request.query_one("limit", "200") or 200), 1000),
            )
            return _json_response({"events": [entry.as_dict() for entry in entries]})

        if path == "/api/audit/verify" and method == "GET":
            self._authenticate(request)
            ok, checked, broken = self.store.verify_audit_chain()
            return _json_response({"ok": ok, "checked": checked, "first_broken_id": broken})

        if path == "/api/metrics" and method == "GET":
            self._authenticate(request)
            metrics_repository_id = request.query_one("repository_id") or None
            from datetime import UTC, datetime

            month = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            summary = self.store.cost_summary(
                since=month.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                repository_id=metrics_repository_id,
            )
            return _json_response(
                {
                    "model_calls": {
                        "requests": summary.requests,
                        "cost_usd": round(summary.cost_usd, 6),
                        "input_tokens": summary.input_tokens,
                        "output_tokens": summary.output_tokens,
                        "cache_hits": summary.cache_hits,
                    },
                    "budget": (
                        self.runtime.gateway.budget_state(
                            metrics_repository_id,
                            self.store.get_settings(metrics_repository_id),
                        ).as_dict()
                        if metrics_repository_id
                        else None
                    ),
                }
            )

        return _error(404, "not_found", f"no route for {method} {path}")

    # ---------------------------------------------------------- webhook path
    def _webhook(self, request: Request) -> Response:
        try:
            delivery = parse_delivery(
                secret=self.settings.require_webhook_secret(),
                body=request.body,
                signature=request.header(SIGNATURE_HEADER),
                delivery_id=request.header(DELIVERY_HEADER),
                event=request.header(EVENT_HEADER),
                max_body_bytes=self.settings.max_body_bytes,
            )
        except MissingSignature:
            return _error(401, "missing_signature", "webhook signature header is required")
        except InvalidSignature:
            return _error(401, "invalid_signature", "webhook signature did not verify")
        except PayloadTooLarge as exc:
            return _error(413, "payload_too_large", str(exc))
        except UnsupportedEvent as exc:
            # Acknowledge with 2xx so the forge does not retry an event this
            # version deliberately ignores; the reason is still reported.
            return _json_response({"status": "ignored", "reason": str(exc)}, status=202)
        except (MalformedPayload, WebhookError) as exc:
            return _error(400, "malformed_payload", str(exc))

        first_seen = self.store.record_delivery(
            delivery_id=delivery.delivery_id,
            repository_id=delivery.repository.id,
            event=delivery.event,
            payload_sha256=delivery.payload_sha256,
            outcome="accepted",
        )
        if not first_seen:
            return _json_response({"status": "duplicate", "delivery_id": delivery.delivery_id})

        repository = self.store.get_repository(delivery.repository.id)
        if repository is None:
            # Discovery is automatic; authority is not. A verified delivery may
            # create the repository row, but no role is granted here, so nobody
            # can approve anything until an operator grants a role explicitly.
            repository = delivery.repository
            self.store.upsert_repository(repository)
            self.store.append_audit(
                event_type="repository.discovered",
                repository_id=repository.id,
                subject_type="repository",
                subject_id=repository.id,
                detail={"full_name": repository.full_name, "source": "webhook"},
            )

        outcome = "ignored"
        event_object = delivery.event_object
        if delivery.event == "issues" and isinstance(event_object, Issue):
            self.store.save_issue(event_object)
            if delivery.action in {"opened", "reopened", "edited"}:
                try:
                    result = self.engine.triage_issue(event_object)
                    outcome = "proposal_created" if result.created else "proposal_existing"
                except WorkflowDisabled:
                    outcome = "workflow_disabled"
                except (WorkflowError, BudgetExceeded):
                    outcome = "generation_failed"
        elif delivery.event == "pull_request" and isinstance(event_object, PullRequest):
            self.store.save_pull_request(event_object)
            outcome = "pull_request_recorded"

        self.store.update_delivery_outcome(delivery.delivery_id, outcome)
        self.store.append_audit(
            event_type="webhook.received",
            repository_id=delivery.repository.id,
            subject_type="delivery",
            subject_id=delivery.delivery_id,
            detail={
                "event": delivery.event,
                "action": delivery.action,
                "outcome": outcome,
                "payload_sha256": delivery.payload_sha256,
            },
        )
        return _json_response({"status": outcome, "delivery_id": delivery.delivery_id}, status=202)

    # ------------------------------------------------------------------ auth
    def _authenticate(self, request: Request) -> str:
        if not request.remote_is_loopback:
            if not self.settings.allow_remote:
                raise Unauthorized("remote access is disabled")
            token = self._bearer(request)
            if not self.settings.admin_token or not constant_time_equals(
                token, self.settings.admin_token
            ):
                raise Unauthorized("remote access requires the admin token")
        actor = (request.header(ACTOR_HEADER) or "").strip()
        if not actor:
            if request.remote_is_loopback:
                return "local-operator"
            raise Unauthorized(f"{ACTOR_HEADER} header is required for mutating requests")
        return actor

    @staticmethod
    def _bearer(request: Request) -> str:
        value = request.header("Authorization") or ""
        prefix = "Bearer "
        return value[len(prefix) :].strip() if value.startswith(prefix) else ""

    # --------------------------------------------------------------- helpers
    def _repository(self, repository_id: str) -> Repository:
        repository = self.store.get_repository(repository_id)
        if repository is None:
            raise NotFound(f"repository {repository_id} is not registered")
        return repository

    @staticmethod
    def _states(request: Request) -> list[str] | None:
        raw = request.query_one("state")
        if not raw:
            return None
        return [item for item in raw.split(",") if item]

    @staticmethod
    def validate_settings(settings: RepositorySettings) -> None:
        """Reject settings that would make the automation unsafe or nonsensical."""

        if settings.daily_request_limit < 1 or settings.daily_request_limit > 10_000:
            raise ValidationError("daily_request_limit must be between 1 and 10000")
        if settings.daily_cost_limit_usd < 0:
            raise ValidationError("daily_cost_limit_usd must not be negative")
        if not 1 <= settings.proposal_ttl_minutes <= 7 * 24 * 60:
            raise ValidationError("proposal_ttl_minutes must be between 1 and 10080")
        if not settings.label_taxonomy:
            raise ValidationError("label_taxonomy must not be empty")
        if len(settings.label_taxonomy) > 50:
            raise ValidationError("label_taxonomy exceeds 50 labels")

    def _static(self, path: str, if_none_match: str | None = None) -> Response:
        relative = "index.html" if path == "/" else path.lstrip("/")
        candidate = (STATIC_DIR / relative).resolve()
        if not str(candidate).startswith(str(STATIC_DIR.resolve())) or not candidate.is_file():
            return _error(404, "not_found", "asset not found")
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
        }.get(candidate.suffix, "application/octet-stream")
        # Assets ship with the process. Revalidation keeps an upgraded server
        # from being masked by a browser's cached copy of app.js or styles.css.
        body = candidate.read_bytes()
        etag = '"' + sha256_hex(body)[:16] + '"'
        headers = {"Cache-Control": "no-cache", "ETag": etag}
        if if_none_match and if_none_match.strip() == etag:
            return Response(status=304, body=b"", content_type=content_type, headers=headers)
        return Response(status=200, body=body, content_type=content_type, headers=headers)


def serve(runtime: Runtime) -> ThreadingHTTPServer:
    api = Api(runtime)
    handler = type("BoundHandler", (_Handler,), {"api": api})
    server = ThreadingHTTPServer((runtime.settings.host, runtime.settings.port), handler)
    return server
