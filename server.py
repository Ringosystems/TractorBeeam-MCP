#!/usr/bin/env python3
"""TractorBeeam365 MCP — server entrypoint and tool wiring.

An independent MCP server for Veeam Backup for Microsoft 365 (VB365). NOT
affiliated with or endorsed by Veeam Software; "Veeam" is a registered trademark
of Veeam Software.

Read-only review tools are ALWAYS available and cannot change anything. The
optional action + restore tools are registered ONLY when their TB_* env flags are
set, are confirm-token gated, and are written to an append-only audit log. See
README.md and .env.example.
"""
from __future__ import annotations

import os
import secrets as _secrets
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from tractorbeeam365 import vb365, analysis, graph, cloud, actions, restore, __version__

# Shared read-only client surface (delegates to the package).
_get = vb365.get
_items = vb365.items
_first = vb365.first
_gib = vb365.gib
_summarize_cloud = vb365.summarize_cloud
_looks_object_backed = vb365.looks_object_backed
BASE_URL = vb365.BASE_URL

# --- MCP transport config ----------------------------------------------------
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
# Default to loopback so exposing the network is a deliberate opt-in, not the default.
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1").strip()
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))

_HTTP_TRANSPORTS = ("http", "streamable-http", "streamablehttp", "streamable_http", "sse")
IS_HTTP_TRANSPORT = MCP_TRANSPORT in _HTTP_TRANSPORTS


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


# The HTTP transport has no per-caller identity. Mutating tools are refused over
# it unless the operator explicitly accepts that risk, turning the README's
# "don't expose the write tier" guidance into an enforced guard.
ALLOW_WRITE_OVER_HTTP = _truthy("TB_ALLOW_WRITE_OVER_HTTP")

mcp = FastMCP("tractorbeeam365", host=MCP_HOST, port=MCP_PORT)


# =====================================================================
# READ-ONLY TOOLS (always registered; every one is a plain GET)
# =====================================================================
@mcp.tool()
def vb365_api_root() -> Any:
    """List the available VB365 REST resources + version (GET /<version>/). Good first call to confirm connectivity and discover endpoint names for this VB365 version."""
    return _get("")


@mcp.tool()
def vb365_list_organizations(limit: int = 200) -> Any:
    """List the backed-up Microsoft 365 organizations (tenants)."""
    return _get("Organizations", {"limit": limit})


@mcp.tool()
def vb365_list_jobs(limit: int = 500) -> Any:
    """List backup jobs, including schedule policy and last status."""
    return _get("Jobs", {"limit": limit})


@mcp.tool()
def vb365_get_job(job_id: str) -> Any:
    """Full details for one backup job (schedule policy, scope, repository, last/next run)."""
    return _get(f"Jobs/{job_id}")


@mcp.tool()
def vb365_job_scope(job_id: str) -> Any:
    """The selected + excluded items of a backup job (which users/groups/sites/teams it protects, and per-user mailbox/OneDrive/site facets). Read-only."""
    sel = _items(_get(f"Jobs/{job_id}/SelectedItems"))
    exc = _items(_get(f"Jobs/{job_id}/ExcludedItems"))

    def norm(rows: list) -> list:
        out = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            u = r.get("user") if isinstance(r.get("user"), dict) else {}
            out.append({"type": r.get("type"),
                        "name": _first(u, "name", "displayName", "mail", "email") or _first(r, "name"),
                        "mailbox": r.get("mailbox"), "archiveMailbox": r.get("archiveMailbox"),
                        "oneDrive": r.get("oneDrive"), "personalSite": r.get("personalSite")})
        return out

    return {"job_id": job_id, "selected": norm(sel), "excluded": norm(exc),
            "selected_count": len(sel), "excluded_count": len(exc)}


@mcp.tool()
def vb365_list_job_sessions(job_id: str = "", limit: int = 100) -> Any:
    """Recent job sessions / run results. Optionally filter to one job_id."""
    params: dict[str, Any] = {"limit": limit}
    if job_id:
        params["jobId"] = job_id
    return _get("JobSessions", params)


