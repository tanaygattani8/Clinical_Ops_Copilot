import datetime
import os
import re

# The warehouse holds 2019-2025. Anchoring "last month" to the real today lands
# outside that range and returns an empty window, which the warehouse then reports
# as an insufficient-data privacy refusal - a confusing answer to what is really
# just a gap in the data. Clamp the anchor to the last day the warehouse covers.
DATA_END = os.getenv("CLINIC_DATA_END", "2025-12-31")


def _anchor(anchor_date: str) -> datetime.date:
    if anchor_date:
        return datetime.date.fromisoformat(anchor_date)
    return min(datetime.date.today(), datetime.date.fromisoformat(DATA_END))


def resolve_date_range(reference: str, anchor_date: str = "") -> dict:
    """Resolve a natural-language date reference to a concrete ISO date range.

    Args:
        reference: Natural-language time expression (e.g. 'last week', 'Q2 2025', 'past 30 days').
        anchor_date: Optional anchor date in YYYY-MM-DD format (defaults to today).
    """
    try:
        anchor = _anchor(anchor_date)
    except ValueError:
        # An LLM supplies this argument, so a malformed one has to come back as an
        # error dict like every other bad input - it used to raise out of the tool.
        return {"reference": reference, "start_date": None, "end_date": None,
                "anchor": anchor_date,
                "error": f"Invalid anchor_date '{anchor_date}'; expected YYYY-MM-DD."}
    ref = reference.strip().lower()

    try:
        # "past N days"
        m = re.match(r"past\s+(\d+)\s+days?", ref)
        if m:
            n = int(m.group(1))
            return {"reference": reference, "start_date": str(anchor - datetime.timedelta(days=n - 1)), "end_date": str(anchor), "anchor": str(anchor)}

        # "last week"
        if ref == "last week":
            # Mon–Sun of previous calendar week
            today_wd = anchor.weekday()  # Mon=0
            last_sun = anchor - datetime.timedelta(days=today_wd + 1)
            last_mon = last_sun - datetime.timedelta(days=6)
            return {"reference": reference, "start_date": str(last_mon), "end_date": str(last_sun), "anchor": str(anchor)}

        # "last month"
        if ref == "last month":
            first_of_this = anchor.replace(day=1)
            last_of_prev = first_of_this - datetime.timedelta(days=1)
            first_of_prev = last_of_prev.replace(day=1)
            return {"reference": reference, "start_date": str(first_of_prev), "end_date": str(last_of_prev), "anchor": str(anchor)}

        # "this week"
        if ref == "this week":
            start = anchor - datetime.timedelta(days=anchor.weekday())
            end = start + datetime.timedelta(days=6)
            return {"reference": reference, "start_date": str(start), "end_date": str(end), "anchor": str(anchor)}

        # "this month"
        if ref == "this month":
            start = anchor.replace(day=1)
            next_m = (start.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
            end = next_m - datetime.timedelta(days=1)
            return {"reference": reference, "start_date": str(start), "end_date": str(end), "anchor": str(anchor)}

        # "Q1 YYYY" ... "Q4 YYYY"
        m = re.match(r"q([1-4])\s+(\d{4})", ref)
        if m:
            q, year = int(m.group(1)), int(m.group(2))
            starts = {1: (1, 1), 2: (4, 1), 3: (7, 1), 4: (10, 1)}
            ends = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
            s = datetime.date(year, *starts[q])
            e = datetime.date(year, *ends[q])
            return {"reference": reference, "start_date": str(s), "end_date": str(e), "anchor": str(anchor)}

        # "this year" / "last year"
        if ref == "this year":
            return {"reference": reference, "start_date": f"{anchor.year}-01-01", "end_date": f"{anchor.year}-12-31", "anchor": str(anchor)}
        if ref == "last year":
            y = anchor.year - 1
            return {"reference": reference, "start_date": f"{y}-01-01", "end_date": f"{y}-12-31", "anchor": str(anchor)}

        return {"reference": reference, "start_date": None, "end_date": None, "anchor": str(anchor), "error": f"Unrecognized reference: '{reference}'"}

    except Exception as e:
        return {"reference": reference, "start_date": None, "end_date": None, "anchor": str(anchor), "error": str(e)}


def get_comparison_periods(reference: str, anchor_date: str = "") -> dict:
    """Return current and previous period date ranges for a natural-language reference.

    Args:
        reference: Natural-language time expression (e.g. 'last month').
        anchor_date: Optional anchor date in YYYY-MM-DD format.
    """
    current = resolve_date_range(reference, anchor_date)
    if "error" in current or not current.get("start_date"):
        return {"reference": reference, "current": current, "previous": current}

    curr_start = datetime.date.fromisoformat(current["start_date"])
    curr_end = datetime.date.fromisoformat(current["end_date"])
    ref = reference.strip().lower()

    if "month" in ref:
        first_of_curr = curr_start.replace(day=1)
        last_of_prev = first_of_curr - datetime.timedelta(days=1)
        first_of_prev = last_of_prev.replace(day=1)
        prev_start, prev_end = first_of_prev, last_of_prev
    elif ref[:1] == "q" and ref[1:2].isdigit():
        year = curr_start.year
        month = curr_start.month
        if month == 4:
            prev_start, prev_end = datetime.date(year, 1, 1), datetime.date(year, 3, 31)
        elif month == 7:
            prev_start, prev_end = datetime.date(year, 4, 1), datetime.date(year, 6, 30)
        elif month == 10:
            prev_start, prev_end = datetime.date(year, 7, 1), datetime.date(year, 9, 30)
        else: # month == 1
            prev_start, prev_end = datetime.date(year - 1, 10, 1), datetime.date(year - 1, 12, 31)
    else:
        period_days = (curr_end - curr_start).days + 1
        prev_end = curr_start - datetime.timedelta(days=1)
        prev_start = prev_end - datetime.timedelta(days=period_days - 1)

    return {
        "reference": reference,
        "current": {"start": current["start_date"], "end": current["end_date"]},
        "previous": {"start": str(prev_start), "end": str(prev_end)}
    }

