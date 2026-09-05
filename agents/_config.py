import os

# Where the three MCP servers and the warehouse live. All three agents spawned
# an MCP server with their own copy of this block; one copy means one place to
# be wrong about the interpreter or the database path.
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# The stdio servers are subprocesses, so they need an interpreter that has the
# project's dependencies. Falls back to whatever "python" resolves to (the
# container, where there is no .venv).
MCP_PYTHON = os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe")
if not os.path.exists(MCP_PYTHON):
    MCP_PYTHON = "python"
DB_PATH = os.getenv("CLINIC_DB_PATH", os.path.join(BASE_DIR, "data", "clinic.duckdb"))
MCP_DIR = os.path.join(BASE_DIR, "mcp_servers")

# Which LLM provider to use. Groq is the default: free, no credit card, 30 req/min.
# Set LLM_PROVIDER=gemini to use Gemini natively instead.
_PROVIDER = os.getenv("LLM_PROVIDER", "groq").lower()

if _PROVIDER == "gemini":
    # Gemini model id string — ADK talks to Gemini natively. Needs GOOGLE_API_KEY.
    # gemini-2.0-flash lost the free tier in 2026; use a 2.5 model.
    MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
else:
    # Groq via ADK's LiteLLM adapter. Needs GROQ_API_KEY and the litellm package.
    from google.adk.models.lite_llm import LiteLlm

    # llama-3.3-70b-versatile was deprecated for free/developer tiers on
    # 2026-06-17; gpt-oss-120b is one of Groq's two named migration targets
    # (qwen/qwen3.6-27b is the other).
    #
    # Groq's free tier caps tokens-per-minute, and a single chat turn fans out
    # across orchestrator + sub-agents, so a normal conversational pace can brush
    # that ceiling and return HTTP 429. The exact TPM figure differs per model and
    # has to be read from console.groq.com/settings/limits rather than assumed -
    # do not copy the old model's number onto this one. num_retries makes LiteLLM
    # honor Groq's `retry-after` header and re-issue the call, turning a hard
    # failure into a slightly slower answer. Extra kwargs are forwarded straight
    # to litellm.acompletion by the ADK adapter.
    MODEL = LiteLlm(
        model=os.getenv("GROQ_MODEL", "groq/openai/gpt-oss-120b"),
        num_retries=int(os.getenv("LLM_NUM_RETRIES", "3")),
    )

    # gpt-oss is a reasoning model: Groq returns a `reasoning` field, LiteLLM
    # renames it to `reasoning_content` on the way in, and LiteLLM's Groq
    # message transform then copies every non-None key back out - so the second
    # turn of any conversation resends `reasoning_content` and Groq answers
    # 400 "property 'reasoning_content' is unsupported". It rejects the field
    # its own model produced. Every tool call creates that second turn, so every
    # specialist agent failed while a bare greeting worked.
    #
    # Stripped here, at the one boundary all four agents share. This reaches into
    # a third-party internal, so tests/test_agent_config.py asserts the behaviour
    # and will fail loudly if a litellm upgrade moves it.
    from litellm.llms.groq.chat.transformation import GroqChatConfig

    _REASONING_KEYS = ("reasoning_content", "reasoning")
    _groq_transform = GroqChatConfig._transform_messages

    def _drop_reasoning(self, messages, model, is_async=False):
        for message in messages:
            get = message.get if isinstance(message, dict) else None
            if get is None or get("role") != "assistant":
                continue
            for key in _REASONING_KEYS:
                message.pop(key, None)
        return _groq_transform(self, messages, model, is_async)

    GroqChatConfig._transform_messages = _drop_reasoning
