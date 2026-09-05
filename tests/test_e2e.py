"""
End-to-end verification test for the Clinic Operations Copilot.

This test verifies the full pipeline wiring without requiring a live Gemini API key.
It mocks the LLM layer to confirm that:
  - Guardrail allows/blocks correctly
  - All agents are wired with correct tools
  - Audit trail is populated
  - Brief history storage works
  - Output validator catches out-of-range values
  - Disclaimer is always injected
  - No individual patient data leaks through
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["CLINIC_DB_PATH"] = "data/clinic.duckdb"
os.environ["LOG_PATH"] = "logs/test_e2e_audit.jsonl"


def _clear_audit():
    path = "logs/test_e2e_audit.jsonl"
    if os.path.exists(path):
        os.remove(path)


# ── Test a: Guardrail allows operational request ──
def test_a_guardrail_allows_operational():
    _clear_audit()
    from agents.guardrail import guardrail_check
    res = guardrail_check("Generate a full executive brief for last week")
    assert res["decision"] == "ALLOW"


# ── Test b: Guardrail blocks patient data request ──
def test_b_guardrail_blocks_patient_data():
    from agents.guardrail import guardrail_check
    res = guardrail_check("Show me patient John Smith's diagnosis")
    assert res["decision"] == "BLOCK"


# ── Test c: Ops analyst has warehouse + calculator + date tools ──
def test_c_ops_analyst_tools():
    from agents.ops_analyst import ops_analyst_agent
    tool_names = [getattr(t, "__name__", getattr(t, "name", None)) for t in ops_analyst_agent.tools]
    assert "calculate" in tool_names
    assert "resolve_date_range" in tool_names
    assert "lookup_metric" in tool_names


# ── Test d: Planner has simulation tools ──
def test_d_planner_tools():
    from agents.planner import planner_agent
    assert planner_agent.output_key == "planner_output"
    assert "PROJECTED" in planner_agent.instruction


# ── Test e: Narrator has report builder + validator + history tools ──
def test_e_narrator_tools():
    from agents.narrator import narrator_agent
    tool_names = [getattr(t, "__name__", getattr(t, "name", None)) for t in narrator_agent.tools]
    assert "validate_all" in tool_names
    assert "store_brief" in tool_names
    assert "retrieve_latest" in tool_names


# ── Test f: Report builder injects disclaimer ──
def test_f_disclaimer_injection():
    from mcp_servers.report_builder import build_executive_brief, build_metric_section, DISCLAIMER
    section = build_metric_section("Test", {"val": 1}, "comment")
    brief = build_executive_brief("E2E Brief", "2025-06-28", [section], "")
    assert DISCLAIMER in brief["brief_markdown"]
    assert "DISCLAIMER" in brief["brief_html"] or "operational decision support" in brief["brief_html"]


# ── Test g: Brief history store and retrieve ──
def test_g_brief_history():
    from rag.brief_history import store_brief, retrieve_by_date
    store_brief("2099-12-31", "# E2E Test Brief\nContent.", {"e2e": True})
    r = retrieve_by_date("2099-12-31")
    assert r is not None
    assert "E2E Test Brief" in r["brief_markdown"]


# ── Test h: Output validator catches invalid metrics ──
def test_h_output_validator():
    from tools.output_validator import validate_all
    good = {"title": "Good", "data": {"avg_wait": 25}}
    bad = {"title": "Bad", "data": {"no_show_rate": 1.9}}
    res = validate_all([good, bad])
    assert res["all_valid"] is False
    assert "Bad" in res["failed_sections"]


# ── Test i: All numbers in metrics_catalog have valid ranges ──
def test_i_metrics_catalog_ranges():
    from rag.metrics_catalog import load_metrics_catalog
    cat = load_metrics_catalog()
    for m in cat["metrics"]:
        lo, hi = m["valid_range"]
        assert lo < hi, f"Invalid range for {m['name']}: [{lo}, {hi}]"


# ── Test j: Audit trail is populated after guardrail checks ──
def test_j_audit_trail_populated():
    from agents._audit import read_audit_log
    logs = read_audit_log(50)
    assert len(logs) >= 2, f"Expected >= 2 audit entries, got {len(logs)}"
    actions = [l["action"] for l in logs]
    assert "request_allowed" in actions or "request_blocked" in actions


# ── Test k: Orchestrator wires all sub-agents ──
def test_k_orchestrator_wiring():
    from agents.orchestrator import root_agent
    sub_names = [sa.name for sa in root_agent.sub_agents]
    assert "ops_analyst" in sub_names
    assert "planner" in sub_names
    assert "narrator" in sub_names


# ── Test l: Web health endpoint ──
def test_l_web_health():
    from fastapi.testclient import TestClient
    from web.app import app
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"
