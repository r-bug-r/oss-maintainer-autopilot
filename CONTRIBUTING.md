# Contributing

## Ground rules

- Nothing reaches a forge without a human approval. Do not add a code path that
  comments, labels, closes, merges, or releases without a stored `approved`
  proposal and a named actor.
- The default configuration must stay zero-cost and offline: local SQLite, the
  fake provider, and the local fixture adapter. A change that needs a paid
  service, a credit card, or a live model call by default will be rejected.
- Untrusted text (issue bodies, pull request titles, comments) never becomes an
  instruction. Keep it inside the `CONTEXT_JSON` block and keep model output
  validated by `autopilot.schemas`.
- Fail closed. Prefer an explicit exception to a silent fallback.

## Development setup

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

Python 3.12 or newer is required. Runtime dependencies are the standard library
only; `pytest`, `ruff`, and `mypy` are development-only.

## Checks before opening a pull request

```bash
python -m ruff format --check src tests scripts
python -m ruff check src tests scripts
python -m mypy
python -m pytest
python scripts/smoke.py
```

All five must pass. The smoke script is the Phase A exit scenario: it starts the
real HTTP server, delivers a signed fixture, approves and executes a proposal,
and asserts exactly one comment and a valid audit chain.

## Testing expectations

Add tests for behavior a consumer can observe:

- new workflow output: schema validation, rejection of unknown fields, and a
  fixture that exercises the fake provider;
- new state transitions: the illegal transition is rejected, not just the legal
  one accepted;
- new HTTP routes: unauthenticated, unauthorized, invalid-body, and not-found
  paths, plus the success path;
- anything touching secrets: redaction in logs and persistence.

Do not add tests that only assert a function exists, that a mock was called, or
that a list is non-empty.

## Commit messages

`<area>: <imperative summary>` — for example `webhooks: reject unsigned
deliveries before parsing`. Explain *why* in the body when the change is not
obvious.

## Security reports

Do not open a public issue. Follow `SECURITY.md`.

## License

Contributions are accepted under Apache-2.0. By submitting a pull request you
confirm you have the right to contribute the change under that license.
