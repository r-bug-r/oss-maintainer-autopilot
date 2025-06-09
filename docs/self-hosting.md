# Self-hosting

Autopilot is one Python process with a SQLite file. There is no queue, no worker,
and no external database in this version. Any host that can run Python 3.12 and
receive an inbound HTTPS request can run it.

## Requirements

- Python 3.12 (3.13 is untested for this version).
- A writable directory for `var/autopilot.db`.
- A public HTTPS endpoint for the webhook, or a tunnel during local testing.
- No runtime Python dependencies beyond the standard library.
- No model provider account on the default path. The fake provider is local.

## Install

```bash
git clone <your-fork-url> oss-maintainer-autopilot
cd oss-maintainer-autopilot
python -m venv .venv
. .venv/bin/activate
pip install .
```

The package installs one console script, `autopilot`. `pip install -e ".[dev]"`
installs the test and lint tools instead.

