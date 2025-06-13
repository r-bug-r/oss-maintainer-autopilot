# Threat Model

Scope: the Autopilot process, its SQLite database, its webhook endpoint, its HTTP
API, and the credentials it holds. It does not model GitHub itself, the host
operating system, or a malicious model provider beyond the data it receives.

## Assets

| Asset | Why it matters |
| --- | --- |
| Forge installation token / GitHub token | Can write to a repository if stolen |
| Webhook secret | Forges trusted event injection if stolen |
| Admin token (remote mode) | Full API access if stolen |
| Issue and pull request content | May contain reporter PII or leaked secrets |
| Proposal and audit records | The only evidence of who approved what |
| Model spend | Unbounded spend is a denial-of-wallet attack |

## Trust boundaries and adversaries

1. **Untrusted internet → webhook.** Anyone can POST to `/webhooks/github`.
2. **Untrusted repository content → model.** Issue and PR text is attacker
   controlled, including text designed to look like instructions.
3. **Model output → business logic.** A model can emit malformed, unknown, or
   manipulative output.
4. **HTTP client → API.** A browser on the same host, or a remote client if
   remote mode is enabled.
5. **Operator → CLI.** An operator with shell access to the host.
6. **Host → database file.** Anyone with filesystem write access.

