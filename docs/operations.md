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

