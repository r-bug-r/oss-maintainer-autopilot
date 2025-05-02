# 2026-10-20 — Target resolution and Phase A start

Status: accepted
Owner: this session only

## Target resolution

The kickoff prompt contains the literal placeholder
`TARGET_PROJECT: <replace-with-one-folder-name>`. This session resolves the
target to **`oss-maintainer-autopilot/`** from two pieces of repository evidence:

1. The session working directory is `C:/dev/open-source/oss-maintainer-autopilot`,
   the only folder whose contents are this project's plan files.
2. The workspace root contains the five valid project folders; no other
   session-owned artifacts exist, and no conflicting target is stated.

Decision recorded as `start` for `oss-maintainer-autopilot/` only. This session
will not edit sibling project folders or shared root plan files. If the operator
intended a different target, this session must be stopped before Phase B because
Phase A artifacts would then belong to the wrong folder.

## Entry conditions (repository evidence)

- `implementation-plan.md` and `ui-ux-plan.md` exist in the target folder.
- No application code, Git repository, package manifest, or user changes exist.
- Runtime available: Python 3.12.10, Git 2.53.0, Node 22.22.0, pytest 9.1.1,
  Chrome and Edge. `uv` is absent; the system Python + venv fallback is used.
- The plan's default stack is Python 3.12, SQLite, local process execution, and a
  deterministic fake provider. The local fake-provider vertical slice is the
  required first milestone, so no external credential or consent is needed for
  Phase A.
- Real GitHub App creation, installation, and a pilot repository require an
  external GitHub account and explicit pilot consent. Those sequence items are
  narrowed to a documented, permission-scoped adapter plus local fixtures until
  consent exists. No live provider call is made.

## Safety and cost posture

- Runtime dependencies: Python standard library only.
- Development dependencies: pytest, ruff, mypy (free, local, MIT/Apache
  licensed tooling). No paid service, credit card, hosted database, or live
  model call is used.
- All repository, issue, and pull-request data is synthetic.
- External side effects are impossible in the default configuration: the local
  adapter writes to SQLite only; the HTTP adapter is disabled unless an
  installation token and explicit opt-in are configured.

## Planned Phase A sequence

1. SQLite schema, canonical hashing, redaction, normalization.
2. Signed webhook receiver, delivery dedupe, event dispatch.
3. Provider contract, fake provider, budget/cost ledger, strict output schemas.
4. Issue triage + deterministic duplicate retrieval, PR summary, release notes.
5. Proposal store, role authorization, expiry, action execution, hash-chained audit.
6. CLI, HTTP API, GitHub adapter boundary, end-to-end smoke.
7. Unit/contract/integration/security tests, then formatter/linter/typecheck/smoke.
8. Architecture, threat model, privacy, operations, self-hosting docs and CI.

Phase B (UI) starts only after a separate dated entry records the Phase A exit
gate as passed.
