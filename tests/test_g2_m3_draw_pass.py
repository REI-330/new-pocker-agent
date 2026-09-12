"""G2 / M3 draw and pass mechanisms (ADR-0012 scenario B prerequisites).

A ``draw`` effect takes a fixed, bounded number of cards from a shared stock into
a player zone; ``pass`` is an action with no effects. A turn selects ``draw``
while the stock is non-empty and ``pass`` otherwise, so both waits are reachable
without any game-specific policy.
"""
from __future__ import annotations

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
from pocker_agent.core.verify import (
    VERIFICATION_SEEDS,
    VERIFICATION_STRATEGIES,
    verify_composed,
)


def draw_pass_ir(*, draw_count: int = 1, actions: int = 16) -> dict:
    return {
        "schema_version": "0.5", "kind": "composed",
        "meta": {"title": "draw/pass test", "description": "ADR-0012 B9."},
        "requirements": [],
        "players": {"count": 2},
        "deck": {"ranks": [str(n) for n in range(2, 10)], "suits": ["S", "H"],
                 "copies": 1, "values": {str(n): n for n in range(2, 10)}},
        "zones": [
            {"id": "hand", "visibility": "owner_only", "scope": "player"},
            {"id": "stock", "visibility": "hidden", "scope": "shared"},
        ],
        "setup": {"deals": [{"zone": "hand", "count": 2, "per_seat": True}],
                  "stock_zone": "stock"},
        "variables": [],
        "actions": [
            {"id": "draw",
             "guard": {"op": "gt", "left": {"op": "zone_count", "zone": "stock"},
                       "right": {"op": "lit", "value": 0}},
             "inputs": [],
             "effects": [{"kind": "draw", "from_zone": "stock", "to_zone": "hand",
                          "count": draw_count}]},
            {"id": "pass", "inputs": [], "effects": []},
        ],
        "flow": {"round_action": "draw", "turn_actions": ["draw", "pass"],
                 "start_seat": "seat0", "resolve": []},
        "scoring": [],
        "terminal": {"max_actor_actions": actions},
        "macros": [],
    }


def _started(payload: dict, seed: int = 0) -> Interpreter:
    compiled = compile_composed(parse_design_ir(payload), core_registry())
    interpreter = Interpreter(compiled.plan, core_registry(), seed=seed)
    interpreter.setup()
    return interpreter


def _hand(interpreter: Interpreter, seat: int) -> int:
    return len(interpreter.state["zones"][f"hand-{seat}"]["cards"])


def test_draw_moves_one_card_from_stock_to_the_actor_hand():
    interpreter = _started(draw_pass_ir())
    assert interpreter.legal_actions() == ["draw"]
    before_stock = len(interpreter.state["zones"]["stock"]["cards"])
    before_hand = _hand(interpreter, 0)
    interpreter.step("draw")
    assert _hand(interpreter, 0) == before_hand + 1
    assert len(interpreter.state["zones"]["stock"]["cards"]) == before_stock - 1
    assert interpreter.state["action_count"] == 1


def test_draw_is_bounded_by_the_stock_and_then_pass_is_offered():
    interpreter = _started(draw_pass_ir())
    seen = set()
    for _ in range(64):
        if interpreter.state["finished"]:
            break
        actions = interpreter.legal_actions()
        assert len(actions) == 1
        seen.add(actions[0])
        interpreter.step(actions[0])
    assert seen == {"draw", "pass"}                  # both waits were reachable
    assert interpreter.state["finished"] is True
    assert interpreter.state["zones"]["stock"]["cards"] == []
    assert interpreter.state["action_count"] == 16


def test_draw_more_than_the_stock_does_not_overdraw():
    interpreter = _started(draw_pass_ir(draw_count=5, actions=8))
    before = _hand(interpreter, 0)
    interpreter.step("draw")
    assert _hand(interpreter, 0) == before + 5
    interpreter.step("draw")                         # only four cards left
    assert interpreter.state["action_count"] == 2
    # The stock emptied and later turns pass; the hand never exceeds stock + 2.
    total = sum(len(entry["cards"])
                for entry in interpreter.state["zones"].values())
    assert total == 16


def test_draw_requires_a_shared_stock_and_a_player_target():
    payload = draw_pass_ir()
    payload["actions"][0]["effects"][0]["from_zone"] = "hand"
    with pytest.raises(ValidationError, match="draw_stock_must_be_shared"):
        parse_design_ir(payload)
    payload = draw_pass_ir()
    payload["actions"][0]["effects"][0]["to_zone"] = "stock"
    with pytest.raises(ValidationError, match="draw_target_must_be_player"):
        parse_design_ir(payload)


def test_the_draw_pass_plan_verifies_and_plays():
    ir = parse_design_ir(draw_pass_ir())
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
        assert interpreter.state["action_count"] == 16
        assert sum(len(entry["cards"])
                   for entry in interpreter.state["zones"].values()) == 16
