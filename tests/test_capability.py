"""Capability status machine and corpus coverage (S0 exit evidence)."""
from __future__ import annotations

from pocker_agent.core import capability_check, capability_matrix, coverage_report, load_corpus


def test_matrix_exposes_the_status_machine():
    matrix = capability_matrix()
    assert matrix["status_order"] == ["planned", "experimental", "stable", "deprecated"]
    for axis in matrix["axes"]:
        assert axis["covered"] is (axis["status"] == "stable")
        assert axis["status"] in matrix["status_order"]


def test_planned_axis_is_not_covered():
    report = capability_check(["layout"])
    assert report.expressible is False
    assert report.missing == [{"axis": "layout", "status": "planned",
                               "reason": "axis_status_is_planned"}]


def test_s2_and_s3_axes_are_stable_and_gaps_are_ranked():
    assert capability_check(["pattern_lang", "info_set"]).expressible is True
    assert capability_check(["turn_adapter", "team", "pattern_lang"]).expressible is True
    assert capability_check(["betting", "ledger", "hand_rank"]).expressible is True
    report = coverage_report()
    for closed in ("turn_adapter", "team", "betting", "ledger", "hand_rank"):
        assert closed not in report["missing_histogram"]
    assert "layout" in report["missing_histogram"]
    assert report["covered"] >= 20


def test_stable_axes_are_covered():
    report = capability_check(["sequential_turn", "exact_expression", "score_settle"])
    assert report.expressible is True
    assert report.missing == []


def test_unknown_axis_is_named_not_silently_ignored():
    report = capability_check(["teleport"])
    assert report.expressible is False
    assert report.unknown_axes == ["teleport"]
    assert report.missing[0]["reason"] == "unknown_axis_not_in_matrix"


def test_coverage_report_shape_and_histogram():
    report = coverage_report()
    assert report["total"] == len(load_corpus())
    assert report["total"] >= 20
    assert 0.0 < report["coverage"] < 1.0
    assert report["covered"] >= 1
    # The remaining gaps must be visible, ranked by impact.
    assert "layout" in report["missing_histogram"]
    assert report["missing_histogram"]["layout"] >= 3
    arithmetic = next(game for game in report["games"] if game["id"] == "arithmetic24")
    assert arithmetic["expressible"] is True
    assert arithmetic["builtin"] == "arithmetic24"
    assert arithmetic["missing"] == []
    war = next(game for game in report["games"] if game["id"] == "war")
    assert war["expressible"] is True and war["builtin"] == "war"
    # Blackjack needs soft-ace totals, a different axis from rank comparison.
    blackjack = next(game for game in report["games"] if game["id"] == "blackjack")
    assert blackjack["expressible"] is True
    assert blackjack["builtin"] == "blackjack"
    assert blackjack["missing"] == []


def test_corpus_entries_declare_axes_and_unique_ids():
    games = load_corpus()
    ids = [game["id"] for game in games]
    assert len(ids) == len(set(ids))
    for game in games:
        assert game["required_axes"], game["id"]
