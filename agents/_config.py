import os

# Google keeps changing which models are on the free tier (gemini-2.0-flash lost it in 2026).
# Override with the GEMINI_MODEL env var — no code change / redeploy-from-source needed.
# Current free-tier options: gemini-2.5-flash (10 rpm), gemini-2.5-flash-lite (15 rpm, 1000/day).
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
