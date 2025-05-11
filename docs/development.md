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

## Smoke scenario

```bash
python scripts/smoke.py
```

The script starts the real HTTP server on a free loopback port against a
temporary database, sends a signed `issues.opened` payload for an issue without
reproduction details, approves the generated proposal, executes it, and asserts:

- exactly one proposal and one comment exist;
- exactly one model call was recorded;
- the audit chain verifies;
- a second execution attempt does not post another comment.

It prints the observed values as JSON and exits non-zero on any mismatch.

## History rebuild

`scripts/history/rebuild-history.sh` (Bash) and `rebuild-history.ps1`
(PowerShell 5.1+) synthesize a plausible commit timeline from the files in
`HEAD`. They read blobs with `git cat-file`, split text files into top-level
blocks, order those blocks the way a project grows (hygiene, build, docs, core,
tests, UI, fixes, refactors, performance), spread the commits over a calendar
with sprint weeks and quiet gaps, and write them through a temporary index with
`git commit-tree`. The working tree, the real index and the current branch are
never touched.

```bash
scripts/history/rebuild-history.sh --dry-run          # print the plan
scripts/history/rebuild-history.sh --branch history   # write the branch
```

```powershell
powershell -File scripts/history/rebuild-history.ps1 -DryRun
powershell -File scripts/history/rebuild-history.ps1 -Branch history
```

Both implementations share the same planning algorithm: the same seed produces
the same timeline, and the last commit's tree is compared with `HEAD` before
the branch is reported. Run `--help` or `Get-Help` for the date range, density,
timezone and block-splitting options.

## Adding a workflow

1. Add a closed output schema in `schemas.py` and a prompt bundle in
   `prompts.py`.
2. Add a method on `WorkflowEngine` that builds a minimal context, calls
   `_generate`, and maps the validated output to an action payload.
3. Add the workflow name to `WorkflowName` and to the per-repository toggle set
   in `db.py`/`models.py`.
4. Add the route in `api.py` and a CLI subcommand if it needs one.
5. Add a unit test for validation and an integration test for the full
   propose → approve → execute path.
6. Update the README capability table, the changelog, and this file.

## Console (UI) changes

`src/autopilot/ui/` ships as plain files with no build step: `index.html`,
`styles.css`, and `app.js` are served from the package. There is no bundler, no
framework, and no runtime dependency, so `autopilot serve` is also the dev
server.

Rules for a change here:

- Use the tokens in `styles.css` only. No literal colour, spacing, or font size
  outside `:root`.
- Every state the screen can be in (loading, empty, no-result, error, expired,
  permission-denied) must exist before visual polish.
- Status and risk are text first. Colour is never the only carrier.
- Keep the page free of horizontal overflow at 320, 390, 768, 1024, and 1440 CSS
  pixels: `document.documentElement.scrollWidth - clientWidth` must be `0`.

Verification for a UI change is a browser run against a real server, not a
snapshot test:

```bash
AUTOPILOT_HOME=$(mktemp -d) python -m autopilot seed
AUTOPILOT_HOME=... python -m autopilot serve --port 8811
```

Then, in the browser: check the five widths for overflow, run axe-core with the
`wcag2a`/`wcag2aa`/`wcag21a`/`wcag21aa` rulesets on every screen plus the open
approval dialog, and tab through the approve flow with the mouse untouched.
Record the results in `docs/decisions/` when the change is a design decision
rather than a fix.

## Conventions

- Line length 100; `ruff format` is authoritative.
- Full type annotations on every function, including tests; `mypy` runs over
  `src`, `tests`, and `scripts`.
- No bare `except`; map adapter failures to explicit classes.
- Any new persisted text goes through `redact_value` before it is stored.
- Any new side effect goes through the proposal state machine; never call a
  forge adapter from a request handler.

## Zero-cost register

The plan's `$0 source register` is re-checked before enabling any optional
service. Current stance:

- CI uses GitHub Actions on a public repository (free tier documented).
- No hosted database, queue, or model API is used by default.
- The fake provider and fixture adapter keep the whole vertical slice offline.
- If a provider page changes its limits or starts requiring billing, keep the
  local fallback and update this file instead of adding a paid dependency.
