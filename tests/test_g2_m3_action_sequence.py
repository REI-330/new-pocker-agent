"""G2 / M3 action sequence: ordered multi-action turns (ADR-0012).

Two-action turns are the smallest shape scenario B needs. These tests pin the
guard-selection chain, the single-action regression (node names unchanged), the
all-guards-fail turn, validation, and the bot/run protocol.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError
from test_g2_m2 import scenario_a_ir

from pocker_agent.core import Interpreter, compile_composed, core_registry, run_bots
from pocker_agent.core.decision import policy_context
from pocker_agent.core.ir import parse_design_ir
from pocker_agent.core.policy import bot_action


def _guard(count: int) -> dict:
    return {"op": "lt", "left": {"op": "ref", "path": "action_count"},
            "right": {"op": "lit", "value": count}}


def two_action_ir() -> dict:
    """``draw`` only while the first action has not happened, then ``pass``."""
    return {
        "schema_version": "0.5", "kind": "composed",
        "meta": {"title": "两动作回合", "description": "ADR-0012 最小验证。"},
        "requirements": [],
        "players": {"count": 2},
        "deck": {"ranks": ["2", "3", "4", "5"], "suits": ["S", "H"], "copies": 1,
                 "values": {str(n): n for n in range(2, 6)}},
        "zones": [
            {"id": "hand", "visibility": "public", "scope": "player"},
            {"id": "stock", "visibility": "hidden", "scope": "shared"},
        ],
        "setup": {"deals": [{"zone": "hand", "count": 2, "per_seat": True}],
                  "stock_zone": "stock"},
        "variables": [],
        "actions": [
            {"id": "draw", "guard": _guard(1), "inputs": [],
             "effects": [{"kind": "refill", "from_zone": "stock", "to_zone": "hand",
                          "target_count": 3, "max_draw": 3}]},
            {"id": "pass", "inputs": [], "effects": []},
        ],
        "flow": {"round_action": "draw", "turn_actions": ["draw", "pass"],
                 "start_seat": "seat0", "resolve": []},
        "scoring": [],
        "terminal": {"max_actor_actions": 4},
        "macros": [],
    }


def all_guards_fail_ir() -> dict:
    payload = two_action_ir()
    payload["actions"][0]["guard"] = _guard(0)          # never true
    payload["actions"][1]["guard"] = _guard(0)          # never true either
    payload["terminal"] = {"max_rounds": 2}
    return payload


def test_a_two_action_turn_offers_the_first_passing_guard():
    compiled = compile_composed(parse_design_ir(two_action_ir()), core_registry())
    interpreter = Interpreter(compiled.plan, core_registry(), seed=0)
    interpreter.setup()
    assert interpreter.legal_actions() == ["draw"]      # action_count == 0
    assert bot_action(policy_context(interpreter)) == ("draw", {})
    interpreter.step("draw")
    assert interpreter.state["action_count"] == 1
    assert interpreter.legal_actions() == ["pass"]      # first guard now false
    assert bot_action(policy_context(interpreter)) == ("pass", {})


def test_run_bots_completes_a_two_action_turn_game():
    compiled = compile_composed(parse_design_ir(two_action_ir()), core_registry())
    interpreter = Interpreter(compiled.plan, core_registry(), seed=1)
    interpreter.setup()
    run_bots(interpreter, human_index=-1)
    assert interpreter.state["finished"] is True
    assert interpreter.state["action_count"] == 4       # pass still counts
    assert len(interpreter.state["zones"]["hand-0"]["cards"]) == 3


def test_a_turn_with_all_guards_failing_advances_without_an_action():
    compiled = compile_composed(parse_design_ir(all_guards_fail_ir()), core_registry())
    interpreter = Interpreter(compiled.plan, core_registry(), seed=0)
    interpreter.setup()                                 # the whole game auto-advances
    assert interpreter.state["finished"] is True
    assert interpreter.state["action_count"] == 0       # no action ever ran
    assert interpreter.state["round"] == 3              # two rounds played
    assert interpreter.legal_actions() == []


def test_single_action_rules_keep_the_m2_node_names():
    compiled = compile_composed(parse_design_ir(scenario_a_ir()), core_registry())
    waits = {node_id for node_id, node in compiled.plan.nodes.items()
             if node.kind == "wait"}
    assert waits == {"turn"}                             # not turn_<action>
    assert "pre_turn" not in compiled.plan.nodes          # scenario A has no guard


def test_the_action_sequence_is_validated():
    payload = two_action_ir()
    payload["flow"]["turn_actions"] = ["draw", "missing"]
    with pytest.raises(ValidationError, match="turn_action_unknown:missing"):
        parse_design_ir(payload)
    payload = two_action_ir()
    payload["flow"]["turn_actions"] = ["draw", "draw"]
    with pytest.raises(ValidationError, match="turn_action_duplicate"):
        parse_design_ir(payload)
    payload = two_action_ir()
    payload["flow"]["turn_actions"] = ["pass"]
    with pytest.raises(ValidationError, match="round_action_not_in_sequence"):
        parse_design_ir(payload)
