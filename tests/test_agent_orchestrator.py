import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from agents._config import MODEL
from agents.orchestrator import root_agent, after_orchestrator


class MockState:
    def __init__(self):
        self.state = {"session_id": "test_session_999"}


def test_orchestrator_configuration():
    assert root_agent.name == "orchestrator"
    assert root_agent.model is MODEL   # provider-agnostic: see agents/_config.py
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
    
    from agents._audit import read_audit_log
    after_orchestrator(ctx)
    logs = read_audit_log(10)
    assert len(logs) == 1
    assert logs[0]["agent"] == "orchestrator"
    assert logs[0]["action"] == "session_completed"
    
    # Cleanup
    if os.path.exists(log_path):
        os.remove(log_path)


@pytest.mark.asyncio
async def test_reasoning_parts_are_not_shown_to_the_user(monkeypatch):
    """gpt-oss is a reasoning model and ADK surfaces its scratchpad as a text part
    flagged `thought`. Concatenating every part printed the model's private
    deliberation in front of its answer on the live site."""
    import agents.orchestrator as orch

    class _Part:
        def __init__(self, text, thought=False):
            self.text, self.thought = text, thought

    class _Event:
        def __init__(self, parts):
            self.content = type("C", (), {"parts": parts})()

    class _FakeRunner:
        app_name = "test"

        def __init__(self, agent=None):
            self.session_service = type("S", (), {
                "create_session": staticmethod(lambda **kw: _noop())})()

        async def run_async(self, **kwargs):
            yield _Event([_Part("The user says hello. I should answer.", thought=True),
                          _Part("Hello! How can I help with clinic operations?")])

    async def _noop():
        return None

    monkeypatch.setattr(orch, "InMemoryRunner", _FakeRunner)
    out = await orch.run_agent("hi", "s1", "u1")
    assert out == "Hello! How can I help with clinic operations?"
    assert "I should answer" not in out
