"""Persistence layer: repositories, proposals, audit chain, and cost ledger.

All state transitions that gate external side effects are single SQL statements
with a WHERE clause encoding the precondition, so two concurrent approvals or a
double-clicked execute cannot both win.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .canonical import canonical_json, hash_canonical, hash_text, sha256_hex
from .db import connect, transaction, utcnow
from .models import (
    Citation,
    Issue,
    Proposal,
    PullRequest,
    Repository,
    RepositorySettings,
    Role,
)

JsonObject = dict[str, Any]

GENESIS_HASH = "0" * 64


class StoreError(Exception):
    """Base class for persistence errors."""


class NotFound(StoreError):
    pass


class Conflict(StoreError):
    """The requested transition is not legal in the current state."""


class Unauthorized(StoreError):
    """The actor is not permitted to perform the requested transition."""


class Expired(StoreError):
    """The proposal expired before the transition."""


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _iso_after(minutes: int) -> str:
    return (
        (datetime.now(UTC) + timedelta(minutes=minutes))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _is_past(timestamp: str) -> bool:
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return True
    return parsed <= datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class AuditEntry:
    seq: int
    id: str
    repository_id: str | None
    actor_login: str | None
    event_type: str
    subject_type: str | None
    subject_id: str | None
    detail: JsonObject
    created_at: str
    prev_hash: str
    hash: str

    def as_dict(self) -> JsonObject:
        return {
            "seq": self.seq,
            "id": self.id,
            "repository_id": self.repository_id,
            "actor_login": self.actor_login,
            "event_type": self.event_type,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "detail": self.detail,
            "created_at": self.created_at,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }


@dataclass(frozen=True, slots=True)
class CostSummary:
    requests: int
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_hits: int


class Store:
    """Thin, explicit repository layer over SQLite.

    One connection is created by the owning thread. HTTP handlers run on worker
    threads, and SQLite connections are not shareable across threads, so each
    worker gets its own connection to the same file. WAL mode plus the schema's
    conditional UPDATEs keep concurrent writes serialized and correct.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._owner_thread = threading.get_ident()
        self._thread_local = threading.local()
        row = connection.execute("PRAGMA database_list").fetchone()
        self._path = row["file"] if row is not None and row["file"] else None

    @property
    def conn(self) -> sqlite3.Connection:
        if threading.get_ident() == self._owner_thread or not self._path:
            return self._connection
        existing: sqlite3.Connection | None = getattr(self._thread_local, "connection", None)
        if existing is None:
            existing = connect(self._path)
            self._thread_local.connection = existing
        return existing

    def close(self) -> None:
        existing: sqlite3.Connection | None = getattr(self._thread_local, "connection", None)
        if existing is not None:
            existing.close()
            self._thread_local.connection = None
        self._connection.close()

    # ----------------------------------------------------------------- repos
    def upsert_repository(self, repository: Repository) -> None:
        now = utcnow()
        with transaction(self.conn) as conn:
            conn.execute(
                """
                INSERT INTO repositories (
                    id, provider, full_name, external_id, owner_login, enabled,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    full_name = excluded.full_name,
                    external_id = excluded.external_id,
                    owner_login = excluded.owner_login,
                    updated_at = excluded.updated_at
                """,
                (
                    repository.id,
                    repository.provider,
                    repository.full_name,
                    repository.external_id,
                    repository.owner_login,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO repository_settings (
                    repository_id, workflow_flags_json, label_taxonomy_json,
                    daily_request_limit, daily_cost_limit_usd, proposal_ttl_minutes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository_id) DO NOTHING
                """,
                (
                    repository.id,
                    canonical_json(RepositorySettings(repository.id).workflows),
                    canonical_json(list(RepositorySettings(repository.id).label_taxonomy)),
                    200,
                    0.0,
                    60,
                    now,
                ),
            )

    def get_repository(self, repository_id: str) -> Repository | None:
        row = self.conn.execute(
            "SELECT * FROM repositories WHERE id = ?", (repository_id,)
        ).fetchone()
        if row is None:
            return None
        return Repository(
            id=row["id"],
            provider=row["provider"],
            full_name=row["full_name"],
            owner_login=row["owner_login"],
            external_id=row["external_id"],
        )

    def list_repositories(self) -> list[JsonObject]:
        rows = self.conn.execute(
            """
            SELECT r.*,
                   (SELECT COUNT(*) FROM proposals p
                     WHERE p.repository_id = r.id) AS proposal_count,
                   (SELECT COUNT(*) FROM proposals p
                     WHERE p.repository_id = r.id
                       AND p.state = 'pending_approval') AS pending_count,
                   (SELECT COUNT(*) FROM issues i WHERE i.repository_id = r.id) AS issue_count
            FROM repositories r
            ORDER BY r.full_name
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def set_repository_enabled(self, repository_id: str, enabled: bool) -> None:
        with transaction(self.conn) as conn:
            cursor = conn.execute(
                "UPDATE repositories SET enabled = ?, updated_at = ? WHERE id = ?",
                (1 if enabled else 0, utcnow(), repository_id),
            )
            if cursor.rowcount == 0:
                raise NotFound(f"repository {repository_id} not found")

    def is_enabled(self, repository_id: str) -> bool:
        row = self.conn.execute(
            "SELECT enabled FROM repositories WHERE id = ?", (repository_id,)
        ).fetchone()
        return bool(row and row["enabled"])

    def get_settings(self, repository_id: str) -> RepositorySettings:
        row = self.conn.execute(
            "SELECT * FROM repository_settings WHERE repository_id = ?", (repository_id,)
        ).fetchone()
        if row is None:
            return RepositorySettings(repository_id=repository_id)
        return RepositorySettings(
            repository_id=repository_id,
            workflows=_loads(row["workflow_flags_json"], {}),
            label_taxonomy=tuple(_loads(row["label_taxonomy_json"], [])),
            daily_request_limit=int(row["daily_request_limit"]),
            daily_cost_limit_usd=float(row["daily_cost_limit_usd"]),
            proposal_ttl_minutes=int(row["proposal_ttl_minutes"]),
        )

    def save_settings(self, settings: RepositorySettings) -> None:
        with transaction(self.conn) as conn:
            conn.execute(
                """
                INSERT INTO repository_settings (
                    repository_id, workflow_flags_json, label_taxonomy_json,
                    daily_request_limit, daily_cost_limit_usd, proposal_ttl_minutes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository_id) DO UPDATE SET
                    workflow_flags_json = excluded.workflow_flags_json,
                    label_taxonomy_json = excluded.label_taxonomy_json,
                    daily_request_limit = excluded.daily_request_limit,
                    daily_cost_limit_usd = excluded.daily_cost_limit_usd,
                    proposal_ttl_minutes = excluded.proposal_ttl_minutes,
                    updated_at = excluded.updated_at
                """,
                (
                    settings.repository_id,
                    canonical_json(settings.workflows),
                    canonical_json(list(settings.label_taxonomy)),
                    settings.daily_request_limit,
                    settings.daily_cost_limit_usd,
                    settings.proposal_ttl_minutes,
                    utcnow(),
                ),
            )

    # ---------------------------------------------------------------- actors
    def grant_role(self, repository_id: str, login: str, role: Role, granted_by: str) -> None:
        now = utcnow()
        with transaction(self.conn) as conn:
            conn.execute(
                "INSERT INTO actors (login, created_at) VALUES (?, ?) "
                "ON CONFLICT(login) DO NOTHING",
                (login, now),
            )
            conn.execute(
                """
                INSERT INTO repository_actors (
                    repository_id, actor_login, role, granted_at, granted_by
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(repository_id, actor_login) DO UPDATE SET
                    role = excluded.role, granted_at = excluded.granted_at,
                    granted_by = excluded.granted_by
                """,
                (repository_id, login, role, now, granted_by),
            )

    def revoke_role(self, repository_id: str, login: str) -> None:
        with transaction(self.conn) as conn:
            conn.execute(
                "DELETE FROM repository_actors WHERE repository_id = ? AND actor_login = ?",
                (repository_id, login),
            )

    def role_of(self, repository_id: str, login: str) -> Role | None:
        row = self.conn.execute(
            "SELECT role FROM repository_actors WHERE repository_id = ? AND actor_login = ?",
            (repository_id, login),
        ).fetchone()
        return row["role"] if row else None

    def list_actors(self, repository_id: str) -> list[JsonObject]:
        rows = self.conn.execute(
            "SELECT actor_login, role, granted_at, granted_by FROM repository_actors "
            "WHERE repository_id = ? ORDER BY role, actor_login",
            (repository_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def authorize(self, repository_id: str, login: str, *, require_owner: bool = False) -> Role:
        """Fail closed unless the actor holds a sufficient role."""

        role = self.role_of(repository_id, login)
        if role is None:
            raise Unauthorized(f"{login} has no role on {repository_id}")
        if require_owner and role != "owner":
            raise Unauthorized(f"{login} must be an owner to perform this action")
        return role

    # ------------------------------------------------------------- deliveries
    def record_delivery(
        self,
        *,
        delivery_id: str,
        repository_id: str | None,
        event: str,
        payload_sha256: str,
        outcome: str,
    ) -> bool:
        """Record a delivery id. Returns False when it was already seen."""

        try:
            with transaction(self.conn) as conn:
                conn.execute(
                    """
                    INSERT INTO webhook_deliveries (
                        delivery_id, repository_id, event, received_at, payload_sha256, outcome
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (delivery_id, repository_id, event, utcnow(), payload_sha256, outcome),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def delivery_seen(self, delivery_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM webhook_deliveries WHERE delivery_id = ?", (delivery_id,)
        ).fetchone()
        return row is not None

    def update_delivery_outcome(self, delivery_id: str, outcome: str) -> None:
        with transaction(self.conn) as conn:
            conn.execute(
                "UPDATE webhook_deliveries SET outcome = ? WHERE delivery_id = ?",
                (outcome, delivery_id),
            )

    # --------------------------------------------------------------- subjects
    def save_issue(self, issue: Issue) -> None:
        now = utcnow()
        with transaction(self.conn) as conn:
            conn.execute(
                """
                INSERT INTO issues (
                    repository_id, number, external_id, title, body, author_login, state,
                    labels_json, url, created_at, updated_at, input_sha256, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository_id, number) DO UPDATE SET
                    external_id = excluded.external_id,
                    title = excluded.title,
                    body = excluded.body,
                    author_login = excluded.author_login,
                    state = excluded.state,
                    labels_json = excluded.labels_json,
                    url = excluded.url,
                    updated_at = excluded.updated_at,
                    input_sha256 = excluded.input_sha256,
                    fetched_at = excluded.fetched_at
                """,
                (
                    issue.repository_id,
                    issue.number,
                    issue.external_id,
                    issue.title,
                    issue.body,
                    issue.author_login,
                    issue.state,
                    canonical_json(list(issue.labels)),
                    issue.url,
                    issue.created_at,
                    issue.updated_at,
                    issue.input_sha256(),
                    now,
                ),
            )

    def get_issue(self, repository_id: str, number: int) -> Issue | None:
        row = self.conn.execute(
            "SELECT * FROM issues WHERE repository_id = ? AND number = ?",
            (repository_id, number),
        ).fetchone()
        if row is None:
            return None
        return Issue(
            repository_id=row["repository_id"],
            number=int(row["number"]),
            title=row["title"],
            body=row["body"],
            author_login=row["author_login"],
            state=row["state"],
            labels=tuple(_loads(row["labels_json"], [])),
            url=row["url"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            external_id=row["external_id"],
        )

    def list_issues(self, repository_id: str, limit: int = 200) -> list[Issue]:
        rows = self.conn.execute(
            "SELECT * FROM issues WHERE repository_id = ? ORDER BY number DESC LIMIT ?",
            (repository_id, limit),
        ).fetchall()
        issues = []
        for row in rows:
            issues.append(
                Issue(
                    repository_id=row["repository_id"],
                    number=int(row["number"]),
                    title=row["title"],
                    body=row["body"],
                    author_login=row["author_login"],
                    state=row["state"],
                    labels=tuple(_loads(row["labels_json"], [])),
                    url=row["url"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    external_id=row["external_id"],
                )
            )
        return issues

    def save_pull_request(self, pull_request: PullRequest) -> None:
        now = utcnow()
        with transaction(self.conn) as conn:
            conn.execute(
                """
                INSERT INTO pull_requests (
                    repository_id, number, external_id, title, body, author_login, state,
                    base_ref, head_ref, files_json, input_sha256, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository_id, number) DO UPDATE SET
                    external_id = excluded.external_id,
                    title = excluded.title,
                    body = excluded.body,
                    author_login = excluded.author_login,
                    state = excluded.state,
                    base_ref = excluded.base_ref,
                    head_ref = excluded.head_ref,
                    files_json = excluded.files_json,
                    input_sha256 = excluded.input_sha256,
                    fetched_at = excluded.fetched_at
                """,
                (
                    pull_request.repository_id,
                    pull_request.number,
                    pull_request.external_id,
                    pull_request.title,
                    pull_request.body,
                    pull_request.author_login,
                    pull_request.state,
                    pull_request.base_ref,
                    pull_request.head_ref,
                    canonical_json([item.as_dict() for item in pull_request.files]),
                    pull_request.input_sha256(),
                    now,
                ),
            )

    def get_pull_request(self, repository_id: str, number: int) -> PullRequest | None:
        from .models import PullRequestFile

        row = self.conn.execute(
            "SELECT * FROM pull_requests WHERE repository_id = ? AND number = ?",
            (repository_id, number),
        ).fetchone()
        if row is None:
            return None
        files = tuple(
            PullRequestFile(
                path=item.get("path", ""),
                additions=int(item.get("additions", 0) or 0),
                deletions=int(item.get("deletions", 0) or 0),
                patch_summary=item.get("patch_summary", ""),
            )
            for item in _loads(row["files_json"], [])
            if isinstance(item, dict)
        )
        return PullRequest(
            repository_id=row["repository_id"],
            number=int(row["number"]),
            title=row["title"],
            body=row["body"],
            author_login=row["author_login"],
            state=row["state"],
            base_ref=row["base_ref"] or "",
            head_ref=row["head_ref"] or "",
            files=files,
            external_id=row["external_id"],
        )

    def list_merged_pull_requests(self, repository_id: str, limit: int = 100) -> list[PullRequest]:
        rows = self.conn.execute(
            "SELECT number FROM pull_requests WHERE repository_id = ? AND state = 'closed' "
            "ORDER BY number DESC LIMIT ?",
            (repository_id, limit),
        ).fetchall()
        results = []
        for row in rows:
            pull_request = self.get_pull_request(repository_id, int(row["number"]))
            if pull_request is not None:
                results.append(pull_request)
        return results

    def save_comment(
        self,
        *,
        comment_id: str,
        repository_id: str,
        issue_number: int,
        body: str,
        author_login: str,
        source: str,
        idempotency_key: str | None = None,
    ) -> bool:
        """Insert a comment record. Returns False if the idempotency key exists."""

        try:
            with transaction(self.conn) as conn:
                conn.execute(
                    """
                    INSERT INTO comments (
                        id, repository_id, issue_number, body, author_login, source,
                        idempotency_key, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        comment_id,
                        repository_id,
                        issue_number,
                        body,
                        author_login,
                        source,
                        idempotency_key,
                        utcnow(),
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def list_comments(
        self, repository_id: str, issue_number: int | None = None
    ) -> list[JsonObject]:
        if issue_number is None:
            rows = self.conn.execute(
                "SELECT * FROM comments WHERE repository_id = ? ORDER BY created_at",
                (repository_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM comments WHERE repository_id = ? AND issue_number = ? "
                "ORDER BY created_at",
                (repository_id, issue_number),
            ).fetchall()
        return [dict(row) for row in rows]

    # -------------------------------------------------------------- proposals
    def create_proposal(self, proposal: Proposal) -> None:
        with transaction(self.conn) as conn:
            conn.execute(
                """
                INSERT INTO proposals (
                    id, repository_id, workflow, subject_type, subject_number, state,
                    action_type, action_payload_json, payload_json, citations_json,
                    input_sha256, input_context_json, prompt_version, provider, model,
                    model_request_id, confidence, risk, cost_usd, created_by, created_at,
                    expires_at, idempotency_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.id,
                    proposal.repository_id,
                    proposal.workflow,
                    proposal.subject_type,
                    proposal.subject_number,
                    proposal.state,
                    proposal.action_type,
                    canonical_json(proposal.action_payload),
                    canonical_json(proposal.payload),
                    canonical_json([citation.as_dict() for citation in proposal.citations]),
                    proposal.input_sha256,
                    canonical_json(proposal.input_context),
                    proposal.prompt_version,
                    proposal.provider,
                    proposal.model,
                    proposal.model_request_id,
                    proposal.confidence,
                    proposal.risk,
                    proposal.cost_usd,
                    proposal.created_by,
                    proposal.created_at,
                    proposal.expires_at,
                    proposal.idempotency_key,
                ),
            )

    def _proposal_from_row(self, row: sqlite3.Row) -> Proposal:
        citations = tuple(
            Citation(
                kind=item.get("kind", "issue"),
                number=int(item.get("number", 0)),
                label=item.get("label", ""),
            )
            for item in _loads(row["citations_json"], [])
            if isinstance(item, dict)
        )
        return Proposal(
            id=row["id"],
            repository_id=row["repository_id"],
            workflow=row["workflow"],
            subject_type=row["subject_type"],
            subject_number=row["subject_number"],
            state=row["state"],
            action_type=row["action_type"],
            action_payload=_loads(row["action_payload_json"], {}),
            payload=_loads(row["payload_json"], {}),
            citations=citations,
            input_sha256=row["input_sha256"],
            input_context=_loads(row["input_context_json"], {}),
            prompt_version=row["prompt_version"],
            provider=row["provider"],
            model=row["model"],
            model_request_id=row["model_request_id"],
            confidence=row["confidence"],
            risk=row["risk"],
            cost_usd=float(row["cost_usd"] or 0.0),
            created_by=row["created_by"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            approved_by=row["approved_by"],
            approved_at=row["approved_at"],
            rejected_by=row["rejected_by"],
            rejected_at=row["rejected_at"],
            executed_at=row["executed_at"],
            attempt_count=int(row["attempt_count"] or 0),
            last_error=row["last_error"],
            error_class=row["error_class"],
            idempotency_key=row["idempotency_key"],
        )

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        row = self.conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        return self._proposal_from_row(row) if row else None

    def find_proposal_by_idempotency(self, key: str) -> Proposal | None:
        row = self.conn.execute(
            "SELECT * FROM proposals WHERE idempotency_key = ?", (key,)
        ).fetchone()
        return self._proposal_from_row(row) if row else None

    def list_proposals(
        self,
        *,
        repository_id: str | None = None,
        states: Sequence[str] | None = None,
        limit: int = 100,
    ) -> list[Proposal]:
        clauses = []
        params: list[object] = []
        if repository_id:
            clauses.append("repository_id = ?")
            params.append(repository_id)
        if states:
            placeholders = ",".join("?" for _ in states)
            clauses.append(f"state IN ({placeholders})")
            params.extend(states)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self.conn.execute(
            f"SELECT * FROM proposals {where} ORDER BY created_at DESC, id DESC LIMIT ?",  # noqa: S608
            params,
        ).fetchall()
        return [self._proposal_from_row(row) for row in rows]

    def approve_proposal(self, proposal_id: str, actor: str) -> Proposal:
        """Atomically approve a pending, unexpired proposal by an authorized actor."""

        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            raise NotFound(f"proposal {proposal_id} not found")
        self.authorize(proposal.repository_id, actor)
        now = utcnow()
        with transaction(self.conn) as conn:
            cursor = conn.execute(
                """
                UPDATE proposals
                   SET state = 'approved', approved_by = ?, approved_at = ?
                 WHERE id = ? AND state = 'pending_approval' AND expires_at > ?
                """,
                (actor, now, proposal_id, now),
            )
            if cursor.rowcount == 0:
                current = conn.execute(
                    "SELECT state, expires_at FROM proposals WHERE id = ?", (proposal_id,)
                ).fetchone()
                if current is None:
                    raise NotFound(f"proposal {proposal_id} not found")
                if current["state"] == "pending_approval" and _is_past(current["expires_at"]):
                    conn.execute(
                        "UPDATE proposals SET state = 'expired' "
                        "WHERE id = ? AND state = 'pending_approval'",
                        (proposal_id,),
                    )
                    raise Expired("proposal expired before approval")
                raise Conflict(f"proposal is {current['state']}, not pending_approval")
        updated = self.get_proposal(proposal_id)
        assert updated is not None
        return updated

    def reject_proposal(self, proposal_id: str, actor: str, reason: str = "") -> Proposal:
        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            raise NotFound(f"proposal {proposal_id} not found")
        self.authorize(proposal.repository_id, actor)
        now = utcnow()
        with transaction(self.conn) as conn:
            cursor = conn.execute(
                """
                UPDATE proposals
                   SET state = 'rejected', rejected_by = ?, rejected_at = ?, last_error = ?
                 WHERE id = ? AND state IN ('pending_approval', 'approved', 'action_failed')
                """,
                (actor, now, reason[:500], proposal_id),
            )
            if cursor.rowcount == 0:
                raise Conflict(f"proposal is {proposal.state}, cannot reject")
        updated = self.get_proposal(proposal_id)
        assert updated is not None
        return updated

    def claim_for_execution(self, proposal_id: str, actor: str) -> Proposal:
        """Move an approved proposal into `executing` exactly once."""

        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            raise NotFound(f"proposal {proposal_id} not found")
        self.authorize(proposal.repository_id, actor)
        with transaction(self.conn) as conn:
            cursor = conn.execute(
                """
                UPDATE proposals
                   SET state = 'executing', attempt_count = attempt_count + 1
                 WHERE id = ? AND state IN ('approved', 'action_failed')
                """,
                (proposal_id,),
            )
            if cursor.rowcount == 0:
                raise Conflict(f"proposal is {proposal.state}, cannot execute")
            conn.execute(
                "UPDATE proposals SET last_error = NULL, error_class = NULL WHERE id = ?",
                (proposal_id,),
            )
        updated = self.get_proposal(proposal_id)
        assert updated is not None
        return updated

    def finish_execution(
        self,
        proposal_id: str,
        *,
        success: bool,
        error: str | None = None,
        error_class: str | None = None,
    ) -> Proposal:
        now = utcnow()
        with transaction(self.conn) as conn:
            cursor = conn.execute(
                """
                UPDATE proposals
                   SET state = ?, executed_at = ?, last_error = ?, error_class = ?
                 WHERE id = ? AND state = 'executing'
                """,
                (
                    "executed" if success else "action_failed",
                    now if success else None,
                    None if success else (error or "action failed")[:500],
                    None if success else error_class,
                    proposal_id,
                ),
            )
            if cursor.rowcount == 0:
                raise Conflict("proposal is not executing")
        updated = self.get_proposal(proposal_id)
        assert updated is not None
        return updated

    def expire_due_proposals(self, now: str | None = None) -> int:
        moment = now or utcnow()
        with transaction(self.conn) as conn:
            cursor = conn.execute(
                "UPDATE proposals SET state = 'expired' "
                "WHERE state = 'pending_approval' AND expires_at <= ?",
                (moment,),
            )
            return cursor.rowcount

    def delete_repository_data(self, repository_id: str) -> dict[str, int]:
        """Delete all stored content for a repository, keeping the audit trail.

        Audit rows hold identifiers, hashes, and workflow metadata only, never
        issue or pull-request content, and the chain is append-only, so deletion
        appends one `repository.deleted` event instead of rewriting history.
        """

        counts: dict[str, int] = {}
        with transaction(self.conn) as conn:
            for table in (
                "proposals",
                "comments",
                "issues",
                "pull_requests",
                "webhook_deliveries",
                "repository_settings",
                "repository_actors",
            ):
                column = "repository_id"
                cursor = conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (repository_id,))  # noqa: S608
                counts[table] = cursor.rowcount
            cursor = conn.execute(
                "UPDATE model_calls SET repository_id = NULL WHERE repository_id = ?",
                (repository_id,),
            )
            counts["model_calls_anonymized"] = cursor.rowcount
            cursor = conn.execute("DELETE FROM repositories WHERE id = ?", (repository_id,))
            counts["repositories"] = cursor.rowcount
        self.append_audit(
            event_type="repository.deleted",
            repository_id=repository_id,
            subject_type="repository",
            subject_id=repository_id,
            detail={"deleted": counts},
        )
        return counts

    # ------------------------------------------------------------ model calls
    def record_model_call(
        self,
        *,
        call_id: str,
        repository_id: str | None,
        workflow: str,
        provider: str,
        model: str,
        started_at: str,
        finished_at: str | None,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: float,
        cache_hit: bool,
        success: bool,
        error_class: str | None,
        prompt_version: str | None,
        input_sha256: str | None,
    ) -> None:
        with transaction(self.conn) as conn:
            conn.execute(
                """
                INSERT INTO model_calls (
                    id, repository_id, workflow, provider, model, started_at, finished_at,
                    input_tokens, output_tokens, estimated_cost_usd, cache_hit, success,
                    error_class, prompt_version, input_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call_id,
                    repository_id,
                    workflow,
                    provider,
                    model,
                    started_at,
                    finished_at,
                    input_tokens,
                    output_tokens,
                    estimated_cost_usd,
                    1 if cache_hit else 0,
                    1 if success else 0,
                    error_class,
                    prompt_version,
                    input_sha256,
                ),
            )

    def cost_summary(self, *, since: str, repository_id: str | None = None) -> CostSummary:
        params: list[object] = [since]
        clause = "WHERE started_at >= ?"
        if repository_id:
            clause += " AND repository_id = ?"
            params.append(repository_id)
        row = self.conn.execute(
            f"""
            SELECT COUNT(*) AS requests,
                   COALESCE(SUM(estimated_cost_usd), 0) AS cost,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(cache_hit), 0) AS cache_hits
              FROM model_calls {clause}
            """,  # noqa: S608
            params,
        ).fetchone()
        assert row is not None
        return CostSummary(
            requests=int(row["requests"]),
            cost_usd=float(row["cost"]),
            input_tokens=int(row["input_tokens"]),
            output_tokens=int(row["output_tokens"]),
            cache_hits=int(row["cache_hits"]),
        )

    def get_cached_response(self, cache_key: str) -> str | None:
        row = self.conn.execute(
            "SELECT response_text FROM model_cache WHERE cache_key = ?", (cache_key,)
        ).fetchone()
        return row["response_text"] if row else None

    def put_cached_response(
        self,
        *,
        cache_key: str,
        provider: str,
        model: str,
        prompt_version: str,
        response_text: str,
        input_sha256: str,
    ) -> None:
        with transaction(self.conn) as conn:
            conn.execute(
                """
                INSERT INTO model_cache (
                    cache_key, provider, model, prompt_version, response_text,
                    input_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET response_text = excluded.response_text
                """,
                (cache_key, provider, model, prompt_version, response_text, input_sha256, utcnow()),
            )

    # ------------------------------------------------------------------ audit
    def append_audit(
        self,
        *,
        event_type: str,
        repository_id: str | None = None,
        actor_login: str | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        detail: JsonObject | None = None,
    ) -> AuditEntry:
        """Append one hash-chained audit event inside its own transaction."""

        payload = detail or {}
        with transaction(self.conn) as conn:
            last = conn.execute(
                "SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = last["hash"] if last else GENESIS_HASH
            created_at = utcnow()
            seed = f"{prev_hash}{event_type}{created_at}{canonical_json(payload)}"
            event_id = f"aud_{sha256_hex(seed)[:24]}"
            digest = hash_canonical(
                {
                    "id": event_id,
                    "repository_id": repository_id,
                    "actor_login": actor_login,
                    "event_type": event_type,
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "detail": payload,
                    "created_at": created_at,
                    "prev_hash": prev_hash,
                }
            )
            conn.execute(
                """
                INSERT INTO audit_events (
                    id, repository_id, actor_login, event_type, subject_type, subject_id,
                    detail_json, created_at, prev_hash, hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    repository_id,
                    actor_login,
                    event_type,
                    subject_type,
                    subject_id,
                    canonical_json(payload),
                    created_at,
                    prev_hash,
                    digest,
                ),
            )
        return AuditEntry(
            seq=0,
            id=event_id,
            repository_id=repository_id,
            actor_login=actor_login,
            event_type=event_type,
            subject_type=subject_type,
            subject_id=subject_id,
            detail=payload,
            created_at=created_at,
            prev_hash=prev_hash,
            hash=digest,
        )

    def list_audit(
        self,
        *,
        repository_id: str | None = None,
        limit: int = 200,
        before_seq: int | None = None,
    ) -> list[AuditEntry]:
        clauses = []
        params: list[object] = []
        if repository_id:
            clauses.append("repository_id = ?")
            params.append(repository_id)
        if before_seq is not None:
            clauses.append("seq < ?")
            params.append(before_seq)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self.conn.execute(
            f"SELECT * FROM audit_events {where} ORDER BY seq DESC LIMIT ?",  # noqa: S608
            params,
        ).fetchall()
        return [
            AuditEntry(
                seq=int(row["seq"]),
                id=row["id"],
                repository_id=row["repository_id"],
                actor_login=row["actor_login"],
                event_type=row["event_type"],
                subject_type=row["subject_type"],
                subject_id=row["subject_id"],
                detail=_loads(row["detail_json"], {}),
                created_at=row["created_at"],
                prev_hash=row["prev_hash"],
                hash=row["hash"],
            )
            for row in rows
        ]

    def verify_audit_chain(self) -> tuple[bool, int, str | None]:
        """Recompute the chain. Returns (ok, checked_count, first_broken_id)."""

        rows = self.conn.execute("SELECT * FROM audit_events ORDER BY seq ASC").fetchall()
        prev = GENESIS_HASH
        for row in rows:
            detail = _loads(row["detail_json"], {})
            expected = hash_canonical(
                {
                    "id": row["id"],
                    "repository_id": row["repository_id"],
                    "actor_login": row["actor_login"],
                    "event_type": row["event_type"],
                    "subject_type": row["subject_type"],
                    "subject_id": row["subject_id"],
                    "detail": detail,
                    "created_at": row["created_at"],
                    "prev_hash": prev,
                }
            )
            if row["prev_hash"] != prev or row["hash"] != expected:
                return False, len(rows), row["id"]
            prev = row["hash"]
        return True, len(rows), None


def new_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{hash_text('|'.join(parts))[:24]}"
