from __future__ import annotations

import pytest
from test_g2_m2 import scenario_a_ir

from pocker_agent.core import Interpreter, core_registry
from pocker_agent.core.ir import parse_design_ir
from pocker_agent.core.rules import compile_composed


def test_a_guard_cannot_read_a_hidden_card_identity() -> None:
    payload = scenario_a_ir()
    payload["actions"][0]["guard"] = {
        "op": "eq",
        "left": {"op": "top", "zone": "stock", "field": "rank"},
        "right": {"op": "lit", "value": "A"},
    }
    with pytest.raises(ValueError, match="guard_reads_hidden_card_identity:play:stock"):
        parse_design_ir(payload)


def test_an_action_cannot_select_cards_from_a_hidden_shared_zone() -> None:
    payload = scenario_a_ir()
    payload["actions"][0]["inputs"][0].update({"zone": "stock", "scope": "shared"})
    with pytest.raises(ValueError, match="action_input_reads_hidden_zone:play:card"):
        parse_design_ir(payload)


def test_a_match_constraint_cannot_reveal_the_top_of_a_hidden_zone() -> None:
    payload = scenario_a_ir()
    payload["actions"][0]["effects"][0]["match_top"] = "stock"
    with pytest.raises(ValueError, match="select_match_zone_must_be_public:play:stock"):
        parse_design_ir(payload)


def test_finishing_does_not_implicitly_reveal_hidden_zones() -> None:
    compiled = compile_composed(parse_design_ir(scenario_a_ir()), core_registry())
    interpreter = Interpreter(compiled.plan, core_registry(), seed=0)
    interpreter.setup()
    assert interpreter.state["zones"]["stock"]["cards"]
    interpreter.state["finished"] = True
    stock = interpreter.view("player-1")["zones"]["stock"]
    assert stock["visible"] is False
    assert stock["cards"] == []


def test_a_guard_cannot_make_host_state_visible_through_legal_actions() -> None:
    payload = scenario_a_ir()
    payload["variables"] = [{
        "name": "secret_gate", "type": "boolean", "visibility": "host", "initial": True,
    }]
    payload["actions"][0]["guard"] = {"op": "ref", "path": "variables.secret_gate"}
    with pytest.raises(ValueError, match="guard_reads_host_variable:play:secret_gate"):
        parse_design_ir(payload)
