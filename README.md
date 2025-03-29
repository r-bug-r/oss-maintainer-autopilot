# OSS Maintainer Autopilot

Approval-gated maintainer automation for GitHub and GitLab repositories.

**One-line promise:** reduce repetitive maintainer work without giving an agent
unchecked repository authority.

Autopilot reads issues and pull requests, drafts triage summaries, missing
reproduction questions, duplicate candidates, review notes, and release notes,
and stores every draft as a **proposal**. A proposal does nothing until a named
human with a repository role approves it, and applying it is a second,
separately audited step. Every model call, approval, and action lands in a
hash-chained audit log.

## What it does today

| Capability | State |
| --- | --- |
| Signed GitHub webhook intake, replay rejection, delivery dedupe | implemented |
| Normalized repository / issue / pull request models | implemented |
| Issue triage: summary, labels, missing-reproduction questions, duplicate candidates | implemented |
| Pull request review notes | implemented |
| Release-note drafting from merged pull requests | implemented |
| Deterministic duplicate retrieval (no model involved) | implemented |
| Proposal store with expiry, role checks, one-shot execution | implemented |
| Hash-chained audit log with verification command | implemented |
| Per-repository request and spend budgets, response cache, call ledger | implemented |
| GitHub adapter (comment, labels, reads) | implemented, opt-in token |
| Local fixture adapter (default, no network) | implemented |
| GitLab adapter | boundary declared, not implemented |
| Dashboard | functional Phase A harness; visual design is Phase B |

## Quick start (no token, no network, $0)

```bash
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

export AUTOPILOT_HOME="$PWD"  # keep the database inside the project
python -m autopilot demo seed
python -m autopilot triage --repo github:demo/autopilot-sandbox --issue 41
python -m autopilot proposals --state pending_approval
python -m autopilot approve <proposal-id> --actor demo-maintainer
python -m autopilot apply <proposal-id> --actor demo-maintainer
python -m autopilot audit verify
```

`demo seed` writes synthetic issues into SQLite. The default provider is the
deterministic fake provider, and the default adapter writes comments to the
local store, so this walkthrough cannot touch a real repository.

## Commands

| Command | Purpose |
| --- | --- |
| `serve` | Run the webhook receiver, JSON API, and dashboard |
| `repo add <owner/name> --owner <login>` | Register a repository and make `<login>` its owner |
| `repo list` / `repo enable <id> [--disable]` | Inspect repositories, toggle automation |
| `repo delete <id> [--confirm]` | Delete stored content; the audit trail is kept |
| `demo seed` | Write synthetic issues and pull requests |
| `triage --repo <id> --issue <n>` | Draft an issue-triage proposal |
| `review-pr --repo <id> --pr <n>` | Draft a pull-request review proposal |
| `proposals [--state <state>]` | List proposals |
| `show <id>` | Show one proposal with citations |
| `approve <id> --actor <login>` / `reject <id> --actor <login>` | Record a decision |
| `apply <id> --actor <login>` | Perform the approved side effect once |
| `audit verify` / `audit list` | Check or read the hash chain |
| `roles grant <id> <login> <role>` | Grant `owner`, `maintainer`, or `approver` |
| `metrics [--repo <id>]` | Model-call spend, tokens, cache hits, budget state |
| `purge` | Apply the retention policy |

## Run the receiver and dashboard

```bash
export AUTOPILOT_WEBHOOK_SECRET=...   # required; the server refuses to start without it
python -m autopilot serve --port 8000
```

Open `http://127.0.0.1:8000/` for the local dashboard. Loopback requests are
trusted; a non-loopback bind requires `AUTOPILOT_ADMIN_TOKEN` and
`Authorization: Bearer <token>`, plus an explicit actor header for mutations.

## Verify

```bash
python -m ruff format --check src tests scripts
python -m ruff check src tests scripts
python -m mypy
python -m pytest
python scripts/smoke.py
```

The smoke script starts the real HTTP server against a temporary database,
delivers a signed `issues.opened` fixture, approves the generated question-only
proposal, executes it, and asserts that exactly one comment and one model call
exist and that the audit chain verifies.

