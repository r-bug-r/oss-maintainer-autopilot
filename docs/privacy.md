# Privacy

## What is stored

| Data | Where | Why |
| --- | --- | --- |
| Repository identity (`owner/name`, provider, external id) | `repositories` | Route events and proposals |
| Repository settings (workflows, label taxonomy, limits, TTL) | `repository_settings` | Per-repository policy |
| Actor logins and roles | `actors`, `repository_actors` | Authorization |
| Webhook delivery IDs, event name, payload SHA-256, outcome | `webhook_deliveries` | Replay protection and audit |
| Issue number, title, body, author login, labels, timestamps | `issues` | Build the triage prompt |
| Pull request metadata and changed-file list | `pull_requests` | Build the review prompt |
| Proposal payload, action draft, redacted input context, hashes | `proposals` | Human review and reproducibility |
| Posted comment body (redacted) | `comments` | Prove what was posted, idempotency |
| Provider, model, token counts, latency, cost | `model_calls` | Budget and attribution |
| Cached model responses keyed by prompt hash | `model_cache` | Avoid duplicate spend |
| Event type, actor, subject, identifiers, hashes | `audit_events` | Tamper-evident history |

Stored text is redacted before persistence: GitHub and GitLab tokens, OpenAI,
Anthropic, Google, Slack and AWS key shapes, private key blocks, `Bearer` values,
`password=`/`secret=`/`token=` assignments, and email addresses. Redaction is
applied to the proposal payload, the stored input context, and every log record.

## What is not stored

- No raw webhook bodies. Only a SHA-256 of the body plus the normalized fields.
- No access tokens. The GitHub token is read from the environment per process and
  never written to SQLite, logs, or the audit trail.
- No prompts. The prompt version and the input hash are stored so a proposal can
  be traced, but the rendered prompt is not persisted.
- No browser cookies or sessions. Loopback access is not authenticated, and no
  session state exists.

## Data sent to a model provider

The default provider is deterministic and local: it receives the context and
returns canned structure derived from it, with no network call. If a live
provider is explicitly enabled, the redacted context below is sent:

- issue: repository name, issue number, title, body, labels, author login, label
  taxonomy, and duplicate-candidate titles with scores;
- pull request: repository name, number, title, body, author login, base/head
  refs, changed file paths, and addition/deletion counts;
- release notes: merged pull request numbers and titles since the last release.

Author logins are included because maintainers need to know who reported an
issue. Email addresses and token shapes are replaced with `[REDACTED:kind]`
before the prompt is built.

## Retention

Defaults: `AUTOPILOT_RETENTION_DAYS=14`, `AUTOPILOT_PROPOSAL_TTL_MINUTES=60`.

- Proposals expire after the TTL and cannot be approved afterwards.
- `autopilot purge` deletes executed, rejected, and expired proposals older than
  the retention window, model calls older than the window, webhook delivery rows
  older than the window, and cache entries older than the window. It appends a
  `retention.purged` audit event with the cutoff and counts.
- Deleting a repository (`autopilot purge` with a repository id, or
  `delete_repository_data`) removes issues, pull requests, comments, proposals,
  settings, roles, and deliveries, anonymizes cost rows by clearing
  `repository_id`, deletes the repository row, and appends one
  `repository.deleted` event containing counts only.
- The audit chain is append-only. It retains event identifiers, actor logins,
  timestamps, and hashes of prior content; it never retains issue or pull
  request text.

## Who can read the data

- Anyone with filesystem access to the SQLite file: the operator and the host
  owner.
- Anyone who can reach the HTTP API: loopback by default; remote only with an
  admin token.
- The dashboard shows repository names, proposal drafts, and audit events to
  whoever can reach it. Bind to loopback unless you intend otherwise.

## Data subject requests

There is no multi-tenant account system. To remove a person's content, delete the
affected repository's data (removes all issue and comment text) or run
`autopilot purge` after lowering the retention window. Audit rows keep the actor
login because they are the record of who approved what; if that identifier must
be removed, the correct action is to destroy and recreate the database, which
also destroys the audit chain — record that decision in `docs/decisions/`.

