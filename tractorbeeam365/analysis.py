"""Pure analysis functions over already-fetched VB365 data.

No network / no imports from vb365 — every function takes plain lists/dicts and
returns plain dicts, so the trend, anomaly and coverage logic is unit-testable in
isolation. server.py fetches the data (read-only) and feeds it here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional


def parse_dt(v: Any) -> Optional[datetime]:
    """Tolerant ISO-8601 parse → aware UTC datetime (or None)."""
    if not v or not isinstance(v, str):
        return None
    s = v.strip().replace("Z", "+00:00")
    # Trim fractional seconds longer than 6 digits (VB365 sometimes emits 7).
    if "." in s:
        head, _, tail = s.partition(".")
        digits = ""
        rest = ""
        for i, ch in enumerate(tail):
            if ch.isdigit():
                digits += ch
            else:
                rest = tail[i:]
                break
        s = f"{head}.{digits[:6]}{rest}" if digits else head + rest
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        try:
            dt = datetime.fromisoformat(s[:19])
        except Exception:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _status_of(sess: dict) -> str:
    return str(sess.get("status") or sess.get("lastStatus") or sess.get("result")
               or sess.get("state") or "Unknown")


def _time_of(sess: dict) -> Optional[datetime]:
    return parse_dt(sess.get("creationTime") or sess.get("endTime")
                    or sess.get("startTime") or sess.get("lastRun"))


def _within(dt: Optional[datetime], now: datetime, days: int) -> bool:
    return dt is not None and (now - dt).total_seconds() <= days * 86400


# ---------------------------------------------------------------------------
# Trends & anomalies
# ---------------------------------------------------------------------------
def trend_job_health(sessions: Iterable[dict], jobs: Iterable[dict],
                     days: int = 30, now: Optional[datetime] = None) -> dict:
    """Per-job success/warning/failure counts + failure rate over a window,
    plus a 'drift' flag when the latest run disagrees with the window trend."""
    now = now or datetime.now(timezone.utc)
    jobs = list(jobs)
    job_name = {str(j.get("id")): j.get("name") for j in jobs if isinstance(j, dict)}

    per: dict[str, dict] = {}
    latest: dict[str, tuple[datetime, str]] = {}
    for s in sessions:
        if not isinstance(s, dict):
            continue
        t = _time_of(s)
        if not _within(t, now, days):
            continue
        jid = str(s.get("jobId") or s.get("jobUid") or s.get("mainSessionId") or "")
        st = _status_of(s).lower()
        d = per.setdefault(jid, {"success": 0, "warning": 0, "failed": 0, "other": 0, "total": 0})
        d["total"] += 1
        if "success" in st:
            d["success"] += 1
        elif "warn" in st:
            d["warning"] += 1
        elif "fail" in st:
            d["failed"] += 1
        else:
            d["other"] += 1
        if jid not in latest or t > latest[jid][0]:
            latest[jid] = (t, st)

    out, flags = [], []
    for jid, d in per.items():
        bad = d["warning"] + d["failed"]
        rate = round(bad / d["total"] * 100, 1) if d["total"] else 0.0
        last_st = latest.get(jid, (None, "unknown"))[1]
        drift = ("success" in last_st) and rate >= 20
        row = {"job": job_name.get(jid, jid) or jid, "window_days": days,
               "runs": d["total"], "success": d["success"], "warning": d["warning"],
               "failed": d["failed"], "fail_or_warn_pct": rate, "last_status": last_st,
               "hidden_instability": drift}
        out.append(row)
        if drift:
            flags.append(f"Job '{row['job']}' last run succeeded but is {rate}% "
                         f"warning/failed over {days}d (hidden instability)")
        elif rate >= 50 and d["total"] >= 2:
            flags.append(f"Job '{row['job']}' is failing/warning {rate}% of runs over {days}d")
    return {"jobs": sorted(out, key=lambda r: -r["fail_or_warn_pct"]), "flags": flags}


def trend_capacity_forecast(sessions: Iterable[dict], repos: Iterable[dict],
                            now: Optional[datetime] = None) -> dict:
    """Crude repo-full projection from recent transferred-bytes velocity and
    current free space. Approximate — flags imminent exhaustion, not a guarantee."""
    now = now or datetime.now(timezone.utc)
    # Daily transferred bytes over the last 30 days as a growth proxy.
    cutoff_days = 30
    transferred = 0
    earliest: Optional[datetime] = None
    for s in sessions:
        if not isinstance(s, dict):
            continue
        t = _time_of(s)
        if not _within(t, now, cutoff_days):
            continue
        stats = s.get("statistics") if isinstance(s.get("statistics"), dict) else {}
        b = (stats.get("transferredDataBytes") if stats else None) or s.get("transferredDataBytes")
        try:
            transferred += int(b or 0)
        except Exception:
            pass
        if t and (earliest is None or t < earliest):
            earliest = t
    span_days = max(1.0, (now - earliest).total_seconds() / 86400) if earliest else float(cutoff_days)
    per_day = transferred / span_days if span_days else 0.0

    out, flags = [], []
    for r in repos:
        if not isinstance(r, dict):
            continue
        cap = r.get("capacityBytes", r.get("capacity"))
        free = r.get("freeSpaceBytes", r.get("freeSpace"))
        days_left = None
        try:
            if free is not None and per_day > 0:
                days_left = round(float(free) / per_day, 1)
        except Exception:
            pass
        row = {"name": r.get("name"),
               "free_gib": round(float(free) / 1024**3, 1) if free else None,
               "capacity_gib": round(float(cap) / 1024**3, 1) if cap else None,
               "approx_growth_gib_per_day": round(per_day / 1024**3, 2) if per_day else 0.0,
               "approx_days_until_full": days_left}
        out.append(row)
        if days_left is not None and days_left <= 60:
            flags.append(f"Repository '{r.get('name')}' may fill in ~{days_left} days "
                         f"at current growth (~{row['approx_growth_gib_per_day']} GiB/day)")
    return {"repositories": out, "flags": flags}


def trend_restore_cadence(restore_sessions: Iterable[dict], organizations: Iterable[dict],
                          days: int = 120, now: Optional[datetime] = None) -> dict:
    """How recently each org has had a restore (Veeam's #1 hardening rec is
    regular restore testing). Flags orgs with no restore in the window."""
    now = now or datetime.now(timezone.utc)
    orgs = [o for o in organizations if isinstance(o, dict)]
    last_by_org: dict[str, datetime] = {}
    counts: dict[str, int] = {}
    for rs in restore_sessions:
        if not isinstance(rs, dict):
            continue
        org = str(rs.get("organization") or rs.get("scopeName") or "")
        t = _time_of(rs)
        if t is None:
            continue
        counts[org] = counts.get(org, 0) + (1 if _within(t, now, days) else 0)
        if org not in last_by_org or t > last_by_org[org]:
            last_by_org[org] = t

    out, flags = [], []
    for o in orgs:
        name = o.get("name") or o.get("officeName") or str(o.get("id"))
        # match restore-session 'organization' loosely against org name
        key = next((k for k in last_by_org if name and (name in k or k in name)), name)
        last = last_by_org.get(key)
        age = round((now - last).total_seconds() / 86400, 1) if last else None
        row = {"organization": name, "restores_in_window": counts.get(key, 0),
               "days_since_last_restore": age, "window_days": days}
        out.append(row)
        if age is None:
            flags.append(f"Organization '{name}' has no restore on record — run a restore test")
        elif age > days:
            flags.append(f"Organization '{name}' last restore was {age} days ago "
                         f"(> {days}d target) — schedule a restore test")
    return {"organizations": out, "flags": flags}


def trend_rpo_compliance(jobs: Iterable[dict], sessions: Iterable[dict],
                         rpo_hours: float = 24.0, now: Optional[datetime] = None) -> dict:
    """Time since each enabled job's last successful run vs an RPO target."""
    now = now or datetime.now(timezone.utc)
    last_success: dict[str, datetime] = {}
    for s in sessions:
        if not isinstance(s, dict):
            continue
        if "success" not in _status_of(s).lower():
            continue
        jid = str(s.get("jobId") or "")
        t = _time_of(s)
        if t and (jid not in last_success or t > last_success[jid]):
            last_success[jid] = t

    out, flags = [], []
    for j in jobs:
        if not isinstance(j, dict) or j.get("isEnabled") is False:
            continue
        jid = str(j.get("id"))
        # Compliance is judged ONLY against the last *successful* run; a recent
        # failed/warning run (which still stamps lastBackup/lastRun) must never
        # count as meeting the RPO.
        success = last_success.get(jid)
        age_h = round((now - success).total_seconds() / 3600, 1) if success else None
        compliant = (age_h is not None and age_h <= rpo_hours)
        # Last run of ANY outcome — for context/display only, not compliance.
        any_run = success or parse_dt(j.get("lastBackup") or j.get("lastRun"))
        last_run_age_h = round((now - any_run).total_seconds() / 3600, 1) if any_run else None
        out.append({"job": j.get("name"), "hours_since_last_success": age_h,
                    "hours_since_last_run": last_run_age_h,
                    "rpo_hours": rpo_hours, "compliant": compliant})
        if age_h is None:
            flags.append(f"Job '{j.get('name')}' has NO successful run on record (RPO not met)")
        elif not compliant:
            flags.append(f"Job '{j.get('name')}' last success was {age_h}h ago "
                         f"(> {rpo_hours}h RPO)")
    return {"jobs": out, "flags": flags}


# ---------------------------------------------------------------------------
# Coverage / gap math (used by the Graph gap-analysis)
# ---------------------------------------------------------------------------
def coverage(live: Iterable[str], protected: Iterable[str]) -> dict:
    """Set comparison of live vs protected identifiers (lower-cased)."""
    live_set = {str(x).strip().lower() for x in live if x}
    prot_set = {str(x).strip().lower() for x in protected if x}
    unprotected = sorted(live_set - prot_set)
    extra = sorted(prot_set - live_set)  # protected but no longer live (stale)
    total = len(live_set)
    covered = len(live_set & prot_set)
    pct = round(covered / total * 100, 1) if total else None
    return {"live": total, "protected_of_live": covered, "coverage_pct": pct,
            "unprotected": unprotected, "unprotected_count": len(unprotected),
            "stale_protected": extra, "stale_protected_count": len(extra)}
