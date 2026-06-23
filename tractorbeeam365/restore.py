"""Restore orchestration: define AND execute granular restores.

Flow (mirrors the VB365 v8 REST 'explore' restore model):
  session_start  -> POST Jobs/{id}/explore or Organizations/{id}/explore  (opens a
                    workload-typed RestoreSession; read-only browse access)
  browse/search  -> GET/POST within the session to find restorable items
  define         -> build a restore spec, return a preview + one-time confirm token
                    (NO mutation; defaults to the safest target; original-location
                    overwrite needs an explicit acknowledgement)
  execute        -> POST the chosen restore/restoreTo/export with the confirm token
  status/stop    -> track progress, then close the session

Every define/execute is gated by TB_ALLOW_RESTORE, confirm-token-bound, and
audited. Exchange bodies are modelled exactly from the OpenAPI spec; OneDrive /
SharePoint / Teams use the same verified path map with a best-effort item body.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

from . import vb365
from . import actions as A
from . import endpoints as ep

_TYPE_TO_WORKLOAD = {v: k for k, v in ep.WORKLOAD_TO_EXPLORE_TYPE.items()}


def _safe_token(value: Any, n: int = 12) -> str:
    """Filename-safe slug from an id (no path separators / traversal)."""
    return re.sub(r"[^A-Za-z0-9]", "", str(value))[:n] or "session"


def _need_restore() -> Optional[dict]:
    return None if A.category_enabled("restore") else A.disabled_note("restore")


def _session_workload(session_id: str) -> Optional[str]:
    s = vb365.get(ep.RESTORE_SESSION.format(rs_id=session_id))
    if isinstance(s, dict) and s.get("error"):
        return None
    t = vb365.first(s, "type")
    return _TYPE_TO_WORKLOAD.get(t)


_WORKLOAD_RP_FLAG = {"exchange": "isExchange", "onedrive": "isOneDrive",
                     "sharepoint": "isSharePoint", "teams": "isTeams"}


def _latest_restore_point_time(scope: str, scope_id: str, workload: str) -> Optional[str]:
    """Most-recent restore-point backupTime matching the scope + workload, used as
    the mandatory explore 'dateTime' when the caller doesn't pass point_in_time."""
    rps = vb365.items(vb365.get("RestorePoints", {"limit": 500}))
    flag = _WORKLOAD_RP_FLAG.get(workload)
    best = None
    for rp in rps:
        if not isinstance(rp, dict):
            continue
        if scope == "job" and str(rp.get("jobId")) != str(scope_id):
            continue
        if scope == "organization" and str(rp.get("organizationId")) != str(scope_id):
            continue
        if flag and not rp.get(flag):
            continue
        bt = rp.get("backupTime")
        if bt and (best is None or bt > best):
            best = bt
    return best


def _target_creds(options: dict) -> dict:
    """M365 credentials VB365 uses to WRITE during a restore-to-production. Taken
    from the restore options, falling back to VB365_RESTORE_* env. Omitted for
    export (which reads from backup and needs no M365 creds)."""
    creds: dict[str, Any] = {}
    user = options.get("userName") or vb365.VB365_RESTORE_USERNAME
    pw = options.get("userPassword") or vb365.VB365_RESTORE_PASSWORD
    app = options.get("applicationId") or vb365.VB365_RESTORE_APP_ID
    if user:
        creds["userName"] = user
    if pw:
        creds["userPassword"] = pw
    if app:
        creds["applicationId"] = app
    return creds


# ---------------------------------------------------------------------------
# 1) session lifecycle
# ---------------------------------------------------------------------------
def session_start(scope: str, scope_id: str, workload: str,
                  point_in_time: str = "", show_deleted: bool = False,
                  repository_id: str = "") -> Any:
    gate = _need_restore()
    if gate:
        return gate
    workload = workload.lower().strip()
    if workload not in ep.WORKLOADS:
        return {"error": f"workload must be one of {sorted(ep.WORKLOADS)}"}
    type_code = ep.WORKLOADS[workload]["type"]
    scope = scope.lower().strip()
    # 'dateTime' is mandatory for explore; default to the latest matching restore point.
    dt = point_in_time or _latest_restore_point_time(scope, scope_id, workload)
    if not dt:
        return {"error": "no restore point found for this scope/workload; pass an explicit "
                         "point_in_time (ISO datetime), or check vb365_list_restore_points",
                "scope": scope, "scope_id": scope_id, "workload": workload}
    body: dict[str, Any] = {"type": type_code, "dateTime": dt,
                            "showDeleted": bool(show_deleted), "showAllVersions": False}
    if scope == "job":
        path = ep.JOB_EXPLORE.format(job_id=scope_id)
    elif scope == "organization":
        path = ep.ORG_EXPLORE.format(org_id=scope_id)
        if repository_id:
            body["repositoryId"] = repository_id
    else:
        return {"error": "scope must be 'job' or 'organization'"}
    res = vb365.action(path, body)
    A.audit("restore", "session_start", {"scope": scope, "id": scope_id, "workload": workload}, res)
    if isinstance(res, dict) and not res.get("error"):
        return {"session_id": res.get("id"), "workload": workload,
                "type": res.get("type"), "state": res.get("state"),
                "next": "restore_browse(session_id) to list restorable objects"}
    return res


