import os
import random
import datetime
import duckdb

def create_database(db_path: str) -> None:
    """Creates the DuckDB file and populates all tables with seeded synthetic data."""
    # Ensure directory exists
    dir_name = os.path.dirname(db_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
        
    con = duckdb.connect(db_path)
    try:
        # Create Tables
        con.execute("""
            CREATE TABLE IF NOT EXISTS daily_metrics (
                date DATE,
                clinic_id TEXT,
                metric_name TEXT,
                metric_value FLOAT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS appointments (
                appt_id TEXT,
                date DATE,
                clinic_id TEXT,
                provider_id TEXT,
                status TEXT,
                wait_minutes INT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS staffing (
                date DATE,
                clinic_id TEXT,
                role TEXT,
                headcount INT,
                fte FLOAT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS patient_satisfaction (
                survey_id TEXT,
                date DATE,
                clinic_id TEXT,
                score FLOAT,
                category TEXT
            )
        """)
        
        con.execute("BEGIN TRANSACTION")
        # Clear existing data
        con.execute("DELETE FROM daily_metrics")
        con.execute("DELETE FROM appointments")
        con.execute("DELETE FROM staffing")
        con.execute("DELETE FROM patient_satisfaction")
        
        # Set stable random seed
        random.seed(42)
        
        _generate_daily_metrics(con)
        _generate_appointments(con)
        _generate_staffing(con)
        _generate_satisfaction(con)
        con.execute("COMMIT")
    except Exception as e:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise e
    finally:
        con.close()

def _generate_daily_metrics(con) -> None:
    start_date = datetime.date(2025, 1, 1)
    metrics_data = []
    
    # 10 clinics, 365 days
    for day in range(365):
        current_date = start_date + datetime.timedelta(days=day)
        weekday = current_date.weekday()  # Monday=0, Tuesday=1, ... Sunday=6
        
        for c in range(1, 11):
            clinic_id = f"CLINIC_{c:02d}"
            
            # 1. CLINIC_01: Tuesday utilization consistently 115-125%
            if clinic_id == "CLINIC_01" and weekday == 1:  # Tuesday is 1 in current_date.weekday()
                utilization = random.uniform(1.15, 1.25)
            else:
                utilization = random.uniform(0.70, 0.90)
                
            # 2. CLINIC_03: Monday no_show_rate consistently 38-42%
            if clinic_id == "CLINIC_03" and weekday == 0:  # Monday is 0
                no_show = random.uniform(0.38, 0.42)
            else:
                no_show = random.uniform(0.10, 0.18)
                
            avg_wait = random.uniform(10.0, 20.0)
            rev_per_visit = random.uniform(120.0, 180.0)
            
            metrics_data.append((current_date, clinic_id, "utilization", utilization))
            metrics_data.append((current_date, clinic_id, "no_show_rate", no_show))
            metrics_data.append((current_date, clinic_id, "avg_wait", avg_wait))
            metrics_data.append((current_date, clinic_id, "revenue_per_visit", rev_per_visit))
            
    con.executemany("INSERT INTO daily_metrics VALUES (?, ?, ?, ?)", metrics_data)

def _generate_appointments(con) -> None:
    start_date = datetime.date(2025, 1, 1)
    appts = []
    appt_counter = 1
    
    for day in range(365):
        current_date = start_date + datetime.timedelta(days=day)
        
        for c in range(1, 11):
            clinic_id = f"CLINIC_{c:02d}"
            # ~12 appointments per clinic per day
            num_appts = random.randint(8, 16)
            for _ in range(num_appts):
                provider_id = f"PROVIDER_{random.randint(1, 10):02d}"
                status = random.choice(["completed", "completed", "completed", "completed", "no_show", "cancelled"])
                
                # Baseline avg wait is 15 mins
                clinic_avg = 15
                
                # 3. PROVIDER_07: avg_wait_minutes consistently 20+ above clinic average
                if provider_id == "PROVIDER_07":
                    wait_minutes = clinic_avg + random.randint(20, 30)
                else:
                    wait_minutes = max(2, clinic_avg + random.randint(-8, 8))
                    
                appts.append((f"APPT_{appt_counter:06d}", current_date, clinic_id, provider_id, status, wait_minutes))
                appt_counter += 1
                
    con.executemany("INSERT INTO appointments VALUES (?, ?, ?, ?, ?, ?)", appts)

def _generate_staffing(con) -> None:
    start_date = datetime.date(2025, 1, 1)
    staff = []
    
    for day in range(365):
        current_date = start_date + datetime.timedelta(days=day)
        for c in range(1, 11):
            clinic_id = f"CLINIC_{c:02d}"
            
            roles = [("physician", 2, 2.0), ("nurse", 4, 4.0), ("ma", 3, 3.0), ("admin", 2, 2.0)]
            for role, headcount, fte in roles:
                # Add slight random variations
                var = random.choice([-1, 0, 1]) if role != "physician" else 0
                hc = max(1, headcount + var)
                ft = float(hc)
                staff.append((current_date, clinic_id, role, hc, ft))
                
    con.executemany("INSERT INTO staffing VALUES (?, ?, ?, ?, ?)", staff)

def _generate_satisfaction(con) -> None:
    start_date = datetime.date(2025, 1, 1)
    surveys = []
    survey_counter = 1
    
    for day in range(365):
        current_date = start_date + datetime.timedelta(days=day)
        for c in range(1, 11):
            clinic_id = f"CLINIC_{c:02d}"
            # ~3 surveys per clinic per day
            for _ in range(random.randint(1, 4)):
                category = random.choice(["overall", "wait_time", "provider", "facility"])
                
                # 4. CLINIC_05: afternoon slot utilization below 70%
                # In satisfaction, overall score for clinic 5 might be slightly lower due to waits or other issues
                score = random.uniform(3.5, 4.9) if clinic_id != "CLINIC_05" else random.uniform(2.8, 4.0)
                
                surveys.append((f"SRV_{survey_counter:06d}", current_date, clinic_id, round(score, 1), category))
                survey_counter += 1
                
    con.executemany("INSERT INTO patient_satisfaction VALUES (?, ?, ?, ?, ?)", surveys)

if __name__ == "__main__":
    db_path = os.getenv("CLINIC_DB_PATH", "data/clinic.duckdb")
    create_database(db_path)
    print(f"Database created and seeded successfully at {db_path}.")
