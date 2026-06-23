"""VB365 REST client: OAuth2 auth, read-only GET, gated POST action, and the
shape/cloud helpers shared across the package.

Extracted from the original single-file server so every other module (analysis,
graph, cloud, actions, restore) shares one authenticated client and one set of
field-normalisation helpers. GETs are always available; `action()` (POST) is the
single low-level mutator used only by the opt-in actions/restore tiers.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

# httpx logs every request at INFO (incl. EXPECTED 404 probes on VB365 v8). Quiet
# the client; real failures still surface in tool output.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# --- connection config -------------------------------------------------------
VB365_HOST = os.getenv("VB365_HOST", "").strip()
VB365_PORT = os.getenv("VB365_PORT", "4443").strip()
VB365_API_VERSION = os.getenv("VB365_API_VERSION", "v8").strip()
VB365_USERNAME = os.getenv("VB365_USERNAME", "")
VB365_PASSWORD = os.getenv("VB365_PASSWORD", "")
VB365_VERIFY_SSL = os.getenv("VB365_VERIFY_SSL", "false").lower() in ("1", "true", "yes")
# Path to a CA bundle (or the VB365 self-signed cert) to trust. Preferred over
# disabling verification: if set, it is used as httpx's `verify` value and turns
# certificate validation ON against that bundle.
VB365_CA_BUNDLE = os.getenv("VB365_CA_BUNDLE", "").strip()
VB365_TIMEOUT = float(os.getenv("VB365_TIMEOUT", "30"))
# Restore data ops (mounting a restore point, enumerating mailbox items, exporting)
# are much slower than config GETs — give them a generous timeout.
VB365_RESTORE_TIMEOUT = float(os.getenv("VB365_RESTORE_TIMEOUT", "300"))

# Optional M365 target credentials VB365 uses to WRITE data back during a
# restore-to-original / restore-to-different-location. Not needed for read-only
# review, job control, reports, or export-to-PST. Restore tools accept overrides.
VB365_RESTORE_USERNAME = os.getenv("VB365_RESTORE_USERNAME", "")
VB365_RESTORE_PASSWORD = os.getenv("VB365_RESTORE_PASSWORD", "")
VB365_RESTORE_APP_ID = os.getenv("VB365_RESTORE_APP_ID", "")

BASE_URL = f"https://{VB365_HOST}:{VB365_PORT}/{VB365_API_VERSION}"
HOST_URL = f"https://{VB365_HOST}:{VB365_PORT}"

# A CA bundle path (verification ON against that bundle) takes precedence;
# otherwise fall back to the on/off VB365_VERIFY_SSL boolean.
_verify: Any = VB365_CA_BUNDLE or VB365_VERIFY_SSL
tls_unverified = not VB365_CA_BUNDLE and not VB365_VERIFY_SSL

_client = httpx.Client(verify=_verify, timeout=VB365_TIMEOUT)
_lock = threading.Lock()
_token: dict[str, Any] = {"access": None, "refresh": None, "expires_at": 0.0}


def tls_warning() -> Optional[str]:
    """One-line warning when the upstream VB365 channel is unverified (MITM-able)."""
    if tls_unverified:
        return ("VB365_VERIFY_SSL is off and no VB365_CA_BUNDLE is set: the connection "
                "to the VB365 host is NOT certificate-verified and is vulnerable to an "
                "active man-in-the-middle. Prefer setting VB365_CA_BUNDLE to the server's "
                "certificate, or VB365_VERIFY_SSL=true with a trusted cert.")
    return None


def _resolve_url(path: str) -> str:
    """Build the absolute request URL from a RELATIVE VB365 resource path.

    Rejects client-supplied absolute URLs and any embedded scheme/host so the
    VB365 bearer token can only ever be sent to the configured VB365 host
    (closes the SSRF / token-exfiltration vector via the public vb365_get tool).
    """
    p = (path or "").strip()
    if "://" in p or p.startswith("//"):
        raise ValueError("path must be a relative VB365 resource, not an absolute URL")
    rel = p.lstrip("/")
    if any(seg == ".." for seg in rel.split("/")):
        raise ValueError("path must not contain '..' segments")
    if "?" in rel or "#" in rel or "\\" in rel:
        raise ValueError("path must not contain a query/fragment; pass params separately")
    return f"{BASE_URL}/{rel}" if rel else f"{BASE_URL}/"


def _redact(text: Any) -> Any:
    """Mask any known secret value that an upstream error body might echo back."""
    if not isinstance(text, str) or not text:
        return text
    for s in (VB365_PASSWORD, VB365_RESTORE_PASSWORD, _token.get("access")):
        if s and isinstance(s, str) and len(s) >= 4:
            text = text.replace(s, "***")
    return text


def config_error() -> Optional[str]:
    missing = [k for k, v in {
        "VB365_HOST": VB365_HOST,
        "VB365_USERNAME": VB365_USERNAME,
        "VB365_PASSWORD": VB365_PASSWORD,
    }.items() if not v]
    if missing:
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        return ("Missing required config: " + ", ".join(missing) +
                f". Set them in the .env file at the project root ({env_path}).")
    return None


def _fetch_token(refresh: bool) -> None:
    if refresh and _token["refresh"]:
        data = {"grant_type": "refresh_token", "refresh_token": _token["refresh"]}
    else:
        data = {"grant_type": "password",
                "username": VB365_USERNAME, "password": VB365_PASSWORD}
    r = _client.post(f"{BASE_URL}/token", data=data,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    r.raise_for_status()
    j = r.json()
    _token["access"] = j.get("access_token")
    _token["refresh"] = j.get("refresh_token", _token["refresh"])
    _token["expires_at"] = time.monotonic() + float(j.get("expires_in", 3600)) - 60


def ensure_token() -> None:
    with _lock:
        if _token["access"] and time.monotonic() < _token["expires_at"]:
            return
        if _token["refresh"]:
            try:
                _fetch_token(refresh=True)
                return
            except Exception:
                pass
        _fetch_token(refresh=False)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token['access']}"}


def get(path: str, params: Optional[dict] = None, timeout: Optional[float] = None) -> Any:
    """Authenticated GET. Returns parsed JSON (or an {'error': ...} dict).
    `timeout` overrides the default for slow calls (e.g. mounting a restore point)."""
    err = config_error()
    if err:
        return {"error": err}
    try:
        ensure_token()
        try:
            url = _resolve_url(path)
        except ValueError as ve:
            return {"error": f"invalid path: {ve}", "path": path}
        kw = {"timeout": timeout} if timeout else {}
        r = _client.get(url, params=params, headers=_auth_headers(), **kw)
        if r.status_code == 401:  # stale token -> re-auth once
            _token["access"] = None
            ensure_token()
            r = _client.get(url, params=params, headers=_auth_headers(), **kw)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return {"status_code": r.status_code, "text": _redact(r.text[:4000])}
    except httpx.HTTPStatusError as e:
        body = ""
        try:
            body = _redact(e.response.text[:1000])
        except Exception:
            pass
        return {"error": f"HTTP {e.response.status_code} on {path}", "detail": body}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {_redact(str(e))}", "path": path,
                "hint": "Check VB365_HOST/PORT reachability and that the REST API is enabled."}


def action(path: str, json_body: Optional[dict] = None, method: str = "POST",
           raw: bool = False, timeout: Optional[float] = None) -> Any:
    """Authenticated mutating request (POST/PUT/DELETE). Used ONLY by the opt-in
    actions/restore tiers — never wired to a generic tool. Returns parsed JSON,
    or {'_bytes': n, 'content_type': ...} for binary responses (e.g. PST/report
    downloads) when raw=True, or {'error': ...}.
    """
    err = config_error()
    if err:
        return {"error": err}
    try:
        ensure_token()
        try:
            url = _resolve_url(path)
        except ValueError as ve:
            return {"error": f"invalid path: {ve}", "path": path}
        kw = {"timeout": timeout} if timeout else {}

        def _send():
            return _client.request(method, url, json=json_body, headers=_auth_headers(), **kw)

        r = _send()
        if r.status_code == 401:
            _token["access"] = None
            ensure_token()
            r = _send()
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if raw or "application/json" not in ctype:
            if "application/json" in ctype:
                pass
            else:
                return {"ok": True, "status_code": r.status_code,
                        "content_type": ctype, "bytes": len(r.content)}
        if not r.content:
            return {"ok": True, "status_code": r.status_code}
        try:
            return r.json()
        except Exception:
            return {"ok": True, "status_code": r.status_code, "text": r.text[:2000]}
    except httpx.HTTPStatusError as e:
        body = ""
        try:
            body = _redact(e.response.text[:1500])
        except Exception:
            pass
        return {"error": f"HTTP {e.response.status_code} on {method} {path}", "detail": body}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {_redact(str(e))}", "path": path, "method": method}


def download(path: str, json_body: Optional[dict] = None, dest_path: str = "",
             method: str = "POST", timeout: Optional[float] = None) -> Any:
    """POST that streams a binary body (PST export / generated report) to a file."""
    err = config_error()
    if err:
        return {"error": err}
    try:
        ensure_token()
        try:
            url = _resolve_url(path)
        except ValueError as ve:
            return {"error": f"invalid path: {ve}", "path": path}
        kw = {"timeout": timeout} if timeout else {}
        r = _client.request(method, url, json=json_body, headers=_auth_headers(), **kw)
        if r.status_code == 401:
            _token["access"] = None
            ensure_token()
            r = _client.request(method, url, json=json_body, headers=_auth_headers(), **kw)
        r.raise_for_status()
        if dest_path:
            with open(dest_path, "wb") as f:
                f.write(r.content)
            return {"ok": True, "saved_to": dest_path, "bytes": len(r.content),
                    "content_type": r.headers.get("content-type", "")}
        return {"ok": True, "bytes": len(r.content),
                "content_type": r.headers.get("content-type", "")}
    except httpx.HTTPStatusError as e:
        body = ""
        try:
            body = _redact(e.response.text[:1500])
        except Exception:
            pass
        return {"error": f"HTTP {e.response.status_code} on {method} {path}", "detail": body}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {_redact(str(e))}", "path": path, "method": method}


# ---------------------------------------------------------------------------
# Shape helpers
# ---------------------------------------------------------------------------
def items(data: Any) -> list:
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return data["results"]
    if isinstance(data, list):
        return data
    return [data] if data else []


def first(d: Any, *keys: str, default: Any = None) -> Any:
    """First present, non-None value among keys."""
    if isinstance(d, dict):
        for k in keys:
            v = d.get(k)
            if v is not None:
                return v
    return default


def gib(n: Any) -> Optional[float]:
    try:
        return None if n is None else round(float(n) / (1024 ** 3), 1)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Object-storage / cloud helpers (tolerant to field-name differences across
# VB365 versions; in v8 cloud storage is surfaced under Backup Repositories).
# ---------------------------------------------------------------------------
def cloud_block(repo: Any) -> Optional[dict]:
    if isinstance(repo, dict) and isinstance(repo.get("objectStorage"), dict):
        return repo["objectStorage"]
    return None


def looks_object_backed(repo: dict) -> bool:
    if cloud_block(repo) is not None:
        return True
    if isinstance(repo, dict) and "objectStorageEncryptionEnabled" in repo:
        return True
    if any(first(repo, k) for k in (
        "objectStorageRepository", "objectStorageId", "objectStorageRepositoryId",
        "bucket", "bucketName", "container", "containerName",
        "servicePoint", "serviceEndpoint",
    )):
        return True
    t = str(first(repo, "type", "repositoryType", "objectStorageType", default="")).lower()
    return any(x in t for x in (
        "object", "s3", "azure", "amazon", "blob", "cloud", "wasabi", "compatible", "glacier",
    ))


def classify_cloud(repo: dict) -> str:
    """Human label for a cloud repo's provider. Backblaze B2 is configured in
    VB365 as 'S3 Compatible', so we inspect the service endpoint to name it."""
    ob = cloud_block(repo) or repo
    t = str(first(ob, "type", "objectStorageType", "cloudType", default="")).lower()
    ep_parts = []
    for bk in ("amazonBucketS3Compatible", "amazonBucketS3Aws", "azureBlobAccount",
               "azureContainer", "bucket"):
        b = ob.get(bk) if isinstance(ob, dict) else None
        if isinstance(b, dict):
            ep_parts += [str(b.get("servicePoint", "")), str(b.get("customRegionId", "")),
                         str(b.get("regionId", "")), str(b.get("regionName", ""))]
    ep_parts.append(str(first(ob, "servicePoint", "serviceEndpoint", "endpoint",
                              "regionId", "region", default="")))
    ep = " ".join(ep_parts).lower()
    blob = f"{t} {ep}"
    if "azure" in blob:
        return "Azure Blob"
    if "wasabi" in blob:
        return "Wasabi"
    if "ibm" in blob:
        return "IBM Cloud"
    if "google" in blob:
        return "Google Cloud"
    if "backblaze" in ep or "b2" in ep:
        return "Backblaze B2 (S3 Compatible)"
    if "glacier" in blob:
        return "Amazon S3 Glacier"
    if "compatible" in blob:
        return "S3 Compatible"
    if "amazon" in blob or "aws" in blob or "s3" in t:
        return "Amazon S3"
    return str(first(ob, "type", "objectStorageType", default="Unknown"))


def cloud_endpoint(repo: dict) -> dict:
    """Best-effort (bucket, endpoint/servicePoint, region, folder) for a cloud
    repo, so cloud.py can talk to the same bucket VB365 uses."""
    ob = cloud_block(repo) or repo
    bucket = folder = endpoint = region = None
    for bk in ("amazonBucketS3Compatible", "amazonBucketS3Aws", "azureContainer", "bucket"):
        b = ob.get(bk) if isinstance(ob, dict) else None
        if isinstance(b, dict):
            bucket = bucket or b.get("name")
            endpoint = endpoint or b.get("servicePoint") or b.get("serviceEndpoint")
            region = region or b.get("regionId") or b.get("customRegionId") or b.get("regionName")
    bucket = bucket or first(ob, "bucket", "bucketName", "container", "containerName")
    endpoint = endpoint or first(ob, "servicePoint", "serviceEndpoint", "endpoint")
    region = region or first(ob, "regionId", "region", "regionName")
    folder = first(ob, "s3Folder", "folder")
    return {"bucket": bucket, "endpoint": endpoint, "region": region, "folder": folder}


def summarize_cloud(repo: dict) -> dict:
    """Normalize a cloud/object-storage repo to the fields that matter for a
    config review: provider, capacity use, immutability, encryption."""
    ob = cloud_block(repo) or {}
    used = first(ob, "usedSpaceBytes", "usedSpace", "usedCapacityBytes")
    size_limit = None
    if ob.get("sizeLimitEnabled"):
        size_limit = first(ob, "sizeLimitBytes", "sizeLimit")
    used_pct = None
    try:
        if used is not None and size_limit and float(size_limit) > 0:
            used_pct = round(float(used) / float(size_limit) * 100, 1)
    except Exception:
        pass
    imm = first(ob, "enableImmutability", "isImmutabilityEnabled", "immutabilityEnabled")
    gov = first(ob, "enableImmutabilityGovernanceMode")
    ep = cloud_endpoint(repo)
    return {
        "name": first(repo, "name"),
        "id": first(repo, "id"),
        "provider": classify_cloud(repo),
        "type_raw": first(ob, "type", "objectStorageType"),
        "bucket": ep["bucket"],
        "endpoint": ep["endpoint"],
        "region": ep["region"],
        "folder": ep["folder"],
        "used_gib": gib(used),
        "size_limit_gib": gib(size_limit),
        "used_pct": used_pct,
        "size_limit_enabled": bool(ob.get("sizeLimitEnabled")),
        "immutability": imm,
        "immutability_days": first(ob, "immutabilityPeriodDays", "immutabilityPeriod",
                                   "immutabilityIntervalDays", "immutabilityDays"),
        "immutability_mode": (("Governance" if gov else "Compliance") if imm else None),
        "encryption": first(repo, "objectStorageEncryptionEnabled", "isEncryptionEnabled",
                            "encryptionEnabled", "enableEncryption"),
        "long_term_archive": first(repo, "isLongTerm"),
    }
