# Security Policy

TractorBeeam365 MCP talks directly to a Veeam Backup for Microsoft 365 (VB365)
server and, when explicitly enabled, can run jobs and execute restores including
overwrite-to-original of production mailboxes, sites, and Teams. Please treat it
accordingly.

## Reporting a vulnerability

Do not open a public issue for security problems. Report privately via GitHub
Security Advisories (the "Report a vulnerability" button on the Security tab),
or by email to the maintainer. Please include the affected version, the
transport (stdio or HTTP), and reproduction steps. When you send logs, redact
`VB365_HOST`, credentials, organization names, mailbox addresses, and audit-log
contents.

## Security model

- **Read-only by default.** With no `TB_*` flags set, only GET-based review
  tools are registered. The action and restore tools physically do not exist in
  that process.
- **Two-call confirm.** Every mutating tool requires a first call that returns a
  one-time, payload-bound confirm token, then a second call carrying that token.
- **Append-only audit log.** Every executed action is written to `TB_AUDIT_LOG`.
  Credentials are never written to the audit log or echoed in previews.

## Deploying safely

- **Prefer stdio.** The local stdio transport is the safe default and has no
  network surface.
- **HTTP transport is sensitive.** The MCP HTTP transport has no per-caller
  identity. This build fails closed:
  - `MCP_HOST` defaults to `127.0.0.1` (loopback). Binding to other interfaces
    is a deliberate choice.
  - The HTTP transport refuses to start unless `MCP_AUTH_TOKEN` is set (clients
    send `Authorization: Bearer <token>`), or you set
    `MCP_ALLOW_UNAUTHENTICATED_HTTP=true` to accept the risk.
  - The action and restore tools are not registered over an HTTP transport
    unless `TB_ALLOW_WRITE_OVER_HTTP=true` is set.
  - Even so, keep the port on a trusted network and front it with TLS and
    authentication (a reverse proxy).
- **TLS to VB365.** `VB365_VERIFY_SSL` defaults to `false` because VB365 ships a
  self-signed certificate, which leaves the link to VB365 open to an active
  man-in-the-middle. Prefer setting `VB365_CA_BUNDLE` to the server's
  certificate (or `VB365_VERIFY_SSL=true` with a trusted cert). The server prints
  a warning at startup whenever the channel is unverified.
- **Least privilege.** Use a dedicated VB365 account (see
  `New-VB365ReviewAccount.ps1`). Run a separate read-only instance for review and
  a tightly controlled operator instance only when you need writes.

## Supported versions

Security fixes target the latest release on the default branch.