def session_stop(session_id: str) -> Any:
    gate = _need_restore()
    if gate:
        return gate
    res = vb365.action(ep.RESTORE_SESSION_STOP.format(rs_id=session_id))
    A.audit("restore", "session_stop", session_id, res)
    return res if (isinstance(res, dict) and res.get("error")) else {"ok": True, "stopped": session_id}


def status(session_id: str) -> Any:
    gate = _need_restore()
    if gate:
        return gate
    s = vb365.get(ep.RESTORE_SESSION.format(rs_id=session_id))
    stats = vb365.get(ep.RESTORE_SESSION_STATS.format(rs_id=session_id))
    if isinstance(s, dict) and s.get("error"):
        return s
    return {"session_id": session_id, "state": vb365.first(s, "state"),
            "result": vb365.first(s, "result"), "type": vb365.first(s, "type"),
            "details": vb365.first(s, "details"), "endTime": vb365.first(s, "endTime"),
            "statistics": stats if not (isinstance(stats, dict) and stats.get("error")) else None}


# ---------------------------------------------------------------------------
# 2) browse / search
# ---------------------------------------------------------------------------
def browse(session_id: str, parent_id: str = "", limit: int = 200) -> Any:
    gate = _need_restore()
    if gate:
        return gate
    wl = _session_workload(session_id)
    if not wl:
        return {"error": "could not resolve session workload; check session_id"}
    cfg = ep.WORKLOADS[wl]
    rt = vb365.VB365_RESTORE_TIMEOUT
    if not parent_id:
        data = vb365.get(f"{ep.RESTORE_SESSION.format(rs_id=session_id)}/{cfg['root']}",
                         {"limit": limit}, timeout=rt)
        kind = {"exchange": "mailboxes", "onedrive": "onedrives",
                "sharepoint": "sites", "teams": "teams"}[wl]
    else:
        sub = cfg["items"].format(parent_id=parent_id)
        data = vb365.get(f"{ep.RESTORE_SESSION.format(rs_id=session_id)}/{sub}",
                         {"limit": limit}, timeout=rt)
        kind = "items"
    if isinstance(data, dict) and data.get("error"):
        return data
    rows = vb365.items(data)
    slim = [{"id": vb365.first(r, "id"), "name": vb365.first(r, "name", "subject", "email"),
             "email": vb365.first(r, "email"), "isArchive": r.get("isArchive"),
             "from": r.get("from"), "type": r.get("itemClass")}
            for r in rows if isinstance(r, dict)]
    return {"session_id": session_id, "workload": wl, "level": kind,
            "count": len(slim), "items": slim,
            "next": "pass an item's id as parent_id to drill in, or restore_define(...) to restore"}


def search(session_id: str, query: str, parent_id: str = "") -> Any:
    gate = _need_restore()
    if gate:
        return gate
    wl = _session_workload(session_id)
    if not wl:
        return {"error": "could not resolve session workload; check session_id"}
    cfg = ep.WORKLOADS[wl]
    sp = cfg["search"]
    path = sp.format(parent_id=parent_id) if "{parent_id}" in sp else sp
    if "{parent_id}" in sp and not parent_id:
        return {"error": f"{wl} search needs a parent_id (the object to search within)"}
    data = vb365.action(f"{ep.RESTORE_SESSION.format(rs_id=session_id)}/{path}",
                        {"query": query}, timeout=vb365.VB365_RESTORE_TIMEOUT)
    if isinstance(data, dict) and data.get("error"):
        return data
    rows = vb365.items(data)
    slim = [{"id": vb365.first(r, "id"), "name": vb365.first(r, "name", "subject"),
             "from": r.get("from"), "type": r.get("itemClass")}
            for r in rows if isinstance(r, dict)]
    return {"session_id": session_id, "workload": wl, "query": query,
            "count": len(slim), "items": slim}


