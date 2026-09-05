"""Guards the one place this project reaches into a third-party internal.

gpt-oss is a reasoning model. Groq returns a `reasoning` field, LiteLLM renames it
to `reasoning_content`, and LiteLLM's Groq message transform copies every non-None
key back into the next request - so turn two of any conversation resends
`reasoning_content` and Groq replies 400, rejecting the field its own model just
produced. Every tool call creates a turn two, so every specialist agent failed
while a bare greeting worked. agents/_config.py strips it at that boundary.

If a litellm upgrade moves or renames that transform, these tests fail rather than
the live demo silently falling back to its no-LLM narrative again.
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

pytest.importorskip("litellm")


def test_reasoning_content_is_stripped_from_assistant_messages():
    import agents._config  # noqa: F401  - importing installs the patch
    from litellm.llms.groq.chat.transformation import GroqChatConfig

    messages = [
        {"role": "user", "content": "what was the no-show rate?"},
        {"role": "assistant", "content": "Let me check.",
         "reasoning_content": "The user wants a metric, so I should call the tool.",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "query_metric", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "0.15"},
    ]
    GroqChatConfig()._transform_messages(messages=messages, model="openai/gpt-oss-120b")

    assistant = [m for m in messages if m.get("role") == "assistant"]
    assert assistant, "the assistant message must survive the transform"
    for message in assistant:
        assert "reasoning_content" not in message
        assert "reasoning" not in message
    # Stripping must not eat the parts Groq actually needs.
    assert assistant[0]["content"] == "Let me check."
    assert assistant[0]["tool_calls"][0]["function"]["name"] == "query_metric"
    # Non-assistant roles are untouched.
    assert messages[0]["content"] == "what was the no-show rate?"
    assert messages[-1]["content"] == "0.15"


def test_model_is_not_the_deprecated_one():
    """llama-3.3-70b-versatile lost free-tier access on 2026-06-17; running it
    means every brief is written by the fallback and nobody notices."""
    from agents._config import MODEL
    name = getattr(MODEL, "model", MODEL)
    assert "llama-3.3-70b-versatile" not in str(name)
