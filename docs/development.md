# Development

## Toolchain (verified on this workstation)

| Tool | Version | Required |
| --- | --- | --- |
| Python | 3.12.10 | 3.12.x |
| Git | 2.53.0 | any recent |
| Node.js | 22.22.0 | not required for Phase A |
| pytest | 9.1.1 | pinned in `.[dev]` |
| ruff | 0.14.2 | pinned in `.[dev]` |
| mypy | 1.18.2 | pinned in `.[dev]` |

No runtime dependency is required: the service uses only the standard library.
The optional live provider speaks the OpenAI-compatible HTTP API with
`urllib.request`. The dev extras are pinned so CI and local runs agree.

Dependency inventory (runtime): none. Dev-only: pytest, ruff, mypy, all
MIT/BSD-licensed and used as tools, not linked into the shipped artifact.

## Setup

```bash
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Task runner

`make` targets mirror the CI workflow:

| Command | What it runs |
| --- | --- |
| `make format` | `ruff format src tests scripts` |
| `make format-check` | `ruff format --check src tests scripts` |
| `make lint` | `ruff check src tests scripts` |
| `make typecheck` | `mypy` |
| `make test` | `pytest` |
| `make security` | `python scripts/scan_secrets.py` |
| `make smoke` | `python scripts/smoke.py` |
| `make check` | all of the above |

Without `make`, run the underlying `python -m ...` commands directly; they are
identical on Windows and POSIX.

## Layout

```text
src/autopilot/
  api.py          HTTP router, authentication, static assets
  adapters.py     forge boundary: fixture adapter, GitHub adapter, GitLab stub
  canonical.py    canonical JSON, hashing, id generation
  cli.py          argparse entry point
  config.py       environment-driven settings and validation
  db.py           schema, migrations, transaction helper
  duplicates.py   deterministic duplicate scoring
  gateway.py      budget checks, cache, call ledger
  models.py       normalized dataclasses and the proposal state machine
  prompts.py      prompt bundles and workflow instructions
  providers.py    fake provider and OpenAI-compatible provider
  redaction.py    secret and email redaction
  runtime.py      wiring: settings, store, adapter, gateway, engine
  schemas.py      closed output schemas and validators
  store.py        SQLite persistence, roles, audit chain
  ui/             console: token-driven CSS, decision rail, no build step
  webhooks.py     signature verification, replay guard, normalization
tests/
  unit/           pure logic, schema, redaction, webhooks, roles
  integration/    router, failures, privacy, real-socket server
  fixtures/       synthetic payload builders
scripts/
  smoke.py        end-to-end Phase A scenario
  scan_secrets.py local secret scan
  history/        synthetic commit timeline rebuilders (Bash, PowerShell)
```

## Testing rules

- Tests never touch the network. The fake provider is deterministic and the
  fixture adapter records writes in SQLite.
- Every test uses a temporary database from the `settings`/`runtime` fixtures.
- A test marked `live` is opt-in and requires explicit credentials; the default
  run deselects nothing because no such test exists yet.
- Assertions target observable behavior: HTTP status and body, proposal state,
  row counts, audit-chain verification, and redaction. Avoid asserting on
  implementation text.

Run one slice:

```bash
python -m pytest tests/integration/test_api_flow.py -q
python -m pytest -q -k "duplicate"
```

