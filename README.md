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

