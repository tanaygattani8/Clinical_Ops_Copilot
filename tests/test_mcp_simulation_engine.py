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


def test_removing_staff_raises_the_projected_wait():
    from mcp_servers.simulation_engine import project_staffing, project_noshow
    base = {"wait_time": 20.0, "utilization": 0.85, "no_show_rate": 0.20,
            "revenue_per_visit": 176.8, "appts_per_day": 18.0}
    assert project_staffing(base, "physician", -2, 30)["projected_metrics"]["wait_time"] > base["wait_time"]
    # Fewer no-shows means more billable slots, so the revenue impact is positive.
    assert project_noshow(base, "sms_reminders", 0.25, 30)["projected_metrics"]["revenue_impact"] > 0


def test_role_changes_the_projection():
    from mcp_servers.simulation_engine import project_staffing
    base = {"wait_time": 20.0, "utilization": 0.85, "no_show_rate": 0.15}
    physician = project_staffing(base, "physician", 2, 30)["projected_metrics"]["wait_time"]
    admin = project_staffing(base, "admin", 2, 30)["projected_metrics"]["wait_time"]
    assert physician < admin, "adding a physician should relieve the queue more than an admin"


def test_no_fabricated_confidence_interval_is_published():
    from mcp_servers.simulation_engine import project_staffing
    base = {"wait_time": 20.0, "utilization": 0.85, "no_show_rate": 0.15}
    assert "confidence_interval" not in project_staffing(base, "nurse", 1, 30)


def test_reduction_must_be_a_fraction():
    import pytest
    from mcp_servers.simulation_engine import project_noshow
    base = {"wait_time": 20.0, "utilization": 0.85, "no_show_rate": 0.15,
            "revenue_per_visit": 176.8, "appts_per_day": 18.0}
    with pytest.raises(ValueError):
        project_noshow(base, "sms", 15, 30)          # 15 meaning "15%" is ambiguous
    assert project_noshow(base, "sms", 0.15, 30)["label"] == "PROJECTED"


def test_revenue_projection_uses_warehouse_numbers_not_constants():
    """The ROI figure was computed from a hardcoded $150/visit and 15 appts/day
    while the warehouse it had just queried said ~177 and ~18. Those constants
    are the whole 'quantify the money' claim, so they have to come from data."""
    from mcp_servers.simulation_engine import project_noshow
    base = {"wait_time": 20.0, "utilization": 0.85, "no_show_rate": 0.20,
            "revenue_per_visit": 200.0, "appts_per_day": 20.0}
    gain = project_noshow(base, "sms", 0.5, 10)["projected_metrics"]["revenue_impact"]
    # 200 * (0.20 - 0.10) * 20 * 10 == 4000. A constant-driven formula cannot land here.
    assert gain == 4000.0, gain
    # Doubling revenue per visit must double the projection.
    doubled = dict(base, revenue_per_visit=400.0)
    assert project_noshow(doubled, "sms", 0.5, 10)["projected_metrics"]["revenue_impact"] == 8000.0


def test_baseline_without_revenue_keys_is_an_error_not_a_guess():
    """A hand-built baseline must fail loudly rather than fall back to a constant."""
    import pytest
    from mcp_servers.simulation_engine import project_noshow
    with pytest.raises(KeyError):
        project_noshow({"wait_time": 20.0, "utilization": 0.85, "no_show_rate": 0.15},
                       "sms", 0.15, 30)
