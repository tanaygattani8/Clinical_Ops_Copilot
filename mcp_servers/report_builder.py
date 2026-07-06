import os
from mcp.server.fastmcp import FastMCP

DISCLAIMER = (
    "⚠️ DISCLAIMER: This report is for operational decision support only. "
    "It does not constitute medical diagnosis, treatment recommendation, or clinical advice."
)

mcp = FastMCP("report_builder")


def _render_markdown_section(title: str, data: dict, commentary: str) -> str:
    lines = [f"## {title}", ""]
    for k, v in data.items():
        lines.append(f"- **{k}**: {v}")
    if commentary:
        lines.append("")
        lines.append(f"> {commentary}")
    return "\n".join(lines)


def _render_html_section(title: str, data: dict, commentary: str) -> str:
    rows = "".join(f"<tr><td><b>{k}</b></td><td>{v}</td></tr>" for k, v in data.items())
    note = f"<blockquote>{commentary}</blockquote>" if commentary else ""
    return f"<h2>{title}</h2><table>{rows}</table>{note}"


def _inject_disclaimer(content: str) -> str:
    return f"{DISCLAIMER}\n\n{content}"


@mcp.tool()
def build_metric_section(title: str, data: dict, commentary: str) -> dict:
    """Render a single metric section for inclusion in an executive brief.

    Args:
        title: Section heading.
        data: Key-value dict of metric names to values.
        commentary: Analyst commentary paragraph.
    """
    md = _render_markdown_section(title, data, commentary)
    html = _render_html_section(title, data, commentary)
    return {"section_markdown": md, "section_html": html}


@mcp.tool()
def build_executive_brief(title: str, date: str, sections: list, disclaimer: str) -> dict:
    """Assemble a full executive brief from sections.

    Args:
        title: Brief title.
        date: Report date (YYYY-MM-DD).
        sections: List of section dicts returned by build_metric_section.
        disclaimer: Optional extra disclaimer text (auto-injected if empty).
    """
    import datetime
    body_parts = [f"# {title}", f"**Date:** {date}", ""]
    for s in sections:
        md = s.get("section_markdown", "")
        body_parts.append(md)
        body_parts.append("")

    body_md = "\n".join(body_parts)
    brief_md = _inject_disclaimer(body_md)

    # HTML version
    html_sections = "".join(s.get("section_html", "") for s in sections)
    brief_html = (
        f"<html><body>"
        f"<p><em>{DISCLAIMER}</em></p>"
        f"<h1>{title}</h1><p><b>Date:</b> {date}</p>"
        f"{html_sections}"
        f"</body></html>"
    )

    return {
        "brief_markdown": brief_md,
        "brief_html": brief_html,
        "metadata": {
            "generated_at": datetime.datetime.utcnow().isoformat(),
            "section_count": len(sections)
        }
    }


@mcp.tool()
def build_comparison_table(title: str, rows: list, columns: list) -> dict:
    """Render a comparison table in markdown and HTML.

    Args:
        title: Table heading.
        rows: List of dicts, one per row.
        columns: Ordered list of column keys to display.
    """
    # Markdown
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    data_rows = []
    for r in rows:
        data_rows.append("| " + " | ".join(str(r.get(c, "")) for c in columns) + " |")
    table_md = f"## {title}\n\n" + "\n".join([header, sep] + data_rows)

    # HTML
    th = "".join(f"<th>{c}</th>" for c in columns)
    trs = "".join(
        "<tr>" + "".join(f"<td>{r.get(c, '')}</td>" for c in columns) + "</tr>"
        for r in rows
    )
    table_html = f"<h2>{title}</h2><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"

    return {"table_markdown": table_md, "table_html": table_html}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "sse":
        mcp.run(transport="sse", host="0.0.0.0", port=8083)
    else:
        mcp.run(transport="stdio")
