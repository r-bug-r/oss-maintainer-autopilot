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

### 2.7 Release and provenance

Every release should include:

- Version, date, and release notes generated from actual history.
- Source commit identifier.
- Reproducible build or container digest where applicable.
- Test and security-scan status.
- Known limitations.
- Migration notes.
- SBOM for distributable binaries or containers when practical.

For historical context, create files such as `docs/retrospectives/2026-10-initial-research.md` with explicit labels. Do not manufacture dates or rewrite existing history to imply work that did not happen.

### 2.8 Community and governance

Start with a benevolent-maintainer model and publish the path toward broader governance:

- Public issue templates for bugs, features, security, and support.
- A maintainer decision log.
- A `good first issue` policy with real bounded tasks.
- Response-time goals stated as goals, not promises.
- Contributor license terms only if legally necessary and clearly explained.
- A roadmap based on user evidence.
- No silent telemetry; make opt-in and document it.

### 2.9 Funding evidence packet

Maintain a `funding/` folder outside the product runtime or a private application workspace containing:

- Repository URL, license, and release history.
- Current project start date.
- Maintainer role and ownership evidence.
- User counts defined precisely.
- Downloads, dependent repositories, external contributors, merged PRs, and issue activity with source links.
- Pilot permissions and anonymized case studies.
- API usage ledger and monthly forecast.
- Privacy and data-retention policy.
- Milestones funded by credits.
- What remains open-source and self-hostable.
- Honest limitations and program-fit gaps.

As of the official pages reviewed for this plan, OpenAI's Codex for Open Source program says eligible maintainers may receive six months of ChatGPT Pro with Codex, conditional Codex Security, and API credits for eligible open-source maintainer workflows; selection and scope are discretionary. Anthropic's Claude for Open Source page advertises six months of Claude Max 20x and lists signals including 500 dependent repos, 100 dependent packages, 200,000 combined monthly downloads, 100 merged PRs into repositories not owned in the prior 12 months, 20 external contributors, or an OpenSSF criticality score of 0.4 or above. Verify current terms before applying because program pages and benefits change.
### 2.10 Momus gap assessment and resolution

The initial plan is **conditionally approved**, with the following gaps resolved here before execution:

| Gap | Severity | Resolution |
|---|---|---|
| Provider, runtime, and deployment choices were open-ended | High | Default to Python 3.12, SQLite, local process execution, and a CLI-first vertical slice. Add a hosted adapter only after local acceptance tests pass. |
| “Zero cost” was not operationally defined | Critical | The $0 path below uses local software, public-repository infrastructure, deterministic fakes, and local models. Paid API calls, paid hosting, paid design tools, and paid domains are not required. |
| Free-service limits and billing risk were unspecified | High | Each optional service has a purpose, a free-use boundary, a no-card preference, and a local fallback. Current limits must be rechecked before use. |
| UI work had no acceptance contract | High | The separate UI/UX plan defines screens, states, tokens, accessibility, responsive breakpoints, and rendered verification gates for every project. |
| Legal and data-use boundaries were incomplete | High | Add license, third-party terms, consent, deletion, retention, export, and model-provider disclosure gates below. |
| Some adoption targets were difficult to measure | Medium | Define each metric's numerator, denominator, time window, source, and consent status before publishing it. |

### 2.11 Default $0 development stack

The following is the default path for the first 90 days. It assumes the developer already has a computer and internet access; it does not treat electricity, an existing device, or personal time as a cash expense. No credit card, paid API, paid hosting plan, paid domain, paid analytics service, or paid design subscription is required.

| Need | Default $0 choice | Why | Fallback or limit |
|---|---|---|---|
| Runtime | Python 3.12, `uv` or `venv`, `pytest`, `ruff`, `mypy` | Fast CLI/server iteration and broad document/API libraries | Use the system Python package manager if `uv` is unavailable |
| Source control | Public GitHub repository with Git | Issues, discussions, releases, and transparent history | Any public Git host works; keep exported release archives |
| CI | GitHub Actions on a public repository | GitHub documents public-repository Actions usage as free | Run locally when quotas or service availability change |
| Static project site | GitHub Pages | GitHub documents Pages availability for public repositories on GitHub Free | Serve `docs/` locally; do not buy a domain |
| Database | SQLite and local filesystem | No account, network, or recurring cost | PostgreSQL only for a later, separately funded deployment |
| Model in development | Deterministic fake provider | Zero cost, repeatable, safe for CI | Ollama with a locally downloaded model when hardware permits |
| Local inference | Ollama or another local runtime | Ollama documents local execution on supported desktop systems | Smaller model, CPU-only mode, or fake provider if hardware is insufficient |
| OCR | Tesseract plus local PDF/text tools | Local and reproducible for document fixtures | Use text fixtures when OCR dependencies are unavailable |
| UI design | Plain Markdown/CSS tokens or self-hosted Penpot | Penpot describes its design tool as free and open source | Do not block implementation on a design SaaS account |
| Browser verification | Playwright plus `@axe-core/playwright` | Playwright documents accessibility testing with axe-core | Manual browser checks if browser binaries cannot be installed |
| Feedback | GitHub Issues, Discussions, and volunteer interviews | Public and auditable, with no form or analytics subscription | Local notes with consent and redaction |
| Demo hosting | Localhost first; GitHub Pages for static fixture demos | Avoids server bills and secret handling | A public demo is optional, never a prerequisite for correctness |

The only model-dependent test in CI should be an explicit, opt-in live smoke test. All normal CI, screenshots, benchmarks, and regression tests must run with fakes or local models.

