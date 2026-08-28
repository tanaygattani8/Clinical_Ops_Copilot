import os
import sys
import pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agents._config import MODEL
from agents.planner import planner_agent, before_planner, after_planner


class MockState:
    def __init__(self):
        self.state = {"session_id": "test_session_456"}


def test_agent_configuration():
    assert planner_agent.name == "planner"
    assert planner_agent.model is MODEL   # provider-agnostic: see agents/_config.py
    assert "clinic operations planner" in planner_agent.instruction
    assert "Always label any simulation results as PROJECTED" in planner_agent.instruction
    assert planner_agent.output_key == "planner_output"
    
    # Check that all core tools are present in the tools list
    tool_names = [getattr(t, "__name__", getattr(t, "name", None)) for t in planner_agent.tools]
    assert "calculate" in tool_names
    assert "percentage_change" in tool_names
    assert "rate_per_unit" in tool_names
    assert "resolve_date_range" in tool_names
    assert "get_comparison_periods" in tool_names


def test_planner_callbacks_write_audit_logs():
    log_path = "logs/test_planner_cb.jsonl"
    os.environ["LOG_PATH"] = log_path
    if os.path.exists(log_path):
        os.remove(log_path)
        
    ctx = MockState()
    
    # Run before callback
    before_planner(ctx)
    from agents._audit import read_audit_log
    logs = read_audit_log(10)
    assert len(logs) == 1
    assert logs[0]["agent"] == "planner"
    assert logs[0]["action"] == "agent_started"
    assert logs[0]["details"]["session_id"] == "test_session_456"
    
    # Run after callback
    after_planner(ctx)
    logs = read_audit_log(10)
    assert len(logs) == 2
    assert logs[1]["action"] == "agent_completed"
    
    # Cleanup
    if os.path.exists(log_path):
        os.remove(log_path)
