import os
import sys
import shutil
import pytest
from unittest.mock import patch, AsyncMock
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Use a copy of the main DB (much faster than re-seeding)
_TEST_DB = "data/test_monitoring.duckdb"
_MAIN_DB = "data/clinic.duckdb"

os.environ["CLINIC_DB_PATH"] = _TEST_DB
os.environ["LOG_PATH"] = "logs/test_monitoring_audit.jsonl"

from monitoring.loop import run_daily_brief
from agents._audit import read_audit_log
import duckdb


_LOG = "logs/test_monitoring_audit.jsonl"


@pytest.fixture(autouse=True)
def _own_log_path():
    """Claim LOG_PATH per test. Setting it at import time is a shared global that
    whichever module imported last silently won."""
    previous = os.environ.get("LOG_PATH")
    os.environ["LOG_PATH"] = _LOG
    yield
    if previous is None:
        os.environ.pop("LOG_PATH", None)
    else:
        os.environ["LOG_PATH"] = previous


@pytest.fixture(scope="module", autouse=True)
def setup_test_db():
    """Copy the main seeded DB instead of re-seeding from scratch."""
    old_db = os.environ.get("CLINIC_DB_PATH")
    os.environ["CLINIC_DB_PATH"] = _TEST_DB
    
    if os.path.exists(_TEST_DB):
        os.remove(_TEST_DB)
    shutil.copy2(_MAIN_DB, _TEST_DB)
    
    yield
    
    if os.path.exists(_TEST_DB):
        os.remove(_TEST_DB)
        
    if old_db is not None:
        os.environ["CLINIC_DB_PATH"] = old_db
    else:
        os.environ.pop("CLINIC_DB_PATH", None)


@pytest.mark.asyncio
@patch("monitoring.loop._trigger_agent_run", new_callable=AsyncMock)
async def test_monitoring_loop_skipped_on_no_anomalies(mock_trigger):
    os.environ["CLINIC_DB_PATH"] = _TEST_DB
    mock_trigger.return_value = "Mocked Brief Markdown"

    if os.path.exists("logs/test_monitoring_audit.jsonl"):
        os.remove("logs/test_monitoring_audit.jsonl")

    # Set utilization to a safe value on a specific date
    con = duckdb.connect(_TEST_DB)
    con.execute("""
        UPDATE daily_metrics
        SET metric_value = 0.80
        WHERE date = '2025-06-15' AND metric_name = 'utilization'
    """)
    con.close()

    res = await run_daily_brief(force=False, target_date="2025-06-15")

    assert res["status"] == "skipped"
    assert res["anomalies"] == 0
    assert mock_trigger.called is False

    logs = read_audit_log(10)
    assert len(logs) == 1
    assert logs[0]["agent"] == "monitoring_loop"
    assert logs[0]["action"] == "monitor_check"
    assert logs[0]["details"]["anomalies"] == 0


@pytest.mark.asyncio
@patch("monitoring.loop._trigger_agent_run", new_callable=AsyncMock)
async def test_monitoring_loop_success_on_anomaly(mock_trigger):
    os.environ["CLINIC_DB_PATH"] = _TEST_DB
    mock_trigger.return_value = "Mocked Brief Markdown"

    if os.path.exists("logs/test_monitoring_audit.jsonl"):
        os.remove("logs/test_monitoring_audit.jsonl")

    # Inject a high utilization anomaly for CLINIC_01
    con = duckdb.connect(_TEST_DB)
    con.execute("""
        UPDATE daily_metrics
        SET metric_value = 1.20
        WHERE date = '2025-06-15' AND metric_name = 'utilization' AND clinic_id = 'CLINIC_01'
    """)
    con.close()

    res = await run_daily_brief(force=False, target_date="2025-06-15")

    assert res["status"] == "success"
    assert "CLINIC_01" in res["flagged_clinics"]
    assert mock_trigger.called is True

    logs = read_audit_log(10)
    assert len(logs) == 1
    assert logs[0]["agent"] == "monitoring_loop"
    assert logs[0]["action"] == "daily_brief_completed"
    assert "CLINIC_01" in logs[0]["details"]["flagged_clinics"]
