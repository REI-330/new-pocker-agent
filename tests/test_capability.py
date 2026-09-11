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
    report = capability_check(["pattern_lang"])
    assert report.expressible is False
    assert report.missing == [{"axis": "pattern_lang", "status": "planned",
                               "reason": "axis_status_is_planned"}]


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
    # The gap that S2 must close is visible, ranked by impact.
    assert "pattern_lang" in report["missing_histogram"]
    assert report["missing_histogram"]["pattern_lang"] >= 10
    arithmetic = next(game for game in report["games"] if game["id"] == "arithmetic24")
    assert arithmetic["expressible"] is True
    assert arithmetic["builtin"] == "arithmetic24"
    assert arithmetic["missing"] == []


def test_corpus_entries_declare_axes_and_unique_ids():
    games = load_corpus()
    ids = [game["id"] for game in games]
    assert len(ids) == len(set(ids))
    for game in games:
        assert game["required_axes"], game["id"]
