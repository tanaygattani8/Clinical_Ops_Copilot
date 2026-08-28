import os
import sys
import pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mcp_servers.report_builder import (
    build_metric_section, build_executive_brief, build_comparison_table, DISCLAIMER
)


def test_build_metric_section():
    res = build_metric_section(
        "No-Show Rate",
        {"mean": 0.12, "trend": "declining"},
        "Rates improved significantly."
    )
    assert "section_markdown" in res
    assert "No-Show Rate" in res["section_markdown"]
    assert "section_html" in res
    assert len(res["section_markdown"]) > 0


def test_build_executive_brief_has_disclaimer():
    section = build_metric_section("Wait Times", {"avg": 18}, "Stable.")
    res = build_executive_brief("Weekly Brief", "2025-06-28", [section], "")
    assert "brief_markdown" in res
    assert DISCLAIMER in res["brief_markdown"]
    assert "metadata" in res
    assert res["metadata"]["section_count"] == 1


def test_build_comparison_table():
    res = build_comparison_table(
        "Clinic Comparison",
        [{"clinic": "C1", "wait": 12}, {"clinic": "C2", "wait": 18}],
        ["clinic", "wait"]
    )
    assert "table_markdown" in res
    assert "C1" in res["table_markdown"]
    assert "C2" in res["table_markdown"]
    assert "table_html" in res


def test_disclaimer_always_present():
    """Ensure no brief can be built without the disclaimer — Constitution Rule 3."""
    section = build_metric_section("Revenue", {"total": 50000}, "")
    res = build_executive_brief("Test Brief", "2025-01-01", [section], "")
    assert DISCLAIMER in res["brief_markdown"]
    assert DISCLAIMER.split()[1] in res["brief_html"]  # partial match in HTML


def test_caller_supplied_disclaimer_is_appended_not_dropped():
    # The parameter was accepted, documented, and then ignored entirely.
    from mcp_servers.report_builder import build_executive_brief, DISCLAIMER
    out = build_executive_brief("T", "2025-01-01", [], "Reviewed by Compliance.")
    assert "Reviewed by Compliance." in out["brief_markdown"]
    assert out["brief_markdown"].startswith(DISCLAIMER)
