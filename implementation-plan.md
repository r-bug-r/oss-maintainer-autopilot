# OSS Maintainer Autopilot — Implementation Plan

> Project-specific section extracted from `../open-source-projects-plan.md`. The shared portfolio guide in that source remains authoritative.

## Shared Development Guide (authoritative source excerpt)

## 2. Shared Development Guide

### 2.1 Repository setup

Each project should begin with:

```text
repo/
  README.md
  LICENSE
  CONTRIBUTING.md
  CODE_OF_CONDUCT.md
  SECURITY.md
  CHANGELOG.md
  GOVERNANCE.md
  docs/
    architecture.md
    threat-model.md
    privacy.md
    operations.md
    decisions/
  src/
  tests/
    unit/
    integration/
    fixtures/
  examples/
  scripts/
  .github/
    workflows/
      ci.yml
      release.yml
      security.yml
```

Recommended baseline:

- TypeScript or Python, chosen according to the ecosystem being integrated.
- A pinned runtime and lockfile.
- Formatting, linting, static typing, unit tests, integration tests, and a smoke command.
- Dependency vulnerability scanning and secret scanning in CI.
- Docker or a documented local install for server projects.
- SQLite for the first local deployment; PostgreSQL only when multi-user concurrency requires it.
- OpenTelemetry-compatible spans for request, model, tool, policy, and persistence events.

Do not create five repositories before validating the first product. Use a monorepo only if shared packages are genuinely reused; otherwise independent repositories make adoption and ownership clearer.

### 2.2 Development loop

1. Write a one-sentence user problem and a measurable acceptance criterion.
2. Add a threat or failure case before adding automation.
3. Implement deterministic domain logic first.
4. Add a provider adapter behind a narrow interface.
5. Test model responses with fixtures and contract checks; never rely only on live calls.
6. Add one end-to-end scenario using a fake provider.
7. Run the real provider only in opt-in integration tests with spend limits.
8. Document data flow and failure behavior.
9. Release a small version with a changelog entry.
10. Collect feedback and update the decision log.

### 2.3 Model integration contract

All projects that call a model should expose an interface equivalent to:

```text
complete(request) -> response
count_tokens(request) -> estimate
stream(request) -> events
```

The adapter must:

- Set request and total spend limits.
- Record provider, model, request ID, input/output token counts, latency, and estimated cost.
- Support cancellation and timeouts.
- Classify retryable versus permanent failures.
- Redact secrets before logs.
- Never retry non-idempotent side effects.
- Permit a deterministic fake implementation for tests.

Prompt templates are versioned artifacts. Store structured output schemas and validate model output before it reaches business logic.

### 2.4 Security baseline

For every project:

- Threat-model secrets, untrusted input, model prompt injection, data exfiltration, and denial of service.
- Treat model output as untrusted data.
- Use allowlists for tools, hosts, paths, repositories, and file types.
- Require explicit authorization for destructive operations.
- Use least-privilege tokens and separate development, test, and production credentials.
- Keep user content out of analytics by default.
- Publish a security contact and a supported-version policy.
- Add a private vulnerability-reporting path before public launch.

### 2.5 Testing ladder

| Layer | What it proves | Required examples |
|---|---|---|
| Unit | Pure rules and transformations | Policy precedence, parsers, budget math, redaction |
| Contract | Provider and integration schemas | Model response validation, webhook payloads, GitHub API fixtures |
| Integration | Components work together | Database, queue, provider fake, repository fixture |
| End-to-end | User workflow works | Install, authorize, run, approve, report, recover |
| Security | Abuse cases are contained | Prompt injection, path traversal, secret leakage, replay abuse |
| Live smoke | Real deployment works | One bounded request using a configured provider |

A test is useful only when it could fail for a plausible user-visible defect. Do not test copied values, source text, or a bare “does not throw” outcome.

### 2.6 API-cost controls

Before requesting funding, implement an auditable cost ledger:

```text
run_id
project
workflow
provider
model
request_id
started_at
input_tokens
output_tokens
estimated_cost
cache_hit
success
error_class
```

Controls:

- Development budget per day.
- Per-run and per-user request limits.
- Hard monthly cap.
- Cheap model for classification and routing; stronger model only for ambiguous work.
- Batch processing for offline evaluation where available.
- Cache immutable prompts and repository metadata when safe.
- Local fake/provider for CI.
- Human approval before expensive or externally visible actions.

The application should show exactly why funded API usage is necessary: evaluation, model comparison, adversarial testing, or the product's core workflow. Do not describe ordinary personal coding assistance as project API usage.

