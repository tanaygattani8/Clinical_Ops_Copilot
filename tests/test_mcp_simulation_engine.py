import os
import sys
import pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["CLINIC_DB_PATH"] = "data/test_simulation.duckdb"

from data.seed import create_database
from mcp_servers.simulation_engine import (
    simulate_staffing_change, simulate_schedule_change, simulate_noshow_intervention
)

@pytest.fixture(scope="module", autouse=True)
def setup_test_db():
    test_db = "data/test_simulation.duckdb"
    if os.path.exists(test_db):
        os.remove(test_db)
    create_database(test_db)
    yield
    if os.path.exists(test_db):
        os.remove(test_db)

def test_simulate_staffing_change():
    res = simulate_staffing_change("CLINIC_01", "physician", 1, 90)
    assert res["label"] == "PROJECTED"
    assert "projected_metrics" in res
    assert "wait_time" in res["projected_metrics"]
    assert "utilization" in res["projected_metrics"]
    assert "throughput" in res["projected_metrics"]

def test_simulate_schedule_change():
    res = simulate_schedule_change("CLINIC_01", 15, 40, 90)
    assert res["label"] == "PROJECTED"
    assert "projected_metrics" in res
    assert "daily_capacity" in res["projected_metrics"]

def test_simulate_noshow_intervention():
    res = simulate_noshow_intervention("CLINIC_01", "sms_reminders", 0.15, 90)
    assert res["label"] == "PROJECTED"
    assert "projected_metrics" in res
    assert "no_show_rate" in res["projected_metrics"]
    assert "revenue_impact" in res["projected_metrics"]


def test_more_capacity_shortens_the_wait():
    # The projection multiplied wait by the capacity factor, so adding capacity
    # was reported as making patients wait longer.
    from mcp_servers.simulation_engine import project_schedule
    base = {"wait_time": 20.0, "utilization": 0.85, "no_show_rate": 0.15}
    more = project_schedule(base, 15, 30, 30)["projected_metrics"]
    less = project_schedule(base, 60, 10, 30)["projected_metrics"]
    same = project_schedule(base, 30, 20, 30)["projected_metrics"]

    assert more["wait_time"] < base["wait_time"] < less["wait_time"]
    assert more["utilization"] < base["utilization"]
    assert same["wait_time"] == base["wait_time"]
    # daily_capacity must be per-day, not the whole horizon.
    assert more["daily_capacity"] == 30 and more["horizon_capacity"] == 900
