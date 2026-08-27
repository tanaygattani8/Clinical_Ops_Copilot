import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from tools.output_validator import validate_metric, validate_brief_section, validate_all

def test_valid_metric():
    res = validate_metric("no_show_rate", 0.12, (0.0, 1.0))
    assert res["valid"] is True

def test_out_of_range_high():
    res = validate_metric("no_show_rate", 1.5, (0.0, 1.0))
    assert res["valid"] is False
    assert "out of range" in res["reason"]

def test_negative_value():
    res = validate_metric("avg_wait", -5.0, (0.0, 300.0))
    assert res["valid"] is False

def test_valid_brief_section():
    res = validate_brief_section({"title": "Wait Times", "data": {"avg_wait": 25}})
    assert res["valid"] is True

def test_validate_all_partial_failure():
    good = {"title": "Good", "data": {"avg_wait": 25}}
    bad  = {"title": "Bad",  "data": {"no_show_rate": 1.9}}
    res = validate_all([good, bad])
    assert res["all_valid"] is False
    assert "Bad" in res["failed_sections"]
    assert len(res["failed_sections"]) == 1


def test_satisfaction_key_is_actually_checked():
    # The KPI dict uses "satisfaction"; only "satisfaction_score" had a range, so
    # the app's own satisfaction figure was never bounds-checked.
    res = validate_all([{"title": "kpis", "data": {"satisfaction": 9.9}}])
    assert res["all_valid"] is False


def test_unknown_metric_is_reported_not_silently_passed():
    res = validate_all([{"title": "kpis", "data": {"mystery_metric": 42.0}}])
    assert "mystery_metric" in res["unchecked_metrics"]
