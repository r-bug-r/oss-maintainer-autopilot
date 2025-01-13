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

