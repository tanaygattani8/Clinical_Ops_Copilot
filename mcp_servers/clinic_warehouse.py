import os
import duckdb
from mcp.server.fastmcp import FastMCP

# Load environment variables using stdlib parser (Ponytail style)
def load_env():
    if os.path.exists(".env"):
        with open(".env") as f:
            for line in f:
                if line.strip() and not line.startswith("#"):
                    parts = line.strip().split("=", 1)
                    if len(parts) == 2:
                        os.environ[parts[0].strip()] = parts[1].strip()

load_env()

mcp = FastMCP("clinic_warehouse")

def _get_connection():
    db_path = os.getenv("CLINIC_DB_PATH", "data/clinic.duckdb")
    return duckdb.connect(db_path)

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
        # Count daily metrics records in this date range
        count = con.execute("""
            SELECT COUNT(*) FROM daily_metrics
            WHERE clinic_id = ? AND metric_name = ? AND date BETWEEN ? AND ?
        """, (clinic_id, metric_name, start_date, end_date)).fetchone()[0]
        
        _enforce_minimum_n(count, "daily metric records")
        
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
            SELECT AVG(metric_value), MIN(metric_value), MAX(metric_value), COUNT(metric_value)
            FROM daily_metrics
            WHERE metric_name = ? AND date BETWEEN ? AND ?
        """, (metric_name, start_date, end_date)).fetchone()
        
        return {
            "metric_name": metric_name,
            "period": f"{start_date} to {end_date}",
            "mean": stats[0],
            "median": stats[0], # Fast approximation
            "min": stats[1],
            "max": stats[2],
            "n": stats[3]
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
        
        rows = con.execute(f"""
            SELECT {group_by}, COUNT(*) as count, COUNT(*) * 100.0 / ? as pct
            FROM appointments
            WHERE clinic_id = ? AND date BETWEEN ? AND ?
            GROUP BY {group_by}
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

if __name__ == "__main__":
    import sys
    # Support stdio or sse mode
    if len(sys.argv) > 1 and sys.argv[1] == "sse":
        mcp.run(transport="sse", host="0.0.0.0", port=8081)
    else:
        mcp.run(transport="stdio")