# ---------------------------------------------------------------------------
# 3) define + execute
# ---------------------------------------------------------------------------
def define(session_id: str, parent_id: str, item_ids: list, target: str,
           options: Optional[dict] = None, confirm_overwrite_original: bool = False) -> Any:
    """Build a restore spec and return a preview + one-time confirm token.
    target: 'export' (safest — reads backup to a local file, no M365 creds),
            'alternate' (writes to a DIFFERENT M365 location),
            'original' (writes back to the ORIGINAL location — destructive)."""
    gate = _need_restore()
    if gate:
        return gate
    options = dict(options or {})
    target = target.lower().strip()
    if target not in ("export", "alternate", "original"):
        return {"error": "target must be 'export', 'alternate', or 'original'"}
    if not item_ids:
        return {"error": "item_ids is empty — nothing to restore"}
    wl = _session_workload(session_id)
    if not wl:
        return {"error": "could not resolve session workload; check session_id"}
    cfg = ep.WORKLOADS[wl]

    # Some workloads (notably Teams) have NO distinct alternate-location endpoint:
    # cfg['restore_alternate'] == cfg['restore_original'], so an "alternate" restore
    # would actually write to the ORIGINAL location. Treat that as destructive too,
    # so the overwrite acknowledgement cannot be sidestepped via target="alternate".
    alt_aliases_original = (target == "alternate"
                            and cfg.get("restore_alternate") == cfg.get("restore_original"))
    writes_original = target == "original" or alt_aliases_original

    if writes_original and not confirm_overwrite_original:
        why = ("original-location restore overwrites/merges into PRODUCTION"
               if target == "original" else
               f"this workload ({wl}) has no separate alternate-location endpoint, so "
               "target='alternate' writes to the ORIGINAL location")
        return {"error": why + "; re-call restore_define(..., confirm_overwrite_original=True) "
                               "to proceed",
                "blast_radius": "HIGH — writes to the original M365 location"}

    verb = {"export": "export", "alternate": "restore_alternate",
            "original": "restore_original"}[target]
    path = f"{ep.RESTORE_SESSION.format(rs_id=session_id)}/{cfg[verb].format(parent_id=parent_id)}"

    # Build the request body. Exchange is modelled exactly; other workloads use a
    # best-effort items body (verified path, generic payload).
    items_body = ([{"id": i} for i in item_ids])
    download = False
    if target == "export":
        body = {"items": items_body}
        if wl == "exchange":
            if options.get("pstSizeLimitBytes"):
                body.update({"enablePstSizeLimit": True,
                             "pstSizeLimitBytes": int(options["pstSizeLimitBytes"])})
            download = True  # exportToPst returns a file
        else:
            download = True  # 'save' streams file bytes
        blast = "safe — reads from backup to a local file; no write to M365"
    else:
        body = {"items": items_body}
        body.update(_target_creds(options))
        if target == "alternate":
            for k in ("mailbox", "folder", "markRestoredAsUnread", "changedItems",
                      "deletedItems", "excludeDeletedItems", "site", "library"):
                if k in options:
                    body[k] = options[k]
            blast = ("HIGH — no distinct alternate endpoint for this workload; writes to the "
                     "ORIGINAL M365 location" if alt_aliases_original
                     else "writes restored items to a DIFFERENT M365 location")
        else:
            blast = "HIGH — writes restored items to the ORIGINAL M365 location"
        if wl != "exchange":
            body.setdefault("_note", "non-exchange body is best-effort; verify fields")

    summary = f"Restore {len(item_ids)} {wl} item(s) -> {target} (session {session_id})"
    payload = {"path": path, "body": body, "download": download, "target": target,
               "workload": wl, "parent_id": parent_id, "count": len(item_ids),
               "session_id": session_id}
    preview_extra = {"target": target, "workload": wl, "item_count": len(item_ids),
                     "endpoint": path}
    if target != "export" and not _target_creds(options):
        preview_extra["warning"] = ("no M365 target credentials set — provide "
                                    "options.userName/userPassword or set VB365_RESTORE_* "
                                    "or the restore will fail at execute")
    return A.prepare("restore", summary, payload, blast, preview_extra)


def execute(session_id: str, confirm: str) -> Any:
    gate = _need_restore()
    if gate:
        return gate
    payload = A.consume(confirm, "restore")
    if isinstance(payload, dict) and payload.get("error"):
        return payload
    # Act on the session bound to the token at define() time, not whatever the
    # caller passed to execute(), so the audit record and export file cannot be
    # mislabeled by a mismatched session_id argument.
    bound_session = payload.get("session_id") or session_id
    rt = vb365.VB365_RESTORE_TIMEOUT
    if payload.get("download"):
        ext = "pst" if payload["workload"] == "exchange" else "bin"
        ddir = A._download_dir()
        dest = os.path.join(ddir, f"restore_{payload['workload']}_{_safe_token(bound_session)}.{ext}")
        # Defense in depth: never write outside the download directory.
        if not os.path.realpath(dest).startswith(os.path.realpath(ddir) + os.sep):
            return {"error": "refusing to write export outside the download directory"}
        res = vb365.download(payload["path"], payload["body"], dest_path=dest, timeout=rt)
    else:
        res = vb365.action(payload["path"], payload["body"], timeout=rt)
    A.audit("restore", f"execute:{payload['target']}",
            {"session": bound_session, "parent": payload["parent_id"],
             "workload": payload["workload"], "count": payload["count"]}, res)
    return res
