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

mcp = FastMCP("simulation_engine")

def _get_connection():
    db_path = os.getenv("CLINIC_DB_PATH", "data/clinic.duckdb")
    return duckdb.connect(db_path)

def _load_baseline(con, clinic_id: str) -> dict:
    # Query latest average wait, utilization, and no-show rate for this clinic
    row = con.execute("""
        SELECT 
            AVG(CASE WHEN metric_name = 'avg_wait' THEN metric_value END),
            AVG(CASE WHEN metric_name = 'utilization' THEN metric_value END),
            AVG(CASE WHEN metric_name = 'no_show_rate' THEN metric_value END)
        FROM daily_metrics
        WHERE clinic_id = ?
    """, (clinic_id,)).fetchone()
    
    return {
        "wait_time": row[0] or 15.0,
        "utilization": row[1] or 0.80,
        "no_show_rate": row[2] or 0.15
    }

def project_staffing(baseline: dict, role: str, delta: int, horizon_days: int) -> dict:
    """Pure projection math for a staffing change. See simulate_staffing_change for semantics."""
    # Simple linear projection model:
    # Adding a physician/nurse decreases wait time and increases capacity
    factor = 0.05 * delta
    proj_wait = max(2.0, baseline["wait_time"] * (1.0 - factor))
    proj_util = min(1.5, max(0.2, baseline["utilization"] * (1.0 - factor * 0.5)))
    proj_throughput = int(30 * horizon_days * (1.0 + factor * 0.3))

    return {
        "scenario": f"staffing_change_{role}_{delta}",
        "projected_metrics": {
            "wait_time": round(proj_wait, 1),
            "utilization": round(proj_util, 2),
            "throughput": proj_throughput
        },
        "confidence_interval": [0.85, 0.95],
        "label": "PROJECTED"
    }

def project_schedule(baseline: dict, slot_duration_minutes: int, slots_per_day: int, horizon_days: int) -> dict:
    """Pure projection math for a schedule change. See simulate_schedule_change for semantics."""
    # Shorter slot duration increases daily capacity but might increase wait time if utilization is high
    capacity_factor = (30.0 / slot_duration_minutes) * (slots_per_day / 20.0)
    proj_capacity = int(slots_per_day * horizon_days)
    proj_wait = max(2.0, baseline["wait_time"] * capacity_factor)
    proj_util = min(1.0, baseline["utilization"] * (1.0 / capacity_factor))

    return {
        "scenario": f"schedule_change_slot_{slot_duration_minutes}_mins",
        "projected_metrics": {
            "daily_capacity": proj_capacity,
            "wait_time": round(proj_wait, 1),
            "utilization": round(proj_util, 2)
        },
        "label": "PROJECTED"
    }

def project_noshow(baseline: dict, intervention: str, expected_reduction_pct: float, horizon_days: int) -> dict:
    """Pure projection math for a no-show intervention. See simulate_noshow_intervention for semantics."""
    # Reducing no-shows reduces the no-show rate, increases slot utilization, and increases revenue
    proj_no_show = max(0.01, baseline["no_show_rate"] * (1.0 - expected_reduction_pct))
    revenue_gain = 150.0 * (baseline["no_show_rate"] - proj_no_show) * 15 * horizon_days
    proj_util = min(1.0, baseline["utilization"] * (1.0 + expected_reduction_pct * 0.1))

    return {
        "intervention": intervention,
        "projected_metrics": {
            "no_show_rate": round(proj_no_show, 3),
            "revenue_impact": round(revenue_gain, 2),
            "slot_utilization": round(proj_util, 2)
        },
        "label": "PROJECTED"
    }

@mcp.tool()
def simulate_staffing_change(clinic_id: str, role: str, delta: int, horizon_days: int) -> dict:
    """Simulate the operational effect of adding or removing staff.

    Args:
        clinic_id: Clinic ID.
        role: Staff role (physician, nurse, ma, admin).
        delta: Number of staff to add (positive) or remove (negative).
        horizon_days: Simulation window in days.
    """
    con = _get_connection()
    try:
        baseline = _load_baseline(con, clinic_id)
        return {"clinic_id": clinic_id, **project_staffing(baseline, role, delta, horizon_days)}
    finally:
        con.close()

@mcp.tool()
def simulate_schedule_change(clinic_id: str, slot_duration_minutes: int, slots_per_day: int, horizon_days: int) -> dict:
    """Simulate the effect of changing schedule slot duration and slots per day.

    Args:
        clinic_id: Clinic ID.
        slot_duration_minutes: Duration of each appt slot in minutes.
        slots_per_day: Number of slots per day.
        horizon_days: Simulation window in days.
    """
    con = _get_connection()
    try:
        baseline = _load_baseline(con, clinic_id)
        return {"clinic_id": clinic_id, **project_schedule(baseline, slot_duration_minutes, slots_per_day, horizon_days)}
    finally:
        con.close()

@mcp.tool()
def simulate_noshow_intervention(clinic_id: str, intervention: str, expected_reduction_pct: float, horizon_days: int) -> dict:
    """Simulate the effect of a no-show intervention (e.g. SMS reminders).

    Args:
        clinic_id: Clinic ID.
        intervention: Name of intervention (e.g. sms_reminders).
        expected_reduction_pct: Expected % reduction in no-shows (e.g. 0.15 for 15%).
        horizon_days: Simulation window in days.
    """
    con = _get_connection()
    try:
        baseline = _load_baseline(con, clinic_id)
        return {"clinic_id": clinic_id, **project_noshow(baseline, intervention, expected_reduction_pct, horizon_days)}
    finally:
        con.close()

def _selfcheck():
    baseline = {"wait_time": 20.0, "utilization": 0.85, "no_show_rate": 0.20}

    staff_up = project_staffing(baseline, "physician", 2, 30)
    assert staff_up["label"] == "PROJECTED"
    assert staff_up["projected_metrics"]["wait_time"] < baseline["wait_time"], "adding staff should lower projected wait"

    staff_down = project_staffing(baseline, "physician", -2, 30)
    assert staff_down["projected_metrics"]["wait_time"] > baseline["wait_time"], "removing staff should raise projected wait"

    sched = project_schedule(baseline, 15, 30, 30)
    assert sched["label"] == "PROJECTED"
    assert sched["projected_metrics"]["daily_capacity"] == 30 * 30

    noshow = project_noshow(baseline, "sms_reminders", 0.25, 30)
    assert noshow["label"] == "PROJECTED"
    assert noshow["projected_metrics"]["no_show_rate"] < baseline["no_show_rate"], "intervention should lower projected no-show rate"
    assert noshow["projected_metrics"]["revenue_impact"] > 0, "reducing no-shows should project positive revenue impact"

    print("OK")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "sse":
        mcp.run(transport="sse", host="0.0.0.0", port=8082)
    elif len(sys.argv) > 1 and sys.argv[1] == "selfcheck":
        _selfcheck()
    else:
        mcp.run(transport="stdio")
