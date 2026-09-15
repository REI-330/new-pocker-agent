from __future__ import annotations

import pytest
from test_g2_m2 import scenario_a_ir

from pocker_agent.core import (
    CardRef,
    GamePlan,
    PolicyContext,
    PolicyProfile,
    ToolError,
    core_registry,
    evaluate_simulations,
    first_observed_legal,
    seat_rotations,
)
from pocker_agent.core.invariants import card_conservation
from pocker_agent.core.ir import parse_design_ir
from pocker_agent.core.rules import compile_composed


def _two_turn_plan(
    *, winners: tuple[int, ...] = (1,), scores: tuple[int, ...] = (0, 1)
) -> GamePlan:
    hands = [
        [CardRef("private-0", "A", "S", 14)],
        [CardRef("private-1", "K", "H", 13)],
    ]
    return GamePlan.model_validate(
        {
            "schema_version": "0.4",
            "game_kind": "simulation_fixture",
            "players": 2,
            "tools": [{"name": "state", "config": {}}],
            "initial": {
                "current_player": 0,
                "round": 1,
                "scores": [0, 0],
                "hands": hands,
                "private_hands": True,
            },
            "entry": "wait_0",
            "nodes": {
                "wait_0": {"kind": "wait", "inputs": {"first": "set_player_1"}},
                "set_player_1": {
                    "kind": "call",
                    "next": "wait_1",
                    "action": {
                        "tool": "state",
                        "operation": "update",
                        "args": {
                            "state": "$state",
                            "values": {"current_player": 1},
                        },
                    },
                },
                "wait_1": {"kind": "wait", "inputs": {"second": "finish"}},
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


def test_each_seat_gets_only_its_policy_and_private_observation() -> None:
    seen: list[tuple[int, str, int]] = []

    def seat_0(context: PolicyContext):
        players = context.observation["players"]
        seen.append((context.player, players[0]["hand"][0]["id"], len(players[1]["hand"])))
        return first_observed_legal(context)

    def seat_1(context: PolicyContext):
        players = context.observation["players"]
        seen.append((context.player, players[1]["hand"][0]["id"], len(players[0]["hand"])))
        return first_observed_legal(context)

    report = evaluate_simulations(
        _two_turn_plan(),
        core_registry(),
        (PolicyProfile("duel", (seat_0, seat_1)),),
        seeds=(0,),
    )

    assert seen == [(0, "private-0", 0), (1, "private-1", 0)]
    assert report.runs[0].actors == (0, 1)
    assert [item["action"] for item in report.runs[0].inputs] == ["first", "second"]
    assert report.seat_win_shares == (0.0, 1.0)
    assert report.profile_completion_rates == {"duel": 1.0}


def test_score_only_result_is_not_reported_as_a_draw() -> None:
    report = evaluate_simulations(
        _two_turn_plan(winners=(), scores=(3, 1)),
        core_registry(),
        {"shared": first_observed_legal},
        seeds=(0, 1),
    )

    assert report.completion_rate == 1.0
    assert report.declared_draw_rate == 0.0
    assert report.score_only_rate == 1.0
    assert report.no_result_rate == 0.0
    assert report.mean_scores == (3.0, 1.0)
    assert all(run.result_kind == "score_only" for run in report.runs)


def test_compiled_composed_game_runs_through_observation_only_policy() -> None:
    compiled = compile_composed(parse_design_ir(scenario_a_ir()), core_registry())

    report = evaluate_simulations(
        compiled.plan,
        core_registry(),
        {"visible-first": first_observed_legal},
        seeds=(0, 1, 7),
        invariants=(card_conservation(16),),
    )

    assert report.completion_rate == 1.0
    assert report.failure_counts == {}
    assert report.profile_completion_rates == {"visible-first": 1.0}
    assert all(run.actions == 6 for run in report.runs)
    assert all(run.result_kind == "declared_winner" for run in report.runs)
    assert all(set(run.actors) == {0, 1} for run in report.runs)
    assert all(
        tuple(item["action"] for item in run.inputs) == ("play",) * 6
        for run in report.runs
    )
    assert all(
        isinstance(item["card"], list) and len(item["card"]) == 1
        for run in report.runs
        for item in run.inputs
    )


def test_seat_rotations_build_every_policy_assignment() -> None:
    def policy_a(context: PolicyContext):
        return first_observed_legal(context)

    def policy_b(context: PolicyContext):
        return first_observed_legal(context)

    profiles = seat_rotations("paired", (policy_a, policy_b))

    assert [profile.name for profile in profiles] == [
        "paired:rotation-0",
        "paired:rotation-1",
    ]
    assert profiles[0].policies == (policy_a, policy_b)
    assert profiles[1].policies == (policy_b, policy_a)


def test_failed_policy_runs_remain_explicit_diagnostics() -> None:
    def no_decision(_context: PolicyContext):
        return None

    report = evaluate_simulations(
        _two_turn_plan(), core_registry(), {"stuck": no_decision}, seeds=(0, 1)
    )

    assert report.completion_rate == 0.0
    assert report.declared_draw_rate is None
    assert report.score_only_rate is None
    assert report.no_result_rate is None
    assert report.seat_parity_score is None
    assert report.failure_counts == {"policy_returned_none": 2}
    assert all(run.result_kind == "failed" for run in report.runs)


def test_invalid_experiment_matrix_is_rejected() -> None:
    plan = _two_turn_plan()

    with pytest.raises(ToolError, match="simulation_requires_seeds"):
        evaluate_simulations(plan, core_registry(), {"shared": first_observed_legal}, seeds=())
    with pytest.raises(ToolError, match="simulation_seeds_must_be_unique"):
        evaluate_simulations(
            plan, core_registry(), {"shared": first_observed_legal}, seeds=(0, 0)
        )
    with pytest.raises(ToolError, match="simulation_profile_seat_count"):
        evaluate_simulations(
            plan,
            core_registry(),
            (PolicyProfile("missing-seat", (first_observed_legal,)),),
            seeds=(0,),
        )
