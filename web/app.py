import os
import re
import time
import duckdb
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from agents.orchestrator import run_agent
from agents.narrator import narrator_agent
from agents._audit import audit_log, read_audit_log
from agents.guardrail import guardrail_check
from tools.groundedness import check_groundedness
from tools.output_validator import validate_all
from rag.brief_history import retrieve_latest, retrieve_by_date, store_brief
from mcp_servers import clinic_warehouse, simulation_engine

app = FastAPI(title="Clinic Operations Copilot")

_STATIC = os.path.join(os.path.dirname(__file__), "static")

# The warehouse spans 2019-2025; the dashboard opens on Q1 2025.
_DEFAULT_START = "2025-01-01"
_DEFAULT_END = "2025-03-31"

DISCLAIMER = (
    "For operational decision support only. "
    "Not a medical diagnosis, treatment recommendation, or clinical advice."
)


def _con():
    db_path = os.getenv("CLINIC_DB_PATH", "data/clinic.duckdb")
    return duckdb.connect(db_path, read_only=True)


# Constitution Rule 2: a group built from fewer than this many records can identify
# the people in it, so it is never returned.
MIN_GROUP_N = 5

# The whole deployment shares one model key, so one caller in a loop can spend
# everyone else's quota. Fixed window per client, counted in-process.
# Known limit: single container only - a second replica gets its own allowance.
_RATE_LIMIT, _RATE_WINDOW_S = 12, 60
_hits: dict = {}


def _rate_limited(request: Request, bucket: str) -> bool:
    now = time.time()
    key = (request.client.host if request.client else "unknown", bucket)
    if len(_hits) > 4096:      # bound memory; worst case a few callers get a fresh allowance
        _hits.clear()
    recent = [t for t in _hits.get(key, []) if now - t < _RATE_WINDOW_S]
    _hits[key] = recent + [now]
    return len(recent) >= _RATE_LIMIT


async def _json_body(request: Request) -> dict:
    """Request bodies come from the network; malformed input is a 400, not a 500."""
    try:
        body = await request.json()
    except Exception:
        raise ValueError("Request body must be valid JSON.")
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object.")
    return body


def _clean_text(body: dict, field: str, max_len: int = 2000) -> str:
    value = body.get(field, "")
    if not isinstance(value, str):
        raise ValueError(f"'{field}' must be a string.")
    if len(value) > max_len:
        raise ValueError(f"'{field}' is too long ({len(value)} chars, max {max_len}).")
    return value


# ── Static frontend ──
@app.get("/", response_class=HTMLResponse)
async def index():
    # no-cache = revalidate against the etag every load, don't skip the request.
    # The whole app is this one file, so a browser holding a stale copy shows a
    # frontend that is versions behind the API it is calling.
    return FileResponse(os.path.join(_STATIC, "index.html"),
                        headers={"Cache-Control": "no-cache"})


# ── Fast SQL endpoints (no LLM) ──
@app.get("/api/dashboard")
async def dashboard(start: str = _DEFAULT_START, end: str = _DEFAULT_END, clinic: str = "all"):
    # Same KPIs and anomaly thresholds the brief uses, so read them from the one
    # place that defines them. A second copy of this SQL here meant the dashboard
    # and the brief could drift apart on what counts as "over capacity", and the
    # dashboard copy had no Rule 2 minimum-n gate.
    try:
        return JSONResponse(clinic_warehouse.brief_metrics(clinic, start, end))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


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
    # Blank/"all" means every clinic aggregated, so the filter simply drops out.
    cf, cp = (" AND clinic_id = ?", [clinic]) if clinic not in ("all", "", None) else ("", [])
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
        # Rule 2 applies to the grain actually returned. Checking a total and then
        # returning per-provider groups let a one-appointment provider through,
        # which is exactly the re-identification this rule exists to stop.
        rows = con.execute(
            f"SELECT {group_by}, COUNT(*), AVG(wait_minutes) FROM appointments "
            "WHERE clinic_id = ? AND date BETWEEN ? AND ? "
            "GROUP BY 1 HAVING COUNT(*) >= ? ORDER BY 1",
            [clinic, start, end, MIN_GROUP_N]).fetchall()
        return JSONResponse([{"key": r[0], "count": r[1],
                              "avg_wait": round(r[2], 1) if r[2] is not None else None} for r in rows])
    finally:
        con.close()


