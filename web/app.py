import os
import duckdb
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from agents.orchestrator import root_agent
from agents._audit import audit_log, read_audit_log
from rag.brief_history import retrieve_latest, retrieve_by_date, store_brief
from google.adk.runners import InMemoryRunner
from google.genai import types

app = FastAPI(title="Clinic Operations Copilot")

_STATIC = os.path.join(os.path.dirname(__file__), "static")

# Data spans calendar 2025; default the dashboard to Q1 2025.
_DEFAULT_START = "2025-01-01"
_DEFAULT_END = "2025-03-31"

DISCLAIMER = (
    "For operational decision support only. "
    "Not a medical diagnosis, treatment recommendation, or clinical advice."
)


def _con():
    db_path = os.getenv("CLINIC_DB_PATH", "data/clinic.duckdb")
    return duckdb.connect(db_path, read_only=True)


# ── Static frontend ──
@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(os.path.join(_STATIC, "index.html"))


# ── Fast SQL endpoints (no LLM) ──
@app.get("/api/dashboard")
async def dashboard(start: str = _DEFAULT_START, end: str = _DEFAULT_END):
    con = _con()
    try:
        def metric_avg(name, table="daily_metrics", col="metric_value"):
            row = con.execute(
                f"SELECT AVG({col}) FROM {table} WHERE metric_name = ? AND date BETWEEN ? AND ?"
                if table == "daily_metrics" else
                f"SELECT AVG({col}) FROM {table} WHERE date BETWEEN ? AND ?",
                ([name, start, end] if table == "daily_metrics" else [start, end]),
            ).fetchone()[0]
            return round(row, 4) if row is not None else None

        kpis = {
            "utilization": metric_avg("utilization"),
            "no_show_rate": metric_avg("no_show_rate"),
            "avg_wait": metric_avg("avg_wait"),
            "revenue_per_visit": metric_avg("revenue_per_visit"),
            "satisfaction": metric_avg(None, table="patient_satisfaction", col="score"),
        }

        # Anomaly flags: over-capacity utilization and high no-show clinics.
        flags = []
        util = con.execute(
            "SELECT clinic_id, AVG(metric_value), MAX(metric_value) FROM daily_metrics "
            "WHERE metric_name='utilization' AND date BETWEEN ? AND ? GROUP BY clinic_id "
            "HAVING MAX(metric_value) > 1.10 ORDER BY clinic_id", [start, end]).fetchall()
        for cid, avg, mx in util:
            flags.append({"clinic": cid, "severity": "high",
                          "issue": f"Utilization peaked at {mx*100:.0f}% (over capacity)"})
        nos = con.execute(
            "SELECT clinic_id, MAX(metric_value) FROM daily_metrics "
            "WHERE metric_name='no_show_rate' AND date BETWEEN ? AND ? GROUP BY clinic_id "
            "HAVING MAX(metric_value) > 0.30 ORDER BY clinic_id", [start, end]).fetchall()
        for cid, mx in nos:
            flags.append({"clinic": cid, "severity": "medium",
                          "issue": f"No-show rate reached {mx*100:.0f}%"})

        return JSONResponse({"start": start, "end": end, "kpis": kpis, "flags": flags})
    finally:
        con.close()


@app.get("/api/metric/by-clinic")
async def metric_by_clinic(metric: str = "utilization", start: str = _DEFAULT_START, end: str = _DEFAULT_END):
    con = _con()
    try:
        rows = con.execute(
            "SELECT clinic_id, AVG(metric_value) FROM daily_metrics "
            "WHERE metric_name = ? AND date BETWEEN ? AND ? GROUP BY clinic_id ORDER BY clinic_id",
            [metric, start, end]).fetchall()
        return JSONResponse([{"clinic": r[0], "value": round(r[1], 4)} for r in rows])
    finally:
        con.close()


@app.get("/api/metric/trend")
async def metric_trend(metric: str = "utilization", start: str = _DEFAULT_START, end: str = _DEFAULT_END):
    con = _con()
    try:
        rows = con.execute(
            "SELECT date_trunc('week', date) AS wk, AVG(metric_value) FROM daily_metrics "
            "WHERE metric_name = ? AND date BETWEEN ? AND ? GROUP BY wk ORDER BY wk",
            [metric, start, end]).fetchall()
        return JSONResponse([{"week": str(r[0]), "value": round(r[1], 4)} for r in rows])
    finally:
        con.close()


