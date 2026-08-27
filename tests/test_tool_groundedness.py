"""The evaluator that Constitution Rule 8 rests on. It had no tests until the audit.

The two cases that matter most are the ones it used to get wrong: figures whose
units don't line up, and prose that cites nothing at all.
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.groundedness import check_groundedness

KPIS = {"utilization": 0.807, "no_show_rate": 0.19, "avg_wait": 16.0,
        "revenue_per_visit": 177.0, "satisfaction": 4.3}
FLAGS = [{"issue": "Utilization peaked at 125% (over capacity)"}]

TRUE_PROSE = ("Utilization ran at 80.7% with no-shows at 19.0%, an average wait of "
              "16.0 minutes, $177 revenue per visit, satisfaction 4.3/5, peaking at 125%.")


def test_accurate_prose_scores_full_marks():
    res = check_groundedness(TRUE_PROSE, KPIS, FLAGS)
    assert res["score"] == 100.0
    assert res["figures"] == 6 and res["unverified"] == []


def test_invented_figure_is_caught():
    res = check_groundedness(TRUE_PROSE + " Utilization also hit 200%.", KPIS, FLAGS)
    assert res["score"] < 100.0
    assert "200%" in res["unverified"]


def test_figures_cannot_verify_against_the_wrong_unit():
    # Every number here is fabricated. Each used to pass because some true value
    # of a different kind sat near it: wait 16.0 min "confirmed" a 16.0% no-show
    # rate, satisfaction 4.3 "confirmed" a 4.5 minute wait, and $177 revenue
    # "confirmed" 177% utilization.
    res = check_groundedness(
        "No-show rate was 16.0% and wait was 4.5 minutes and utilization 177.0%.",
        KPIS, FLAGS)
    assert res["verified"] == 0
    assert res["score"] == 0.0


def test_citing_nothing_is_not_a_pass():
    # Hedging prose must not outrank prose that commits to numbers.
    res = check_groundedness("Everything looks broadly fine this quarter.", KPIS, FLAGS)
    assert res["figures"] == 0
    assert res["score"] == 0.0


def test_rounding_is_still_tolerated():
    # 80.68 -> "80.7%" is a rounding artefact, not a hallucination.
    assert check_groundedness("Utilization was 80.7%.",
                              {"utilization": 0.8068}, [])["score"] == 100.0
