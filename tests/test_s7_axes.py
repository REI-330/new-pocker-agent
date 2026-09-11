"""S7 axis: trigger (declarative special-card effects), proven by a UNO-like game."""
from __future__ import annotations

import pytest

from pocker_agent.core import (
    CardRef,
    Interpreter,
    check_ir,
    core_registry,
    host_compile,
    parse_ir,
    playtest,
    required_axes,
)
from pocker_agent.core.cards import CardRef as C
from pocker_agent.core.contracts import ToolError
from pocker_agent.core.ir import UnoIR
from pocker_agent.core.playtest import card_first
from pocker_agent.core.reference import REFERENCE_GAMES
from pocker_agent.core.tools import LogicTool
from pocker_agent.core.trigger_tools import TriggerTool


def card(rank: str, suit: str = "S") -> CardRef:
    return C(f"{rank}{suit}", rank, suit, 0)


def uno_ir(**overrides) -> dict:
    payload = {"kind": "uno", "game_id": "my-uno", "title": "我的UNO", "hand_size": 5}
    payload.update(overrides)
    return payload


def table() -> dict:
    return {"hands": [[card("5")], [card("7")]], "stock": [card("9"), card("4"), card("3")],
            "table": [card("2", "H")], "current_player": 0, "skip": 0, "direction": 1}


# -------------------------------------------------------------------- logic

def test_logic_gains_mul_and_mod_for_turn_maths():
    logic = LogicTool()
    assert logic.evaluate({"mul": [3, 4]}) == 12
    assert logic.evaluate({"mod": [7, 3]}) == 1
    assert logic.evaluate({"mod": [{"add": [-1, 1]}, 2]}) == 0


# ------------------------------------------------------------------ trigger

def test_draw_effect_targets_the_next_seat_and_conserves_cards():
    trigger = TriggerTool()
    state = table()
    before = len(state["stock"]) + sum(len(hand) for hand in state["hands"])
    result = trigger.apply(state, [{"do": "draw", "target": "next", "count": 2}])
    assert result["applied"][0]["seats"] == [1]
    assert len(state["hands"][1]) == 3
    after = len(state["stock"]) + sum(len(hand) for hand in state["hands"])
    assert after == before


def test_skip_and_reverse_and_active_suit():
    trigger = TriggerTool()
    state = table()
    trigger.apply(state, [{"do": "skip", "count": 1}])
    assert state["skip"] == 1
    trigger.apply(state, [{"do": "reverse"}])
    assert state["direction"] == -1
    trigger.apply(state, [{"do": "set_active_suit", "suit": "H"}])
    assert state["active_suit"] == "H"


def test_effects_are_validated():
    trigger = TriggerTool()
    with pytest.raises(ToolError, match="unknown_effect"):
        trigger.apply(table(), [{"do": "explode"}])
    with pytest.raises(ToolError, match="effects_must_be_a_list"):
        trigger.apply(table(), {"do": "skip"})
    with pytest.raises(ToolError, match="set_active_suit_requires_suit"):
        trigger.apply(table(), [{"do": "set_active_suit"}])


def test_draw_recycles_the_discard_when_the_stock_runs_out():
    trigger = TriggerTool()
    state = {"hands": [[card("5")], [card("7")]], "stock": [],
             "table": [card("9", "H"), card("8", "H"), card("2", "H")],
             "current_player": 0}
    result = trigger.apply(state, [{"do": "draw", "target": "next", "count": 2}])
    assert result["applied"][0]["drawn"] == 2
    assert len(state["table"]) == 1                       # the top card is kept


# ---------------------------------------------------------------------- uno

def test_uno_ir_compiles_and_reports_axes():
    ir = parse_ir(uno_ir())
    assert isinstance(ir, UnoIR)
    assert required_axes(ir) == ["sequential_turn", "pattern_lang", "info_set", "trigger"]
    assert check_ir(ir).expressible is True
    assert host_compile(ir).game_kind == "uno"


def test_uno_ir_validation():
    with pytest.raises(ValueError, match="特殊牌点数"):
        parse_ir(uno_ir(skip_rank="ZZ"))
    with pytest.raises(ValueError):
        parse_ir(uno_ir(hand_size=40))


def test_uno_playtest_passes_for_three_seeds():
    game = REFERENCE_GAMES["uno"]
    report = playtest(game.build(), core_registry(), card_first, seeds=(0, 7, 23))
    assert report.ok, report.failures


def test_uno_full_game_hides_hands_and_terminates():
    plan = host_compile(parse_ir(uno_ir()))
    interpreter = Interpreter(plan, core_registry(), seed=5)
    interpreter.setup()
    view = interpreter.view("player-1")
    assert view["private_hands"] is True and view["players"][1]["hand"] == []

    for _ in range(1000):
        if interpreter.state["finished"]:
            break
        action, payload = card_first(interpreter)
        interpreter.step(action, **payload)
    assert interpreter.state["finished"] is True
    assert interpreter.state["winners"]


def test_uno_draw_two_effect_is_applied_by_the_plan():
    """A played '2' must make the next seat draw two and lose a turn."""
    plan = host_compile(parse_ir(uno_ir(hand_size=4)))
    interpreter = Interpreter(plan, core_registry(), seed=7)
    interpreter.setup()
    # Force the seat to hold a '2' that matches the top card by suit.
    top = interpreter.state["table"][-1]
    interpreter.state["hands"][0] = [card("2", top.suit), card("9", top.suit)]
    interpreter.state["hands"][1] = [card("5", "H"), card("6", "H")]
    interpreter.pc = "turn"
    interpreter.advance()                                # recompute the choices
    before = len(interpreter.state["hands"][1])
    interpreter.step("play", card_index=0)
    assert len(interpreter.state["hands"][1]) == before + 2
    assert interpreter.state["current_player"] == 0        # skipped back to the player
    assert interpreter.state["skip"] == 0                  # the plan consumed the skip
