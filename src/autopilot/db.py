"""SQLite persistence and schema migrations."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS repositories (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider IN ('github', 'gitlab')),
    full_name TEXT NOT NULL,
    external_id TEXT,
    owner_login TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    retention_days INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (provider, full_name)
);

CREATE TABLE IF NOT EXISTS repository_settings (
    repository_id TEXT PRIMARY KEY REFERENCES repositories(id) ON DELETE CASCADE,
    workflow_flags_json TEXT NOT NULL,
    label_taxonomy_json TEXT NOT NULL,
    daily_request_limit INTEGER NOT NULL,
    daily_cost_limit_usd REAL NOT NULL,
    proposal_ttl_minutes INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actors (
    login TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repository_actors (
    repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    actor_login TEXT NOT NULL REFERENCES actors(login) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'maintainer')),
    granted_at TEXT NOT NULL,
    granted_by TEXT,
    PRIMARY KEY (repository_id, actor_login)
);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    delivery_id TEXT PRIMARY KEY,
    repository_id TEXT,
    event TEXT NOT NULL,
    received_at TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    outcome TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS issues (
    repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    external_id TEXT,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    author_login TEXT,
    state TEXT NOT NULL,
    labels_json TEXT NOT NULL DEFAULT '[]',
    url TEXT,
    created_at TEXT,
    updated_at TEXT,
    input_sha256 TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (repository_id, number)
);

CREATE TABLE IF NOT EXISTS pull_requests (
    repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    external_id TEXT,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    author_login TEXT,
    state TEXT NOT NULL,
    base_ref TEXT,
    head_ref TEXT,
    files_json TEXT NOT NULL DEFAULT '[]',
    input_sha256 TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (repository_id, number)
);

CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    issue_number INTEGER NOT NULL,
    body TEXT NOT NULL,
    author_login TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('fetched', 'posted')),
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    workflow TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_number INTEGER,
    state TEXT NOT NULL CHECK (state IN (
        'pending_approval', 'approved', 'executing', 'executed', 'rejected',
        'expired', 'action_failed'
    )),
    action_type TEXT NOT NULL CHECK (action_type IN ('comment', 'label', 'none')),
    action_payload_json TEXT NOT NULL DEFAULT '{}',
    payload_json TEXT NOT NULL DEFAULT '{}',
    citations_json TEXT NOT NULL DEFAULT '[]',
    input_sha256 TEXT NOT NULL,
    input_context_json TEXT NOT NULL DEFAULT '{}',
    prompt_version TEXT,
    provider TEXT,
    model TEXT,
    model_request_id TEXT,
    confidence REAL,
    risk TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    rejected_by TEXT,
    rejected_at TEXT,
    executed_at TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    error_class TEXT,
    idempotency_key TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS proposals_state_idx ON proposals (state, created_at);
CREATE INDEX IF NOT EXISTS proposals_repo_idx ON proposals (repository_id, created_at);

CREATE TABLE IF NOT EXISTS model_calls (
    id TEXT PRIMARY KEY,
    repository_id TEXT,
    workflow TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    cache_hit INTEGER NOT NULL DEFAULT 0 CHECK (cache_hit IN (0, 1)),
    success INTEGER NOT NULL DEFAULT 0 CHECK (success IN (0, 1)),
    error_class TEXT,
    prompt_version TEXT,
    input_sha256 TEXT
);

CREATE INDEX IF NOT EXISTS model_calls_started_idx ON model_calls (started_at);
CREATE INDEX IF NOT EXISTS model_calls_repo_idx ON model_calls (repository_id, started_at);

CREATE TABLE IF NOT EXISTS model_cache (
    cache_key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    response_text TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    repository_id TEXT,
    actor_login TEXT,
    event_type TEXT NOT NULL,
    subject_type TEXT,
    subject_id TEXT,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS audit_repo_idx ON audit_events (repository_id, seq);

CREATE TRIGGER IF NOT EXISTS audit_events_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;
"""

_SCHEMA_V2 = """
-- v1 constrained roles to owner/maintainer while the CLI and API already
-- advertised `approver`. SQLite cannot alter a CHECK constraint, so rebuild.
CREATE TABLE repository_actors_v2 (
    repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    actor_login TEXT NOT NULL REFERENCES actors(login) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'maintainer', 'approver')),
    granted_at TEXT NOT NULL,
    granted_by TEXT,
    PRIMARY KEY (repository_id, actor_login)
);

INSERT INTO repository_actors_v2 (
    repository_id, actor_login, role, granted_at, granted_by
)
SELECT repository_id, actor_login, role, granted_at, granted_by
FROM repository_actors;

DROP TABLE repository_actors;
ALTER TABLE repository_actors_v2 RENAME TO repository_actors;
"""

_MIGRATIONS: tuple[tuple[int, str], ...] = ((1, _SCHEMA_V1), (2, _SCHEMA_V2))


