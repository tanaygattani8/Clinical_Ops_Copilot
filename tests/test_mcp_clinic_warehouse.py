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

def test_aggregate_guardrail_violation():
    # If we query a range that has fewer than 5 records, it should raise ValueError
    with pytest.raises(ValueError, match="Insufficient data"):
        query_metric("no_show_rate", "CLINIC_01", "2025-01-01", "2025-01-04")