@app.get("/api/clinic/satisfaction")
async def clinic_satisfaction(clinic: str, start: str = _DEFAULT_START, end: str = _DEFAULT_END):
    con = _con()
    try:
        # Rule 2: survey categories with a handful of responses are identifying.
        rows = con.execute(
            "SELECT category, AVG(score), COUNT(*) FROM patient_satisfaction "
            "WHERE clinic_id = ? AND date BETWEEN ? AND ? "
            "GROUP BY 1 HAVING COUNT(*) >= ? ORDER BY 1",
            [clinic, start, end, MIN_GROUP_N]).fetchall()
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
    try:
        body = await _json_body(request)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    clinic = body.get("clinic", "")
    scenario = body.get("scenario", "")
    params = body.get("params", {})
    if not isinstance(params, dict):
        return JSONResponse({"error": "'params' must be a JSON object."}, status_code=400)

    if scenario not in _SIM_PROJECTORS:  # whitelist: scenario picks the projector to run
        return JSONResponse({"error": "scenario must be one of: staffing, schedule, noshow"}, status_code=400)

    con = _con()
    try:
        # Honour the period the user picked instead of averaging all seven years.
        baseline = simulation_engine._load_baseline(
            con, clinic, body.get("start", ""), body.get("end", ""))
        projected = _SIM_PROJECTORS[scenario](baseline, params)
    except Exception as e:
        return JSONResponse({"error": f"simulation failed ({type(e).__name__}): {e}"}, status_code=400)
    finally:
        con.close()

    audit_log("web", "simulation_run", {"clinic": clinic, "scenario": scenario, "params": params})
    # Rule 3: the disclaimer travels with every output surface, not just briefs.
    return JSONResponse({"clinic": clinic, "scenario": scenario, "baseline": baseline,
                         "projected": projected, "disclaimer": DISCLAIMER})


# ── Agent-powered endpoints ──
def _retry_after_seconds(detail: str):
    """Pull the wait hint out of a Groq 429 body ('try again in 8.5s' / 'retry after 12')."""
    m = re.search(r"(?:try again in|retry[- ]after[\"':\s]*)\s*"
                  r"(?:([0-9.]+)\s*(?:m|min)[a-z]*\s*)?([0-9.]+)?\s*(s|sec)?", detail, re.I)
    if not m or not (m.group(1) or m.group(2)):
        return None
    # "1m30.5s" carries both parts; reading only the first number told the user to
    # retry 30 seconds early, straight into another 429.
    secs = float(m.group(1) or 0) * 60 + float(m.group(2) or 0)
    return max(1, round(secs))


def _explain_llm_error(e: Exception) -> str:
    """Turn a raw provider exception into something a user can act on.

    The previous version printed only `type(e).__name__`, which hid the provider's
    actual message — including the rate-limit reset hint — and made live failures
    undiagnosable without server logs. The full detail goes to the audit trail;
    the user gets a plain-language version.
    """
    detail = str(e).strip()
    kind = type(e).__name__
    audit_log("web", "chat_error", {"error_type": kind, "detail": detail[:600]})

    lowered = detail.lower()
    if "rate limit" in lowered or "rate_limit" in lowered or "429" in lowered or kind == "RateLimitError":
        wait = _retry_after_seconds(detail)
        when = f"about {wait} second{'s' if wait != 1 else ''}" if wait else "roughly 30 seconds"
        return (
            # No hardcoded ceiling here: 12,000 TPM was llama-3.3-70b's limit and
            # did not survive the migration. Groq's own retry hint below is the
            # number that is actually true at the time it is shown.
            f"⏳ Rate limit reached on the free Groq tier. "
            f"Each question runs several agents, so a fast back-and-forth can hit the cap. "
            f"Please wait {when} and send it again — the dashboard, simulator and briefs "
            f"keep working in the meantime."
        )
    if "invalid api key" in lowered or "authentication" in lowered or kind == "AuthenticationError":
        return ("🔑 The model API key is missing or invalid. Set `GROQ_API_KEY` "
                "(Space → Settings → Variables and secrets) and restart.")
    if "context" in lowered and "length" in lowered:
        return ("📏 That conversation grew past the model's context window. "
                "Start a new chat or ask a narrower question.")
    # Anything else: show the real message so the failure is diagnosable.
    return f"⚠ The assistant hit an error ({kind}): {detail[:300]}"


