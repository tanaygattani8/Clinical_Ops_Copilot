import os
import sys
import duckdb
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from data.seed import create_database

def test_seeded_data():
    test_db = "data/test_clinic.duckdb"
    if os.path.exists(test_db):
        os.remove(test_db)
        
    try:
        # 1. Create database
        create_database(test_db)
        assert os.path.exists(test_db)
        
        con = duckdb.connect(test_db)
        
        # 2-5. Verify rows exist
        daily_metrics_count = con.execute("SELECT COUNT(*) FROM daily_metrics").fetchone()[0]
        assert daily_metrics_count > 0
        
        appointments_count = con.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
        assert appointments_count > 0
        
        staffing_count = con.execute("SELECT COUNT(*) FROM staffing").fetchone()[0]
        assert staffing_count > 0
        
        satisfaction_count = con.execute("SELECT COUNT(*) FROM patient_satisfaction").fetchone()[0]
        assert satisfaction_count > 0
        
        # 6. Verify 10 distinct clinic_ids
        distinct_clinics = con.execute("SELECT DISTINCT clinic_id FROM daily_metrics").fetchall()
        assert len(distinct_clinics) == 10
        
        # 7. Tuesday (strftime('%w', date)='2') utilization for CLINIC_01 between 1.10 and 1.30
        avg_util_tuesday = con.execute("""
            SELECT AVG(metric_value) FROM daily_metrics 
            WHERE clinic_id='CLINIC_01' 
              AND metric_name='utilization' 
              AND strftime('%w', date)='2'
        """).fetchone()[0]
        assert avg_util_tuesday is not None
        assert 1.10 <= avg_util_tuesday <= 1.30
        
        # 8. Monday (strftime('%w', date)='1') no_show_rate for CLINIC_03 between 0.35 and 0.45
        avg_no_show_monday = con.execute("""
            SELECT AVG(metric_value) FROM daily_metrics 
            WHERE clinic_id='CLINIC_03' 
              AND metric_name='no_show_rate' 
              AND strftime('%w', date)='1'
        """).fetchone()[0]
        assert avg_no_show_monday is not None
        assert 0.35 <= avg_no_show_monday <= 0.45
        
        con.close()
    finally:
        # 9. Clean up
        if os.path.exists(test_db):
            os.remove(test_db)


def test_appointments_and_daily_metrics_describe_the_same_clinics():
    """The two tables were generated from different models, so the dashboard and the
    appointment drill-down reported contradictory facts about the same clinic."""
    import duckdb, os
    con = duckdb.connect(os.getenv("CLINIC_DB_PATH", "data/clinic.duckdb"), read_only=True)
    try:
        rows = con.execute("""
            WITH a AS (SELECT clinic_id,
                              AVG(CASE WHEN status='no_show' THEN 1.0 ELSE 0 END) ns,
                              AVG(wait_minutes) w
                       FROM appointments WHERE date BETWEEN '2025-01-01' AND '2025-12-31'
                       GROUP BY 1),
                 d AS (SELECT clinic_id,
                              AVG(CASE WHEN metric_name='no_show_rate' THEN metric_value END) ns,
                              AVG(CASE WHEN metric_name='avg_wait' THEN metric_value END) w
                       FROM daily_metrics WHERE date BETWEEN '2025-01-01' AND '2025-12-31'
                       GROUP BY 1)
            SELECT a.ns, d.ns, a.w, d.w FROM a JOIN d USING(clinic_id)
        """).fetchall()
    finally:
        con.close()
    assert rows
    for a_ns, d_ns, a_w, d_w in rows:
        assert abs(a_ns - d_ns) < 0.04, f"no-show rates disagree: {a_ns} vs {d_ns}"
        assert abs(a_w - d_w) < 2.0, f"wait times disagree: {a_w} vs {d_w}"


def test_every_clinic_day_clears_the_aggregate_floor():
    import duckdb, os
    con = duckdb.connect(os.getenv("CLINIC_DB_PATH", "data/clinic.duckdb"), read_only=True)
    try:
        worst = con.execute("SELECT MIN(c) FROM (SELECT COUNT(*) c FROM patient_satisfaction "
                            "GROUP BY date, clinic_id)").fetchone()[0]
    finally:
        con.close()
    assert worst >= 5, f"a clinic-day held only {worst} survey responses"
