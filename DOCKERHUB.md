# TractorBeeam365 MCP

An independent **Model Context Protocol (MCP)** server for **Veeam Backup for Microsoft 365 (VB365)**. It talks directly to the VB365 REST API so an MCP client (Claude Code, Claude Desktop) can review your backup configuration, find protection gaps, spot trends, and verify cloud immutability. When you explicitly opt in, it can also run jobs and orchestrate restores.

> Independent project. Not affiliated with, endorsed by, or supported by Veeam Software or Microsoft. "Veeam" is a registered trademark of Veeam Software; the "Beeam" spelling is a deliberate, distinct play on words.

- **Source and full docs:** https://github.com/Ringosystems/TractorBeeam-MCP
- **Security model:** https://github.com/Ringosystems/TractorBeeam-MCP/blob/main/SECURITY.md
- **MCP Registry:** `io.github.Ringosystems/tractorbeeam-mcp`
- **License:** MIT

## Tags

- `latest` and the exact version (e.g. `2.2.0`), Alpine based (minimal CVE surface), multi-arch (linux/amd64 + linux/arm64).

Also published to GitHub Container Registry at `ghcr.io/ringosystems/tractorbeeam-mcp`.

## Two tiers

| Tier | Default | What it can do |
|------|---------|----------------|
| Read-only review | on | GET only. Config and health, gap analysis, trends, cloud-lock cross-check. Cannot change anything. |
| Action / Restore | off | Opt-in via `TB_*` env flags. Run and stop jobs, generate reports, sync orgs, define and execute restores. Every write is confirm-token gated and audited. |

If you never set the `TB_*` flags, this is a pure read-only reviewer.

## Run it (stdio, for an MCP client)

The image defaults to the stdio transport, so an MCP client can launch it directly:

```bash
docker run -i --rm \
  -e VB365_HOST=192.168.1.10 \
  -e VB365_USERNAME='.\svc-vb365-review' \
  -e VB365_PASSWORD='your-password' \
  ringosystems/tractorbeeam365-mcp:latest
```

## Run it (persistent HTTP service)

Set `MCP_TRANSPORT=streamable-http`. The HTTP transport has no per-caller identity, so it fails closed: it will not start without `MCP_AUTH_TOKEN` (clients then send `Authorization: Bearer <token>`), and the write/restore tools are not exposed over HTTP unless `TB_ALLOW_WRITE_OVER_HTTP=true`.

```bash
docker run -d --name tractorbeeam365-mcp -p 8766:8000 \
  -e MCP_TRANSPORT=streamable-http \
  -e MCP_AUTH_TOKEN='a-long-random-secret' \
  -e VB365_HOST=192.168.1.10 \
  -e VB365_USERNAME='.\svc-vb365-review' \
  -e VB365_PASSWORD='your-password' \
  ringosystems/tractorbeeam365-mcp:latest
```

## Configuration

Required (read-only review works with just these):

| Variable | Description |
|----------|-------------|
| `VB365_HOST` | IP or hostname of the VB365 (Veeam Backup Server) host |
| `VB365_USERNAME` | VB365 account for the REST API (`DOMAIN\user`, `.\localuser`, or `user@domain`) |
| `VB365_PASSWORD` | Password for that account |

Common optional settings: `VB365_PORT` (default 4443), `VB365_API_VERSION` (default v8), `VB365_VERIFY_SSL` (default false; prefer `VB365_CA_BUNDLE` to trust the VB365 certificate), `MCP_TRANSPORT`, `MCP_HOST`, `MCP_PORT`, `MCP_AUTH_TOKEN`. Optional feature tiers add `GRAPH_*` (Microsoft 365 gap analysis), `B2_*` / `S3_*` (cloud object-lock cross-check), and the `TB_*` action/restore flags. See the full README for the complete list.

## Security notes

- TLS to VB365 is unverified by default because VB365 ships a self-signed certificate. Prefer setting `VB365_CA_BUNDLE` to the server certificate, or `VB365_VERIFY_SSL=true` with a trusted certificate.
- Keep the HTTP transport on a trusted network and front it with TLS and authentication. See [SECURITY.md](https://github.com/Ringosystems/TractorBeeam-MCP/blob/main/SECURITY.md).
- No credentials are baked into the image; everything is supplied at runtime.

## More

Full setup, the complete tool list, and the restore workflow are documented in the [README](https://github.com/Ringosystems/TractorBeeam-MCP/blob/main/README.md).
