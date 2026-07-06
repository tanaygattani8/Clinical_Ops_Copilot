import sys, os
import pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["CLINIC_DB_PATH"] = "data/test_brief_history.duckdb"

from data.seed import create_database
from rag.brief_history import store_brief, retrieve_latest, retrieve_by_date, search_briefs


import shutil

@pytest.fixture(scope="module", autouse=True)
def setup_test_db():
    test_db = "data/test_brief_history.duckdb"
    main_db = "data/clinic.duckdb"
    if os.path.exists(test_db):
        os.remove(test_db)
    shutil.copy2(main_db, test_db)
    yield
    if os.path.exists(test_db):
        os.remove(test_db)


def test_store_and_retrieve_latest():
    store_brief("2025-06-01", "# Test Brief\nContent here.", {"version": 1})
    results = retrieve_latest(1)
    assert len(results) >= 1
    assert results[0]["brief_markdown"] == "# Test Brief\nContent here."


def test_retrieve_by_date():
    store_brief("2025-06-02", "# June 2nd Brief\nContent.", {})
    r = retrieve_by_date("2025-06-02")
    assert r is not None
    assert "June 2nd Brief" in r["brief_markdown"]


def test_retrieve_nonexistent_date():
    r = retrieve_by_date("1999-01-01")
    assert r is None


def test_search_briefs():
    results = search_briefs("Content here")
    assert len(results) >= 1