@app.post("/api/chat")
async def chat(request: Request):
    try:
        body = await _json_body(request)
        message = _clean_text(body, "message")
        session_id = _clean_text(body, "session_id", max_len=128) or "default"
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if _rate_limited(request, "chat"):
        return JSONResponse(
            {"response": f"⏳ Too many requests. This demo allows {_RATE_LIMIT} messages "
                         f"per minute so one visitor cannot exhaust the shared model quota.",
             "session_id": session_id, "disclaimer": DISCLAIMER}, status_code=429)
    audit_log("web", "chat_request", {"message_snippet": message[:120], "session_id": session_id})
    try:
        response_text = await run_agent(message, session_id, "web_user")
        if not response_text.strip():
            response_text = "I couldn't produce a response for that. Try rephrasing, or ask about a specific clinic/metric/period."
    except Exception as e:
        response_text = _explain_llm_error(e)
    # Rule 3: non-diagnostic disclaimer on every output, chat included.
    return JSONResponse({"response": response_text, "session_id": session_id,
                         "disclaimer": DISCLAIMER})


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
    try:
        body = await _json_body(request)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if _rate_limited(request, "brief"):
        return JSONResponse({"error": f"Too many requests - this demo allows {_RATE_LIMIT} "
                                      f"brief generations per minute."}, status_code=429)
    start = body.get("start", _DEFAULT_START)
    end = body.get("end", _DEFAULT_END)
    clinic = body.get("clinic") or "all"
    # start/end/clinic are the request's free-text fields. All three are stored with
    # the brief and replayed to every later viewer, so they are checked at the door.
    if not all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d)) for d in (start, end)):
        return JSONResponse({"error": "Dates must be YYYY-MM-DD."}, status_code=400)
    if clinic != "all" and not re.fullmatch(r"CLINIC_\d{2}", str(clinic)):
        return JSONResponse({"error": "Unknown clinic."}, status_code=400)
    scope = "all clinics" if clinic == "all" else clinic

    # Deterministic data first, read through the warehouse MCP tool so the brief
    # inherits its Rule 2 minimum-n gate instead of querying the tables directly.
    try:
        data = clinic_warehouse.brief_metrics(clinic, start, end)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    # Constitution Rule 4: the validator runs before any number enters a brief.
    # It was only ever registered as a tool the narrator could choose to call, so
    # on this path nothing was bounds-checked. Absurd values stop here.
    validation = validate_all([{"title": "kpis", "data": data["kpis"]}])
    if not validation["all_valid"]:
        audit_log("web", "brief_rejected_by_validator",
                  {"clinic": clinic, "start": start, "end": end, "issues": validation["issues"]})
        return JSONResponse({"error": "Source data failed validation; no brief was generated.",
                             "issues": validation["issues"]}, status_code=422)

    flag_txt = "; ".join(f"{f['clinic']}: {f['issue']}" for f in data["flags"]) or "no anomalies detected"

    prompt = (
        f"Write a concise executive-brief narrative for {scope} from {start} to {end}. "
        f"Key metrics: utilization {data['kpis']['utilization']}, no-show rate {data['kpis']['no_show_rate']}, "
        f"average wait {data['kpis']['avg_wait']} minutes, revenue per visit {data['kpis']['revenue_per_visit']}, "
        f"satisfaction {data['kpis']['satisfaction']} of 5. Flagged issues: {flag_txt}. "
        f"Give 2-3 short paragraphs: what happened, the main risks, and one recommended intervention."
    )
    # Guardrail runs first (Constitution Rule 1) and its verdict is binding here:
    # a BLOCK returns before any model call, and no brief is written.
    gd = guardrail_check(prompt)
    if gd["decision"] == "BLOCK":
        audit_log("web", "brief_blocked", {"clinic": clinic, "reason": gd["reason"]})
        return JSONResponse(
            {"error": gd["reason"],
             "trace": [{"agent": "guardrail", "action": "screen request", "status": "BLOCK"}]},
            status_code=403)

    # Never 500 on an LLM hiccup: fall back to a deterministic narrative so a brief always renders + saves.
    try:
        # The narrator directly, not the orchestrator: the figures were already
        # fetched and validated above, so routing this through root_agent only
        # risked a fan-out to specialists that would re-fetch them - against a
        # token budget the brief path exists to stay inside. This is also what
        # makes the trace below honest: it names the narrator, so the narrator
        # is what runs.
        narrative = await run_agent(prompt, f"brief_{start}_{end}_{clinic}",
                                    "brief_generator", agent=narrator_agent)
        if not narrative.strip():
            raise ValueError("empty narrative")
        source = f"Groq ({os.getenv('GROQ_MODEL', 'groq/openai/gpt-oss-120b')})"
    except Exception as e:
        # Record WHY before degrading. This swallowed the exception silently, so a
        # brief served entirely by the fallback looked identical whether the model
        # was deprecated, the key was wrong, or a tool subprocess died - and the
        # only way to tell from outside was that it never said "Groq". The brief
        # still renders (never 500); the cause now reaches the audit trail.
        audit_log("web", "brief_llm_failed",
                  {"clinic": clinic, "error_type": type(e).__name__,
                   "error": str(e)[:300]})
        narrative = _fallback_narrative(start, end, data)
        source = "deterministic fallback (LLM unavailable)"

    # Evaluator (Constitution Rule 8: separate from the generator) re-checks every cited figure.
    grounded = check_groundedness(narrative, data["kpis"], data["flags"])

    trace = [
        {"agent": "guardrail", "action": "screen request", "status": gd["decision"]},
        {"agent": "clinic_warehouse (MCP)", "action": "brief_metrics · minimum-n gate",
         "status": f"{len(data['flags'])} flags, 5 KPIs"},
        {"agent": "output_validator", "action": "bounds-check every figure",
         "status": f"{validation['total_sections']} section(s) valid"
                   + (f", unchecked: {', '.join(validation['unchecked_metrics'])}"
                      if validation["unchecked_metrics"] else "")},
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


# The audit trail records what users typed, why a request was blocked, and raw
# provider errors. The endpoint is public and unauthenticated, so only the
# non-identifying shape of each event is published: who acted, what they did, and
# counts. Anything free-text stays on disk for a compliance reviewer.
_PUBLIC_DETAIL_KEYS = {
    "decision", "action", "scenario", "clinic", "start", "end", "date",
    "flags", "groundedness", "score", "session_id", "error_type", "status",
}


def _public_entry(entry: dict) -> dict:
    details = entry.get("details") or {}
    safe = {k: v for k, v in details.items() if k in _PUBLIC_DETAIL_KEYS}
    redacted = sorted(set(details) - set(safe))
    if redacted:
        safe["redacted_fields"] = redacted
    return {"timestamp": entry.get("timestamp"), "agent": entry.get("agent"),
            "action": entry.get("action"), "details": safe}


@app.get("/api/audit-log")
async def get_audit_log(n: int = 50):
    n = max(1, min(int(n), 200))
    return JSONResponse([_public_entry(e) for e in read_audit_log(n)])


@app.get("/health")
async def health():
    return JSONResponse({"status": "healthy"})


@app.on_event("startup")
async def _start_monitoring():
    """Run the daily anomaly scan in the background when enabled.

    Off by default: the scan itself is pure SQL and free, but finding an anomaly
    wakes the agents, and an unattended deployment should opt into spending model
    tokens rather than discover it. Set MONITORING_ENABLED=1 to turn it on.
    """
    if os.getenv("MONITORING_ENABLED", "").lower() not in ("1", "true", "yes"):
        return
    import asyncio
    from monitoring.loop import start_scheduler
    interval = int(os.getenv("MONITORING_INTERVAL_SECONDS", "86400"))
    asyncio.create_task(start_scheduler(interval))
    audit_log("web", "monitoring_started", {"interval_seconds": interval})
