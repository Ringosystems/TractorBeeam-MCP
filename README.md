# TractorBeeam365 MCP

A Model Context Protocol server for **Veeam Backup for Microsoft 365 (VB365)**.
It talks **directly** to the VB365 REST API so an MCP client (Claude Code / Claude
Desktop) can **review your backup configuration, find protection gaps, spot
trends, verify cloud immutability — and, when you explicitly opt in, run jobs and
orchestrate restores.**

> **Independent project — not affiliated with, endorsed by, or supported by Veeam
> Software.** "Veeam" is a registered trademark of Veeam Software; the "Beeam"
> spelling is a deliberate, distinct play on words. Use at your own risk.
>
> 🔱 The name: a *tractor beam* pulls your data back in — and *Beeam* winks at the
> backup product underneath. Read-only review is the safe default; the "pull"
> (restore/actions) is opt-in.

## Two tiers

| Tier | Default | What it can do |
|------|---------|----------------|
| **Read-only review** | ✅ always on | GET-only. Cannot change anything. Config/health, gap-analysis, trends, cloud-lock cross-check. |
| **Action / Restore** | ⛔ off | Opt-in via `TB_*` env flags. Run/stop jobs, generate reports, sync orgs, **define + execute restores**. Every write is confirm-token gated and audited. |

If you never set the `TB_*` flags, this is a pure read-only reviewer that
*physically cannot* modify your environment — the action/restore tools aren't even
registered.

## Quick deploy (prebuilt image)

No clone or build required. From v2.2.0 the image is published **multi-arch
(amd64 + arm64)** to GitHub Container Registry and Docker Hub, so it runs
natively on x86 servers, Apple Silicon, and ARM boxes (Raspberry Pi, ARM cloud).
On an ARM host, pin the `:2.2.0` (or later) tag until a multi-arch `:latest` has
been published.

### Persistent HTTP service (Docker Compose)

Grab two files and start it:

```bash
curl -fsSLO https://raw.githubusercontent.com/Ringosystems/TractorBeeam-MCP/main/docker-compose.deploy.yml
curl -fsSL  https://raw.githubusercontent.com/Ringosystems/TractorBeeam-MCP/main/.env.example -o .env
# Edit .env: set VB365_HOST / VB365_USERNAME / VB365_PASSWORD and a long MCP_AUTH_TOKEN.
docker compose -f docker-compose.deploy.yml up -d
```

MCP is then live at `http://<host>:8766/mcp`. Attach a client with the bearer
token you set:

```bash
claude mcp add --transport http tractorbeeam365 http://<host>:8766/mcp \
  --header "Authorization: Bearer <your MCP_AUTH_TOKEN>"
```

Update later:

```bash
docker compose -f docker-compose.deploy.yml pull && docker compose -f docker-compose.deploy.yml up -d
```

### Unraid (one click)

In Unraid open **Docker → Add Container**, and paste this template URL into the
**Template** field:

```text
https://raw.githubusercontent.com/Ringosystems/TractorBeeam-MCP/main/deploy/unraid/tractorbeeam365.xml
```

Fill in `VB365_HOST`, `VB365_USERNAME`, `VB365_PASSWORD`, and a long
`MCP_AUTH_TOKEN`, then Apply. The audit and downloads folders map under
`/mnt/user/appdata/tractorbeeam365/`.

### Single MCP client, no service (stdio)

For a client that launches the server itself, run the image on demand over stdio:

```bash
docker run -i --rm \
  -e VB365_HOST=... -e VB365_USERNAME=... -e VB365_PASSWORD=... \
  ghcr.io/ringosystems/tractorbeeam-mcp:latest
```

## Read-only tools (always available)

| Tool | What it returns |
|------|-----------------|
| `vb365_health_summary` | One-shot review: orgs, jobs + last status, repos, **cloud storage (B2/S3/Azure)**, proxies, license, **trend anomalies**, and a `flags` list. **Start here.** |
| `vb365_trends` | Per-job success/failure **drift**, repo **capacity-full projection**, **restore-test cadence** per org, **RPO compliance**. |
| `gap_coverage_summary` / `gap_unprotected_objects` | **Backup coverage %** and **unprotected** live M365 objects, by comparing Microsoft Graph against what jobs protect *(needs `GRAPH_*`)*. |
| `cloud_lock_audit` | Cross-checks **Object Lock** mode/retention/versioning at the **bucket** vs what VB365 reports *(needs `B2_*`/`S3_*`)*. |
| `vb365_api_root` | Resource list + version (confirms connectivity). |
| `vb365_list_organizations` / `vb365_org_inventory` | Protected orgs; an org's users/groups/sites/teams. |
| `vb365_list_jobs` / `vb365_get_job` / `vb365_job_scope` | Jobs, one job's config, and exactly what a job protects (selected/excluded). |
| `vb365_list_job_sessions` | Recent run results (optionally per job). |
| `vb365_list_repositories` / `vb365_list_restore_points` | Repos (capacity/retention/immutability); restore points. |
| `vb365_list_object_storage` / `vb365_get_object_storage` | Cloud/object storage (B2/S3/Azure/Wasabi/IBM/Glacier). |
| `vb365_list_proxies` / `vb365_license` | Proxies + status; license/edition/expiry. |
| `vb365_get` | Read-only GET to any other `/<version>/...` resource. |

