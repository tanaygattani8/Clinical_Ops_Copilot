import os
import duckdb
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from agents.orchestrator import root_agent
from agents._audit import audit_log, read_audit_log
from agents.guardrail import guardrail_check
from tools.groundedness import check_groundedness
from rag.brief_history import retrieve_latest, retrieve_by_date, store_brief
from mcp_servers import simulation_engine
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
def _clinic_clause(clinic):
    """Returns (sql_fragment, params) — empty when 'all'/blank so all clinics aggregate."""
    return (" AND clinic_id = ?", [clinic]) if clinic not in ("all", "", None) else ("", [])


@app.get("/api/dashboard")
async def dashboard(start: str = _DEFAULT_START, end: str = _DEFAULT_END, clinic: str = "all"):
    con = _con()
    cf, cp = _clinic_clause(clinic)
    try:
        def avg_metric(name):
            r = con.execute(
                "SELECT AVG(metric_value) FROM daily_metrics "
                "WHERE metric_name = ? AND date BETWEEN ? AND ?" + cf,
                [name, start, end] + cp).fetchone()[0]
            return round(r, 4) if r is not None else None

        sat = con.execute(
            "SELECT AVG(score) FROM patient_satisfaction WHERE date BETWEEN ? AND ?" + cf,
            [start, end] + cp).fetchone()[0]

        kpis = {
            "utilization": avg_metric("utilization"),
            "no_show_rate": avg_metric("no_show_rate"),
            "avg_wait": avg_metric("avg_wait"),
            "revenue_per_visit": avg_metric("revenue_per_visit"),
            "satisfaction": round(sat, 4) if sat is not None else None,
        }

        # Anomaly flags: over-capacity utilization and high no-show (scoped to clinic if set).
        flags = []
        util = con.execute(
            "SELECT clinic_id, MAX(metric_value) FROM daily_metrics "
            "WHERE metric_name='utilization' AND date BETWEEN ? AND ?" + cf +
            " GROUP BY clinic_id HAVING MAX(metric_value) > 1.10 ORDER BY clinic_id",
            [start, end] + cp).fetchall()
        for cid, mx in util:
            flags.append({"clinic": cid, "severity": "high",
                          "issue": f"Utilization peaked at {mx*100:.0f}% (over capacity)"})
        nos = con.execute(
            "SELECT clinic_id, MAX(metric_value) FROM daily_metrics "
            "WHERE metric_name='no_show_rate' AND date BETWEEN ? AND ?" + cf +
            " GROUP BY clinic_id HAVING MAX(metric_value) > 0.30 ORDER BY clinic_id",
            [start, end] + cp).fetchall()
        for cid, mx in nos:
            flags.append({"clinic": cid, "severity": "medium",
                          "issue": f"No-show rate reached {mx*100:.0f}%"})

        return JSONResponse({"start": start, "end": end, "clinic": clinic, "kpis": kpis, "flags": flags})
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
async def metric_trend(metric: str = "utilization", start: str = _DEFAULT_START,
                       end: str = _DEFAULT_END, clinic: str = "all"):
    con = _con()
    cf, cp = _clinic_clause(clinic)
    try:
        rows = con.execute(
            "SELECT date_trunc('week', date) AS wk, AVG(metric_value) FROM daily_metrics "
            "WHERE metric_name = ? AND date BETWEEN ? AND ?" + cf + " GROUP BY wk ORDER BY wk",
            [metric, start, end] + cp).fetchall()
        return JSONResponse([{"week": str(r[0]), "value": round(r[1], 4)} for r in rows])
    finally:
        con.close()


@app.get("/api/clinic/appointments")
async def clinic_appointments(clinic: str, group_by: str = "status",
                              start: str = _DEFAULT_START, end: str = _DEFAULT_END):
    if group_by not in ("status", "provider_id"):  # whitelist: value is interpolated into SQL
        return JSONResponse({"error": "group_by must be 'status' or 'provider_id'"}, status_code=400)
    con = _con()
    try:
        rows = con.execute(
            f"SELECT {group_by}, COUNT(*), AVG(wait_minutes) FROM appointments "
            "WHERE clinic_id = ? AND date BETWEEN ? AND ? GROUP BY 1 ORDER BY 1",
            [clinic, start, end]).fetchall()
        return JSONResponse([{"key": r[0], "count": r[1],
                              "avg_wait": round(r[2], 1) if r[2] is not None else None} for r in rows])
    finally:
        con.close()


@app.get("/api/clinic/satisfaction")
async def clinic_satisfaction(clinic: str, start: str = _DEFAULT_START, end: str = _DEFAULT_END):
    con = _con()
    try:
        rows = con.execute(
            "SELECT category, AVG(score), COUNT(*) FROM patient_satisfaction "
            "WHERE clinic_id = ? AND date BETWEEN ? AND ? GROUP BY 1 ORDER BY 1",
            [clinic, start, end]).fetchall()
        return JSONResponse([{"category": r[0], "score": round(r[1], 2), "n": r[2]} for r in rows])
    finally:
        con.close()


