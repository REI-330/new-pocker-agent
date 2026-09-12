"""G2 / M3 terminal modes: action budget and score threshold (ADR-0010).

Pins the semantics the ADR fixes: an action budget ends the game exactly at the
boundary (no extra action), a score threshold ends it at the round boundary,
a threshold without a hard bound is rejected, a round-scoring rule's action
budget must align with full rounds, and the independent terminal monitor rejects
a plan whose budget or winner diverges from the declared rule.
"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError
from test_g2_m2 import scenario_a_ir

from pocker_agent.core import (
    Interpreter,
    compile_composed,
    contract_check,
    core_registry,
    run_bots,
)
from pocker_agent.core.ir import parse_design_ir
from pocker_agent.core.plan import GamePlan
from pocker_agent.core.playtest import boundary_first
from pocker_agent.core.verify import (
    VERIFICATION_SEEDS,
    VERIFICATION_STRATEGIES,
    verify_composed,
)


def action_budget_ir(budget: int = 4) -> dict:
    return scenario_a_ir(terminal={"max_actor_actions": budget})


def threshold_ir() -> dict:
    return scenario_a_ir(terminal={"max_rounds": 3, "score_reaches": 1})


def monitor(report, name):
    return next(check for check in report.checks if check.name == name)


def mutate(plan, edit):
    data = copy.deepcopy(plan.model_dump(mode="json"))
    edit(data["nodes"])
    return GamePlan.model_validate(data)


# ------------------------------------------------------------------ action budget
def test_an_action_budget_ends_the_game_without_extra_actions():
    ir = parse_design_ir(action_budget_ir(4))
    compiled, result = verify_composed(ir, core_registry())
    assert result.ok, result.failures
    for seed in range(6):
        interpreter = Interpreter(compiled.plan, core_registry(), seed=seed)
        interpreter.setup()
        run_bots(interpreter, human_index=-1)
        assert interpreter.state["finished"] is True
        assert interpreter.state["action_count"] == 4            # exactly, not 5
        assert interpreter.state["phase"] == "finished"
    contract = contract_check(ir, compiled.plan, core_registry(),
                              VERIFICATION_STRATEGIES, VERIFICATION_SEEDS)
    assert contract.ok, contract.failures()


def test_a_score_threshold_ends_the_game_at_the_round_boundary():
    ir = parse_design_ir(threshold_ir())
    compiled, result = verify_composed(ir, core_registry())
    assert result.ok, result.failures
    early = 0
    for seed in range(6):
        interpreter = Interpreter(compiled.plan, core_registry(), seed=seed)
        interpreter.setup()
        run_bots(interpreter, human_index=-1)
        assert interpreter.state["finished"] is True
        assert max(interpreter.state["scores"]) >= 1
        assert interpreter.state["action_count"] % 2 == 0        # round boundary
        early += interpreter.state["action_count"] < 6
    assert early, "at least one seed must end before the round bound"


# -------------------------------------------------------------------- validation
def test_a_threshold_without_a_hard_bound_is_rejected():
    with pytest.raises(ValidationError, match="terminal_requires_a_hard_bound"):
        parse_design_ir(scenario_a_ir(terminal={"score_reaches": 3}))


def test_a_round_scoring_budget_must_align_with_rounds():
    with pytest.raises(ValidationError, match="terminal_action_budget_must_align_with_rounds"):
        parse_design_ir(scenario_a_ir(terminal={"max_actor_actions": 3}))


# ------------------------------------------------------------------ mutations
def test_mutating_the_action_budget_is_rejected_by_the_monitor():
    ir = parse_design_ir(action_budget_ir(4))
    rules = compile_composed(ir, core_registry())

    def edit(nodes):
        nodes["after_resolve"]["action"]["args"]["expression"]["ge"][1] = 2

    report = contract_check(ir, mutate(rules.plan, edit), core_registry(),
                            [boundary_first], (0,))
    assert report.ok is False
    terminal = monitor(report, "terminal")
    assert terminal.ok is False
    assert "no_bound_satisfied" in terminal.detail


def test_hard_coded_winner_is_rejected_with_an_action_budget():
    ir = parse_design_ir(action_budget_ir(4))
    rules = compile_composed(ir, core_registry())

    def edit(nodes):
        nodes["declare"]["action"]["args"]["values"]["winners"] = [0]

    report = contract_check(ir, mutate(rules.plan, edit), core_registry(),
                            VERIFICATION_STRATEGIES, VERIFICATION_SEEDS)
    assert report.ok is False
    assert monitor(report, "terminal").ok is False
