"""planner — the what-if specialist.

Runs staffing / schedule / no-show simulations via the simulation_engine MCP
server. Every projection it returns is labeled PROJECTED (Constitution Rule 5):
simulated numbers must never be mistakable for actual, measured data. Its output
is published under `output_key="planner_output"` for the narrator to fold into
the brief alongside the analyst's real figures.
"""
import os
from google.adk.agents import Agent
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from mcp.client.stdio import StdioServerParameters

from tools.calculator import calculate, percentage_change, rate_per_unit
from tools.date_resolver import resolve_date_range, get_comparison_periods
from agents._audit import audit_log
from agents._config import MODEL, MCP_PYTHON, MCP_DIR, DB_PATH

# Stdio connection parameters for simulation_engine MCP server
simulation_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=MCP_PYTHON,
            args=[os.path.join(MCP_DIR, "simulation_engine.py")],
            env={"CLINIC_DB_PATH": DB_PATH}
        )
    )
)

def before_planner(callback_context):
    audit_log("planner", "agent_started", {"session_id": callback_context.state.get("session_id", "default")})
    return None

def after_planner(callback_context):
    audit_log("planner", "agent_completed", {"session_id": callback_context.state.get("session_id", "default")})
    return None

planner_agent = Agent(
    name="planner",
    model=MODEL,
    instruction=(
        "You are a clinic operations planner. Your role is to run operational what-if simulations. "
        "Always label any simulation results as PROJECTED (e.g. 'Projected Wait Time: 12 minutes'). "
        "Use the calculator for estimation, and date_resolver for planning periods. "
        "Present plans with clear outcomes, estimates, and confidence parameters."
    ),
    tools=[
        simulation_toolset,
        calculate,
        percentage_change,
        rate_per_unit,
        resolve_date_range,
        get_comparison_periods
    ],
    output_key="planner_output",
    before_agent_callback=before_planner,
    after_agent_callback=after_planner
)
