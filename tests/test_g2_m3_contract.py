"""G2 / M3 contract check: independent IR monitors and mutation rejection.

The M3 gate demands that ``contract_check`` be generated from host-supported
clause types and reject a product that merely *runs*. These tests re-derive the
product from the IR, then deliberately mutate the plan (award points, hard-coded
winner, round count, zone visibility, compared positions) and require the
matching monitor to fail. They also replay from the recorded input log and
require the events and state to match separately.
"""
from __future__ import annotations

import copy

from test_g2_m2 import scenario_a_ir

from pocker_agent.core import compile_composed, contract_check, core_registry
from pocker_agent.core.ir import parse_design_ir
from pocker_agent.core.plan import GamePlan
from pocker_agent.core.playtest import boundary_first
from pocker_agent.core.verify import (
    VERIFICATION_SEEDS,
    VERIFICATION_STRATEGIES,
    compare_traces,
    record_trace,
    replay_trace,
    verify_composed,
)

ALL_MONITORS = {"replay", "card_conservation", "selection_move", "compare_outcome",
                "scoring", "pair_scoring", "refill", "terminal", "zone_visibility"}


def compiled():
    ir = parse_design_ir(scenario_a_ir())
    return ir, compile_composed(ir, core_registry())


def monitor(report, name):
    return next(check for check in report.checks if check.name == name)


def mutate(plan: GamePlan, edit) -> GamePlan:
    data = copy.deepcopy(plan.model_dump(mode="json"))
    edit(data["nodes"])
    return GamePlan.model_validate(data)


# ------------------------------------------------------------------ valid case
def test_the_independent_check_passes_the_valid_product():
    ir, rules = compiled()
    report = contract_check(ir, rules.plan, core_registry(),
                            VERIFICATION_STRATEGIES, VERIFICATION_SEEDS)
    assert report.ok, report.failures()
    assert set(report.covered()) == ALL_MONITORS


def test_verify_composed_attaches_the_contract_report():
    _, result = verify_composed(scenario_a_ir(), core_registry())
    assert result.ok
    assert result.contract["ok"] is True
    assert {check["name"] for check in result.contract["checks"]} == ALL_MONITORS


# ------------------------------------------------------------- input-log replay
def test_the_input_log_replays_to_the_same_events_and_state():
    _, rules = compiled()
    trace = record_trace(rules.plan, core_registry(), boundary_first, seed=0)
    assert trace.finished
    assert compare_traces(trace, replay_trace(rules.plan, core_registry(), trace)) == []


def test_a_changed_input_diverges_and_is_reported():
    _, rules = compiled()
    trace = record_trace(rules.plan, core_registry(), boundary_first, seed=0)
    start = trace.spans[0][0]
    before = trace.observations[start - 1].state
    hand = [card.id for card in before["zones"]["hand-0"]["cards"]]
    other = next(card_id for card_id in hand if card_id != trace.inputs[0]["card"][0])
    tampered_inputs = ({**trace.inputs[0], "card": [other]}, *trace.inputs[1:])
    from pocker_agent.core.verify import GameTrace

    tampered = GameTrace(seed=trace.seed, inputs=tampered_inputs, events=trace.events,
                         observations=trace.observations, spans=trace.spans,
                         finished=trace.finished)
    problems = compare_traces(trace, replay_trace(rules.plan, core_registry(), tampered))
    assert problems, "a changed input must not silently replay"


# ------------------------------------------------------------------- mutations
def test_hard_coded_winner_is_rejected():
    ir, rules = compiled()

    def edit(nodes):
        nodes["declare"]["action"]["args"]["values"]["winners"] = [0]

    report = contract_check(ir, mutate(rules.plan, edit), core_registry(),
                            VERIFICATION_STRATEGIES, VERIFICATION_SEEDS)
    assert report.ok is False
    assert monitor(report, "terminal").ok is False


def test_award_points_mutation_is_rejected():
    ir, rules = compiled()

    def edit(nodes):
        for node in nodes.values():
            if node.get("kind") == "call" and node["action"]["tool"] == "score_settle":
                node["action"]["args"]["points"] = 99

    report = contract_check(ir, mutate(rules.plan, edit), core_registry(),
                            [boundary_first], (0,))
    assert report.ok is False
    assert monitor(report, "scoring").ok is False


def test_round_count_mutation_is_rejected():
    ir, rules = compiled()

    def edit(nodes):
        nodes["round_check"]["action"]["args"]["expression"]["gt"][1] = 2

    report = contract_check(ir, mutate(rules.plan, edit), core_registry(),
                            [boundary_first], (0,))
    assert report.ok is False
    assert monitor(report, "terminal").ok is False


def test_visibility_mutation_is_rejected():
    ir, rules = compiled()

    def edit(nodes):
        zones = nodes["init"]["action"]["args"]["values"]["zones"]
        zones["hand-0"]["visibility"] = "hidden"

    report = contract_check(ir, mutate(rules.plan, edit), core_registry(),
                            [boundary_first], (0,))
    assert report.ok is False
    assert monitor(report, "zone_visibility").ok is False


def test_compared_positions_mutation_is_rejected():
    ir, rules = compiled()

    def edit(nodes):
        args = nodes["resolve_cmp_0"]["action"]["args"]
        args["left"], args["right"] = args["right"], args["left"]

    report = contract_check(ir, mutate(rules.plan, edit), core_registry(),
                            [boundary_first], (0,))
    assert report.ok is False
    assert monitor(report, "compare_outcome").ok is False
