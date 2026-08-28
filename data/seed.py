import os
import csv
import math
import random
import datetime
import tempfile
import duckdb


def _bulk_insert(con, table, rows):
    """Load rows via a temp CSV + COPY. DuckDB's executemany is per-row and
    pathologically slow for 100k+ rows; COPY is C-level bulk load."""
    if not rows:
        return
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        with open(path, "w", newline="") as f:
            csv.writer(f).writerows(rows)  # date objects -> ISO 'YYYY-MM-DD' via str()
        con.execute(
            f"COPY {table} FROM '{path.replace(chr(92), '/')}' "
            "(FORMAT CSV, HEADER FALSE, DATEFORMAT '%Y-%m-%d')"
        )
    finally:
        os.remove(path)

# Span is env-overridable so the container / tests can shrink it if needed.
START_DATE = datetime.date(int(os.getenv("SEED_START_YEAR", "2019")), 1, 1)
END_DATE = datetime.date(int(os.getenv("SEED_END_YEAR", "2025")), 12, 31)
TOTAL_DAYS = (END_DATE - START_DATE).days + 1


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _clinic_profiles():
    """Each clinic gets a stable baseline personality (size, busyness, mood)
    so the 10 clinics aren't statistically identical. Dedicated RNG => order-independent."""
    rng = random.Random(7)
    profiles = {}
    for c in range(1, 11):
        profiles[f"CLINIC_{c:02d}"] = {
            "util": rng.uniform(0.66, 0.86),
            "no_show": rng.uniform(0.09, 0.16),
            "wait": rng.uniform(11.0, 18.0),
            "rev": rng.uniform(120.0, 175.0),
            "sat": rng.uniform(3.8, 4.6),
            "volume": rng.uniform(0.8, 1.5),
        }
    return profiles


PROFILES = _clinic_profiles()


def _season(date):
    """Yearly cycle: +1 mid-January (flu season, busy), -1 mid-summer (quiet)."""
    doy = date.timetuple().tm_yday
    return math.cos((doy - 15) / 365.0 * 2 * math.pi)


def _year_factor(date, rate):
    """Compounding year-over-year trend (inflation, growth) relative to START."""
    return (1 + rate) ** (date.year - START_DATE.year)


def _covid(date):
    """Spring–summer 2020 operational shock: fewer visits, more no-shows, lower use."""
    return datetime.date(2020, 3, 15) <= date <= datetime.date(2020, 6, 30)


