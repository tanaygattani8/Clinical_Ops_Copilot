"""Guards on /api/generate-brief that the audit found missing.

Each test fails if one of the three critical fixes is reverted: request fields
are whitelisted before storage, the brief reads through the warehouse MCP tool
(so Rule 2's minimum-n applies), and the agent trace names only what really ran.
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["LOG_PATH"] = "logs/test_brief_boundary.jsonl"
os.environ["CLINIC_DB_PATH"] = "data/clinic.duckdb"

from fastapi.testclient import TestClient
from web.app import app

client = TestClient(app)
OK = {"start": "2025-01-01", "end": "2025-03-31", "clinic": "all"}


def _post(**over):
    return client.post("/api/generate-brief", json={**OK, **over})


def test_html_in_clinic_is_refused_not_stored():
    assert _post(clinic="<img src=x onerror=alert(1)>").status_code == 400


def test_unknown_clinic_is_refused():
    assert _post(clinic="CLINIC_99x").status_code == 400
    assert _post(clinic="CLINIC_01").status_code == 200


def test_non_date_is_refused():
    assert _post(end="<script>").status_code == 400


def test_window_too_small_hits_minimum_n():
    # Rule 2 counts people. A window covering no appointments is refused...
    res = _post(start="1990-01-01", end="1990-01-02", clinic="CLINIC_01")
    assert res.status_code == 400
    assert "minimum of 5" in res.json()["error"]


def test_a_single_clinic_day_is_not_a_privacy_risk():
    # ...but one clinic on one real day covers a whole day of patients and must
    # not be. The gate used to count metric rows, of which there are 4 per
    # clinic-day, so it refused the narrowest query while allowing the widest.
    assert _post(start="2025-01-01", end="2025-01-01", clinic="CLINIC_01").status_code == 200


def test_trace_names_only_components_that_ran():
    trace = _post().json()["trace"]
    agents = [t["agent"] for t in trace]
    # ops_analyst does not run on this path; claiming it did was the original bug.
    assert "ops_analyst" not in agents
    assert "clinic_warehouse (MCP)" in agents
