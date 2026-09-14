"""Language-independent integer action inputs for bets and resource amounts."""
from __future__ import annotations

import pytest
from pydantic import ValidationError
from test_g2_m2 import scenario_a_ir

from pocker_agent.core import Interpreter, SessionStore, ToolError, core_registry, playtest
from pocker_agent.core.actions import descriptor_for, payload_for
from pocker_agent.core.ir import parse_design_ir
from pocker_agent.core.policy import bot_action
from pocker_agent.core.rules import compile_composed


def wager_ir() -> dict:
    payload = scenario_a_ir()
    payload["variables"] = [
        {"name": "minimum_wager", "type": "integer", "initial": 2},
        {"name": "maximum_wager", "type": "integer", "initial": 7},
        {"name": "last_wager", "type": "integer", "initial": 0},
    ]
    payload["actions"][0]["inputs"].append({
            "id": "amount",
            "kind": "integer_range",
            "minimum": {"op": "ref", "path": "variables.minimum_wager"},
            "maximum": {"op": "ref", "path": "variables.maximum_wager"},
    })
    payload["actions"][0]["effects"].append({
        "kind": "assign",
        "variable": "last_wager",
        "value": {"op": "ref", "path": "input.amount"},
    })
    payload["terminal"] = {
        "max_actor_actions": 2,
        "winner": "highest_score",
        "tie": "allow",
    }
    return payload


def test_integer_range_compiles_and_is_resolved_in_the_player_view() -> None:
    compiled = compile_composed(parse_design_ir(wager_ir()), core_registry())
    descriptor = descriptor_for(compiled.plan, "play")
    assert descriptor is not None

    interpreter = Interpreter(compiled.plan, core_registry(), seed=0)
    interpreter.setup()
    payload = payload_for(interpreter.state, descriptor)
    assert payload["amount"] == 2
    action = interpreter.view("player-1")["actions"][0]
    amount = next(item for item in action["inputs"] if item["id"] == "amount")
    assert amount == {"id": "amount", "kind": "integer_range",
                      "minimum": 2, "maximum": 7}


def test_interpreter_enforces_integer_type_and_dynamic_bounds_transactionally() -> None:
    compiled = compile_composed(parse_design_ir(wager_ir()), core_registry())
    interpreter = Interpreter(compiled.plan, core_registry(), seed=0)
    interpreter.setup()
    before = interpreter.serialize()
    descriptor = descriptor_for(compiled.plan, "play")
    valid = payload_for(interpreter.state, descriptor)

    with pytest.raises(ToolError, match="input_must_be_integer:amount"):
        interpreter.step("play", **{**valid, "amount": True})
    assert interpreter.serialize() == before

    with pytest.raises(ToolError, match="integer_out_of_range:amount:8:2:7"):
        interpreter.step("play", **{**valid, "amount": 8})
    assert interpreter.serialize() == before

    interpreter.step("play", **{**valid, "amount": 6})
    assert interpreter.state["v_last_wager"] == 6


def test_generic_policy_can_complete_a_game_with_integer_inputs() -> None:
    compiled = compile_composed(parse_design_ir(wager_ir()), core_registry())
    report = playtest(compiled.plan, core_registry(), bot_action, seeds=(0, 1, 7))
    assert report.ok, report.failures


def test_session_rejects_an_out_of_range_integer_without_advancing(tmp_path) -> None:
    compiled = compile_composed(parse_design_ir(wager_ir()), core_registry())
    report = playtest(compiled.plan, core_registry(), bot_action, seeds=(0,))
    store = SessionStore(tmp_path / "integer-input.sqlite")
    store.register_plan(
        "integer-input", compiled.plan.model_dump(mode="json"), report.as_dict()
    )
    session = store.create("integer-input", seed=0)
    action = session.interpreter.view("player-1")["actions"][0]
    card = next(item for item in action["inputs"] if item["id"] == "card")
    inputs = {"card": [card["options"][0]], "amount": 8}

    with pytest.raises(ToolError, match="integer_out_of_range"):
        store.act_generic(session.id, "play", inputs, revision=0)
    assert store.get(session.id).revision == 0

    inputs["amount"] = 2
    response = store.act_generic(session.id, "play", inputs, revision=0)
    assert response["state"]["revision"] == 1
    assert store.get(session.id).interpreter.state["finished"] is True


def test_integer_bounds_are_typed_and_cannot_depend_on_the_same_action_input() -> None:
    wrong_type = wager_ir()
    wrong_type["actions"][0]["inputs"][-1]["minimum"] = {"op": "lit", "value": "2"}
    with pytest.raises(ValidationError, match="integer_bound_type_mismatch"):
        parse_design_ir(wrong_type)

    circular = wager_ir()
    circular["actions"][0]["inputs"][-1]["minimum"] = {
        "op": "ref", "path": "input.amount"
    }
    with pytest.raises(ValidationError, match="integer_bound_cannot_read_action_input"):
        parse_design_ir(circular)
