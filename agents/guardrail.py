"""
agents/guardrail.py

Constitution Rule 1: Guardrail runs first on every request. No exceptions.
Constitution Rule 2: Block requests for individual patient data.

The guardrail is implemented as a before_agent_callback on the root orchestrator
(see agents/orchestrator.py) — before the agent runs at all, not merely before
the model call, so no sub-agent can be reached without passing it. It uses
keyword matching (no LLM call) for speed and reliability, and logs every
decision to the audit trail.
"""

import re
from typing import Optional
from agents._audit import audit_log

# Patterns that indicate a request for individual patient data or clinical diagnosis
_BLOCK_PATTERNS = [
    r"\bpatient\s+(id|#|number)\b",
    # A named individual. The name part is case-SENSITIVE (scoped (?-i:...)), so
    # ordinary aggregate phrasing - "patient satisfaction", "patient wait times" -
    # is not caught; the lookahead covers the title-cased spelling of those too.
    r"\bpatient\s+(?!(?:satisfaction|wait|visit|volume|count|survey|no)\b)"
    r"(?-i:[A-Z][a-z]+\s+[A-Z][a-z]+)",
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


def _user_text(callback_context) -> str:
    """Pull the user's message text out of the ADK callback context.

    Reads the parts properly rather than stringifying the Content object. The old
    path looked up a "_messages" state key that ADK does not set, so it always came
    back empty and fell through to str(user_content) - which happened to contain
    the text inside a repr, meaning the guardrail was screening a debug string.
    """
    content = getattr(callback_context, "user_content", None)
    parts = getattr(content, "parts", None) or []
    text = " ".join(p.text for p in parts if getattr(p, "text", None))
    if text.strip():
        return text
    # Last resort: never screen nothing. An empty string matches no pattern and
    # would silently ALLOW, so fall back to the object's text form.
    return str(content) if content is not None else ""


def guardrail_callback(callback_context) -> Optional[object]:
    """ADK before_agent_callback — blocks prohibited requests before reaching the LLM.

    Returns a Content object to short-circuit the pipeline, or None to allow through.
    """
    result = guardrail_check(_user_text(callback_context))

    if result["decision"] == "BLOCK":
        # Import here to avoid circular imports at module load
        from google.genai import types
        return types.Content(
            role="model",
            parts=[types.Part(text=f"🚫 Request blocked by compliance guardrail: {result['reason']}")]
        )
    return None  # Allow through
