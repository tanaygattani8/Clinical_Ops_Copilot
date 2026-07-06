import os
from google.adk.agents import Agent
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from mcp.client.stdio import StdioServerParameters

from tools.output_validator import validate_all
from rag.brief_history import store_brief, retrieve_latest
from agents._audit import audit_log

# Determine absolute path for python.exe inside the virtual environment (.venv)
_base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_venv_python = os.path.join(_base_dir, ".venv", "Scripts", "python.exe")
if not os.path.exists(_venv_python):
    _venv_python = "python"

_report_script = os.path.join(_base_dir, "mcp_servers", "report_builder.py")

# Stdio connection parameters for report_builder MCP server
report_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=_venv_python,
            args=[_report_script]
        )
    )
)

def before_narrator(callback_context):
    audit_log("narrator", "agent_started", {"session_id": callback_context.state.get("session_id", "default")})
    return None

def after_narrator(callback_context):
    audit_log("narrator", "agent_completed", {"session_id": callback_context.state.get("session_id", "default")})
    return None

narrator_agent = Agent(
    name="narrator",
    model="gemini-2.0-flash",
    instruction=(
        "You are an executive brief narrator. Your job is to format validated operational reports.\n"
        "1. Retrieve the last 4 briefs using retrieve_latest() at the start of your run.\n"
        "2. Read analyst_output and planner_output from session state.\n"
        "3. Run validate_all() on all data sections before building the brief. Do not include invalid data.\n"
        "4. If previous briefs exist, include a summary of last week's flagged issues in the 'What Happened' section.\n"
        "5. If a clinic (e.g. CLINIC_01 or CLINIC_03) was flagged in previous briefs and is still flagged, state so explicitly.\n"
        "6. Use report_builder tools to build and format the brief.\n"
        "7. Store the completed brief using store_brief().\n"
        "8. The non-diagnostic disclaimer is auto-injected by the report_builder; do not write your own disclaimer."
    ),
    tools=[
        report_toolset,
        validate_all,
        store_brief,
        retrieve_latest
    ],
    output_key="narrator_output",
    before_agent_callback=before_narrator,
    after_agent_callback=after_narrator
)
