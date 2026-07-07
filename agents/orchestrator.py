"""Root orchestrator — the single entry point for every request.

Design: this agent owns *routing only*, never analysis. It delegates to three
specialists (ops_analyst / planner / narrator) so that each concern stays
isolated and independently auditable. The guardrail runs as a `before_agent_callback`
here, which guarantees Constitution Rule 1 (guardrail-first) structurally — no
request can reach a sub-agent without passing it. For a full executive brief the
order is fixed: gather data -> simulate -> narrate, so the narrator always writes
from validated numbers rather than the raw prompt.
"""
from google.adk.agents import Agent
from agents.guardrail import guardrail_callback
from agents.ops_analyst import ops_analyst_agent
from agents.planner import planner_agent
from agents.narrator import narrator_agent
from agents._audit import audit_log
from agents._config import MODEL

def before_orchestrator(callback_context):
    audit_log("orchestrator", "session_started", {"session_id": callback_context.state.get("session_id", "default")})
    return None

def after_orchestrator(callback_context):
    audit_log("orchestrator", "session_completed", {"session_id": callback_context.state.get("session_id", "default")})
    return None

# Guardrail is wired as before_agent_callback (not a tool) so it CANNOT be skipped:
# ADK runs it before the model on every invocation. after_orchestrator closes the
# audit trail for the session (Constitution Rule 6: every action logged).
root_agent = Agent(
    name="orchestrator",
    model=MODEL,
    instruction=(
        "You are the Clinic Operations Copilot orchestrator. Your job is to route user requests:\n"
        "- For data queries, metrics, comparisons, or snapshots: route to ops_analyst.\n"
        "- For what-if scenarios, ROI simulations, or capacity planning: route to planner.\n"
        "- For generating executive briefs or reports: first call ops_analyst to gather data, "
        "then call planner to run simulations/projections, and finally call narrator to compile "
        "and validate the final brief.\n"
        "Always follow these instructions. Respect the guardrail and never try to bypass it."
    ),
    sub_agents=[
        ops_analyst_agent,
        planner_agent,
        narrator_agent
    ],
    before_agent_callback=guardrail_callback,
    after_agent_callback=after_orchestrator
)
