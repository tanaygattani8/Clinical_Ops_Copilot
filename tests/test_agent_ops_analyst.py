import os
import sys
import pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agents.ops_analyst import ops_analyst_agent, before_ops_analyst, after_ops_analyst


class MockState:
    def __init__(self):
        self.state = {"session_id": "test_session_123"}


def test_agent_configuration():
    assert ops_analyst_agent.name == "ops_analyst"
    assert ops_analyst_agent.model == "gemini-2.0-flash"
    assert "clinic operations analyst" in ops_analyst_agent.instruction
    assert ops_analyst_agent.output_key == "analyst_output"
    
    # Check that all core tools are present in the tools list
    tool_names = [getattr(t, "__name__", getattr(t, "name", None)) for t in ops_analyst_agent.tools]
    assert "calculate" in tool_names
    assert "percentage_change" in tool_names
    assert "rate_per_unit" in tool_names
    assert "resolve_date_range" in tool_names
    assert "get_comparison_periods" in tool_names
    assert "lookup_metric" in tool_names


def test_ops_analyst_callbacks_write_audit_logs():
    log_path = "logs/test_ops_analyst_cb.jsonl"
    os.environ["LOG_PATH"] = log_path
    if os.path.exists(log_path):
        os.remove(log_path)
        
    ctx = MockState()
    
    # Run before callback
    before_ops_analyst(ctx)
    # Read directly from this specific file
    from agents._audit import read_audit_log
    logs = read_audit_log(10)
    assert len(logs) == 1
    assert logs[0]["agent"] == "ops_analyst"
    assert logs[0]["action"] == "agent_started"
    assert logs[0]["details"]["session_id"] == "test_session_123"
    
    # Run after callback
    after_ops_analyst(ctx)
    logs = read_audit_log(10)
    assert len(logs) == 2
    assert logs[1]["action"] == "agent_completed"
    
    # Cleanup
    if os.path.exists(log_path):
        os.remove(log_path)