@mcp.tool()
def vb365_list_repositories() -> Any:
    """List backup repositories (capacity, free space, retention, immutability)."""
    return _get("BackupRepositories")


@mcp.tool()
def vb365_list_restore_points(limit: int = 200) -> Any:
    """List backup restore points (snapshots): time, job/org, workloads present, immutability-expiry, copy/archive flags. Read-only — feeds restore + trends."""
    return _get("RestorePoints", {"limit": limit})


@mcp.tool()
def vb365_org_inventory(org_id: str, kind: str = "users") -> Any:
    """VB365's view of an organization's objects. kind = users | groups | sites | teams. Read-only."""
    paths = {"users": "Users", "groups": "Groups", "sites": "Sites", "teams": "Teams"}
    k = kind.lower().strip()
    if k not in paths:
        return {"error": f"kind must be one of {sorted(paths)}"}
    return _get(f"Organizations/{org_id}/{paths[k]}", {"limit": 999})


@mcp.tool()
def vb365_list_proxies() -> Any:
    """List backup proxies and their status."""
    return _get("Proxies")


@mcp.tool()
def vb365_list_object_storage(limit: int = 200) -> Any:
    """List object storage (cloud) repositories — Amazon S3, S3 Compatible (e.g.
    Backblaze B2), Azure Blob, Wasabi, IBM Cloud, Glacier. Queries the dedicated
    ObjectStorageRepositories resource AND object-storage-backed backup repos (v8)."""
    out: dict[str, Any] = {"object_storage_repositories": [],
                           "object_backed_backup_repositories": [], "notes": []}
    osr = _get("ObjectStorageRepositories", {"limit": limit})
    if isinstance(osr, dict) and osr.get("error"):
        out["notes"].append(f"ObjectStorageRepositories: {osr['error']}")
    else:
        out["object_storage_repositories"] = _items(osr)
    reps = _get("BackupRepositories")
    if isinstance(reps, dict) and reps.get("error"):
        out["notes"].append(f"BackupRepositories: {reps['error']}")
    else:
        out["object_backed_backup_repositories"] = [
            r for r in _items(reps) if isinstance(r, dict) and _looks_object_backed(r)]
    return out


@mcp.tool()
def vb365_get_object_storage(repo_id: str) -> Any:
    """Full configuration for one object storage / cloud repository by id. Read-only."""
    res = _get(f"ObjectStorageRepositories/{repo_id}")
    if isinstance(res, dict) and res.get("error"):
        return _get(f"BackupRepositories/{repo_id}")
    return res


@mcp.tool()
def vb365_license() -> Any:
    """License information (edition, licensed users, expiry)."""
    return _get("License")


@mcp.tool()
def vb365_get(path: str, params: Optional[dict] = None) -> Any:
    """Read-only GET against ANY VB365 REST resource under the API version base.
    Examples: path='Jobs', path='Organizations/<id>/Users'. GET only — cannot
    modify anything. Use vb365_api_root if unsure of resource names."""
    return _get(path, params)


# --- analytics (read-only) ---------------------------------------------------
@mcp.tool()
def vb365_trends(days: int = 30, rpo_hours: float = 24.0) -> Any:
    """Read-only trend & anomaly analysis over job-session history: per-job
    success/failure drift (a job that 'succeeded' today but fails often), repo
    capacity-full projection, restore-test cadence per org, and RPO compliance.
    Surfaces issues a single latest-status check misses."""
    jobs = _items(_get("Jobs", {"limit": 500}))
    sessions = _items(_get("JobSessions", {"limit": 1000}))
    repos = _items(_get("BackupRepositories"))
    orgs = _items(_get("Organizations"))
    rsess = _items(_get("RestoreSessions", {"limit": 500}))
    health = analysis.trend_job_health(sessions, jobs, days=days)
    cap = analysis.trend_capacity_forecast(sessions, repos)
    cadence = analysis.trend_restore_cadence(rsess, orgs, days=max(days, 90))
    rpo = analysis.trend_rpo_compliance(jobs, sessions, rpo_hours=rpo_hours)
    flags = health["flags"] + cap["flags"] + cadence["flags"] + rpo["flags"]
    return {"window_days": days, "job_health": health["jobs"],
            "capacity_forecast": cap["repositories"], "restore_cadence": cadence["organizations"],
            "rpo_compliance": rpo["jobs"], "flags": flags or ["No trend anomalies detected."]}


