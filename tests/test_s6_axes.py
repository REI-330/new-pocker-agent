"""S6 axis: hidden_draw (taking from a hidden hand), proven by playable Go Fish."""
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
from pocker_agent.core.hidden_tools import HiddenDrawTool
from pocker_agent.core.ir import GoFishIR
from pocker_agent.core.playtest import resilient_first
from pocker_agent.core.reference import REFERENCE_GAMES


def card(rank: str, suit: str = "S") -> CardRef:
    return C(f"{rank}{suit}", rank, suit, 0)


def go_fish_ir(**overrides) -> dict:
    payload = {"kind": "go_fish", "game_id": "my-go-fish", "title": "我的钓鱼", "cards_each": 5}
    payload.update(overrides)
    return payload


def table() -> dict:
    return {"hands": [[card("7", "S"), card("7", "H"), card("2", "S")],
                      [card("7", "D"), card("9", "C")]],
            "stock": [card("K", "S"), card("3", "H")], "pairs": [0, 0], "current_player": 0}


# --------------------------------------------------------------- hidden_draw

def test_askable_lists_ranks_the_player_holds():
    tool = HiddenDrawTool()
    assert tool.askable(table()) == ["ask:7", "ask:2"]


def test_ask_transfers_every_matching_hidden_card():
    tool = HiddenDrawTool()
    state = table()
    result = tool.ask(state, "ask:7")
    assert result == {"got": 1, "fished": False, "source": 1}
    assert [card.rank for card in state["hands"][0]].count("7") == 3
    assert all(card.rank != "7" for card in state["hands"][1])


def test_ask_rejects_a_rank_the_asker_does_not_hold():
    tool = HiddenDrawTool()
    with pytest.raises(ToolError, match="must_hold_the_rank"):
        tool.ask(table(), "ask:K")
    with pytest.raises(ToolError, match="invalid_ask"):
        tool.ask(table(), "play")


def test_a_failed_ask_fishes_one_card_from_the_stock():
    tool = HiddenDrawTool()
    state = table()
    result = tool.ask(state, "ask:2")          # opponent has no 2
    assert result["fished"] is True and result["drawn"] == 1
    assert len(state["hands"][0]) == 4 and len(state["stock"]) == 1


def test_discard_pairs_scores_and_removes_cards():
    tool = HiddenDrawTool()
    state = table()
    result = tool.discard_pairs(state)
    assert result["pairs"] == 1 and result["total"] == 1
    assert [card.rank for card in state["hands"][0]].count("7") == 0
    assert state["pairs"] == [1, 0]


def test_refill_and_finished():
    tool = HiddenDrawTool()
    state = table()
    state["hands"][0] = []
    assert tool.refill(state)["drew"] == 1
    assert tool.is_finished(state) is False
    state["stock"] = []
    state["hands"][ 0] = []
    assert tool.is_finished(state) is True


# ------------------------------------------------------------------ go fish

def test_go_fish_ir_compiles_and_reports_axes():
    ir = parse_ir(go_fish_ir())
    assert isinstance(ir, GoFishIR)
    assert required_axes(ir) == ["sequential_turn", "hidden_draw", "info_set"]
    assert check_ir(ir).expressible is True
    assert host_compile(ir).game_kind == "go_fish"


def test_go_fish_ir_validation():
    with pytest.raises(ValueError, match="牌堆"):
        parse_ir(go_fish_ir(ranks=["A", "2", "3", "4"], cards_each=10))
    with pytest.raises(ValueError):
        parse_ir(go_fish_ir(cards_each=30))


def test_go_fish_playtest_passes_for_three_seeds():
    game = REFERENCE_GAMES["go_fish"]
    report = playtest(game.build(), core_registry(), resilient_first, seeds=(0, 7, 23))
    assert report.ok, report.failures


def test_go_fish_exposes_concrete_ask_actions_and_rejects_the_wildcard():
    plan = host_compile(parse_ir(go_fish_ir()))
    interpreter = Interpreter(plan, core_registry(), seed=7)
    interpreter.setup()

    actions = interpreter.legal_actions()
    assert actions and all(action.startswith("ask:") and action != "ask:*" for action in actions)
    with pytest.raises(ToolError, match="illegal_action"):
        interpreter.step("ask:*")                     # the literal wildcard is refused
    with pytest.raises(ToolError, match="illegal_action"):
        interpreter.step("ask:ZZ")                    # an unoffered rank is refused
    interpreter.step(actions[0])                      # a concrete option is accepted


def test_go_fish_full_game_hides_hands_and_scores_pairs():
    plan = host_compile(parse_ir(go_fish_ir()))
    interpreter = Interpreter(plan, core_registry(), seed=11)
    interpreter.setup()

    view = interpreter.view("player-1")
    assert view["private_hands"] is True
    assert len(view["players"][0]["hand"]) == 5
    assert view["players"][1]["hand"] == [] and view["players"][1]["hidden_count"] == 5

    for _ in range(2000):
        if interpreter.state["finished"]:
            break
        action, payload = resilient_first(interpreter)
        interpreter.step(action, **payload)

    assert interpreter.state["finished"] is True
    assert sum(interpreter.state["pairs"]) <= 26      # 52 cards -> at most 26 pairs
    assert interpreter.state["winners"]
    revealed = interpreter.view("player-1")
    assert revealed["players"][1]["hidden_count"] == 0
