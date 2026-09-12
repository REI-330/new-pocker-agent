"""G2 / M3 skip trigger and fixed effect order (ADR-0012 sections 5-7).

The trigger phase runs only on the continue path of the terminal gate, so a game
that reaches its threshold never skips (B10). A skipped seat never reaches
``advance_turn``, so it is not counted (B11); the shared phase dispatches the
action that actually ran through ``state.input.action``.
"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

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


def skip_ir(condition: dict | None = None, *, terminal: dict | None = None,
            trigger: list[dict] | None = None) -> dict:
    if trigger is None:
        trigger = [{"kind": "skip", "count": 1, **({"condition": condition} if condition else {})}]
    return {
        "schema_version": "0.5", "kind": "composed",
        "meta": {"title": "skip test", "description": "ADR-0012 trigger phase."},
        "requirements": [],
        "players": {"count": 3},
        "deck": {"ranks": [str(n) for n in range(2, 7)], "suits": ["S", "H"],
                 "copies": 1, "values": {str(n): n for n in range(2, 7)}},
        "zones": [
            {"id": "hand", "visibility": "public", "scope": "player"},
            {"id": "discard", "visibility": "public", "scope": "shared"},
            {"id": "stock", "visibility": "hidden", "scope": "shared"},
        ],
        "setup": {"deals": [{"zone": "hand", "count": 2, "per_seat": True},
                            {"zone": "discard", "count": 1, "per_seat": False}],
                  "stock_zone": "stock"},
        "variables": [],
        "actions": [{"id": "play", "inputs": [],
                     "effects": [{"kind": "refill", "from_zone": "stock",
                                  "to_zone": "hand", "target_count": 2, "max_draw": 2}],
                     "trigger": trigger}],
        "flow": {"round_action": "play", "start_seat": "seat0", "resolve": []},
        "scoring": [],
        "terminal": terminal if terminal is not None else {"max_actor_actions": 6},
        "macros": [],
    }


def _started(payload: dict, seed: int = 0) -> Interpreter:
    compiled = compile_composed(parse_design_ir(payload), core_registry())
    interpreter = Interpreter(compiled.plan, core_registry(), seed=seed)
    interpreter.setup()
    return interpreter


def mutate(plan: GamePlan, edit) -> GamePlan:
    data = copy.deepcopy(plan.model_dump(mode="json"))
    edit(data["nodes"])
    return GamePlan.model_validate(data)


def test_skip_advances_past_the_next_seat_and_is_not_counted():
    interpreter = _started(skip_ir())
    assert interpreter.legal_actions() == ["play"]
    interpreter.step("play")
    assert interpreter.state["action_count"] == 1     # the skipped seat is not counted
    assert interpreter.state["current_player"] == 2   # seat 1 was skipped
    assert interpreter.state["skip_next"] == 0        # the pending skip was consumed
    assert interpreter.legal_actions() == ["play"]


def test_a_false_trigger_condition_does_not_skip():
    condition = {"op": "eq", "left": {"op": "ref", "path": "round"},
                 "right": {"op": "lit", "value": 99}}
    interpreter = _started(skip_ir(condition))
    interpreter.step("play")
    assert interpreter.state["current_player"] == 1


def test_a_true_trigger_condition_skips():
    condition = {"op": "eq", "left": {"op": "ref", "path": "round"},
                 "right": {"op": "lit", "value": 1}}
    interpreter = _started(skip_ir(condition))
    interpreter.step("play")
    assert interpreter.state["current_player"] == 2


def test_the_terminal_gate_runs_before_the_trigger():
    ir = parse_design_ir(skip_ir(terminal={"max_actor_actions": 1}))
    compiled = compile_composed(ir, core_registry())
    interpreter = Interpreter(compiled.plan, core_registry(), seed=0)
    interpreter.setup()
    interpreter.step("play")
    assert interpreter.state["finished"] is True
    assert interpreter.state["action_count"] == 1
    report = contract_check(ir, compiled.plan, core_registry(), [boundary_first], (0,))
    assert report.ok, report.failures()
    assert next(check for check in report.checks
                if check.name == "trigger_after_terminal").ok is True


def test_reordering_the_trigger_before_the_terminal_gate_is_rejected():
    ir = parse_design_ir(skip_ir(terminal={"max_actor_actions": 1}))
    compiled = compile_composed(ir, core_registry())

    def edit(nodes):
        # Run the skip chain before the terminal gate; the game still finishes,
        # but the independent monitor must catch the out-of-order trigger.
        nodes["set_action_count"]["next"] = "trigger_play_check"
        nodes["trig_play_0_set"]["next"] = "after_action"

    report = contract_check(ir, mutate(compiled.plan, edit), core_registry(),
                            [boundary_first], (0,))
    assert report.ok is False
    assert next(check for check in report.checks
                if check.name == "trigger_after_terminal").ok is False


def test_counting_the_skipped_seat_is_rejected():
    ir = parse_design_ir(skip_ir())

    def edit(nodes):
        # Advance the action counter for a skipped seat: action_count would then
        # exceed the number of recorded actions.
        nodes["trig_play_0_set"]["action"]["args"]["values"]["action_count"] = \
            {"add": ["$state.action_count", 1]}

    report = contract_check(ir, mutate(contract_check_plan(ir), edit), core_registry(),
                            [boundary_first], (0,))
    assert report.ok is False
    assert next(check for check in report.checks
                if check.name == "action_counting").ok is False


def contract_check_plan(ir):
    return compile_composed(ir, core_registry()).plan


def test_skip_must_be_declared_as_a_trigger():
    payload = skip_ir()
    payload["actions"][0]["effects"].append({"kind": "skip", "count": 1})
    with pytest.raises(ValidationError, match="skip_must_be_a_trigger"):
        parse_design_ir(payload)


def test_only_skip_is_allowed_in_the_trigger_list():
    payload = skip_ir(trigger=[{"kind": "refill", "from_zone": "stock",
                                "to_zone": "hand", "target_count": 2, "max_draw": 2}])
    with pytest.raises(ValidationError, match="trigger_only_supports_skip"):
        parse_design_ir(payload)


def test_the_skip_plan_verifies_and_plays_to_the_end():
    ir = parse_design_ir(skip_ir())
    compiled, result = verify_composed(ir, core_registry())
    assert result.ok, result.failures
    contract = contract_check(ir, compiled.plan, core_registry(),
                             VERIFICATION_STRATEGIES, VERIFICATION_SEEDS)
    assert contract.ok, contract.failures()
    for seed in range(4):
        interpreter = Interpreter(compiled.plan, core_registry(), seed=seed)
        interpreter.setup()
        run_bots(interpreter, human_index=-1)
        assert interpreter.state["finished"] is True
        assert interpreter.state["action_count"] == 6        # six real actions, skips excluded
