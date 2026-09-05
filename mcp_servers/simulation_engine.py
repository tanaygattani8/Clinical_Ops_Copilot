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

mcp = FastMCP("simulation_engine")

def _get_connection():
    db_path = os.getenv("CLINIC_DB_PATH", "data/clinic.duckdb")
    # Read-only: this server only SELECTs, and a read-write handle clashes with
    # the web app's read-only connection to the same file.
    return duckdb.connect(db_path, read_only=True)

def _load_baseline(con, clinic_id: str, start_date: str = "", end_date: str = "") -> dict:
    """Average wait, utilization and no-show rate for a clinic over a window.

    Without a window this averaged all seven years, so the panel labelled
    "Baseline (current)" ignored whatever period the user had selected.
    """
    window, params = "", [clinic_id]
    if start_date and end_date:
        window = " AND date BETWEEN ? AND ?"
        params += [start_date, end_date]
    row = con.execute("""
        SELECT
            AVG(CASE WHEN metric_name = 'avg_wait' THEN metric_value END),
            AVG(CASE WHEN metric_name = 'utilization' THEN metric_value END),
            AVG(CASE WHEN metric_name = 'no_show_rate' THEN metric_value END),
            AVG(CASE WHEN metric_name = 'revenue_per_visit' THEN metric_value END)
        FROM daily_metrics
        WHERE clinic_id = ?""" + window, params).fetchone()

    # No rows means the clinic id is unknown or has no metrics. Substituting
    # plausible-looking constants here made a projection off invented data
    # indistinguishable from a real one - and `or` also swallowed a genuine 0.0.
    if row is None or row[0] is None:
        raise ValueError(f"No metrics found for clinic '{clinic_id}'; cannot project from an empty baseline.")

    # Appointments per day comes from the appointments table because no metric
    # records it. The revenue projection needs both this and revenue_per_visit;
    # they used to be hardcoded as 15 and 150.0 while this same warehouse said
    # ~18 and ~177, which made the one number the ROI pitch rests on invented.
    volume = con.execute("""
        SELECT COUNT(*) * 1.0 / NULLIF(COUNT(DISTINCT date), 0) FROM appointments
        WHERE clinic_id = ?""" + window, params).fetchone()[0]
    if volume is None:
        raise ValueError(f"No appointments found for clinic '{clinic_id}'; cannot project revenue.")

    return {
        "wait_time": row[0],
        "utilization": row[1],
        "no_show_rate": row[2],
        "revenue_per_visit": row[3],
        "appts_per_day": volume,
    }

# Roles do not move the queue equally: a physician adds appointment capacity
# directly, a nurse partially, an admin barely touches clinical throughput.
# Rough weights, but the role was previously accepted and then ignored, so two
# very different plans produced identical projections.
_ROLE_WEIGHT = {"physician": 1.0, "nurse": 0.6, "admin": 0.2}


def project_staffing(baseline: dict, role: str, delta: int, horizon_days: int) -> dict:
    """Pure projection math for a staffing change. See simulate_staffing_change for semantics."""
    if abs(delta) > 50:
        raise ValueError(f"delta {delta} is outside the supported range of -50..50")
    # Simple linear projection model:
    # Adding a physician/nurse decreases wait time and increases capacity
    factor = 0.05 * delta * _ROLE_WEIGHT.get(role.lower(), 0.5)
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
        # No confidence interval is published. This is a linear heuristic, not a
        # fitted model, and the fixed [0.85, 0.95] that used to sit here was a
        # made-up statistic dressed in statistical language.
        "model": "linear heuristic, not a fitted statistical model",
        "label": "PROJECTED"
    }

def project_schedule(baseline: dict, slot_duration_minutes: int, slots_per_day: int, horizon_days: int) -> dict:
    """Pure projection math for a schedule change. See simulate_schedule_change for semantics."""
    # capacity_factor > 1 means more appointments served per day. Against unchanged
    # demand that shortens the queue and leaves more slots idle, so wait and
    # utilization both fall. Multiplying wait here had extra capacity making
    # patients wait longer.
    capacity_factor = (30.0 / slot_duration_minutes) * (slots_per_day / 20.0)
    proj_wait = max(2.0, baseline["wait_time"] / capacity_factor)
    proj_util = min(1.0, baseline["utilization"] / capacity_factor)

    return {
        "scenario": f"schedule_change_slot_{slot_duration_minutes}_mins",
        "projected_metrics": {
            "daily_capacity": slots_per_day,
            "horizon_capacity": int(slots_per_day * horizon_days),
            "wait_time": round(proj_wait, 1),
            "utilization": round(proj_util, 2)
        },
        "label": "PROJECTED"
    }

def project_noshow(baseline: dict, intervention: str, expected_reduction_pct: float, horizon_days: int) -> dict:
    """Pure projection math for a no-show intervention. See simulate_noshow_intervention for semantics."""
    # A fraction, not percentage points. Passing 15 for "15%" used to be accepted
    # and drove the projection to absurd numbers, so say so rather than guessing
    # which unit the caller meant.
    if not 0.0 <= expected_reduction_pct <= 1.0:
        raise ValueError(
            f"expected_reduction_pct must be a fraction between 0 and 1 "
            f"(got {expected_reduction_pct}; use 0.15 for 15%)")
    # Reducing no-shows reduces the no-show rate, increases slot utilization, and increases revenue
    proj_no_show = max(0.01, baseline["no_show_rate"] * (1.0 - expected_reduction_pct))
    # Revenue per visit and appointments per day come from the warehouse via
    # _load_baseline. Indexed, not .get() with a default: a missing key means the
    # caller built the baseline by hand, and a made-up constant here is exactly
    # the fabricated figure this project exists to catch.
    revenue_gain = (baseline["revenue_per_visit"]
                    * (baseline["no_show_rate"] - proj_no_show)
                    * baseline["appts_per_day"] * horizon_days)
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

# The projection math's runnable check is tests/test_mcp_simulation_engine.py.
if __name__ == "__main__":
    mcp.run(transport="stdio")
