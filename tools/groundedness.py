"""
tools/groundedness.py

Constitution Rule 8: generator != evaluator. This is the *evaluator* — a
separate, deterministic module that re-checks the generator's prose against the
ground-truth numbers pulled from SQL. No LLM, no API cost.

It extracts every meaningful figure the narrative cites (percentages, dollar
amounts, decimals) and verifies each is within tolerance of a known-true value.
Anything that isn't is surfaced as "unverified" — i.e. a potential hallucination.
"""

import re

# Only meaningful figures: percentages, $amounts, decimals. Bare integers
# (years, counts, "of 5" scales) are ignored to avoid false positives.
_TOKEN = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?|\d+(?:\.\d+)?%|\d+\.\d+")


def _to_float(tok: str) -> float:
    return float(tok.replace("$", "").replace(",", "").replace("%", "").strip())


def _truth_values(kpis: dict, flags: list) -> list:
    """Canonical true numbers, in the units prose uses (utilization as a percent, etc.)."""
    vals = [5.0]  # satisfaction scale bound ("/5") is always legitimate
    if kpis.get("utilization") is not None:
        vals.append(kpis["utilization"] * 100)
    if kpis.get("no_show_rate") is not None:
        vals.append(kpis["no_show_rate"] * 100)
    for key in ("avg_wait", "revenue_per_visit", "satisfaction"):
        if kpis.get(key) is not None:
            vals.append(float(kpis[key]))
    for f in flags or []:
        for m in re.findall(r"\d+(?:\.\d+)?", f.get("issue", "")):
            vals.append(float(m))
    return vals


def check_groundedness(narrative: str, kpis: dict, flags: list) -> dict:
    """Score how many cited figures in `narrative` are backed by the true numbers.

    Args:
        narrative: The generated brief prose.
        kpis: Ground-truth KPI dict (from SQL).
        flags: Ground-truth flag list (from SQL).
    """
    truths = _truth_values(kpis, flags)
    verified, unverified = 0, []
    for tok in _TOKEN.findall(narrative or ""):
        val = _to_float(tok)
        # tolerance absorbs rounding ("80.7%" vs 80.68); wider for large $ figures
        if any(abs(val - t) <= max(0.6, 0.02 * abs(t)) for t in truths):
            verified += 1
        else:
            unverified.append(tok)
    total = verified + len(unverified)
    score = 100.0 if total == 0 else round(100 * verified / total, 1)
    return {"score": score, "figures": total, "verified": verified,
            "unverified": unverified[:8]}


if __name__ == "__main__":
    k = {"utilization": 0.807, "no_show_rate": 0.19, "avg_wait": 16.0,
         "revenue_per_visit": 177.0, "satisfaction": 4.3}
    fl = [{"issue": "Utilization peaked at 125% (over capacity)"}]
    good = ("Utilization ran at 80.7% with no-shows at 19.0%, an average wait of "
            "16.0 minutes, $177 revenue per visit, satisfaction 4.3/5, peaking at 125%.")
    r_good = check_groundedness(good, k, fl)
    assert r_good["score"] == 100.0, r_good
    r_bad = check_groundedness(good + " Utilization also hit 200%.", k, fl)
    assert r_bad["score"] < 100 and "200%" in r_bad["unverified"], r_bad
    print("groundedness self-check OK:", r_good, r_bad)
