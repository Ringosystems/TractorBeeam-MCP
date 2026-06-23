"""Microsoft 365 (Graph) read-only client for backup-coverage / gap analysis.

App-only (client-credentials) auth with READ scopes only. Optional: if GRAPH_*
env is unset the whole feature degrades to a clear 'configure Graph' note, so the
read-only VB365 reviewer keeps working without it.

Required Entra app application permissions (admin-consented), all read-only:
  User.Read.All, Group.Read.All, Sites.Read.All, Directory.Read.All
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Optional

import httpx

GRAPH_TENANT_ID = os.getenv("GRAPH_TENANT_ID", "").strip()
GRAPH_CLIENT_ID = os.getenv("GRAPH_CLIENT_ID", "").strip()
GRAPH_CLIENT_SECRET = os.getenv("GRAPH_CLIENT_SECRET", "").strip()
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

_client = httpx.Client(timeout=float(os.getenv("GRAPH_TIMEOUT", "30")))
_lock = threading.Lock()
_token: dict[str, Any] = {"access": None, "expires_at": 0.0}


def config_error() -> Optional[str]:
    missing = [k for k, v in {
        "GRAPH_TENANT_ID": GRAPH_TENANT_ID,
        "GRAPH_CLIENT_ID": GRAPH_CLIENT_ID,
        "GRAPH_CLIENT_SECRET": GRAPH_CLIENT_SECRET,
    }.items() if not v]
    if missing:
        return ("Microsoft Graph gap-analysis is not configured. Set " +
                ", ".join(missing) + " in .env (Entra app with read scopes: "
                "User.Read.All, Group.Read.All, Sites.Read.All, Directory.Read.All).")
    return None


def _ensure_token() -> Optional[str]:
    with _lock:
        if _token["access"] and time.monotonic() < _token["expires_at"]:
            return None
        url = f"https://login.microsoftonline.com/{GRAPH_TENANT_ID}/oauth2/v2.0/token"
        data = {"grant_type": "client_credentials", "client_id": GRAPH_CLIENT_ID,
                "client_secret": GRAPH_CLIENT_SECRET,
                "scope": "https://graph.microsoft.com/.default"}
        r = _client.post(url, data=data)
        r.raise_for_status()
        j = r.json()
        _token["access"] = j.get("access_token")
        _token["expires_at"] = time.monotonic() + float(j.get("expires_in", 3600)) - 60
        return None


def _get_all(path: str, params: Optional[dict] = None, cap: int = 5000) -> Any:
    """GET with @odata.nextLink paging. Returns a list, or an {'error': ...} dict."""
    err = config_error()
    if err:
        return {"error": err}
    try:
        _ensure_token()
        url = path if path.startswith("http") else f"{GRAPH_BASE}/{path.lstrip('/')}"
        out: list = []
        headers = {"Authorization": f"Bearer {_token['access']}"}
        while url and len(out) < cap:
            r = _client.get(url, params=params, headers=headers)
            if r.status_code == 401:
                _token["access"] = None
                _ensure_token()
                headers = {"Authorization": f"Bearer {_token['access']}"}
                r = _client.get(url, params=params, headers=headers)
            r.raise_for_status()
            j = r.json()
            out.extend(j.get("value", []) if isinstance(j, dict) else [])
            url = j.get("@odata.nextLink") if isinstance(j, dict) else None
            params = None  # nextLink already carries the query
        return out
    except httpx.HTTPStatusError as e:
        host = ""
        try:
            host = e.request.url.host
        except Exception:
            pass
        # A failure on the token endpoint means bad GRAPH_* creds. Return a generic
        # message rather than the Azure AD (AADSTS) diagnostic body.
        if host and "login.microsoftonline" in host:
            return {"error": "Graph authentication failed — check GRAPH_TENANT_ID / "
                             "GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET for the Entra app."}
        body = ""
        try:
            body = e.response.text[:600]
        except Exception:
            pass
        return {"error": f"Graph HTTP {e.response.status_code} on {path}", "detail": body}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "path": path}


# ---------------------------------------------------------------------------
# Live inventory (normalised identity strings for coverage comparison)
# ---------------------------------------------------------------------------
def live_mailboxes() -> Any:
    """Licensed/member users that have a mailbox (mail set). Returns list of
    {upn, mail, enabled} or an error dict."""
    rows = _get_all("users", {"$select": "id,userPrincipalName,mail,accountEnabled,userType",
                              "$top": "999"})
    if isinstance(rows, dict):
        return rows
    return [{"upn": u.get("userPrincipalName"), "mail": u.get("mail"),
             "enabled": u.get("accountEnabled")}
            for u in rows if u.get("mail") and u.get("userType") != "Guest"]


def live_sharepoint_sites() -> Any:
    rows = _get_all("sites", {"search": "*", "$select": "id,displayName,name,webUrl"})
    if isinstance(rows, dict):
        return rows
    return [{"name": s.get("displayName") or s.get("name"), "url": s.get("webUrl"),
             "id": s.get("id")} for s in rows]


def live_groups_teams() -> Any:
    rows = _get_all("groups", {"$select": "id,displayName,mail,resourceProvisioningOptions",
                               "$top": "999"})
    if isinstance(rows, dict):
        return rows
    out = []
    for g in rows:
        is_team = "Team" in (g.get("resourceProvisioningOptions") or [])
        out.append({"name": g.get("displayName"), "mail": g.get("mail"),
                    "id": g.get("id"), "is_team": is_team})
    return out


def live_teams() -> Any:
    g = live_groups_teams()
    if isinstance(g, dict):
        return g
    return [x for x in g if x.get("is_team")]
