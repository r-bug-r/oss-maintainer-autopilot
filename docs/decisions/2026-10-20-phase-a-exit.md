# 2026-10-20 — Phase A exit gate

Status: accepted
Owner: this session only
Supersedes: nothing; extends `2026-10-20-target-resolution-and-phase-a.md`

## Decision

Phase A is **passed** for the local, zero-cost vertical slice. Phase B (visual
UI/UX) may start.

This is not a claim that the product is deployed. Items that need an external
account, installation, or human consent are recorded as `deferred` below and are
not simulated.

## Commands run and observed results

All commands were run from `C:/dev/open-source/oss-maintainer-autopilot` with the
system Python 3.12.10.

| Command | Observed result |
| --- | --- |
| `python -m ruff format --check src tests scripts` | `40 files already formatted` |
| `python -m ruff check src tests scripts` | `All checks passed!` |
| `python -m mypy` | `Success: no issues found in 40 source files` |
| `python -m pytest` | `109 passed` |
| `python scripts/scan_secrets.py` | `secret scan clean (67 files)` |
| `python scripts/smoke.py` | exit 0; see the check list below |

Smoke output (`checks` array, verbatim):

```text
GET /api/health -> 200
signed webhook -> 202 proposal_created
one pending comment proposal
proposal asks for missing reproduction details
approve -> approved
execute -> executed
second execute rejected
exactly one comment, one model call
audit trail verifies
dashboard served
metrics report one $0 model call
```

The smoke run starts the real `autopilot serve` process on a free loopback port
against a temporary database, sends a signed `issues.opened` payload for an issue
without reproduction details, and drives the public HTTP API. The assertions are
on persisted rows and HTTP responses, not on logs.

## CLI slice run manually

A disposable `AUTOPILOT_HOME` was used:

```text
demo seed                                  -> 4 synthetic issues
triage --repo github:demo/autopilot-sandbox --issue 41
                                           -> pending_approval proposal, 2 reproduction questions,
                                              duplicate candidate #42
approve <id> --actor demo-maintainer       -> state approved
apply <id> --actor demo-maintainer         -> state executed, attempt_count 1
apply <id> --actor demo-maintainer (again) -> refused: proposal is executed
audit verify                               -> {"ok": true, "checked": 4}
metrics --repo github:demo/autopilot-sandbox
                                           -> 1 request, 436 input / 205 output tokens, $0.00
purge                                      -> retention applied
repo delete <id> (dry run)                 -> {"dry_run": true}
repo delete <id> --confirm                 -> deleted 1 comment, 4 issues, 1 proposal, 1 PR
audit verify                               -> {"ok": true, "checked": 6}
```

The delete path removed content and anonymized the cost row while the audit chain
still verified, which is the privacy invariant the plan requires.

## Requirements closed in this pass

- `approver` role: the CLI and API advertised it while a `CHECK` constraint
  rejected it. Migration 2 rebuilds `repository_actors`; a test creates a v1
  database, migrates it, and proves the old owner row survives.
- Stored proposal input context is now redacted. A secret in an issue body no
  longer persists verbatim; `tests/integration/test_privacy.py` asserts the
  database row does not contain the token.
- Repository deletion is reachable from both surfaces:
  `DELETE /api/repositories/{id}` (owner-only, requires `{"confirm": true}`) and
  `autopilot repo delete <id> --confirm` (dry run without the flag).
- `autopilot metrics` exposes the same spend/budget view as `/api/metrics`.
- Unsupported webhook events and actions return `202 {"status": "ignored"}` so a
  forge does not retry them; a test pins the response and proves no proposal is
  created.
- Webhook dispatch uses `isinstance` narrowing instead of `hasattr` plus
  `type: ignore`.

## Known limitations

- **No live pilot.** Implementation sequence items 9 and 10 (install on a
  consenting repository, publish a pilot report) are deferred. They require a
  GitHub account, an App installation, and written consent. No credential exists
  and none was fabricated.
- **GitLab is a declared boundary only.** `GitLabAdapter` raises
  `NotImplementedError`; the normalized models and the adapter protocol are
  shared.
- **Live provider path is untested against a real endpoint.** It is gated behind
  `AUTOPILOT_ALLOW_LIVE_PROVIDER`, a base URL, a key, and both cost caps. Only
  the fake provider is exercised in tests, by design for the $0 gate.
- **GitHub adapter is untested against the live API** for the same reason; the
  fixture adapter covers the execution path.
- **No OpenTelemetry exporter.** The plan asks for cost attribution, which the
  `model_calls` ledger provides; span export is out of scope for this version.
- **Dashboard is a functional harness.** Phase B replaces its presentation; the
  routes and data it uses are already final.
- **SQLite single-writer.** Fine for one process; a multi-tenant hosted
  deployment needs PostgreSQL, which the plan already lists as a later step.
- **No backdated or synthetic Git history.** The repository is initialized with
  the actual date; no commit claims work that did not happen.

