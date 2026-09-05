import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["LOG_PATH"] = "logs/test_web_audit.jsonl"
os.environ["CLINIC_DB_PATH"] = "data/clinic.duckdb"

from fastapi.testclient import TestClient
from web.app import app


client = TestClient(app)


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "healthy"}


def test_index_returns_html():
    res = client.get("/")
    assert res.status_code == 200
    assert "Clinic Operations Copilot" in res.text
    assert "operational decision support" in res.text  # Disclaimer present


def test_briefs_list():
    res = client.get("/api/briefs?n=3")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_brief_not_found():
    res = client.get("/api/briefs/1900-01-01")
    assert res.status_code == 404


def test_audit_log_returns_list():
    res = client.get("/api/audit-log?n=10")
    assert res.status_code == 200
    assert isinstance(res.json(), list)
