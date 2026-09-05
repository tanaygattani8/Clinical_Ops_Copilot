"""ops_analyst — the data specialist.

Reads the warehouse and turns raw rows into rates/comparisons. It reaches the
DuckDB warehouse only through the clinic_warehouse MCP server (below), never with
direct SQL in the agent, so the aggregates-of-5+ rule (Constitution Rule 2) is
enforced at the tool boundary. Its result is published to session state under
`output_key="analyst_output"`, which the narrator later reads — this is how data
flows agent-to-agent without the orchestrator re-passing it by hand.
"""
import os
from google.adk.agents import Agent
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from mcp.client.stdio import StdioServerParameters

from tools.calculator import calculate, percentage_change, rate_per_unit
from tools.date_resolver import resolve_date_range, get_comparison_periods
from rag.metrics_catalog import lookup_metric
from agents._audit import audit_log
from agents._config import MODEL, MCP_PYTHON, MCP_DIR, DB_PATH

# Stdio connection parameters for clinic_warehouse MCP server
warehouse_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=MCP_PYTHON,
            args=[os.path.join(MCP_DIR, "clinic_warehouse.py")],
            env={"CLINIC_DB_PATH": DB_PATH}
        )
    )
)

def before_ops_analyst(callback_context):
    audit_log("ops_analyst", "agent_started", {"session_id": callback_context.state.get("session_id", "default")})
    return None

def after_ops_analyst(callback_context):
    audit_log("ops_analyst", "agent_completed", {"session_id": callback_context.state.get("session_id", "default")})
    return None

ops_analyst_agent = Agent(
    name="ops_analyst",
    model=MODEL,
    instruction=(
        "You are a clinic operations analyst. Your role is to analyze clinic data, "
        "resolve timeframes, perform rate or percentage calculations, and lookup metric "
        "plausibility ranges. Use the warehouse tools to retrieve metrics and staffing numbers. "
        "Always cite the source clinic and time range. Never return data for fewer than 5 entities."
    ),
    tools=[
        warehouse_toolset,
        calculate,
        percentage_change,
        rate_per_unit,
        resolve_date_range,
        get_comparison_periods,
        lookup_metric
    ],
    output_key="analyst_output",
    before_agent_callback=before_ops_analyst,
    after_agent_callback=after_ops_analyst
)
