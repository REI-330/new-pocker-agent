"""The generation benchmark harness itself must be deterministic and green."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "benchmark_generation", ROOT / "scripts" / "benchmark_generation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bench = _load()


def test_cases_declare_an_expected_outcome():
    cases = bench.load_cases()
    assert len(cases) >= 6
    assert {case["expect"] for case in cases} >= {"arithmetic", "war", "unsupported"}
    for case in cases:
        assert case["goal"] and case["expect"]
        if case["expect"] == "unsupported":
            assert case["missing"], case["id"]


def test_scripted_benchmark_is_green():
    report = bench.benchmark(bench.load_cases(), bench.ScriptedModel)
    assert report["success_rate"] == 1.0, report["cases"]
    assert report["playtest_pass_rate"] == 1.0, report["cases"]
    assert report["correctly_unsupported"] == report["unsupported_cases"]
    assert report["missing_histogram"], "boundary cases must name the missing axis"


def test_every_finalized_case_actually_passed_playtest():
    report = bench.benchmark(bench.load_cases(), bench.ScriptedModel)
    for record in report["cases"]:
        if record["finalized"]:
            assert record["playtest_ok"] is True, record


def test_deterministic_report_is_reproducible():
    first = bench.benchmark(bench.load_cases(), bench.ScriptedModel)
    second = bench.benchmark(bench.load_cases(), bench.ScriptedModel)
    assert first == second


def test_unsupported_goals_name_the_missing_axis():
    report = bench.benchmark(bench.load_cases(), bench.ScriptedModel)
    by_id = {record["id"]: record for record in report["cases"]}
    assert by_id["go_fish"]["kind"] == "proposal"       # now playable
    assert by_id["spoons"]["kind"] == "unsupported"
    assert by_id["spoons"]["missing"] == ["trigger"]
    assert by_id["klondike"]["missing"] == ["layout"]
