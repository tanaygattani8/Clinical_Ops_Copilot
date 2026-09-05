import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agents._config import MODEL
from agents.narrator import narrator_agent, before_narrator, after_narrator


class MockState:
    def __init__(self):
        self.state = {"session_id": "test_session_789"}


def test_agent_configuration():
    assert narrator_agent.name == "narrator"
    assert narrator_agent.model is MODEL   # provider-agnostic: see agents/_config.py
    assert "executive brief narrator" in narrator_agent.instruction
    assert "Retrieve the last 4 briefs" in narrator_agent.instruction
    assert "disclaimer is auto-injected by the report_builder" in narrator_agent.instruction
    assert narrator_agent.output_key == "narrator_output"
    
    # Check that all core tools are present in the tools list
    tool_names = [getattr(t, "__name__", getattr(t, "name", None)) for t in narrator_agent.tools]
    assert "validate_all" in tool_names
    assert "store_brief" in tool_names
    assert "retrieve_latest" in tool_names


def test_narrator_callbacks_write_audit_logs():
    log_path = "logs/test_narrator_cb.jsonl"
    os.environ["LOG_PATH"] = log_path
    if os.path.exists(log_path):
        os.remove(log_path)
        
    ctx = MockState()
    
    # Run before callback
    before_narrator(ctx)
    from agents._audit import read_audit_log
    logs = read_audit_log(10)
    assert len(logs) == 1
    assert logs[0]["agent"] == "narrator"
    assert logs[0]["action"] == "agent_started"
    assert logs[0]["details"]["session_id"] == "test_session_789"
    
    # Run after callback
    after_narrator(ctx)
    logs = read_audit_log(10)
    assert len(logs) == 2
    assert logs[1]["action"] == "agent_completed"
    
    # Cleanup
    if os.path.exists(log_path):
        os.remove(log_path)
