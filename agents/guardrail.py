"""
agents/guardrail.py

Constitution Rule 1: Guardrail runs first on every request. No exceptions.
Constitution Rule 2: Block requests for individual patient data.

The guardrail is implemented as a before_model_callback on the root orchestrator.
It uses keyword matching (no LLM call) for speed and reliability, and logs every
decision to the audit trail.
"""

import re
from typing import Optional
from agents._audit import audit_log

# Patterns that indicate a request for individual patient data or clinical diagnosis
_BLOCK_PATTERNS = [
    r"\bpatient\s+(id|#|number)\b",
    r"\bpatient\s+\w+\s+\w+\b",          # "patient John Smith"
    r"\b(diagnosis|diagnose|diagnoses)\b",
    r"\btreatment\s+(plan|recommendation)\b",
    r"\bprescri(be|ption)\b",
    r"\bmedical\s+record\b",
    r"\bindividual\s+patient\b",
    r"\bblood\s+(pressure|glucose|count)\b",
    r"\bpersonal\s+health\b",
    r"\bSSN\b",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _BLOCK_PATTERNS]


def guardrail_check(user_message: str) -> dict:
    """Classify a message as ALLOW or BLOCK.

    Args:
        user_message: Raw user request text.
    """
    for pattern in _COMPILED:
        if pattern.search(user_message):
            result = {
                "decision": "BLOCK",
                "reason": f"Request matches prohibited pattern: '{pattern.pattern}'. "
                          "No individual patient data or clinical diagnoses are returned."
            }
            audit_log("guardrail", "request_blocked", {"message_snippet": user_message[:120], "pattern": pattern.pattern})
            return result

    audit_log("guardrail", "request_allowed", {"message_snippet": user_message[:120]})
    return {"decision": "ALLOW", "reason": "No prohibited content detected."}


def guardrail_callback(callback_context) -> Optional[object]:
    """ADK before_model_callback — blocks prohibited requests before reaching the LLM.

    Returns a Content object to short-circuit the pipeline, or None to allow through.
    """
    # Extract the latest user message text
    user_text = ""
    try:
        for content in callback_context.state.get("_messages", []):
            if hasattr(content, "parts"):
                for part in content.parts:
                    if hasattr(part, "text") and part.text:
                        user_text += part.text + " "
    except Exception:
        pass

    # Fallback: try direct attribute access (ADK version-dependent)
    if not user_text:
        try:
            user_text = str(callback_context.user_content) or ""
        except Exception:
            user_text = ""

    result = guardrail_check(user_text)

    if result["decision"] == "BLOCK":
        # Import here to avoid circular imports at module load
        from google.genai import types
        return types.Content(
            role="model",
            parts=[types.Part(text=f"🚫 Request blocked by compliance guardrail: {result['reason']}")]
        )
    return None  # Allow through
