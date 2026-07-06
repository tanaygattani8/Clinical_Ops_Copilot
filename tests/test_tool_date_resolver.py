import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from tools.date_resolver import resolve_date_range, get_comparison_periods

def test_last_week():
    res = resolve_date_range("last week", "2025-06-28")
    assert res["start_date"] == "2025-06-16"
    assert res["end_date"] == "2025-06-22"

def test_q2_2025():
    res = resolve_date_range("Q2 2025")
    assert res["start_date"] == "2025-04-01"
    assert res["end_date"] == "2025-06-30"

def test_past_30_days():
    res = resolve_date_range("past 30 days", "2025-06-28")
    assert res["start_date"] == "2025-05-30"
    assert res["end_date"] == "2025-06-28"

def test_comparison_periods_last_month():
    res = get_comparison_periods("last month", "2025-06-28")
    assert res["current"]["start"] == "2025-05-01"
    assert res["previous"]["start"] == "2025-04-01"

def test_invalid_reference_no_exception():
    res = resolve_date_range("invalid_gibberish")
    assert "error" in res
    assert res["start_date"] is None