@mcp.tool()
def cloud_lock_audit() -> Any:
    """Cross-check cloud immutability at the SOURCE: queries each Backblaze B2 / S3
    bucket VB365 uses (Object Lock mode, retention window, versioning) and flags
    any mismatch vs what VB365 reports. Needs B2_*/S3_* creds; read-only."""
    return cloud.lock_audit()


# --- M365 Graph gap-analysis (read-only) -------------------------------------
def _protected_facets() -> dict:
    """Walk every job's SelectedItems to learn which mailboxes/OneDrives are
    protected. Returns {'mailbox': set(emails), 'onedrive': set(emails),
    'whole_org': bool, 'groups': [...]}. Lower-cased emails."""
    prot = {"mailbox": set(), "onedrive": set(), "whole_org": False, "groups": []}
    for j in _items(_get("Jobs", {"limit": 500})):
        if not isinstance(j, dict):
            continue
        for it in _items(_get(f"Jobs/{j.get('id')}/SelectedItems")):
            if not isinstance(it, dict):
                continue
            t = str(it.get("type", "")).lower()
            if t in ("organization", "allusers", "alldata"):
                prot["whole_org"] = True
                continue
            if t == "group":
                u = it.get("group") if isinstance(it.get("group"), dict) else {}
                prot["groups"].append(_first(u, "name", "displayName", "mail"))
                continue
            u = it.get("user") if isinstance(it.get("user"), dict) else {}
            email = (_first(u, "mail", "email", "name", "userPrincipalName") or "").lower()
            if not email:
                continue
            if it.get("mailbox"):
                prot["mailbox"].add(email)
            if it.get("oneDrive"):
                prot["onedrive"].add(email)
    return prot


@mcp.tool()
def gap_coverage_summary() -> Any:
    """Backup COVERAGE % per workload by comparing LIVE Microsoft 365 (Graph)
    against what VB365 actually protects. Reveals licensed users/sites/teams that
    are NOT backed up. Read-only; needs GRAPH_* creds."""
    gerr = graph.config_error()
    if gerr:
        return {"error": gerr}
    prot = _protected_facets()
    live_mb = graph.live_mailboxes()
    if isinstance(live_mb, dict):
        return live_mb
    live_emails = [m.get("mail") for m in live_mb if m.get("mail")]
    if prot["whole_org"]:
        mb_cov = {"coverage_pct": 100.0, "live": len(live_emails), "unprotected": [],
                  "note": "a job protects the whole organization"}
    else:
        mb_cov = analysis.coverage(live_emails, prot["mailbox"])
        od_cov = analysis.coverage(live_emails, prot["onedrive"])
        mb_cov["onedrive_coverage_pct"] = od_cov["coverage_pct"]
    lic = _get("License")
    flags = []
    if isinstance(mb_cov, dict) and mb_cov.get("unprotected_count"):
        flags.append(f"{mb_cov['unprotected_count']} live mailbox(es) are NOT backed up")
    return {"mailbox_coverage": mb_cov,
            "license": {k: lic.get(k) for k in ("type", "totalNumber", "usedNumber")
                        if isinstance(lic, dict)},
            "protected_groups": [g for g in prot["groups"] if g],
            "flags": flags or ["All live mailboxes appear protected."]}


