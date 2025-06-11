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

## Register the webhook

1. Create a GitHub App (or a repository webhook during evaluation) with:
   - **Repository permissions:** Issues — read and write; Pull requests — read;
     Contents — read; Metadata — read.
   - **Subscribe to events:** Issues, Pull request, Release.
   - **Webhook URL:** `https://<your-host>/webhooks/github`.
   - **Webhook secret:** a random 32-byte value; this is
     `AUTOPILOT_WEBHOOK_SECRET`.
2. Install the App on exactly the repositories you intend to automate. Do not
   install it organization-wide during evaluation.
3. Register the repository locally and grant roles:

```bash
python -m autopilot repo add github:owner/name --owner owner-login
python -m autopilot roles grant github:owner/name reviewer-login approver
```

The `--owner` login becomes the repository owner automatically. Only owners can
delete stored data or change destructive settings.

4. Set `AUTOPILOT_GITHUB_TOKEN` only if you want the process to read repository
   data or post approved comments. Without it, the fixture adapter is used and no
   network call is made.

## Environment

```bash
AUTOPILOT_HOME=/srv/autopilot
AUTOPILOT_DB=/srv/autopilot/var/autopilot.db
AUTOPILOT_WEBHOOK_SECRET=<32-byte random hex>
AUTOPILOT_HOST=127.0.0.1
AUTOPILOT_PORT=8000
AUTOPILOT_RETENTION_DAYS=14
```

Keep the process bound to loopback and put a TLS-terminating reverse proxy in
front of it. If the proxy is on another host, set `AUTOPILOT_ALLOW_REMOTE=1` and
`AUTOPILOT_ADMIN_TOKEN=<long random value>`; the API then requires
`Authorization: Bearer <token>` for every non-webhook request.

## Reverse proxy

The proxy must:

- terminate TLS;
- forward `POST /webhooks/github` unchanged (the signature covers the raw body);
- cap the request body at or below `AUTOPILOT_MAX_BODY_BYTES` (default 1 MiB);
- not buffer and rewrite the body;
- set `X-Forwarded-For` only if you also set `AUTOPILOT_TRUST_PROXY=1`.

Example Caddy site:

```caddyfile
autopilot.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Example nginx location:

```nginx
location / {
    client_max_body_size 1m;
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
}
```

## systemd unit

```ini
[Unit]
Description=OSS Maintainer Autopilot
After=network-online.target

[Service]
Type=simple
User=autopilot
WorkingDirectory=/srv/autopilot
EnvironmentFile=/etc/autopilot/env
ExecStart=/srv/autopilot/.venv/bin/python -m autopilot serve
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/srv/autopilot/var

[Install]
WantedBy=multi-user.target
```

`EnvironmentFile` holds the webhook secret, the optional GitHub token, and any
provider settings. Keep it mode `0600` and owned by the service user.

## Docker

There is no published image. If you build one, keep the same rules:

- run as a non-root user;
- mount `/srv/autopilot/var` as a volume;
- pass secrets as environment variables or a secret file, never in the image;
- expose only the HTTP port, and only to the proxy network.

## Backup and restore

See `docs/operations.md`. In short: use `sqlite3.Connection.backup`, keep backups
under the same retention window, and verify the audit chain after restoring.

## Data location

Everything is in one SQLite file plus its `-wal`/`-shm` companions. Deleting the
directory removes all content and the audit chain. To remove one repository's
data while keeping the chain, use `autopilot repo delete <id> --confirm` or
`DELETE /api/repositories/<id>` with `{"confirm": true}` as an owner.

## Cost

The default configuration costs nothing: the fake provider and fixture adapter
make no network calls. A live OpenAI-compatible provider requires all of:

- `AUTOPILOT_PROVIDER=openai_compatible`
- `AUTOPILOT_ALLOW_LIVE_PROVIDER=1`
- `AUTOPILOT_OPENAI_BASE_URL` and `AUTOPILOT_OPENAI_API_KEY`
- positive `AUTOPILOT_DAILY_COST_LIMIT_USD` and `AUTOPILOT_MONTHLY_COST_LIMIT_USD`

Leaving any of them unset fails startup instead of silently calling a paid API.
