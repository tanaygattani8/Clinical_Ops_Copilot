"""Web-layer guards added after the audit: minimum-n on the SQL endpoints, request
validation, per-client rate limiting, the Rule 3 disclaimer, and audit-log redaction.

These endpoints had no coverage at all, which is where most of the audit findings lived.
Tests avoid the LLM paths deliberately - every assertion here resolves before any
model call, so the suite stays fast and needs no API key.
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["LOG_PATH"] = "logs/test_web_guards.jsonl"
os.environ["CLINIC_DB_PATH"] = "data/clinic.duckdb"

from fastapi.testclient import TestClient
import web.app as appmod
from web.app import app, MIN_GROUP_N, _RATE_LIMIT, _rate_limited

client = TestClient(app)


class _Req:
    def __init__(self, ip):
        self.client = type("C", (), {"host": ip})()


# ── Constitution Rule 2 at the grain actually returned ──

def test_appointment_groups_below_the_floor_are_withheld():
    rows = client.get("/api/clinic/appointments?clinic=CLINIC_01&group_by=provider_id"
                      "&start=2025-01-01&end=2025-01-02").json()
    assert all(r["count"] >= MIN_GROUP_N for r in rows)


def test_satisfaction_categories_below_the_floor_are_withheld():
    rows = client.get("/api/clinic/satisfaction?clinic=CLINIC_01"
                      "&start=2025-01-01&end=2025-01-02").json()
    assert all(r["n"] >= MIN_GROUP_N for r in rows)


def test_dashboard_reads_through_the_warehouse_minimum_n_gate():
    """The dashboard used to carry its own copy of the brief's KPI/flag SQL, with no
    Rule 2 gate. It now reads the same warehouse tool the brief does."""
    ok = client.get("/api/dashboard?start=2025-01-01&end=2025-03-31&clinic=all")
    assert ok.status_code == 200
    body = ok.json()
    assert set(body["kpis"]) == {"utilization", "no_show_rate", "avg_wait",
                                 "revenue_per_visit", "satisfaction"}
    assert all(f["severity"] in ("high", "medium") for f in body["flags"])
    # Sharing the gate must not mean inheriting a broken one: a single clinic-day
    # is a legitimate dashboard view and used to 400 with a privacy error.
    assert client.get("/api/dashboard?start=2025-01-01&end=2025-01-01&clinic=CLINIC_01").status_code == 200
    # A window covering no patients is still refused.
    too_small = client.get("/api/dashboard?start=1990-01-01&end=1990-01-02&clinic=CLINIC_01")
    assert too_small.status_code == 400
    assert "minimum of 5" in too_small.json()["error"]


# ── Malformed input is a 400, never a 500 ──

def test_malformed_json_is_rejected_cleanly():
    assert client.post("/api/chat", content="{oops").status_code == 400
    assert client.post("/api/simulate", content="{oops").status_code == 400


def test_wrong_types_and_oversized_input_are_rejected():
    assert client.post("/api/chat", json={"message": 123}).status_code == 400
    assert client.post("/api/chat", json={"message": "x" * 5000}).status_code == 400
    assert client.post("/api/simulate", json={"scenario": "staffing", "params": []}).status_code == 400


def test_unknown_clinic_does_not_get_an_invented_baseline():
    res = client.post("/api/simulate", json={"clinic": "NOPE", "scenario": "staffing", "params": {}})
    assert res.status_code == 400


# ── Rule 3: disclaimer on every output surface ──

def test_simulate_carries_the_disclaimer():
    res = client.post("/api/simulate", json={
        "clinic": "CLINIC_01", "scenario": "staffing", "params": {"role": "nurse", "delta": 1}})
    assert res.status_code == 200
    assert res.json()["disclaimer"]


# ── Per-client rate limiting ──

def test_rate_limit_is_per_client_and_per_route():
    appmod._hits.clear()
    caller = _Req("203.0.113.7")
    allowed = [_rate_limited(caller, "chat") for _ in range(_RATE_LIMIT)]
    assert not any(allowed), "the first _RATE_LIMIT calls must pass"
    assert _rate_limited(caller, "chat"), "the next one must be throttled"
    assert not _rate_limited(_Req("198.51.100.2"), "chat"), "a different caller is unaffected"
    assert not _rate_limited(caller, "brief"), "a different route has its own allowance"
    appmod._hits.clear()


# ── The public audit log must not republish what users typed ──

def test_audit_log_redacts_free_text():
    client.post("/api/chat", content="{malformed")   # cheap event, no model call
    for entry in client.get("/api/audit-log?n=25").json():
        details = entry.get("details") or {}
        assert "message_snippet" not in details
        assert "detail" not in details
        assert "reason" not in details


def test_audit_log_bounds_n():
    assert len(client.get("/api/audit-log?n=100000").json()) <= 200


def test_retry_hint_reads_both_minutes_and_seconds():
    # "1m30.5s" used to yield 60, telling the user to retry 30 seconds early.
    from web.app import _retry_after_seconds
    assert _retry_after_seconds("try again in 1m30.5s") == 90
    assert _retry_after_seconds("try again in 8.5s") == 8
    assert _retry_after_seconds("try again in 2m") == 120
    assert _retry_after_seconds("no hint here") is None


def test_brief_llm_failure_is_recorded_not_swallowed(monkeypatch):
    """A brief served by the fallback used to look identical whether the model was
    deprecated, the key was wrong, or a tool subprocess died. The cause has to
    reach the audit trail, or the next outage is undiagnosable from outside."""
    import web.app as appmod

    async def _boom(*a, **kw):
        raise RuntimeError("simulated provider failure")

    logged = []
    monkeypatch.setattr(appmod, "run_agent", _boom)
    monkeypatch.setattr(appmod, "audit_log",
                        lambda agent, action, details: logged.append((action, details)))
    res = client.post("/api/generate-brief",
                      json={"start": "2025-01-01", "end": "2025-03-31", "clinic": "all"})
    # Never 500: the brief still renders from the deterministic fallback.
    assert res.status_code == 200
    failures = [d for a, d in logged if a == "brief_llm_failed"]
    assert failures, f"no brief_llm_failed entry in {[a for a, _ in logged]}"
    assert failures[0]["error_type"] == "RuntimeError"
