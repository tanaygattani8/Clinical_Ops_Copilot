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

# Root Agent orchestration
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
