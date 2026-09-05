"""
tools/groundedness.py

Constitution Rule 8: generator != evaluator. This is the *evaluator* — a
separate, deterministic module that re-checks the generator's prose against the
ground-truth numbers pulled from SQL. No LLM, no API cost.

It extracts every meaningful figure the narrative cites (percentages, dollar
amounts, decimals), works out what each figure is *measuring*, and verifies it
against ground truth of that same kind. Anything that can't be matched — wrong
value, or a figure whose meaning can't be established — is surfaced as
"unverified", i.e. a potential hallucination.

Matching by kind is the point. Pooling every true number into one list let a
fabricated "no-show rate of 16.0%" pass because average wait happened to be
16.0 minutes; a unit-blind check is barely a check at all.
"""

import re

# Only meaningful figures: percentages, $amounts, decimals. Bare integers
# (years, counts, "of 5" scales) are ignored to avoid false positives.
_TOKEN = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?|\d+(?:\.\d+)?%|\d+\.\d+")

# What follows a bare decimal usually says what it is: "16.0 minutes", "4.3/5".
_AS_MINUTES = re.compile(r"\s*(?:minutes?|mins?)\b", re.IGNORECASE)
_AS_RATING = re.compile(r"\s*(?:/|\s+out\s+of\s+)\s*5\b", re.IGNORECASE)


def _to_float(tok: str) -> float:
    return float(tok.replace("$", "").replace(",", "").replace("%", "").strip())


def _truths_by_kind(kpis: dict, flags: list) -> dict:
    """Canonical true numbers grouped by what they measure.

    A cited figure is only ever compared against truths of its own kind, so a
    wait time can never be "confirmed" by a satisfaction score that sits near it.
    """
    t = {"percent": [], "money": [], "minutes": [], "rating": [5.0]}
    if kpis.get("utilization") is not None:
        t["percent"].append(kpis["utilization"] * 100)
    if kpis.get("no_show_rate") is not None:
        t["percent"].append(kpis["no_show_rate"] * 100)
    if kpis.get("avg_wait") is not None:
        t["minutes"].append(float(kpis["avg_wait"]))
    if kpis.get("revenue_per_visit") is not None:
        t["money"].append(float(kpis["revenue_per_visit"]))
    if kpis.get("satisfaction") is not None:
        t["rating"].append(float(kpis["satisfaction"]))
    # Flag text is generated from the same SQL, so its figures are ground truth too.
    for f in flags or []:
        issue = f.get("issue", "")
        for m in re.findall(r"(\d+(?:\.\d+)?)\s*%", issue):
            t["percent"].append(float(m))
        for m in re.findall(r"(\d+(?:\.\d+)?)\s*(?:minutes?|mins?)\b", issue, re.IGNORECASE):
            t["minutes"].append(float(m))
    return t


def _kind_of(text: str, match) -> str:
    """What a cited figure measures, or None when the prose doesn't say."""
    tok = match.group()
    if tok.startswith("$"):
        return "money"
    if tok.endswith("%"):
        return "percent"
    after = text[match.end():match.end() + 20]
    if _AS_RATING.match(after):
        return "rating"
    if _AS_MINUTES.match(after):
        return "minutes"
    before = text[max(0, match.start() - 48):match.start()].lower()
    if "wait" in before:
        return "minutes"
    if "satisfaction" in before or "rating" in before:
        return "rating"
    return None


def check_groundedness(narrative: str, kpis: dict, flags: list) -> dict:
    """Score how many cited figures in `narrative` are backed by the true numbers.

    Args:
        narrative: The generated brief prose.
        kpis: Ground-truth KPI dict (from SQL).
        flags: Ground-truth flag list (from SQL).
    """
    truths = _truths_by_kind(kpis, flags)
    text = narrative or ""
    verified, unverified = 0, []
    for m in _TOKEN.finditer(text):
        val = _to_float(m.group())
        candidates = truths.get(_kind_of(text, m)) or []
        # tolerance absorbs rounding ("80.7%" vs 80.68); wider for large $ figures
        if any(abs(val - t) <= max(0.6, 0.02 * abs(t)) for t in candidates):
            verified += 1
        else:
            unverified.append(m.group())
    total = verified + len(unverified)
    # A brief that cites no figures has proved nothing. Scoring it 100% rewarded
    # hedging prose over prose that commits to numbers and gets them right.
    score = 0.0 if total == 0 else round(100 * verified / total, 1)
    return {"score": score, "figures": total, "verified": verified,
            "unverified": unverified[:8]}
# The runnable check for this module is tests/test_tool_groundedness.py, which
# already asserts the same four cases (and rounding tolerance) under pytest.
