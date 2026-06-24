# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project uses semantic
versioning.

## [2.1.2] - 2026-06-23

### Added
- Richer published metadata: `title` and `websiteUrl` in the MCP Registry entry,
  standard `org.opencontainers.image.*` labels on the container image, and the
  README synced to the Docker Hub repository overview on each release.

## [2.1.1] - 2026-06-23

### Added
- Published to the official MCP Registry as `io.github.Ringosystems/tractorbeeam-mcp`
  (OCI package on GHCR), with `server.json` and an OIDC-based publish workflow.

### Changed
- The container image now defaults to the **stdio** transport so MCP clients can
  run it directly (`docker run -i ...`); `docker-compose` still sets
  `streamable-http` for the persistent HTTP service.
- Added the `io.modelcontextprotocol.server.name` image label (registry ownership
  verification).

## [2.1.0] - 2026-06-23

> **Breaking (HTTP transport only):** the HTTP transport now binds `127.0.0.1` by
> default and refuses to start without `MCP_AUTH_TOKEN` (or an explicit
> `MCP_ALLOW_UNAUTHENTICATED_HTTP=true`), and it no longer registers the
> write/restore tools unless `TB_ALLOW_WRITE_OVER_HTTP=true`. The default stdio
> transport is unaffected.

### Security
- `vb365_get` and the internal client no longer accept absolute URLs. Resource
  paths are resolved strictly under the configured VB365 host, closing an
  SSRF / bearer-token-exfiltration vector.
- HTTP transport fails closed: `MCP_HOST` now defaults to `127.0.0.1`, the HTTP
  transport refuses to start without `MCP_AUTH_TOKEN` (or an explicit
  `MCP_ALLOW_UNAUTHENTICATED_HTTP=true` override), and the action/restore tools
  are not registered over HTTP unless `TB_ALLOW_WRITE_OVER_HTTP=true`.
- Restore: a workload with no distinct alternate-location endpoint (Teams) now
  requires the overwrite-original acknowledgement for `target="alternate"`,
  and the preview reports the real blast radius.
- Restore export filenames are sanitized and contained to the download
  directory, and the audit record is bound to the session captured at define
  time.
- Added `VB365_CA_BUNDLE` so the VB365 certificate can be trusted instead of
  disabling verification, plus a startup warning when the channel is unverified.
- Upstream error bodies are scanned for known secret values before being
  returned, and Microsoft Graph token-endpoint failures no longer surface the
  Azure AD diagnostic body.
- Cloud object-lock audit now flags buckets it could not verify and compares
  retention period and mode (Compliance vs Governance), instead of reporting
  "no mismatch" by default.

### Changed
- RPO compliance is judged only against the last successful run.
- Replaced a hardcoded local `.env` path with one derived from the project root.

### Added
- `LICENSE` (MIT), `SECURITY.md`, `CONTRIBUTING.md`, and this changelog.
- Trademark and independence notices for Microsoft, Backblaze B2, AWS, and other
  referenced providers.

## [2.0.1]
- Surface the package version in the startup banner.

## [2.0.0]
- Rebrand to TractorBeeam365 MCP. Added gap-analysis, trend/anomaly analysis,
  cloud object-lock cross-check, and the opt-in, gated action and restore tiers
  on top of the original read-only reviewer.