## Action / Restore tools (opt-in, gated)

Registered only when the matching flag is set. **Two-call confirm pattern:** call
once to get a preview + a one-time `confirm_token`; call again with
`confirm=<token>` to execute. Every execution is appended to `TB_AUDIT_LOG`.

| Tool (flag) | What it does |
|-------------|--------------|
| `action_job` (`TB_ALLOW_JOB_CONTROL`) | start / stop / enable / disable a backup job |
| `action_generate_report` (`TB_ALLOW_REPORTS`) | mailbox/onedrive/sharepoint/teams/user protection, license, storage reports → file |
| `action_org_sync` (`TB_ALLOW_ORG_SYNC`) | trigger a directory resync for an org |
| `action_proxy` (`TB_ALLOW_PROXY`) | rescan / maintenance-mode a proxy |
| `restore_session_start` → `restore_browse` / `restore_search` → `restore_define` → `restore_execute` → `restore_status` → `restore_session_stop` (`TB_ALLOW_RESTORE`) | **Full restore orchestration** across Exchange / OneDrive / SharePoint / Teams: open a point-in-time session, browse/search restorable items, then **define** (pick items + target + options) and **execute** a restore. |

### Restore: define **what**, **where**, **how**

```text
restore_session_start(scope="job", scope_id="<jobId>", workload="exchange")
   → { session_id }                         # opens a point-in-time explore session
restore_browse(session_id)                  # list mailboxes
restore_browse(session_id, parent_id=<mailboxId>)   # list items in a mailbox
restore_define(session_id, parent_id=<mailboxId>, item_ids=[...], target="export")
   → { confirm_token, preview }             # DRY RUN — nothing changed
restore_execute(session_id, confirm=<confirm_token>)   # executes + audits
restore_session_stop(session_id)
```

- **target** = `export` (safest — reads backup to a local file; no M365 creds),
  `alternate` (writes to a *different* M365 location), or `original` (writes back
  to the **original** location — destructive; also pass `confirm_overwrite_original=True`).
- **options** may carry an alternate `mailbox`/`folder` and the M365 target
  credentials VB365 uses to write back (`userName`/`userPassword`/`applicationId`),
  or set `VB365_RESTORE_*` in `.env`.
- **PST export requires 64-bit Outlook 2010+ installed on the VB365 server**
  (a Veeam requirement); without it, `exportToPst` returns a server-side error.

## Setup

1. **Install Python 3.10+** where Claude Code runs.
2. From this folder:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in `VB365_HOST`, `VB365_USERNAME`,
   `VB365_PASSWORD` (keep `VB365_PORT=4443`, `VB365_API_VERSION=v8` unless yours
   differ). Optionally add `GRAPH_*`, `B2_*`/`S3_*`, and the `TB_*` flags.

   > The REST API account needs to be a VB365 administrator (or restore
   > operator) — VB365 has no read-only API role. The read-only **review** tools
   > are GET-only regardless. `New-VB365ReviewAccount.ps1` creates a dedicated
   > service account on the VB365 server. Keep `.env` out of source control.

## Register with Claude Code

```powershell
# Replace <repo> with the absolute path to your clone.
claude mcp add tractorbeeam365 --scope user -- "<repo>\.venv\Scripts\python.exe" "<repo>\server.py"
```

> On Linux/macOS use forward slashes and `<repo>/.venv/bin/python`.

