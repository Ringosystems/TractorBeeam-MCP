"""Opt-in, gated operational actions (the write tier) + the shared safety
framework (category flags, two-call confirm tokens, append-only audit log).

NOTHING here runs unless explicitly enabled by env flags, and every mutating
call requires a second invocation carrying a one-time confirm token that is bound
to the exact payload previewed on the first call. Restore (restore.py) reuses
this same framework.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from . import vb365
from . import endpoints as ep

# --- category flags ---------------------------------------------------------
_TRUE = ("1", "true", "yes", "on")


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUE


def actions_enabled() -> bool:
    return _flag("TB_ENABLE_ACTIONS")


# category -> env flag that must ALSO be true (master switch + per-category)
CATEGORY_FLAG = {
    "job_control": "TB_ALLOW_JOB_CONTROL",
    "reports": "TB_ALLOW_REPORTS",
    "org_sync": "TB_ALLOW_ORG_SYNC",
    "proxy": "TB_ALLOW_PROXY",
    "restore": "TB_ALLOW_RESTORE",
}


def category_enabled(category: str) -> bool:
    if not actions_enabled():
        return False
    flag = CATEGORY_FLAG.get(category)
    return bool(flag) and _flag(flag)


def disabled_note(category: str) -> dict:
    return {"error": "action disabled",
            "category": category,
            "how_to_enable": f"set TB_ENABLE_ACTIONS=true and {CATEGORY_FLAG.get(category)}=true",
            "why": "TractorBeeam365 ships read-only by default; write actions are opt-in."}


# --- confirm-token store (two-call pattern) ---------------------------------
_CONFIRM_TTL_S = float(os.getenv("TB_CONFIRM_TTL_S", "300"))
_pending: dict[str, dict] = {}
_pending_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def prepare(category: str, summary: str, payload: dict, blast_radius: str,
            extra_preview: Optional[dict] = None) -> dict:
    """First call: stash the exact payload and hand back a one-time token + a
    human-readable preview. No mutation happens here."""
    token = secrets.token_hex(8)
    with _pending_lock:
        _pending[token] = {"category": category, "summary": summary,
                           "payload": payload, "created": time.monotonic()}
    preview = {"action": summary, "blast_radius": blast_radius,
               "confirm_token": token, "expires_in_s": int(_CONFIRM_TTL_S),
               "note": "DRY RUN — nothing changed. Call the same tool again with "
                       f"confirm='{token}' to execute."}
    if extra_preview:
        preview.update(extra_preview)
    return preview


def consume(token: str, category: str) -> Any:
    """Second call: validate + atomically remove the token. Returns the bound
    payload, or an {'error': ...} dict if missing/expired/mismatched."""
    with _pending_lock:
        rec = _pending.get(token)
        if not rec:
            return {"error": "invalid or already-used confirm token; re-run to get a new one"}
        if rec["category"] != category:
            return {"error": "confirm token does not match this action"}
        if time.monotonic() - rec["created"] > _CONFIRM_TTL_S:
            _pending.pop(token, None)
            return {"error": "confirm token expired; re-run to get a new one"}
        _pending.pop(token, None)
        return rec["payload"]


# --- audit log (append-only JSONL) ------------------------------------------
_AUDIT_PATH = os.getenv("TB_AUDIT_LOG", os.path.join("audit", "actions.jsonl"))
_audit_lock = threading.Lock()


def audit(category: str, action: str, target: Any, result: Any,
          extra: Optional[dict] = None) -> None:
    rec = {"ts": now_iso(), "category": category, "action": action,
           "target": target, "ok": not (isinstance(result, dict) and result.get("error")),
           "result_summary": _summarize_result(result)}
    if extra:
        rec.update(extra)
    try:
        d = os.path.dirname(_AUDIT_PATH)
        if d:
            os.makedirs(d, exist_ok=True)
        with _audit_lock, open(_AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass  # auditing must never break the action path


def _summarize_result(result: Any) -> Any:
    if isinstance(result, dict):
        if result.get("error"):
            return {"error": result["error"]}
        return {k: result[k] for k in ("ok", "id", "sessionId", "saved_to", "status_code")
                if k in result} or "ok"
    return "ok"


def _download_dir() -> str:
    d = os.getenv("TB_DOWNLOAD_DIR", "downloads")
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Non-restore actions
# ---------------------------------------------------------------------------
def job_action(job_id: str, op: str, confirm: str = "", full: bool = False) -> Any:
    """Start/stop/enable/disable one backup job (two-call confirm)."""
    if not category_enabled("job_control"):
        return disabled_note("job_control")
    op = op.lower().strip()
    paths = {"start": ep.JOB_START, "stop": ep.JOB_STOP,
             "enable": ep.JOB_ENABLE, "disable": ep.JOB_DISABLE}
    if op not in paths:
        return {"error": f"op must be one of {sorted(paths)}"}
    path = paths[op].format(job_id=job_id)
    body = {"full": bool(full)} if op == "start" else None
    summary = f"Job {op} (job_id={job_id}{', full' if full and op == 'start' else ''})"
    if not confirm:
        risk = {"start": "runs a backup now (proxy/repo load)",
                "stop": "interrupts an in-flight backup",
                "enable": "re-enables the job schedule",
                "disable": "stops future scheduled runs"}[op]
        return prepare("job_control", summary, {"path": path, "body": body, "op": op}, risk)
    payload = consume(confirm, "job_control")
    if isinstance(payload, dict) and payload.get("error"):
        return payload
    res = vb365.action(payload["path"], payload["body"])
    audit("job_control", payload["op"], job_id, res)
    return res


def proxy_action(proxy_id: str, op: str, confirm: str = "") -> Any:
    """Rescan a proxy or toggle its maintenance mode (two-call confirm)."""
    if not category_enabled("proxy"):
        return disabled_note("proxy")
    op = op.lower().strip()
    paths = {"rescan": ep.PROXY_RESCAN,
             "maintenance_enable": ep.PROXY_MAINT_ENABLE,
             "maintenance_disable": ep.PROXY_MAINT_DISABLE}
    if op not in paths:
        return {"error": f"op must be one of {sorted(paths)}"}
    path = paths[op].format(proxy_id=proxy_id)
    summary = f"Proxy {op} (proxy_id={proxy_id})"
    if not confirm:
        risk = "network probe, no data change" if op == "rescan" else "changes job scheduling on this proxy"
        return prepare("proxy", summary, {"path": path, "op": op}, risk)
    payload = consume(confirm, "proxy")
    if isinstance(payload, dict) and payload.get("error"):
        return payload
    res = vb365.action(payload["path"])
    audit("proxy", payload["op"], proxy_id, res)
    return res


def org_sync(org_id: str, sync_type: str = "Incremental", confirm: str = "") -> Any:
    """Trigger an on-demand directory resync for one organization."""
    if not category_enabled("org_sync"):
        return disabled_note("org_sync")
    sync_type = "Full" if str(sync_type).lower() == "full" else "Incremental"
    path = ep.ORG_SYNC.format(org_id=org_id)
    summary = f"Organization sync ({sync_type}, org_id={org_id})"
    if not confirm:
        return prepare("org_sync", summary, {"path": path, "body": {"type": sync_type}},
                       "re-reads M365 directory; no local data change")
    payload = consume(confirm, "org_sync")
    if isinstance(payload, dict) and payload.get("error"):
        return payload
    res = vb365.action(payload["path"], payload["body"])
    audit("org_sync", "sync", org_id, res)
    return res


def generate_report(kind: str, organization_id: str = "", fmt: str = "PDF",
                    confirm: str = "") -> Any:
    """Generate a protection/license/storage report and save it to TB_DOWNLOAD_DIR."""
    if not category_enabled("reports"):
        return disabled_note("reports")
    kind = kind.lower().strip()
    if kind not in ep.REPORTS:
        return {"error": f"kind must be one of {sorted(ep.REPORTS)}"}
    fmt = "CSV" if str(fmt).upper() == "CSV" else "PDF"
    path = ep.REPORTS[kind]
    body: dict[str, Any] = {"format": fmt}
    if organization_id:
        body["organizationId"] = organization_id
    summary = f"Generate report '{kind}' ({fmt})"
    if not confirm:
        return prepare("reports", summary, {"path": path, "body": body, "kind": kind, "fmt": fmt},
                       "read-only export; writes a report file locally")
    payload = consume(confirm, "reports")
    if isinstance(payload, dict) and payload.get("error"):
        return payload
    ext = payload["fmt"].lower()
    dest = os.path.join(_download_dir(),
                        f"{payload['kind']}_{int(time.time())}.{ext}")
    res = vb365.download(payload["path"], payload["body"], dest_path=dest)
    audit("reports", payload["kind"], organization_id or "all", res)
    return res
