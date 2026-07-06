import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from tools.calculator import calculate, percentage_change, rate_per_unit

def test_multiply():
    res = calculate("100 * 0.15", "no-show cost")
    assert res["result"] == 15.0
    assert res["validated"] is True

def test_percentage_decrease():
    res = percentage_change(100, 85)
    assert res["change_pct"] == -15.0
    assert res["direction"] == "decrease"

def test_percentage_increase():
    res = percentage_change(80, 100)
    assert res["change_pct"] == 25.0
    assert res["direction"] == "increase"

def test_rate_per_unit():
    res = rate_per_unit(500, 40, "visits/provider")
    assert res["rate"] == 12.5

def test_division_by_zero_returns_error():
    res = calculate("1/0", "div-by-zero")
    assert res["validated"] is False
    assert "error" in res
    assert res["result"] is None
