import os
import json
import duckdb
import datetime


def _get_connection():
    db_path = os.getenv("CLINIC_DB_PATH", "data/clinic.duckdb")
    return duckdb.connect(db_path)


def _ensure_table():
    con = _get_connection()
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS brief_history (
                date DATE,
                brief_markdown TEXT,
                metadata JSON,
                created_at TIMESTAMP
            )
        """)
    finally:
        con.close()


def store_brief(date: str, brief_markdown: str, metadata: dict) -> None:
    """Store a generated executive brief.

    Args:
        date: Brief date (YYYY-MM-DD).
        brief_markdown: Full brief content in markdown.
        metadata: Arbitrary metadata dict (JSON-serialisable).
    """
    _ensure_table()
    clinic = (metadata or {}).get("clinic", "all")
    con = _get_connection()
    try:
        # Replace an existing brief for the same period + clinic (not across clinics).
        con.execute(
            "DELETE FROM brief_history WHERE date = ? "
            "AND COALESCE(json_extract_string(metadata, '$.clinic'), 'all') = ?",
            (date, clinic))
        con.execute(
            "INSERT INTO brief_history VALUES (?, ?, ?, ?)",
            (date, brief_markdown, json.dumps(metadata), datetime.datetime.utcnow())
        )
    finally:
        con.close()


def retrieve_latest(n: int = 5) -> list:
    """Return the N most recent stored briefs.

    Args:
        n: Maximum number of briefs to return.
    """
    _ensure_table()
    con = _get_connection()
    try:
        rows = con.execute(
            "SELECT date, brief_markdown, metadata FROM brief_history ORDER BY created_at DESC LIMIT ?", (n,)
        ).fetchall()
        return [{"date": str(r[0]), "brief_markdown": r[1], "metadata": json.loads(r[2]) if r[2] else {}} for r in rows]
    finally:
        con.close()


def retrieve_by_date(date: str) -> dict:
    """Return the brief for a specific date or None.

    Args:
        date: Date string (YYYY-MM-DD).
    """
    _ensure_table()
    con = _get_connection()
    try:
        row = con.execute(
            "SELECT date, brief_markdown, metadata FROM brief_history WHERE date = ? LIMIT 1", (date,)
        ).fetchone()
        if row is None:
            return None
        return {"date": str(row[0]), "brief_markdown": row[1], "metadata": json.loads(row[2]) if row[2] else {}}
    finally:
        con.close()


def search_briefs(query: str, n: int = 3) -> list:
    """Simple text search over stored briefs.

    Args:
        query: Search string.
        n: Maximum number of results.
    """
    _ensure_table()
    con = _get_connection()
    try:
        rows = con.execute(
            "SELECT date, brief_markdown, metadata FROM brief_history WHERE brief_markdown LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{query}%", n)
        ).fetchall()
        return [{"date": str(r[0]), "brief_markdown": r[1], "metadata": json.loads(r[2]) if r[2] else {}} for r in rows]
    finally:
        con.close()
