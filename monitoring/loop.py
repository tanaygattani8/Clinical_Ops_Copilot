import os
import asyncio
import duckdb
from agents.orchestrator import run_agent
from agents._audit import audit_log
from rag.brief_history import store_brief

def _get_connection():
    db_path = os.getenv("CLINIC_DB_PATH", "data/clinic.duckdb")
    # Read-only: the scan only SELECTs, and a read-write handle clashes with the
    # web app's read-only connection to the same file.
    return duckdb.connect(db_path, read_only=True)

async def _trigger_agent_run(query: str) -> str:
    """Send a query to the orchestrator agent and get the text response."""
    return await run_agent(query, "monitor_session", "system_monitor")

async def run_daily_brief(force: bool = False, target_date: str = None) -> dict:
    """Check for utilization anomalies and run the agent brief pipeline if found or forced."""
    try:
        # `with` closes the handle on every exit path. The old form closed it by
        # hand and then again in the except clause, where a failure to *open* the
        # database left `con` unbound and raised NameError over the real error.
        with _get_connection() as con:
            # Determine the target date to scan (default to latest date in DB)
            if not target_date:
                latest_row = con.execute("SELECT MAX(date) FROM daily_metrics").fetchone()
                target_date = str(latest_row[0]) if latest_row and latest_row[0] else "2025-06-28"

            # 1. Query clinic_warehouse directly (no LLM, zero API calls)
            # Check utilization > 110% (1.10)
            anomalies = con.execute("""
                SELECT clinic_id, metric_value FROM daily_metrics
                WHERE date = ? AND metric_name = 'utilization' AND metric_value > 1.10
            """, (target_date,)).fetchall()
    except Exception as e:
        # A failed scan is a monitoring failure and has to leave a trace; returning
        # it as data meant start_scheduler dropped it and slept for another day.
        audit_log("monitoring_loop", "monitor_scan_failed", {"error": str(e), "date": target_date})
        return {"status": "error", "error": str(e)}

    # 2. IF no anomalies found and not forced
    if not anomalies and not force:
        audit_log("monitoring_loop", "monitor_check", {"anomalies": 0, "date": target_date})
        return {"status": "skipped", "anomalies": 0, "date": target_date}

    # 3. IF anomalies found or forced
    flagged_clinics = [r[0] for r in anomalies]
    clinic_context = ", ".join(flagged_clinics) if flagged_clinics else "all clinics"

    # force only widens the trigger; it must not throw away what the scan found.
    # Overwriting the query here made a forced run ask for an unfocused brief while
    # still reporting flagged_clinics to the caller.
    query = f"Generate a full executive brief for date {target_date} focusing on flagged clinics: {clinic_context}"
    if force and not flagged_clinics:
        query = f"Generate a full executive brief for date {target_date} for all clinics (forced run)"

    # Fire agent pipeline
    brief_markdown = await _trigger_agent_run(query)
    
    audit_log("monitoring_loop", "daily_brief_completed", {
        "status": "success",
        "date": target_date,
        "flagged_clinics": flagged_clinics,
        "forced": force
    })
    
    # Persist it. A scan that wakes the agents and then discards their output has
    # done the expensive part for nothing.
    stored = False
    try:
        store_brief(target_date, brief_markdown,
                    {"source": "monitoring_loop", "flagged_clinics": flagged_clinics,
                     "forced": force})
        stored = True
    except Exception as e:
        audit_log("monitoring_loop", "brief_store_failed", {"error": str(e), "date": target_date})

    return {
        "status": "success",
        "brief_markdown": brief_markdown,
        "date": target_date,
        "flagged_clinics": flagged_clinics,
        "stored": stored
    }

async def start_scheduler(interval_seconds: int = 86400) -> None:
    """Run the anomaly scan on a fixed interval, forever.

    Args:
        interval_seconds: Seconds between scans (default 24h).
    """
    while True:
        try:
            result = await run_daily_brief(force=False)
            # run_daily_brief reports a failed scan by returning, not raising, so
            # the handler below never saw it and a broken scan looked like a quiet one.
            if result.get("status") == "error":
                audit_log("monitoring_loop", "scheduler_error", {"error": result.get("error")})
        except Exception as e:
            audit_log("monitoring_loop", "scheduler_error", {"error": str(e)})
        await asyncio.sleep(interval_seconds)


if __name__ == "__main__":
    # Runnable on its own: `python monitoring/loop.py` performs one scan and prints
    # the result; `--watch` keeps scanning on the interval; `--force` briefs anyway.
    import sys
    if "--watch" in sys.argv:
        asyncio.run(start_scheduler())
    else:
        print(asyncio.run(run_daily_brief(force="--force" in sys.argv)))
