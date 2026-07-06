METRIC_RANGES = {
    "no_show_rate":      (0.0, 1.0),
    "avg_wait":          (0.0, 300.0),
    "utilization":       (0.0, 1.5),   # allow over 100% (capacity pressure)
    "revenue_per_visit": (0.0, 10000.0),
    "satisfaction_score": (0.0, 5.0),
    "patient_satisfaction": (0.0, 5.0),
}


def validate_metric(name: str, value: float, expected_range: tuple) -> dict:
    """Check that a metric value is within a plausible range.

    Args:
        name: Metric name.
        value: Metric value to check.
        expected_range: (min, max) tuple.
    """
    lo, hi = expected_range
    valid = lo <= value <= hi
    reason = "OK" if valid else f"Value {value} is out of range [{lo}, {hi}]"
    return {"name": name, "value": value, "valid": valid, "reason": reason}


def validate_brief_section(section: dict) -> dict:
    """Validate a single report section's data values against known ranges.

    Args:
        section: Dict with 'title' (str) and 'data' (dict of metric_name -> value).
    """
    title = section.get("title", "unknown")
    data = section.get("data", {})
    issues = []

    for metric_name, value in data.items():
        if not isinstance(value, (int, float)):
            continue
        if metric_name in METRIC_RANGES:
            lo, hi = METRIC_RANGES[metric_name]
            if not (lo <= value <= hi):
                issues.append(f"{metric_name}={value} out of range [{lo}, {hi}]")

    return {"section_title": title, "valid": len(issues) == 0, "issues": issues}


def validate_all(sections: list) -> dict:
    """Batch-validate all sections in a brief.

    Args:
        sections: List of section dicts (each with 'title' and 'data').
    """
    details = [validate_brief_section(s) for s in sections]
    failed = [d["section_title"] for d in details if not d["valid"]]
    return {
        "all_valid": len(failed) == 0,
        "total_sections": len(sections),
        "failed_sections": failed,
        "details": details
    }
