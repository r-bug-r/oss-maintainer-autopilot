# Operations

This is the runbook for a single Autopilot instance: one process, one SQLite
file, one webhook endpoint, one dashboard.

## Configuration

All configuration is environment-driven. `AUTOPILOT_HOME` sets the base
directory (default: the current directory); the database defaults to
`$AUTOPILOT_HOME/var/autopilot.db`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `AUTOPILOT_HOME` | `.` | Base directory for the default database path |
| `AUTOPILOT_DB` | `$AUTOPILOT_HOME/var/autopilot.db` | SQLite file |
| `AUTOPILOT_WEBHOOK_SECRET` | empty | GitHub webhook secret; required by `serve`, minimum 16 characters |
| `AUTOPILOT_HOST` | `127.0.0.1` | Bind address |
| `AUTOPILOT_PORT` | `8000` | Bind port |
| `AUTOPILOT_MAX_BODY_BYTES` | `1048576` | Request body ceiling, 1 KiB–8 MiB |
| `AUTOPILOT_PROVIDER` | `fake` | `fake` or `openai_compatible` |
| `AUTOPILOT_ALLOW_LIVE_PROVIDER` | off | Required before any non-fake provider |
| `AUTOPILOT_OPENAI_BASE_URL` | empty | OpenAI-compatible base URL |
| `AUTOPILOT_OPENAI_API_KEY` | empty | Provider key; never persisted |
| `AUTOPILOT_MODEL` | `gpt-4o-mini` | Model identifier sent to the provider |
| `AUTOPILOT_MODEL_TIMEOUT_SECONDS` | `20` | Per-call timeout |
| `AUTOPILOT_DAILY_REQUEST_LIMIT` | `200` | Per-repository daily calls |
| `AUTOPILOT_DAILY_COST_LIMIT_USD` | `0` | Per-repository daily cap; must be positive for a live provider |
| `AUTOPILOT_MONTHLY_COST_LIMIT_USD` | `0` | Per-repository monthly cap; must be positive for a live provider |
| `AUTOPILOT_PROPOSAL_TTL_MINUTES` | `60` | Approval window, 1 minute–7 days |
| `AUTOPILOT_RETENTION_DAYS` | `14` | Purge window, 1–365 days |
| `AUTOPILOT_ALLOW_REMOTE` | off | Permit non-loopback requests; requires an admin token |
| `AUTOPILOT_ADMIN_TOKEN` | empty | Bearer token for non-loopback requests |
| `AUTOPILOT_TRUST_PROXY` | off | Trust `X-Forwarded-For` when deciding loopback |
| `AUTOPILOT_CACHE_MODEL_OUTPUTS` | on | Cache identical prompt hashes |
| `AUTOPILOT_GITHUB_TOKEN` | empty | Optional; selects the GitHub adapter instead of the fixture adapter |

Invalid combinations fail at startup with `ConfigError`: a live provider without
opt-in, base URL, key, and both cost caps; a remote bind without an admin token;
a body limit outside 1 KiB–8 MiB; a TTL outside 1 minute–7 days.

## Start and stop

```bash
export AUTOPILOT_WEBHOOK_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python -m autopilot serve --host 127.0.0.1 --port 8000
```

`serve` refuses to start without a webhook secret. Stop with `Ctrl-C`; SQLite is
in WAL mode, so an interrupted process leaves a recoverable database. Run
`python -m autopilot audit verify` after an unclean stop.

## Health checks

| Check | Command | Healthy result |
| --- | --- | --- |
| Process alive | `GET /api/health` | `{"status": "ok", ...}` |
| Schema current | `python -m autopilot audit verify` | `{"ok": true, "events": N}` |
| Budget state | `GET /api/metrics` | per-repository request and spend totals |
| Pending work | `python -m autopilot proposals --state pending_approval` | list, oldest first |

## Routine tasks

**Expire stale proposals.** The HTTP API and CLI call `expire_due_proposals`
before listing or acting. A scheduled run keeps the table small:

```bash
python -m autopilot purge
```

`purge` deletes executed, rejected, and expired proposals past the retention
window, plus old model calls, deliveries, and cache rows, and appends one
`retention.purged` audit event.

**Back up.** Copy the database with SQLite's backup API or stop the process first;
copying only the main file while WAL is active can miss recent commits:

```bash
python - <<'PY'
import sqlite3
src = sqlite3.connect("var/autopilot.db")
dst = sqlite3.connect("backups/autopilot-$(date +%F).db")
src.backup(dst)
PY
```

Backups contain issue text (redacted for known secret shapes), actor logins, and
the audit chain. Treat them as private data with the same retention window.

**Restore.** Stop the process, replace `var/autopilot.db`, remove stale
`-wal`/`-shm` files, start, then run `audit verify`.

## Failure playbook

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `ConfigError: remote binding requires AUTOPILOT_ADMIN_TOKEN` | `AUTOPILOT_HOST` is not loopback | set a long random admin token or bind to `127.0.0.1` |
| `ConfigError: live providers require AUTOPILOT_ALLOW_LIVE_PROVIDER=1` | provider set without opt-in | confirm intent, then set the flag and both cost caps |
| Webhook returns `401` | wrong secret or tampered body | rotate the secret in both GitHub and the environment |
| Webhook returns `409 duplicate_delivery` | GitHub retried a delivery already recorded | no action; this is the replay guard |
| Webhook returns `422` | payload lacks required fields | inspect `webhook_deliveries` for the payload hash and outcome |
| Proposal stuck `pending_approval` | nobody approved, or TTL passed | approve before expiry; expired proposals need a new generation |
| Proposal `action_failed` with `error_class=auth` | forge token rejected | rotate `AUTOPILOT_GITHUB_TOKEN`, then re-run `apply` |
| Proposal `action_failed` with `error_class=transport` | forge timeout or 5xx | re-run `apply`; the claim is single-shot so no duplicate comment is posted |
| `budget_exhausted` | per-repository cap reached | raise the cap deliberately or wait for the daily reset |
| `audit verify` returns `ok: false` | chain broken or database edited outside the API | stop writes, restore from backup, investigate before continuing |

## Upgrades

1. Back up the database.
2. Stop the process.
3. Install the new version, run `python -m autopilot audit verify`.
4. Start the process; `init_db` applies pending migrations in order.
5. Re-run the smoke script against a temporary database.

Migrations are forward-only and recorded in `schema_migrations`. Never edit an
applied migration; add the next numbered one.

