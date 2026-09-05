import os
import sys
import pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Set environment variable to use test database for tests
os.environ["CLINIC_DB_PATH"] = "data/test_warehouse.duckdb"

from data.seed import create_database
from mcp_servers.clinic_warehouse import (
    query_metric, compare_clinics, summary_stats, appointment_volume, staffing_snapshot
)

@pytest.fixture(scope="module", autouse=True)
def setup_test_db():
    test_db = "data/test_warehouse.duckdb"
    if os.path.exists(test_db):
        os.remove(test_db)
    create_database(test_db)
    yield
    if os.path.exists(test_db):
        os.remove(test_db)

def test_query_metric():
    res = query_metric("no_show_rate", "CLINIC_01", "2025-01-01", "2025-01-31")
    assert "values" in res
    assert len(res["values"]) >= 5
    assert res["metric_name"] == "no_show_rate"

def test_compare_clinics():
    res = compare_clinics("avg_wait", ["CLINIC_01", "CLINIC_02"], "2025-06-15")
    assert "comparisons" in res
    assert len(res["comparisons"]) == 2

def test_summary_stats():
    res = summary_stats("utilization", "2025-01-01", "2025-12-31")
    assert "mean" in res
    assert res["n"] >= 5

def test_appointment_volume():
    res = appointment_volume("CLINIC_01", "2025-01-01", "2025-03-31", "status")
    assert "groups" in res
    assert len(res["groups"]) >= 1

def test_staffing_snapshot():
    res = staffing_snapshot("CLINIC_01", "2025-06-15")
    assert "staff" in res
    assert len(res["staff"]) >= 1

def test_compare_clinics_rejects_an_empty_list():
    # Empty produced a raw "IN ()" SQL parse error instead of a usable message.
    with pytest.raises(ValueError, match="at least one clinic"):
        compare_clinics("avg_wait", [], "2025-06-15")


def test_aggregate_guardrail_violation():
    # A range covering fewer than 5 appointments cannot be returned as an aggregate.
    with pytest.raises(ValueError, match="Insufficient data"):
        query_metric("no_show_rate", "CLINIC_01", "1990-01-01", "1990-01-04")


def test_minimum_n_counts_people_not_metric_rows():
    """The Rule 2 gate counted daily_metrics rows, which is one per clinic/date/
    metric. One clinic on one day gave 4 and was refused as a privacy risk, while
    all ten clinics that same day gave 40 and passed - the refusal fired in
    inverse proportion to the real risk, and it 400'd the dashboard."""
    from mcp_servers import clinic_warehouse as w
    one_day = w.brief_metrics("CLINIC_01", "2025-01-01", "2025-01-01")
    assert one_day["kpis"]["utilization"] is not None
    # The gate must still fire on a window that genuinely covers too few people.
    import pytest
    with pytest.raises(ValueError, match="appointments"):
        w.brief_metrics("CLINIC_01", "1990-01-01", "1990-01-02")
