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

