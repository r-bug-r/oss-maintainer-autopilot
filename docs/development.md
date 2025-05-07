# Development

## Toolchain (verified on this workstation)

| Tool | Version | Required |
| --- | --- | --- |
| Python | 3.12.10 | 3.12.x |
| Git | 2.53.0 | any recent |
| Node.js | 22.22.0 | not required for Phase A |
| pytest | 9.1.1 | pinned in `.[dev]` |
| ruff | 0.14.2 | pinned in `.[dev]` |
| mypy | 1.18.2 | pinned in `.[dev]` |

No runtime dependency is required: the service uses only the standard library.
The optional live provider speaks the OpenAI-compatible HTTP API with
`urllib.request`. The dev extras are pinned so CI and local runs agree.

Dependency inventory (runtime): none. Dev-only: pytest, ruff, mypy, all
MIT/BSD-licensed and used as tools, not linked into the shipped artifact.

