import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agents.guardrail import guardrail_check


def test_block_individual_patient_name():
    os.environ["LOG_PATH"] = "logs/test_guardrail_cb.jsonl"
    res = guardrail_check("What is patient John Smith's diagnosis?")
    assert res["decision"] == "BLOCK"


def test_block_patient_id():
    res = guardrail_check("Give me the blood pressure reading for patient ID 12345")
    assert res["decision"] == "BLOCK"


def test_allow_aggregate_query():
    res = guardrail_check("What is the average wait time across all clinics?")
    assert res["decision"] == "ALLOW"


def test_allow_operational_query():
    res = guardrail_check("Show me the no-show rate for clinic 01 last quarter")
    assert res["decision"] == "ALLOW"


def test_audit_log_written():
    log_path = "logs/test_guardrail_cb.jsonl"
    os.environ["LOG_PATH"] = log_path
    if os.path.exists(log_path):
        os.remove(log_path)
    
    guardrail_check("What is patient Jane's diagnosis?")
    
    from agents._audit import read_audit_log
    logs = read_audit_log(10)
    assert len(logs) >= 1
    assert logs[0]["agent"] == "guardrail"
