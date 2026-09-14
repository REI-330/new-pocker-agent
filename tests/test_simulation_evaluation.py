from __future__ import annotations

import pytest

from pocker_agent.core import GamePlan, ToolError, core_registry, evaluate_simulations


def _terminal_plan(
    *, winners: tuple[int, ...] = (0,), scores: tuple[int, ...] = (1, 0)
) -> GamePlan:
    return GamePlan.model_validate(
        {
            "schema_version": "0.4",
            "game_kind": "simulation_fixture",
            "players": 2,
            "tools": [{"name": "state", "config": {}}],
            "initial": {"current_player": 0, "round": 1},
            "entry": "wait",
            "nodes": {
                "wait": {"kind": "wait", "inputs": {"go": "finish"}},
                "finish": {
                    "kind": "call",
                    "next": "end",
                    "action": {
                        "tool": "state",
                        "operation": "update",
                        "args": {
                            "state": "$state",
                            "values": {
                                "finished": True,
                                "winners": list(winners),
                                "scores": list(scores),
                            },
                        },
                    },
                },
                "end": {"kind": "end"},
            },
            "step_limit": 16,
        }
    )


def _go(_interpreter):
    return "go", {}


def _stuck(_interpreter):
    return None


def test_evaluation_reports_seed_policy_matrix_and_outcome_metrics() -> None:
    report = evaluate_simulations(
        _terminal_plan(), core_registry(), {"go": _go}, seeds=(0, 1, 7)
    )

    assert len(report.runs) == 3
    assert report.completion_rate == 1.0
    assert report.draw_rate == 0.0
    assert report.seat_win_shares == (1.0, 0.0)
    assert report.seat_parity_score == 0.0
    assert report.starter_advantage == 1.0
    assert report.mean_scores == (1.0, 0.0)
    assert report.action_counts.as_dict() == {
        "count": 3,
        "min": 1.0,
        "max": 1.0,
        "mean": 1.0,
        "median": 1.0,
    }
    assert report.strategy_completion_rates == {"go": 1.0}
    assert report.failure_counts == {}
    assert report.as_dict()["policy_dependent"] is True


def test_tied_winners_split_credit_without_hiding_draw_rate() -> None:
    report = evaluate_simulations(
        _terminal_plan(winners=(0, 1), scores=(1, 1)),
        core_registry(),
        _go,
        seeds=(0, 1),
    )

    assert report.draw_rate == 0.0
    assert report.seat_win_shares == (0.5, 0.5)
    assert report.seat_parity_score == 1.0
    assert report.starter_advantage == 0.0


def test_failed_policy_runs_are_retained_as_diagnostics() -> None:
    report = evaluate_simulations(
        _terminal_plan(), core_registry(), {"stuck": _stuck}, seeds=(0, 1)
    )

    assert report.completion_rate == 0.0
    assert report.draw_rate is None
    assert report.seat_parity_score is None
    assert report.failure_counts == {"strategy_returned_none": 2}
    assert report.action_counts.count == 0
    assert all(run.error == "strategy_returned_none" for run in report.runs)


def test_evaluation_rejects_an_empty_experiment_matrix() -> None:
    with pytest.raises(ToolError, match="simulation_requires_seeds"):
        evaluate_simulations(_terminal_plan(), core_registry(), _go, seeds=())

    with pytest.raises(ToolError, match="simulation_requires_strategies"):
        evaluate_simulations(_terminal_plan(), core_registry(), (), seeds=(0,))
