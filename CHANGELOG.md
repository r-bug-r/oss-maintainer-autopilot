# Changelog

All notable changes to this project are documented here. Dates are the date the
change landed. The project follows a `MAJOR.MINOR.PATCH` scheme from `0.1.0`.

## Unreleased

### Fixed

- Fake provider now reads the full `CONTEXT_JSON` block. The previous extractor
  stopped at the closing delimiter and silently produced empty context, so the
  fake provider always emitted a generic summary and no duplicate candidates.
- `delete_repository_data` appended a `repository.deleted` audit event instead of
  rewriting existing audit rows, which the append-only triggers rejected.
- The `approver` role was advertised by the CLI and API but rejected by a
  `CHECK` constraint. Migration 2 rebuilds `repository_actors` to accept it.
- The stored proposal input context now goes through `redact_value`, so a
  secret in an issue body cannot persist verbatim.
- Unsupported webhook events and actions now return `202 {"status": "ignored"}`
  instead of an error-shaped body, so the forge does not retry them.

### Added

- `autopilot metrics` for the same spend and budget view as `/api/metrics`.
- `autopilot repo delete <id> [--confirm]` and `DELETE /api/repositories/{id}`
  so an owner can remove stored content while keeping the audit trail.
- `scripts/scan_secrets.py` plus a `make security` target, wired into CI.
- `scripts/history/rebuild-history.sh` and `rebuild-history.ps1`: deterministic
  synthetic commit timelines from `HEAD`, written with git plumbing without
  touching the working tree or the index.
- Shared synthetic webhook fixtures in `tests/fixtures/payloads.py`.
- Failure-path tests: provider timeout, failed generation leaving no cache entry,
  forge failure producing a retryable `action_failed` proposal, per-repository
  cost attribution, budget rejection before provider invocation, repository
  deletion, and log redaction.
- Fake-provider contract tests that fail if prompt context stops reaching the
  provider.
- `scripts/smoke.py`: end-to-end Phase A smoke scenario over the real HTTP
  server with a signed fixture.
- Documentation set: architecture, threat model, privacy, operations,
  self-hosting, development, governance, security policy, contributing guide,
  code of conduct, and license.
- CI, security, and release workflows.
- Static landing page in `site/`: responsive light/dark layouts, a social
  preview card, and a GitHub Pages workflow for deployment.

### Changed

- The console (`src/autopilot/ui/`) has a visual design: token file, decision
  rail, and layouts for overview, queue, proposal detail, audit, and settings.
  Routes, API calls, and the data they read are unchanged from Phase A.
- Clear filters is disabled when no filter is set, and focus falls back to the
  status filter instead of being dropped when the control becomes inert.

## 0.1.0 — 2026-10-20

### Added

- Normalized repository, issue, pull request, and comment models with canonical
  JSON hashing.
- SQLite schema with migrations, foreign keys, append-only audit triggers, and
  single-statement state transitions.
- Signed GitHub webhook receiver: size check, HMAC verification, event and action
  allowlist, delivery dedupe, normalized event dispatch.
- Deterministic duplicate retrieval (token overlap and title similarity) with no
  model involvement.
- Versioned prompts with untrusted-content framing and injection signals.
- Strict output schemas for issue triage, pull request review, and release notes;
  unknown fields and invalid action types are rejected.
- Deterministic fake provider and an OpenAI-compatible provider behind an
  explicit opt-in.
- Model gateway: pre-call budget enforcement, response cache, and a call ledger
  attributing tokens and cost to repository and workflow.
- Proposal store: expiry, role authorization, claim-once execution, retry after
  failure, and a hash-chained audit log with `audit verify`.
- Local fixture adapter and a GitHub adapter (read issues/pull requests, post
  comments, add labels). GitLab is a declared, unimplemented boundary.
- CLI: `serve`, `repo add|list|enable`, `demo seed`, `triage`, `review-pr`,
  `proposals`, `show`, `approve`, `reject`, `apply`, `audit verify|list`,
  `roles grant`, `purge`.
- HTTP API: health, repositories, proposals, approve/reject/execute, settings,
  audit, audit verification, metrics, and a functional dashboard harness.
- Retention purge that deletes expired content while keeping the audit chain.
