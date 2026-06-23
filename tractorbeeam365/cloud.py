"""Cloud-side object-lock cross-check.

VB365 reports what it *thinks* the immutability/retention is; this module asks the
bucket directly (Backblaze B2 via its S3-compatible API, or AWS S3/Glacier) what
it actually enforces, and flags any mismatch. Optional: needs B2_*/S3_* creds;
without them it returns a clear 'configure cloud creds' note. boto3 is imported
lazily so the read-only core runs even if boto3 isn't installed.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from . import vb365

B2_KEY_ID = os.getenv("B2_KEY_ID", "").strip()
B2_APP_KEY = os.getenv("B2_APP_KEY", "").strip()
B2_S3_ENDPOINT = os.getenv("B2_S3_ENDPOINT", "").strip()  # e.g. https://s3.us-west-004.backblazeb2.com
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID", "").strip()
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "").strip()
S3_REGION = os.getenv("S3_REGION", "").strip()


def config_present() -> bool:
    return bool((B2_KEY_ID and B2_APP_KEY) or (S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY))


def _is_b2(provider: str, endpoint: Optional[str]) -> bool:
    blob = f"{provider or ''} {endpoint or ''}".lower()
    return "backblaze" in blob or "b2" in blob


def _client_for(provider: str, endpoint: Optional[str], region: Optional[str]):
    try:
        import boto3  # lazy
        from botocore.config import Config
    except Exception as e:
        return None, f"boto3 not installed ({e}); pip install boto3 to enable cloud checks"
    cfg = Config(signature_version="s3v4", retries={"max_attempts": 2})
    if _is_b2(provider, endpoint):
        if not (B2_KEY_ID and B2_APP_KEY):
            return None, "Backblaze B2 repo found but B2_KEY_ID/B2_APP_KEY not set"
        ep = endpoint or B2_S3_ENDPOINT
        if ep and not ep.startswith("http"):
            ep = "https://" + ep
        return boto3.client("s3", endpoint_url=ep or None,
                            aws_access_key_id=B2_KEY_ID, aws_secret_access_key=B2_APP_KEY,
                            config=cfg), None
    if not (S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY):
        return None, "AWS S3/Glacier repo found but S3_ACCESS_KEY_ID/S3_SECRET_ACCESS_KEY not set"
    kw: dict[str, Any] = {"aws_access_key_id": S3_ACCESS_KEY_ID,
                          "aws_secret_access_key": S3_SECRET_ACCESS_KEY, "config": cfg}
    if region or S3_REGION:
        kw["region_name"] = region or S3_REGION
    if endpoint:
        kw["endpoint_url"] = endpoint if endpoint.startswith("http") else "https://" + endpoint
    return boto3.client("s3", **kw), None


def _err_code(e: Exception) -> str:
    """A non-sensitive error label: the botocore error Code when present (never
    the raw message, which can embed bucket/endpoint/access-key-id), else the
    exception type name."""
    resp = getattr(e, "response", None)
    if isinstance(resp, dict):
        code = (resp.get("Error") or {}).get("Code")
        if code:
            return str(code)
    return type(e).__name__


def _bucket_lock(client, bucket: str) -> dict:
    out: dict[str, Any] = {}
    try:
        olc = client.get_object_lock_configuration(Bucket=bucket).get("ObjectLockConfiguration", {})
        out["object_lock_enabled"] = olc.get("ObjectLockEnabled") == "Enabled"
        rule = (olc.get("Rule") or {}).get("DefaultRetention") or {}
        out["default_mode"] = rule.get("Mode")            # GOVERNANCE | COMPLIANCE
        out["default_days"] = rule.get("Days")
        out["default_years"] = rule.get("Years")
    except Exception as e:
        out["object_lock_error"] = _err_code(e)
    try:
        ver = client.get_bucket_versioning(Bucket=bucket)
        out["versioning"] = ver.get("Status", "Disabled")
    except Exception as e:
        out["versioning_error"] = _err_code(e)
    return out


def lock_audit() -> Any:
    """For every cloud repo VB365 uses, compare VB365's reported immutability to
    what the bucket actually enforces. Returns rows + flags."""
    if not config_present():
        return {"error": "cloud-side check not configured",
                "how_to_enable": "set B2_KEY_ID/B2_APP_KEY (and B2_S3_ENDPOINT) for Backblaze B2, "
                                 "and/or S3_ACCESS_KEY_ID/S3_SECRET_ACCESS_KEY/S3_REGION for AWS S3/Glacier",
                "note": "This compares the live bucket's object-lock to what VB365 reports."}

    reps = vb365.get("BackupRepositories")
    if isinstance(reps, dict) and reps.get("error"):
        return reps
    clouds = [vb365.summarize_cloud(r) for r in vb365.items(reps)
              if isinstance(r, dict) and vb365.looks_object_backed(r)]

    rows, flags = [], []
    for c in clouds:
        bucket = c.get("bucket")
        row: dict[str, Any] = {"repo": c.get("name"), "provider": c.get("provider"),
                               "bucket": bucket, "vb365_immutability": c.get("immutability"),
                               "vb365_immutability_days": c.get("immutability_days"),
                               "vb365_mode": c.get("immutability_mode")}
        if not bucket:
            row["bucket_check"] = "skipped — no bucket name resolved from VB365 config"
            rows.append(row)
            continue
        client, cerr = _client_for(c.get("provider", ""), c.get("endpoint"), c.get("region"))
        if cerr:
            row["bucket_check"] = cerr
            rows.append(row)
            continue
        live = _bucket_lock(client, bucket)
        row["bucket"] = bucket
        row["bucket_object_lock"] = live
        rows.append(row)

        # If the lock status could not be read, say so explicitly — never let an
        # unverifiable bucket fall through into the reassuring "no mismatch" summary.
        if live.get("object_lock_error"):
            flags.append(f"Could NOT verify Object Lock for '{c.get('name')}' bucket '{bucket}' "
                         f"({live['object_lock_error']}) — immutability is unconfirmed, not assumed safe")
            continue

        # Cross-check flags
        if live.get("object_lock_enabled") is False:
            flags.append(f"Cloud '{c.get('name')}' bucket '{bucket}' has Object Lock DISABLED "
                         f"at the provider (backups are deletable)")
        elif live.get("object_lock_enabled") and not (live.get("default_days") or live.get("default_years")):
            flags.append(f"Cloud '{c.get('name')}' bucket '{bucket}' has Object Lock on but NO "
                         f"default retention window")
        if c.get("immutability") and live.get("object_lock_enabled") is False:
            flags.append(f"MISMATCH: VB365 reports immutability ON for '{c.get('name')}' but the "
                         f"bucket '{bucket}' has Object Lock OFF")
        # Mode mismatch: VB365 claims Compliance but the bucket is only Governance (bypassable).
        if str(c.get("immutability_mode", "")).upper() == "COMPLIANCE" and live.get("default_mode") == "GOVERNANCE":
            flags.append(f"MISMATCH: VB365 reports COMPLIANCE mode for '{c.get('name')}' but bucket "
                         f"'{bucket}' is in GOVERNANCE mode (weaker — can be bypassed)")
        # Retention-window mismatch: bucket enforces fewer days than VB365 claims.
        try:
            vb_days, b_days = c.get("immutability_days"), live.get("default_days")
            if c.get("immutability") and vb_days and b_days and int(b_days) < int(vb_days):
                flags.append(f"MISMATCH: VB365 reports a {vb_days}-day immutability for "
                             f"'{c.get('name')}' but bucket '{bucket}' enforces only {b_days} day(s)")
        except (TypeError, ValueError):
            pass
        if live.get("default_mode") == "GOVERNANCE":
            flags.append(f"Cloud '{c.get('name')}' bucket '{bucket}' uses GOVERNANCE mode "
                         f"(can be bypassed) — COMPLIANCE recommended for ransomware resistance")
        if str(live.get("versioning", "")).lower() != "enabled":
            flags.append(f"Cloud '{c.get('name')}' bucket '{bucket}' versioning is "
                         f"{live.get('versioning')} (Object Lock needs versioning)")
    if not flags:
        flags.append("Cloud-side object-lock matches VB365's reported immutability (no mismatch).")
    return {"clouds": rows, "flags": flags}