@mcp.tool()
def gap_unprotected_objects(workload: str = "mailbox") -> Any:
    """List LIVE Microsoft 365 objects that are NOT protected by any VB365 job.
    workload = mailbox | onedrive | sharepoint | teams. Read-only; needs GRAPH_*."""
    gerr = graph.config_error()
    if gerr:
        return {"error": gerr}
    workload = workload.lower().strip()
    prot = _protected_facets()
    if prot["whole_org"] and workload in ("mailbox", "onedrive"):
        return {"workload": workload, "unprotected": [],
                "note": "a job protects the whole organization — nothing unprotected"}
    if workload == "mailbox":
        live = graph.live_mailboxes()
        if isinstance(live, dict):
            return live
        cov = analysis.coverage([m.get("mail") for m in live if m.get("mail")], prot["mailbox"])
        return {"workload": "mailbox", **cov}
    if workload == "onedrive":
        live = graph.live_mailboxes()
        if isinstance(live, dict):
            return live
        cov = analysis.coverage([m.get("mail") for m in live if m.get("mail")], prot["onedrive"])
        return {"workload": "onedrive", **cov}
    if workload == "sharepoint":
        live = graph.live_sharepoint_sites()
        if isinstance(live, dict):
            return live
        return {"workload": "sharepoint", "live_sites": len(live), "sites": live,
                "note": "compare against vb365_org_inventory(org_id,'sites') for protected set"}
    if workload == "teams":
        live = graph.live_teams()
        if isinstance(live, dict):
            return live
        return {"workload": "teams", "live_teams": len(live), "teams": live,
                "note": "compare against vb365_org_inventory(org_id,'teams') for protected set"}
    return {"error": "workload must be mailbox | onedrive | sharepoint | teams"}


