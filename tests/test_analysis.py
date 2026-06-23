"""Unit tests for the pure analysis functions (no network)."""
from datetime import datetime, timezone

from tractorbeeam365 import analysis as A

NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)


def _iso(days_ago: float) -> str:
    from datetime import timedelta
    return (NOW - timedelta(days=days_ago)).isoformat()


def test_parse_dt_variants():
    assert A.parse_dt("2026-06-22T09:00:02.094Z").year == 2026
    # 7-digit fractional seconds (VB365 emits these) must not crash
    assert A.parse_dt("2025-09-17T21:09:26.9275764Z") is not None
    assert A.parse_dt("") is None
    assert A.parse_dt(None) is None


def test_coverage_math():
    cov = A.coverage(["a@x.com", "B@x.com", "c@x.com"], ["a@x.com", "b@x.com"])
    assert cov["live"] == 3
    assert cov["protected_of_live"] == 2          # case-insensitive
    assert cov["coverage_pct"] == 66.7
    assert cov["unprotected"] == ["c@x.com"]
    assert cov["unprotected_count"] == 1


def test_coverage_empty():
    assert A.coverage([], [])["coverage_pct"] is None


def test_trend_job_health_hidden_instability():
    jobs = [{"id": "j1", "name": "Daily"}]
    # latest run succeeded, but 2 of 5 in-window runs failed -> 40% -> hidden instability
    sessions = [
        {"jobId": "j1", "status": "Success", "creationTime": _iso(0)},
        {"jobId": "j1", "status": "Failed", "creationTime": _iso(3)},
        {"jobId": "j1", "status": "Warning", "creationTime": _iso(5)},
        {"jobId": "j1", "status": "Success", "creationTime": _iso(7)},
        {"jobId": "j1", "status": "Success", "creationTime": _iso(9)},
        {"jobId": "j1", "status": "Failed", "creationTime": _iso(90)},  # out of window
    ]
    res = A.trend_job_health(sessions, jobs, days=30, now=NOW)
    row = res["jobs"][0]
    assert row["runs"] == 5 and row["failed"] == 1 and row["warning"] == 1
    assert row["fail_or_warn_pct"] == 40.0
    assert row["hidden_instability"] is True
    assert any("hidden instability" in f for f in res["flags"])


def test_trend_capacity_forecast_flags_imminent_full():
    # ~1 GiB/day for 10 days, ~5 GiB free -> ~5 days to full -> flagged (<=60d)
    gib = 1024 ** 3
    sessions = [{"creationTime": _iso(d), "statistics": {"transferredDataBytes": gib}}
                for d in range(0, 10)]
    repos = [{"name": "R", "capacityBytes": 100 * gib, "freeSpaceBytes": 5 * gib}]
    res = A.trend_capacity_forecast(sessions, repos, now=NOW)
    assert res["repositories"][0]["approx_days_until_full"] is not None
    assert any("may fill" in f for f in res["flags"])


def test_trend_restore_cadence_flags_stale():
    rsess = [{"organization": "Contoso", "creationTime": _iso(200), "result": "Success"}]
    orgs = [{"name": "Contoso"}]
    res = A.trend_restore_cadence(rsess, orgs, days=120, now=NOW)
    assert any("restore test" in f for f in res["flags"])


def test_trend_rpo_compliance():
    jobs = [{"id": "j1", "name": "Daily", "isEnabled": True}]
    sessions = [{"jobId": "j1", "status": "Success", "creationTime": _iso(2)}]  # 48h ago
    res = A.trend_rpo_compliance(jobs, sessions, rpo_hours=24, now=NOW)
    assert res["jobs"][0]["compliant"] is False
    assert any("RPO" in f for f in res["flags"])
