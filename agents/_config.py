import os

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
    MODEL = LiteLlm(model=os.getenv("GROQ_MODEL", "groq/llama-3.3-70b-versatile"))