# =====================================================================
# ONE-SHOT HEALTH SUMMARY (read-only)
# =====================================================================
@mcp.tool()
def vb365_health_summary() -> Any:
    """One-shot READ-ONLY config/health review: organizations, jobs + last status,
    repositories (capacity / retention / immutability), cloud storage, proxies,
    license, trend anomalies, plus a 'flags' list of likely concerns. Every section
    is fetched read-only and isolated, so a partial failure still returns the rest."""
    out: dict[str, Any] = {"flags": [], "base_url": BASE_URL}
    cfg = vb365.config_error()
    if cfg:
        return {"error": cfg}

    try:
        orgs = _items(_get("Organizations"))
        out["organizations"] = {"count": len(orgs),
                                "names": [o.get("name") for o in orgs if isinstance(o, dict)]}
    except Exception as e:
        out["organizations"] = {"error": str(e)}

    try:
        jobs = _items(_get("Jobs"))
        by_status: dict[str, int] = {}
        flagged, never, disabled = [], [], []
        for j in jobs:
            if not isinstance(j, dict):
                continue
            st = j.get("lastStatus") or j.get("status") or "Unknown"
            by_status[st] = by_status.get(st, 0) + 1
            if str(st).lower() in ("failed", "warning"):
                flagged.append({"name": j.get("name"), "lastStatus": st})
            if not (j.get("lastRun") or j.get("lastRunTime")):
                never.append(j.get("name"))
            if j.get("isEnabled") is False:
                disabled.append(j.get("name"))
        out["jobs"] = {"total": len(jobs), "by_last_status": by_status,
                       "failed_or_warning": flagged, "never_run": never, "disabled": disabled}
        if flagged:
            out["flags"].append(f"{len(flagged)} job(s) last finished Failed/Warning")
        if disabled:
            out["flags"].append(f"{len(disabled)} job(s) are disabled")
        if never:
            out["flags"].append(f"{len(never)} job(s) have never run")
    except Exception as e:
        out["jobs"] = {"error": str(e)}

    try:
        repos = _items(_get("BackupRepositories"))
        rep = []
        for r in repos:
            if not isinstance(r, dict):
                continue
            cap = r.get("capacityBytes", r.get("capacity"))
            free = r.get("freeSpaceBytes", r.get("freeSpace"))
            used_pct = None
            try:
                if cap and free is not None and float(cap) > 0:
                    used_pct = round((1 - float(free) / float(cap)) * 100, 1)
            except Exception:
                pass
            imm = r.get("isImmutabilityEnabled")
            if imm is None:
                imm = r.get("immutabilityEnabled")
            rep.append({"name": r.get("name"), "used_pct": used_pct,
                        "retention": r.get("retentionPeriod", r.get("retentionType")),
                        "immutability": imm})
            if used_pct is not None and used_pct >= 85:
                out["flags"].append(f"Repository '{r.get('name')}' is {used_pct}% full")
            if imm is False:
                out["flags"].append(f"Repository '{r.get('name')}' has immutability disabled")
        out["repositories"] = rep
    except Exception as e:
        out["repositories"] = {"error": str(e)}

    try:
        proxies = _items(_get("Proxies"))
        plist = []
        for p in proxies:
            if not isinstance(p, dict):
                continue
            name = p.get("hostName") or p.get("name")
            status = p.get("status")
            plist.append({"name": name, "status": status})
            if status and str(status).lower() not in ("online", "healthy"):
                out["flags"].append(f"Proxy '{name}' status: {status}")
        out["proxies"] = plist
    except Exception as e:
        out["proxies"] = {"error": str(e)}

    # Object storage (cloud) — from both the dedicated resource and v8 object-backed repos.
    try:
        cloud_rows: list = []
        seen: set = set()
        sources_ok = 0
        candidates: list = []
        osr = _get("ObjectStorageRepositories")
        if not (isinstance(osr, dict) and osr.get("error")):
            sources_ok += 1
            candidates.extend(_items(osr))
        creps = _get("BackupRepositories")
        if not (isinstance(creps, dict) and creps.get("error")):
            sources_ok += 1
            candidates.extend(r for r in _items(creps)
                              if isinstance(r, dict) and _looks_object_backed(r))
        for repo in candidates:
            if not isinstance(repo, dict):
                continue
            key = _first(repo, "id", "name")
            if key in seen:
                continue
            seen.add(key)
            s = _summarize_cloud(repo)
            cloud_rows.append(s)
            label = s["name"] or s["provider"]
            if s["immutability"] is False:
                out["flags"].append(f"Cloud storage '{label}' ({s['provider']}) has immutability DISABLED")
            if s["immutability"] and not s["immutability_days"]:
                out["flags"].append(f"Cloud storage '{label}' has immutability enabled but a "
                                    f"{s['immutability_days']}-day period (no effective lock window)")
            if s["encryption"] is False:
                out["flags"].append(f"Cloud storage '{label}' ({s['provider']}) has encryption disabled")
            if s["used_pct"] is not None and s["used_pct"] >= 85:
                out["flags"].append(f"Cloud storage '{label}' is {s['used_pct']}% of its size limit")
        out["cloud_storage"] = cloud_rows
        if sources_ok == 0:
            out["flags"].append("Could not read object storage (cloud) configuration from the API.")
        elif not cloud_rows:
            out["flags"].append("No object storage (cloud) repositories found — confirm B2 / AWS S3 are configured.")
    except Exception as e:
        out["cloud_storage"] = {"error": str(e)}

    # Trend anomalies (read-only; folds top trend flags into the summary).
    try:
        sessions = _items(_get("JobSessions", {"limit": 500}))
        jobs2 = out.get("jobs") and _items(_get("Jobs")) or []
        th = analysis.trend_job_health(sessions, jobs2, days=30)
        cap = analysis.trend_capacity_forecast(sessions, _items(_get("BackupRepositories")))
        trend_flags = th["flags"] + cap["flags"]
        if trend_flags:
            out["flags"].extend(trend_flags)
        out["trend_summary"] = {"job_health": th["jobs"][:5], "capacity": cap["repositories"]}
    except Exception as e:
        out["trend_summary"] = {"error": str(e)}

    try:
        out["license"] = _get("License")
    except Exception as e:
        out["license"] = {"error": str(e)}

    if not out["flags"]:
        out["flags"].append("No obvious config/health concerns detected.")
    return out


