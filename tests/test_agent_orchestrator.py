import os
import sys
import pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agents.orchestrator import root_agent, before_orchestrator, after_orchestrator


class MockState:
    def __init__(self):
        self.state = {"session_id": "test_session_999"}


def test_orchestrator_configuration():
    assert root_agent.name == "orchestrator"
    assert root_agent.model == "gemini-2.0-flash"
    assert "Clinic Operations Copilot orchestrator" in root_agent.instruction
    
    # Verify sub-agents are wired correctly
    sub_agent_names = [sa.name for sa in root_agent.sub_agents]
    assert "ops_analyst" in sub_agent_names
    assert "planner" in sub_agent_names
    assert "narrator" in sub_agent_names


def test_orchestrator_callbacks():
    log_path = "logs/test_orchestrator_cb.jsonl"
    os.environ["LOG_PATH"] = log_path
    if os.path.exists(log_path):
        os.remove(log_path)
        
    ctx = MockState()
    
    # Run before_orchestrator
    before_orchestrator(ctx)
    from agents._audit import read_audit_log
    logs = read_audit_log(10)
    assert len(logs) == 1
    assert logs[0]["agent"] == "orchestrator"
    assert logs[0]["action"] == "session_started"
    
    # Run after_orchestrator
    after_orchestrator(ctx)
    logs = read_audit_log(10)
    assert len(logs) == 2
    assert logs[1]["action"] == "session_completed"
    
    # Cleanup
    if os.path.exists(log_path):
        os.remove(log_path)