_SIM_PROJECTORS = {
    "staffing": lambda baseline, p: simulation_engine.project_staffing(
        baseline, p.get("role", "physician"), int(p.get("delta", 0)), int(p.get("horizon_days", 30))),
    "schedule": lambda baseline, p: simulation_engine.project_schedule(
        baseline, int(p.get("slot_duration_minutes", 20)), int(p.get("slots_per_day", 24)), int(p.get("horizon_days", 30))),
    "noshow": lambda baseline, p: simulation_engine.project_noshow(
        baseline, p.get("intervention", "sms_reminders"), float(p.get("expected_reduction_pct", 0.15)), int(p.get("horizon_days", 30))),
}


@app.post("/api/simulate")
async def simulate(request: Request):
    body = await request.json()
    clinic = body.get("clinic", "")
    scenario = body.get("scenario", "")
    params = body.get("params", {})

    if scenario not in _SIM_PROJECTORS:  # whitelist: scenario picks the projector to run
        return JSONResponse({"error": "scenario must be one of: staffing, schedule, noshow"}, status_code=400)

    con = _con()
    try:
        baseline = simulation_engine._load_baseline(con, clinic)
        projected = _SIM_PROJECTORS[scenario](baseline, params)
    except Exception as e:
        return JSONResponse({"error": f"simulation failed ({type(e).__name__}): {e}"}, status_code=400)
    finally:
        con.close()

    audit_log("web", "simulation_run", {"clinic": clinic, "scenario": scenario, "params": params})
    return JSONResponse({"clinic": clinic, "scenario": scenario, "baseline": baseline, "projected": projected})


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
    clinic = data.get("clinic", "all")
    scope = "all clinics" if clinic in ("all", "", None) else clinic
    risk = "; ".join(f"{f['clinic']} — {f['issue']}" for f in flags) or \
        "No material anomalies were flagged this period."
    rec = ("Rebalance peak-day demand at the over-capacity site and run targeted no-show outreach."
           if flags else "Maintain current staffing; keep monitoring weekly utilization for drift.")
    return (
        f"## Operations summary — {scope}, {start} to {end}\n\n"
        f"For {scope}, average provider utilization ran at **{(k['utilization'] or 0)*100:.1f}%** "
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
    clinic = body.get("clinic", "all")
    scope = "all clinics" if clinic in ("all", "", None) else clinic

    # Deterministic data first (never depends on the model).
    dash = await dashboard(start, end, clinic)
    import json as _json
    data = _json.loads(bytes(dash.body).decode())
    flag_txt = "; ".join(f"{f['clinic']}: {f['issue']}" for f in data["flags"]) or "no anomalies detected"

    prompt = (
        f"Write a concise executive-brief narrative for {scope} from {start} to {end}. "
        f"Key metrics: utilization {data['kpis']['utilization']}, no-show rate {data['kpis']['no_show_rate']}, "
        f"average wait {data['kpis']['avg_wait']} minutes, revenue per visit {data['kpis']['revenue_per_visit']}, "
        f"satisfaction {data['kpis']['satisfaction']} of 5. Flagged issues: {flag_txt}. "
        f"Give 2-3 short paragraphs: what happened, the main risks, and one recommended intervention."
    )
    # Guardrail runs first (Constitution Rule 1) — real ALLOW/BLOCK decision on this path too.
    gd = guardrail_check(prompt)

    # Never 500 on an LLM hiccup: fall back to a deterministic narrative so a brief always renders + saves.
    try:
        narrative = await _run_agent(prompt, f"brief_{start}_{end}_{clinic}", "brief_generator")
        if not narrative.strip():
            raise ValueError("empty narrative")
        source = f"Groq ({os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')})"
    except Exception:
        narrative = _fallback_narrative(start, end, data)
        source = "deterministic fallback (LLM unavailable)"

    # Evaluator (Constitution Rule 8: separate from the generator) re-checks every cited figure.
    grounded = check_groundedness(narrative, data["kpis"], data["flags"])

    trace = [
        {"agent": "guardrail", "action": "screen request", "status": gd["decision"]},
        {"agent": "ops_analyst", "action": "query warehouse (SQL)",
         "status": f"{len(data['flags'])} flags, 5 KPIs"},
        {"agent": "narrator", "action": "compose brief", "status": source},
        {"agent": "evaluator", "action": "groundedness check",
         "status": f"{grounded['score']}% · {grounded['verified']}/{grounded['figures']} figures verified"},
        {"agent": "brief_history", "action": "store brief", "status": f"{end} · {scope}"},
    ]

    # Save server-side so history always populates (dedup keyed by end date + clinic).
    try:
        store_brief(end, narrative, {"start": start, "end": end, "clinic": clinic,
                                     "kpis": data["kpis"], "flags": data["flags"],
                                     "groundedness": grounded})
    except Exception:
        pass
    audit_log("web", "brief_generated", {"start": start, "end": end, "clinic": clinic,
                                         "flags": len(data["flags"]), "groundedness": grounded["score"]})

    return JSONResponse({"date": end, "narrative": narrative, **data,
                         "groundedness": grounded, "trace": trace, "disclaimer": DISCLAIMER})


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