# =====================================================================
# OPT-IN ACTION TOOLS (registered only when TB_* flags are set)
# =====================================================================
def _register_action_tools() -> list:
    registered = []

    # Refuse to expose mutating tools over the unauthenticated/per-caller-anonymous
    # HTTP transport unless the operator explicitly opts in.
    if IS_HTTP_TRANSPORT and not ALLOW_WRITE_OVER_HTTP:
        return registered

    if actions.category_enabled("job_control"):
        @mcp.tool()
        def action_job(job_id: str, op: str, confirm: str = "", full: bool = False) -> Any:
            """[WRITE — gated] Control a backup job. op = start | stop | enable | disable.
            Call once to get a preview + confirm_token, then again with confirm=<token>
            to execute. Audited. Requires TB_ALLOW_JOB_CONTROL."""
            return actions.job_action(job_id, op, confirm=confirm, full=full)
        registered.append("action_job")

    if actions.category_enabled("proxy"):
        @mcp.tool()
        def action_proxy(proxy_id: str, op: str, confirm: str = "") -> Any:
            """[WRITE — gated] op = rescan | maintenance_enable | maintenance_disable.
            Two-call confirm. Requires TB_ALLOW_PROXY."""
            return actions.proxy_action(proxy_id, op, confirm=confirm)
        registered.append("action_proxy")

    if actions.category_enabled("org_sync"):
        @mcp.tool()
        def action_org_sync(org_id: str, sync_type: str = "Incremental", confirm: str = "") -> Any:
            """[WRITE — gated] Trigger a directory resync for an organization.
            Two-call confirm. Requires TB_ALLOW_ORG_SYNC."""
            return actions.org_sync(org_id, sync_type, confirm=confirm)
        registered.append("action_org_sync")

    if actions.category_enabled("reports"):
        @mcp.tool()
        def action_generate_report(kind: str, organization_id: str = "", fmt: str = "PDF",
                                   confirm: str = "") -> Any:
            """[WRITE — gated, low risk] Generate a protection/license/storage report
            and save it to TB_DOWNLOAD_DIR. kind = mailbox_protection | onedrive_protection |
            sharepoint_protection | teams_protection | user_protection | license_overview |
            storage_consumption. Two-call confirm. Requires TB_ALLOW_REPORTS."""
            return actions.generate_report(kind, organization_id, fmt, confirm=confirm)
        registered.append("action_generate_report")

    return registered


# =====================================================================
# OPT-IN RESTORE TOOLS (registered only when TB_ALLOW_RESTORE is set)
# =====================================================================
def _register_restore_tools() -> list:
    if IS_HTTP_TRANSPORT and not ALLOW_WRITE_OVER_HTTP:
        return []
    if not actions.category_enabled("restore"):
        return []

    @mcp.tool()
    def restore_session_start(scope: str, scope_id: str, workload: str,
                              point_in_time: str = "", show_deleted: bool = False,
                              repository_id: str = "") -> Any:
        """[RESTORE — gated] Open a restore session. scope = job | organization;
        scope_id = that job/org id; workload = exchange | onedrive | sharepoint | teams;
        point_in_time = ISO datetime (omit for latest). Returns a session_id. Safe
        (read-only browse access). Requires TB_ALLOW_RESTORE."""
        return restore.session_start(scope, scope_id, workload, point_in_time,
                                     show_deleted, repository_id)

    @mcp.tool()
    def restore_browse(session_id: str, parent_id: str = "", limit: int = 200) -> Any:
        """[RESTORE — gated] List restorable objects in a session. No parent_id =
        top level (mailboxes/onedrives/sites/teams); pass an object's id as parent_id
        to drill into its items. Read-only."""
        return restore.browse(session_id, parent_id, limit)

    @mcp.tool()
    def restore_search(session_id: str, query: str, parent_id: str = "") -> Any:
        """[RESTORE — gated] Search restorable items in a session (onedrive/sharepoint
        need a parent_id). Read-only."""
        return restore.search(session_id, query, parent_id)

    @mcp.tool()
    def restore_define(session_id: str, parent_id: str, item_ids: list, target: str,
                       options: Optional[dict] = None,
                       confirm_overwrite_original: bool = False) -> Any:
        """[RESTORE — gated] Define a restore and get a preview + confirm_token (NO
        execution). parent_id = the mailbox/onedrive/site/team id; item_ids = items to
        restore; target = export (safest: file, no M365 creds) | alternate (different
        M365 location) | original (overwrites PRODUCTION — also pass
        confirm_overwrite_original=True). options may carry mailbox/folder + target
        M365 creds (userName/userPassword/applicationId)."""
        return restore.define(session_id, parent_id, item_ids, target, options,
                              confirm_overwrite_original)

    @mcp.tool()
    def restore_execute(session_id: str, confirm: str) -> Any:
        """[RESTORE — gated] Execute the restore previewed by restore_define, using its
        confirm_token. Audited. Writes to M365 (alternate/original) or a local file (export)."""
        return restore.execute(session_id, confirm)

    @mcp.tool()
    def restore_status(session_id: str) -> Any:
        """[RESTORE — gated] Restore session state, result, and statistics. Read-only."""
        return restore.status(session_id)

    @mcp.tool()
    def restore_session_stop(session_id: str) -> Any:
        """[RESTORE — gated] Close a restore session and free resources. Audited."""
        return restore.session_stop(session_id)

    return ["restore_session_start", "restore_browse", "restore_search", "restore_define",
            "restore_execute", "restore_status", "restore_session_stop"]


