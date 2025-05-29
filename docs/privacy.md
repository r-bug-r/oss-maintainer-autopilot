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