# ── Agent-powered endpoints ──
async def _run_agent(prompt: str, session_id: str, user_id: str) -> str:
    runner = InMemoryRunner(agent=root_agent)
    await runner.session_service.create_session(
        app_name=runner.app_name, user_id=user_id, session_id=session_id
    )
    msg = types.Content(role="user", parts=[types.Part(text=prompt)])
    text = ""
    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=msg):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if getattr(part, "text", None):
                    text += part.text
    return text


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    message = body.get("message", "")
    session_id = body.get("session_id", "default")
    audit_log("web", "chat_request", {"message_snippet": message[:120], "session_id": session_id})
    try:
        response_text = await _run_agent(message, session_id, "web_user")
        if not response_text.strip():
            response_text = "I couldn't produce a response for that. Try rephrasing, or ask about a specific clinic/metric/period."
    except Exception as e:
        response_text = (f"⚠ The assistant is temporarily unavailable ({type(e).__name__}). "
                         "On the free tier this is usually a rate limit — wait a minute and retry.")
    return JSONResponse({"response": response_text, "session_id": session_id})


def _fallback_narrative(start: str, end: str, data: dict) -> str:
    """Deterministic brief written from the SQL numbers, used when the LLM is unavailable."""
    k, flags = data["kpis"], data["flags"]
    risk = "; ".join(f"{f['clinic']} — {f['issue']}" for f in flags) or \
        "No material anomalies were flagged this period."
    rec = ("Rebalance peak-day demand at the over-capacity site and run targeted no-show outreach."
           if flags else "Maintain current staffing; keep monitoring weekly utilization for drift.")
    return (
        f"## Operations summary — {start} to {end}\n\n"
        f"Across all clinics, average provider utilization ran at **{(k['utilization'] or 0)*100:.1f}%** "
        f"with a no-show rate of **{(k['no_show_rate'] or 0)*100:.1f}%**, an average wait of "
        f"**{(k['avg_wait'] or 0):.1f} minutes**, and revenue per visit of **${(k['revenue_per_visit'] or 0):.0f}**. "
        f"Mean patient satisfaction was **{(k['satisfaction'] or 0):.1f}/5**.\n\n"
        f"**Key risks.** {risk}\n\n"
        f"**Recommended intervention.** {rec}\n\n"
        f"_Figures are aggregate operational indicators for decision support, not clinical measures._"
    )


@app.post("/api/generate-brief")
async def generate_brief(request: Request):
    body = await request.json()
    start = body.get("start", _DEFAULT_START)
    end = body.get("end", _DEFAULT_END)

    # Deterministic data first (never depends on the model).
    dash = await dashboard(start, end)
    import json as _json
    data = _json.loads(bytes(dash.body).decode())
    flag_txt = "; ".join(f"{f['clinic']}: {f['issue']}" for f in data["flags"]) or "no anomalies detected"

    prompt = (
        f"Write a concise executive-brief narrative for clinic operations from {start} to {end}. "
        f"Key metrics: utilization {data['kpis']['utilization']}, no-show rate {data['kpis']['no_show_rate']}, "
        f"average wait {data['kpis']['avg_wait']} minutes, revenue per visit {data['kpis']['revenue_per_visit']}, "
        f"satisfaction {data['kpis']['satisfaction']} of 5. Flagged issues: {flag_txt}. "
        f"Give 2-3 short paragraphs: what happened, the main risks, and one recommended intervention."
    )
    # Never 500 on an LLM hiccup: fall back to a deterministic narrative so a brief always renders + saves.
    try:
        narrative = await _run_agent(prompt, f"brief_{start}_{end}", "brief_generator")
        if not narrative.strip():
            narrative = _fallback_narrative(start, end, data)
    except Exception:
        narrative = _fallback_narrative(start, end, data)

    # Save server-side so history always populates (keyed by end date).
    try:
        store_brief(end, narrative, {"start": start, "end": end, "kpis": data["kpis"], "flags": data["flags"]})
    except Exception:
        pass
    audit_log("web", "brief_generated", {"start": start, "end": end, "flags": len(data["flags"])})

    return JSONResponse({"date": end, "narrative": narrative, **data, "disclaimer": DISCLAIMER})


@app.get("/api/briefs")
async def list_briefs(n: int = 5):
    return JSONResponse(retrieve_latest(n))


@app.get("/api/briefs/{date}")
async def get_brief(date: str):
    brief = retrieve_by_date(date)
    if brief is None:
        return JSONResponse({"error": "Brief not found"}, status_code=404)
    return JSONResponse(brief)


@app.get("/api/audit-log")
async def get_audit_log(n: int = 50):
    return JSONResponse(read_audit_log(n))


@app.get("/health")
async def health():
    return JSONResponse({"status": "healthy"})