_ACTION_TOOLS = _register_action_tools()
_RESTORE_TOOLS = _register_restore_tools()


def _bearer_auth_app(app, token: str):
    """Minimal ASGI wrapper that requires `Authorization: Bearer <token>` on every
    HTTP request. The MCP HTTP transport has no built-in auth, so this is the
    fence in front of it."""
    expected = f"Bearer {token}"

    async def asgi(scope, receive, send):
        if scope.get("type") == "http":
            headers = dict(scope.get("headers") or [])
            provided = headers.get(b"authorization", b"").decode("latin-1")
            if not (provided and _secrets.compare_digest(provided, expected)):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await app(scope, receive, send)

    return asgi


if __name__ == "__main__":
    import sys
    mode = "READ-ONLY"
    if _ACTION_TOOLS or _RESTORE_TOOLS:
        mode = "OPERATOR (write tools enabled: " + ", ".join(_ACTION_TOOLS + _RESTORE_TOOLS) + ")"
    print(f"[tractorbeeam365 v{__version__}] starting - {mode}", file=sys.stderr)

    tls_warn = vb365.tls_warning()
    if tls_warn:
        print(f"[tractorbeeam365] WARNING: {tls_warn}", file=sys.stderr)

    if IS_HTTP_TRANSPORT:
        import uvicorn
        if actions.actions_enabled() and not ALLOW_WRITE_OVER_HTTP:
            print("[tractorbeeam365] NOTE: write/restore flags are set but the HTTP transport "
                  "anonymizes callers, so mutating tools are NOT registered. Set "
                  "TB_ALLOW_WRITE_OVER_HTTP=true to override (only behind your own auth proxy).",
                  file=sys.stderr)
        if MCP_HOST not in ("127.0.0.1", "localhost", "::1"):
            print(f"[tractorbeeam365] NOTE: HTTP transport is bound to {MCP_HOST} (network-reachable).",
                  file=sys.stderr)
        auth_token = os.getenv("MCP_AUTH_TOKEN", "").strip()
        if not auth_token and not _truthy("MCP_ALLOW_UNAUTHENTICATED_HTTP"):
            print("[tractorbeeam365] REFUSING TO START: the HTTP transport is unauthenticated. "
                  "Set MCP_AUTH_TOKEN=<secret> (clients send 'Authorization: Bearer <secret>'), "
                  "or set MCP_ALLOW_UNAUTHENTICATED_HTTP=true to accept the risk (LAN-only / behind "
                  "a reverse proxy that adds auth).", file=sys.stderr)
            sys.exit(2)
        app = mcp.sse_app() if MCP_TRANSPORT == "sse" else mcp.streamable_http_app()
        if auth_token:
            app = _bearer_auth_app(app, auth_token)
            print("[tractorbeeam365] HTTP transport requires a bearer token (MCP_AUTH_TOKEN).",
                  file=sys.stderr)
        uvicorn.run(app, host=MCP_HOST, port=MCP_PORT, log_level="info")
    else:
        mcp.run()  # stdio (default)
