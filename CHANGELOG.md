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

