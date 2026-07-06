import os
import asyncio
import duckdb
from agents.orchestrator import root_agent
from google.adk.runners import InMemoryRunner
from google.genai import types
from agents._audit import audit_log

def _get_connection():
    db_path = os.getenv("CLINIC_DB_PATH", "data/clinic.duckdb")
    return duckdb.connect(db_path)

async def _trigger_agent_run(query: str) -> str:
    """Send a query to the orchestrator agent and get the text response."""
    runner = InMemoryRunner(agent=root_agent)
    user_msg = types.Content(role="user", parts=[types.Part(text=query)])
    
    # Run the orchestrator
    response_text = ""
    async for event in runner.run_async(
        user_id="system_monitor",
        session_id="monitor_session",
        new_message=user_msg
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    response_text += part.text
    return response_text

async def run_daily_brief(force: bool = False, target_date: str = None) -> dict:
    """Check for utilization anomalies and run the agent brief pipeline if found or forced."""
    con = _get_connection()
    try:
        # Determine the target date to scan (default to latest date in DB)
        if not target_date:
            latest_row = con.execute("SELECT MAX(date) FROM daily_metrics").fetchone()
            if latest_row and latest_row[0]:
                target_date = str(latest_row[0])
            else:
                target_date = "2025-06-28"  # Fallback
        
        # 1. Query clinic_warehouse directly (no LLM, zero API calls)
        # Check utilization > 110% (1.10)
        anomalies = con.execute("""
            SELECT clinic_id, metric_value FROM daily_metrics
            WHERE date = ? AND metric_name = 'utilization' AND metric_value > 1.10
        """, (target_date,)).fetchall()
        
        con.close()
    except Exception as e:
        if con:
            con.close()
        return {"status": "error", "error": str(e)}
        
    # 2. IF no anomalies found and not forced
    if not anomalies and not force:
        audit_log("monitoring_loop", "monitor_check", {"anomalies": 0, "date": target_date})
        return {"status": "skipped", "anomalies": 0, "date": target_date}
        
    # 3. IF anomalies found or forced
    flagged_clinics = [r[0] for r in anomalies]
    clinic_context = ", ".join(flagged_clinics) if flagged_clinics else "all clinics"
    
    query = f"Generate a full executive brief for date {target_date} focusing on flagged clinics: {clinic_context}"
    if force:
        query = f"Generate a full executive brief for date {target_date} for all clinics (forced run)"
        
    # Fire agent pipeline
    brief_markdown = await _trigger_agent_run(query)
    
    audit_log("monitoring_loop", "daily_brief_completed", {
        "status": "success",
        "date": target_date,
        "flagged_clinics": flagged_clinics,
        "forced": force
    })
    
    return {
        "status": "success",
        "brief_markdown": brief_markdown,
        "date": target_date,
        "flagged_clinics": flagged_clinics
    }

async def start_scheduler() -> None:
    """Start the standard library asyncio sleep loop to run every 24 hours."""
    while True:
        try:
            await run_daily_brief(force=False)
        except Exception as e:
            audit_log("monitoring_loop", "scheduler_error", {"error": str(e)})
        # Sleep for 24 hours
        await asyncio.sleep(86400)
