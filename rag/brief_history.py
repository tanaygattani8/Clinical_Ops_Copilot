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
            # Naive UTC on purpose: created_at is a plain TIMESTAMP, and DuckDB
            # converts an aware value to *local* time on the way in, which would
            # order new rows hours behind the ones already stored.
            (date, brief_markdown, json.dumps(metadata),
             datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None))
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
            # One row per (date, clinic), so without an order this returned an
            # arbitrary clinic's brief for a shared date.
            "SELECT date, brief_markdown, metadata FROM brief_history "
            "WHERE date = ? ORDER BY created_at DESC LIMIT 1", (date,)
        ).fetchone()
        if row is None:
            return None
        return {"date": str(row[0]), "brief_markdown": row[1], "metadata": json.loads(row[2]) if row[2] else {}}
    finally:
        con.close()
