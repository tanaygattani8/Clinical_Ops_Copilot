import json
import os

_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "metrics_catalog.json")


def load_metrics_catalog() -> dict:
    """Load the metrics catalog from JSON. Returns dict with 'metrics' key."""
    with open(_CATALOG_PATH) as f:
        return json.load(f)


def lookup_metric(name: str) -> dict:
    """Return a single metric entry by name, or None if not found.

    Args:
        name: Metric name (e.g. 'no_show_rate').
    """
    catalog = load_metrics_catalog()
    for m in catalog["metrics"]:
        if m["name"] == name:
            return m
    return None


def get_metrics_by_category(category: str) -> list:
    """Return all metrics belonging to a category.

    Args:
        category: Category name (access, efficiency, financial, quality).
    """
    catalog = load_metrics_catalog()
    return [m for m in catalog["metrics"] if m.get("category") == category]
