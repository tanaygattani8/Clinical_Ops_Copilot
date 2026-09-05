import os
import json
import datetime


def _log_path() -> str:
    path = os.getenv("LOG_PATH", "logs/audit.jsonl")
    # dirname("audit.jsonl") is "", and makedirs("") raises - so a bare filename
    # took down every endpoint that logs.
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    return path


def audit_log(agent_name: str, action: str, details: dict) -> None:
    """Append a single audit entry to the audit log.

    Args:
        agent_name: Name of the agent or component logging this event.
        action: Short action label (e.g. 'request_screened', 'brief_generated').
        details: Arbitrary JSON-serialisable dict with context.
    """
    entry = {
        # utcnow() is deprecated and returns a naive value the "Z" only claimed to
        # describe; now(timezone.utc) is genuinely UTC and says so in the string.
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "agent": agent_name,
        "action": action,
        "details": details
    }
    with open(_log_path(), "a") as f:
        f.write(json.dumps(entry) + "\n")


def read_audit_log(n: int = 50) -> list:
    """Return the last N entries from the audit log.

    Args:
        n: Maximum number of entries to return.
    """
    path = _log_path()
    if not os.path.exists(path):
        return []
    with open(path) as f:
        lines = f.readlines()
    return [json.loads(l) for l in lines[-n:] if l.strip()]
