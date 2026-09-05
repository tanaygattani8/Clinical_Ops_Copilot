import os
import duckdb
from mcp.server.fastmcp import FastMCP

# Minimal .env reader - avoids a dependency just to read five keys.
def load_env():
    """Load .env without overriding anything already set in the environment.

    Three bugs lived here: it overwrote real deployment secrets with stale local
    values, it kept surrounding quotes so KEY="v" became the literal '"v"', and it
    resolved .env against the current working directory rather than the project.
    """
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

load_env()

mcp = FastMCP("clinic_warehouse")

def _get_connection():
    db_path = os.getenv("CLINIC_DB_PATH", "data/clinic.duckdb")
    # Read-only: every tool here is a SELECT, and a read-write handle would clash
    # with the web app's read-only connection to the same file.
    return duckdb.connect(db_path, read_only=True)

def _enforce_minimum_n(count: int, entity_name: str = "records"):
    """Enforces Constitution Rule 2: aggregates of 5+ only."""
    if count < 5:
        raise ValueError(f"Insufficient data: only {count} {entity_name} found. A minimum of 5 is required to protect patient privacy.")

@mcp.tool()
def query_metric(metric_name: str, clinic_id: str, start_date: str, end_date: str) -> dict:
    """Retrieve a single metric time-series for a clinic.
    
    Args:
        metric_name: Metric name (utilization, no_show_rate, avg_wait, revenue_per_visit).
        clinic_id: Clinic ID (CLINIC_01 to CLINIC_10).
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
    """
    con = _get_connection()
    try:
        # Rule 2 protects people, not rows. daily_metrics holds one row per
        # clinic/date/metric, so counting it measured how many metric names exist:
        # one clinic on one day gave 4 and was refused for "privacy", while all ten
        # clinics that same day gave 40 and passed - the gate fired in inverse
        # proportion to the actual re-identification risk. Count the patient-grain
        # table the metrics are derived from instead.
        count = con.execute("""
            SELECT COUNT(*) FROM appointments
            WHERE clinic_id = ? AND date BETWEEN ? AND ?
        """, (clinic_id, start_date, end_date)).fetchone()[0]

        _enforce_minimum_n(count, "appointments")
        
        rows = con.execute("""
            SELECT date, metric_value FROM daily_metrics
            WHERE clinic_id = ? AND metric_name = ? AND date BETWEEN ? AND ?
            ORDER BY date
        """, (clinic_id, metric_name, start_date, end_date)).fetchall()
        
        return {
            "metric_name": metric_name,
            "clinic_id": clinic_id,
            "start_date": start_date,
            "end_date": end_date,
            "values": [{"date": str(r[0]), "value": r[1]} for r in rows]
        }
    finally:
        con.close()

@mcp.tool()
def compare_clinics(metric_name: str, clinic_ids: list[str], date: str) -> dict:
    """Compare a metric across multiple clinics on a specific date.
    
    Args:
        metric_name: Metric name.
        clinic_ids: List of clinic IDs.
        date: Specific date (YYYY-MM-DD).
    """
    # An LLM fills this list; an empty one produced a raw "IN ()" SQL parse error.
    if not clinic_ids:
        raise ValueError("clinic_ids must name at least one clinic.")
    con = _get_connection()
    try:
        # Verify that each clinic has enough appointments on that date to be an aggregate of 5+
        for cid in clinic_ids:
            appt_count = con.execute("""
                SELECT COUNT(*) FROM appointments
                WHERE clinic_id = ? AND date = ?
            """, (cid, date)).fetchone()[0]
            _enforce_minimum_n(appt_count, f"appointments for {cid}")
            
        placeholders = ",".join(["?"] * len(clinic_ids))
        rows = con.execute(f"""
            SELECT clinic_id, metric_value FROM daily_metrics
            WHERE metric_name = ? AND date = ? AND clinic_id IN ({placeholders})
        """, [metric_name, date] + clinic_ids).fetchall()
        
        return {
            "metric_name": metric_name,
            "date": date,
            "comparisons": [{"clinic_id": r[0], "value": r[1]} for r in rows]
        }
    finally:
        con.close()

@mcp.tool()
def summary_stats(metric_name: str, start_date: str, end_date: str) -> dict:
    """Calculate summary statistics for a metric across all clinics in a date range.
    
    Args:
        metric_name: Metric name.
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
    """
    con = _get_connection()
    try:
        count = con.execute("""
            SELECT COUNT(*) FROM daily_metrics
            WHERE metric_name = ? AND date BETWEEN ? AND ?
        """, (metric_name, start_date, end_date)).fetchone()[0]
        
        _enforce_minimum_n(count, "daily metric records")
        
        stats = con.execute("""
            SELECT AVG(metric_value), MEDIAN(metric_value), MIN(metric_value), MAX(metric_value), COUNT(metric_value)
            FROM daily_metrics
            WHERE metric_name = ? AND date BETWEEN ? AND ?
        """, (metric_name, start_date, end_date)).fetchone()

        return {
            "metric_name": metric_name,
            "period": f"{start_date} to {end_date}",
            "mean": stats[0],
            "median": stats[1],
            "min": stats[2],
            "max": stats[3],
            "n": stats[4]
        }
    finally:
        con.close()