def create_database(db_path: str) -> None:
    """Creates the DuckDB file and populates all tables with seeded synthetic data."""
    dir_name = os.path.dirname(db_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    con = duckdb.connect(db_path)
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS daily_metrics (
                date DATE, clinic_id TEXT, metric_name TEXT, metric_value FLOAT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS appointments (
                appt_id TEXT, date DATE, clinic_id TEXT, provider_id TEXT,
                status TEXT, wait_minutes INT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS staffing (
                date DATE, clinic_id TEXT, role TEXT, headcount INT, fte FLOAT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS patient_satisfaction (
                survey_id TEXT, date DATE, clinic_id TEXT, score FLOAT, category TEXT
            )
        """)

        con.execute("BEGIN TRANSACTION")
        con.execute("DELETE FROM daily_metrics")
        con.execute("DELETE FROM appointments")
        con.execute("DELETE FROM staffing")
        con.execute("DELETE FROM patient_satisfaction")

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
    rows = []
    for day in range(TOTAL_DAYS):
        d = START_DATE + datetime.timedelta(days=day)
        weekday = d.weekday()  # Mon=0 .. Sun=6
        season = _season(d)
        covid = _covid(d)

        for cid, p in PROFILES.items():
            # Utilization: profile baseline + winter surge + slow yearly creep + noise.
            if cid == "CLINIC_01" and weekday == 1:  # anomaly: Tuesday over-capacity
                util = random.uniform(1.15, 1.25)
            else:
                util = p["util"] * _year_factor(d, 0.01) + 0.06 * season + random.uniform(-0.05, 0.05)
                if covid:
                    util *= 0.55
                util = _clamp(util, 0.35, 1.08)

            # No-show: baseline + winter/weather bump + noise.
            if cid == "CLINIC_03" and weekday == 0:  # anomaly: Monday no-shows
                no_show = random.uniform(0.38, 0.42)
            else:
                no_show = p["no_show"] + 0.03 * max(0.0, season) + random.uniform(-0.03, 0.03)
                if covid:
                    no_show += 0.12
                no_show = _clamp(no_show, 0.04, 0.35)

            avg_wait = _clamp(p["wait"] + 3 * season + random.uniform(-3, 3), 5, 40)
            rev = p["rev"] * _year_factor(d, 0.04) + random.uniform(-8, 8)

            rows.append((d, cid, "utilization", util))
            rows.append((d, cid, "no_show_rate", no_show))
            rows.append((d, cid, "avg_wait", avg_wait))
            rows.append((d, cid, "revenue_per_visit", rev))

    _bulk_insert(con, "daily_metrics", rows)


def _generate_appointments(con) -> None:
    appts = []
    counter = 1
    for day in range(TOTAL_DAYS):
        d = START_DATE + datetime.timedelta(days=day)
        season = _season(d)
        covid = _covid(d)

        weekday = d.weekday()

        for cid, p in PROFILES.items():
            base = 12 * p["volume"] * _year_factor(d, 0.03) * (1 + 0.15 * season)
            if covid:
                base *= 0.5
            # min 6 keeps every clinic-day an aggregate of 5+ (privacy rule).
            num_appts = max(6, int(random.gauss(base, 2)))

            # Draw status and wait from the SAME model _generate_daily_metrics uses.
            # These used to be a flat 1-in-6 no-show and a wait that ignored the
            # clinic profile, so the two tables described different clinics: the
            # dashboard and the appointment drill-down disagreed by up to 54%,
            # and the seeded CLINIC_03 anomaly was missing here entirely.
            if cid == "CLINIC_03" and weekday == 0:      # anomaly: Monday no-shows
                no_show_p = random.uniform(0.38, 0.42)
            else:
                no_show_p = p["no_show"] + 0.03 * max(0.0, season) + random.uniform(-0.03, 0.03)
                if covid:
                    no_show_p += 0.12
                no_show_p = _clamp(no_show_p, 0.04, 0.35)
            cancel_p = 0.05
            day_wait = _clamp(p["wait"] + 3 * season + random.uniform(-3, 3), 5, 40)

            for _ in range(num_appts):
                provider_id = f"PROVIDER_{random.randint(1, 10):02d}"
                roll = random.random()
                status = ("no_show" if roll < no_show_p
                          else "cancelled" if roll < no_show_p + cancel_p
                          else "completed")
                # PROVIDER_07 keeps its long-wait anomaly; the other nine sit slightly
                # below the daily mean so the blend still lands on day_wait.
                centre = day_wait + 22 if provider_id == "PROVIDER_07" else day_wait - 2.5
                wait = max(2, int(random.gauss(centre, 3)))
                appts.append((f"APPT_{counter:07d}", d, cid, provider_id, status, wait))
                counter += 1

    _bulk_insert(con, "appointments", appts)


def _generate_staffing(con) -> None:
    staff = []
    for day in range(TOTAL_DAYS):
        d = START_DATE + datetime.timedelta(days=day)
        for cid, p in PROFILES.items():
            size = p["volume"]  # bigger clinics carry more staff
            roles = [
                ("physician", round(2 * size), 0.0),
                ("nurse", round(4 * size), 0.0),
                ("ma", round(3 * size), 0.0),
                ("admin", max(1, round(2 * size)), 0.0),
            ]
            for role, base_hc, _ in roles:
                var = random.choice([-1, 0, 1]) if role != "physician" else 0
                hc = max(1, base_hc + var)
                staff.append((d, cid, role, hc, float(hc)))

    _bulk_insert(con, "staffing", staff)


def _generate_satisfaction(con) -> None:
    surveys = []
    counter = 1
    for day in range(TOTAL_DAYS):
        d = START_DATE + datetime.timedelta(days=day)
        season = _season(d)
        for cid, p in PROFILES.items():
            # CLINIC_05 runs chronically lower (anomaly); winter (busy) dips everyone slightly.
            mean = (3.3 if cid == "CLINIC_05" else p["sat"]) - 0.08 * season
            # 5-12 responses a day keeps every clinic-day above the aggregate-of-5
            # floor, so the privacy rule never has to blank an ordinary chart.
            for _ in range(random.randint(5, 12)):
                category = random.choice(["overall", "wait_time", "provider", "facility"])
                score = round(_clamp(random.gauss(mean, 0.4), 1.0, 5.0), 1)
                surveys.append((f"SRV_{counter:07d}", d, cid, score, category))
                counter += 1

    _bulk_insert(con, "patient_satisfaction", surveys)


if __name__ == "__main__":
    db_path = os.getenv("CLINIC_DB_PATH", "data/clinic.duckdb")
    create_database(db_path)

    # Self-check: span + seeded anomalies must survive generation.
    con = duckdb.connect(db_path, read_only=True)
    yrs = con.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM daily_metrics").fetchone()
    c01 = con.execute("SELECT MAX(metric_value) FROM daily_metrics "
                      "WHERE clinic_id='CLINIC_01' AND metric_name='utilization'").fetchone()[0]
    c03 = con.execute("SELECT MAX(metric_value) FROM daily_metrics "
                      "WHERE clinic_id='CLINIC_03' AND metric_name='no_show_rate'").fetchone()[0]
    con.close()
    assert yrs[0].year == START_DATE.year and yrs[1].year == END_DATE.year, "date span wrong"
    assert c01 > 1.10, "CLINIC_01 over-utilization anomaly missing"
    assert c03 > 0.30, "CLINIC_03 no-show anomaly missing"
    print(f"Seeded {yrs[2]:,} daily_metrics rows spanning {yrs[0]}..{yrs[1]} at {db_path}.")
    print(f"Anomalies OK: CLINIC_01 peak util {c01:.2f}, CLINIC_03 peak no-show {c03:.2f}.")
