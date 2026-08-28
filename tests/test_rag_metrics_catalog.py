import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from rag.metrics_catalog import load_metrics_catalog, lookup_metric

def test_load_catalog():
    cat = load_metrics_catalog()
    assert "metrics" in cat
    assert len(cat["metrics"]) >= 5

def test_lookup_existing():
    m = lookup_metric("no_show_rate")
    assert m is not None
    assert m["name"] == "no_show_rate"

def test_lookup_nonexistent():
    m = lookup_metric("does_not_exist")
    assert m is None