@mcp.tool()
def appointment_volume(clinic_id: str, start_date: str, end_date: str, group_by: str) -> dict:
    """Retrieve appointment volume grouped by status or provider.
    
    Args:
        clinic_id: Clinic ID.
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
        group_by: Grouping key (status or provider_id).
    """
    if group_by not in ("status", "provider_id"):
        raise ValueError("group_by must be 'status' or 'provider_id'")
        
    con = _get_connection()
    try:
        total_count = con.execute("""
            SELECT COUNT(*) FROM appointments
            WHERE clinic_id = ? AND date BETWEEN ? AND ?
        """, (clinic_id, start_date, end_date)).fetchone()[0]
        
        _enforce_minimum_n(total_count, "appointments")
        
        # The checked grain must be the returned grain: a passing total said nothing
        # about a provider group holding a single appointment.
        rows = con.execute(f"""
            SELECT {group_by}, COUNT(*) as count, COUNT(*) * 100.0 / ? as pct
            FROM appointments
            WHERE clinic_id = ? AND date BETWEEN ? AND ?
            GROUP BY {group_by}
            HAVING COUNT(*) >= 5
        """, (total_count, clinic_id, start_date, end_date)).fetchall()
        
        return {
            "clinic_id": clinic_id,
            "period": f"{start_date} to {end_date}",
            "groups": [{"group_key": r[0], "count": r[1], "pct": r[2]} for r in rows]
        }
    finally:
        con.close()

@mcp.tool()
def staffing_snapshot(clinic_id: str, date: str) -> dict:
    """Retrieve staffing headcount and FTE snapshot for a clinic on a date.
    
    Args:
        clinic_id: Clinic ID.
        date: Date (YYYY-MM-DD).
    """
    con = _get_connection()
    try:
        # Check staffing rows count
        count = con.execute("""
            SELECT COUNT(*) FROM staffing
            WHERE clinic_id = ? AND date = ?
        """, (clinic_id, date)).fetchone()[0]
        
        # Minimum aggregate check on headcount total
        total_headcount = con.execute("""
            SELECT SUM(headcount) FROM staffing
            WHERE clinic_id = ? AND date = ?
        """, (clinic_id, date)).fetchone()[0] or 0
        
        _enforce_minimum_n(total_headcount, "total staff headcount")
        
        rows = con.execute("""
            SELECT role, headcount, fte FROM staffing
            WHERE clinic_id = ? AND date = ?
        """, (clinic_id, date)).fetchall()
        
        return {
            "clinic_id": clinic_id,
            "date": date,
            "staff": [{"role": r[0], "headcount": r[1], "fte": r[2]} for r in rows]
        }
    finally:
        con.close()

@mcp.tool()
def brief_metrics(clinic_id: str, start_date: str, end_date: str) -> dict:
    """Headline KPIs and anomaly flags for one executive brief.

    Args:
        clinic_id: Clinic ID (CLINIC_01 to CLINIC_10), or "all" to aggregate every clinic.
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
    """
    cf, cp = (" AND clinic_id = ?", [clinic_id]) if clinic_id not in ("all", "", None) else ("", [])
    con = _get_connection()
    try:
        # Rule 2 applies to the brief's own input, so a window too small to be an
        # aggregate never reaches a narrative. Counted over appointments, not
        # daily_metrics - see query_metric for why the metrics grain was wrong.
        count = con.execute(
            "SELECT COUNT(*) FROM appointments WHERE date BETWEEN ? AND ?" + cf,
            [start_date, end_date] + cp).fetchone()[0]
        _enforce_minimum_n(count, "appointments")

        def avg_metric(name):
            r = con.execute(
                "SELECT AVG(metric_value) FROM daily_metrics "
                "WHERE metric_name = ? AND date BETWEEN ? AND ?" + cf,
                [name, start_date, end_date] + cp).fetchone()[0]
            return round(r, 4) if r is not None else None

        sat = con.execute(
            "SELECT AVG(score) FROM patient_satisfaction WHERE date BETWEEN ? AND ?" + cf,
            [start_date, end_date] + cp).fetchone()[0]

        flags = []
        for metric, threshold, severity, tmpl in (
            ("utilization", 1.10, "high", "Utilization peaked at {v:.0%} (over capacity)"),
            ("no_show_rate", 0.30, "medium", "No-show rate reached {v:.0%}"),
        ):
            rows = con.execute(
                "SELECT clinic_id, MAX(metric_value) FROM daily_metrics "
                "WHERE metric_name = ? AND date BETWEEN ? AND ?" + cf +
                " GROUP BY clinic_id HAVING MAX(metric_value) > ? ORDER BY clinic_id",
                [metric, start_date, end_date] + cp + [threshold]).fetchall()
            flags += [{"clinic": cid, "severity": severity, "issue": tmpl.format(v=mx)}
                      for cid, mx in rows]

        return {
            "start": start_date, "end": end_date, "clinic": clinic_id,
            "kpis": {
                "utilization": avg_metric("utilization"),
                "no_show_rate": avg_metric("no_show_rate"),
                "avg_wait": avg_metric("avg_wait"),
                "revenue_per_visit": avg_metric("revenue_per_visit"),
                "satisfaction": round(sat, 4) if sat is not None else None,
            },
            "flags": flags,
        }
    finally:
        con.close()

if __name__ == "__main__":
    mcp.run(transport="stdio")