It is also published to the [MCP Registry](https://registry.modelcontextprotocol.io)
as `io.github.Ringosystems/tractorbeeam-mcp` (an OCI image on GHCR), so MCP
clients that support the registry can install it directly. To run the published
image as a stdio server:

```bash
docker run -i --rm \
  -e VB365_HOST=... -e VB365_USERNAME=... -e VB365_PASSWORD=... \
  ghcr.io/ringosystems/tractorbeeam-mcp:latest
```

Then ask: **"Run a VB365 health summary and tell me if anything is misconfigured."**

## Enabling the action / restore tier

The action/restore tools stay hidden until you opt in. In `.env`:

```ini
TB_ENABLE_ACTIONS=true        # master switch
TB_ALLOW_JOB_CONTROL=true     # + any of the per-category flags you want
TB_ALLOW_RESTORE=true
TB_AUDIT_LOG=audit/actions.jsonl
```

> The action/restore tools are **not** registered over an HTTP transport unless
> you also set `TB_ALLOW_WRITE_OVER_HTTP=true`, because that transport has no
> per-caller identity. Run the operator tier over stdio, or only behind your own
> authenticating reverse proxy.

Safety model: tools are **only registered when enabled**, every mutation needs a
**second call with a one-time confirm token**, restores **default to a safe
target** (original-location overwrite needs an explicit acknowledgement), and
**every action is written to an append-only audit log**. Recommended: run a
separate "operator" instance with the flags on, and the default instance
read-only.

## Run as a persistent service (Docker / UNRAID)

The image itself defaults to the stdio transport (so MCP clients can `docker run
-i` it). Via `docker compose` it runs `streamable-http` on `0.0.0.0:8000` inside
the container, **read-only** (no `TB_*` flags). No credentials are baked in. The
HTTP transport **fails closed**: it refuses to start unless you set
`MCP_AUTH_TOKEN` (clients then send `Authorization: Bearer <token>`), or you
explicitly accept the risk with `MCP_ALLOW_UNAUTHENTICATED_HTTP=true`.

```bash
# Put MCP_AUTH_TOKEN=<a long random secret> in .env first.
docker compose up -d --build          # builds locally and runs read-only
# MCP is live at http://<host>:8766/mcp ; attach with:
claude mcp add --transport http tractorbeeam365 http://<host>:8766/mcp \
  --header "Authorization: Bearer <token>"
```

### Prebuilt images

Each GitHub Release publishes a multi-arch, multi-tag image via CI
([.github/workflows/publish.yml](.github/workflows/publish.yml)) to GHCR, Docker
Hub, and the MCP Registry. The full release runbook is in
[PUBLISHING.md](PUBLISHING.md).

```bash
# GitHub Container Registry (published automatically on every release):
docker pull ghcr.io/ringosystems/tractorbeeam-mcp:latest
# Docker Hub (published when the DOCKERHUB_USERNAME / DOCKERHUB_TOKEN repo
# secrets are configured):
docker pull ringosystems/tractorbeeam365-mcp:latest
```

To publish manually instead:

```bash
docker build -t <dockerhubuser>/tractorbeeam365-mcp:latest .
docker push <dockerhubuser>/tractorbeeam365-mcp:latest
```

> **Security:** the MCP HTTP transport has no per-caller identity. This build
> binds loopback by default, requires `MCP_AUTH_TOKEN` before it will serve HTTP,
> and hides the write/restore tools over HTTP unless `TB_ALLOW_WRITE_OVER_HTTP=true`.
> Still keep the port on a trusted network and front it with TLS + auth. The audit
> log and any generated PST/report files live in the mounted `audit/` and
> `downloads/` volumes. See [SECURITY.md](SECURITY.md).

## Quick connectivity test (no MCP client needed)

```powershell
.\.venv\Scripts\Activate.ps1
python -c "import server; print(server.vb365_api_root())"
```

## Notes

- `.gitignore` excludes `.env`, the venv, and the operator-tier `audit/`,
  `downloads/`, `*.pst` artifacts. Never commit credentials or restored data.
- VB365 ships a self-signed cert, so `VB365_VERIFY_SSL` defaults to `false`. That
  leaves the link to VB365 open to an active man-in-the-middle, so prefer setting
  `VB365_CA_BUNDLE` to the server's certificate (verification on against it), or
  `VB365_VERIFY_SSL=true` with a trusted cert. The server warns at startup when
  the channel is unverified.
- Endpoint names vary slightly by VB365 version. If a curated tool 404s, call
  `vb365_api_root` and use `vb365_get` with the exact resource name.

## Trademarks

This is an independent project and is not affiliated with, endorsed by, or
sponsored by any of the companies below. Product names are used only for
identification (nominative use).

- "Veeam" is a registered trademark of Veeam Software. The "Beeam" spelling is a
  deliberate, distinct wordplay and is not the trademark.
- Microsoft, Microsoft 365, Microsoft Graph, Outlook, and the Office Deployment
  Tool are trademarks of the Microsoft group of companies.
- Backblaze and B2 are trademarks of Backblaze, Inc.
- Amazon Web Services, AWS, Amazon S3, and S3 Glacier are trademarks of
  Amazon.com, Inc. or its affiliates.
- Wasabi, IBM and IBM Cloud, Azure, and Google Cloud are trademarks of their
  respective owners.

## License

Released under the [MIT License](LICENSE). See [SECURITY.md](SECURITY.md) for the
security model and reporting, [CONTRIBUTING.md](CONTRIBUTING.md) to contribute, and
[PUBLISHING.md](PUBLISHING.md) for how releases are built and published.
