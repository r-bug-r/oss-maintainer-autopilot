# Security Policy

## Supported versions

The project is at `0.1.x`. Security fixes land on `main` and are released as a
patch version. There are no maintained older branches.

## Reporting a vulnerability

Report privately through the repository's GitHub Security Advisory tab
("Report a vulnerability"), or by email to the maintainers listed in
`GOVERNANCE.md` if advisories are unavailable. Do not open a public issue.

Include:

- the version or commit,
- the configuration (provider, adapter, bind address, auth settings),
- a minimal reproduction,
- the impact you believe it has.

You will get an acknowledgement within seven days. Please allow time for a fix
before publishing details.

## Security model in one page

Autopilot is a local-first service. Its security posture depends on four
controls; a report that breaks any of them is in scope.

1. **Webhook authenticity.** Deliveries must carry a valid
   `X-Hub-Signature-256` HMAC over the raw body, using the configured secret.
   The body size is checked first, the signature before parsing, and delivery
   IDs are deduplicated. Invalid signatures are rejected with `401` and never
   parsed into domain objects.
2. **Human approval.** A proposal can only be executed from `approved` state,
   and only by an actor holding `owner`, `maintainer`, or `approver` for that
   repository. Approval by an actor without a role is rejected. Execution is
   claimed with a conditional `UPDATE`, so two concurrent executions cannot both
   post.
3. **Local trust boundary.** Loopback requests are trusted. A non-loopback bind
   requires `AUTOPILOT_ALLOW_REMOTE=true`, `AUTOPILOT_ADMIN_TOKEN`, and a bearer
   token; `X-Forwarded-For` is ignored unless `AUTOPILOT_TRUST_PROXY=true`.
4. **No implicit spend or authority.** The default provider is the deterministic
   fake; a live provider requires an explicit opt-in, an API key, and a positive
   spend cap. The default adapter writes to SQLite; a GitHub token is required
   for real writes.

## Known limitations (accepted for 0.1)

- The local single-user mode treats loopback as an authenticated operator. Do not
  bind to a shared host without the admin token.
- The audit chain detects modification of stored rows; it does not prevent an
  operator with write access to the database file from deleting the newest rows.
  Export or mirror the chain if you need tamper evidence against the host owner.
- GitLab support is a declared boundary, not an implementation. Selecting it
  raises rather than degrading.
- Prompt injection is mitigated by framing, closed output schemas, and
  approval-gated actions; it is not "solved". A model can still produce
  misleading draft text, which is why a human must approve it.

## Dependency and secret hygiene

- Runtime has no third-party dependencies.
- CI runs a dependency review and a secret scan (see `.github/workflows/security.yml`).
- Never commit `.env` files, tokens, database files, or fixture captures that
  contain real repository content.
