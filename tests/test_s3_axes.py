"""S3 axes: turn_adapter (tricks/follow-suit) and team, proven by Whist."""
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
from pocker_agent.core.decision import policy_context
from pocker_agent.core.ir import WhistIR
from pocker_agent.core.playtest import card_first
from pocker_agent.core.tools import TrickTool


def card(rank: str, suit: str, value: int) -> CardRef:
    return C(f"{rank}{suit}", rank, suit, value)


def whist_ir(**overrides) -> dict:
    payload = {"kind": "whist", "game_id": "my-whist", "title": "我的 Whist", "cards_each": 5}
    payload.update(overrides)
    return payload


def whist_state() -> dict:
    return {"hands": [[card("2", "S", 2), card("9", "H", 9)],
                      [card("K", "S", 13), card("3", "H", 3)],
                      [card("7", "S", 7)],
                      [card("4", "S", 4)]],
            "led_suit": "", "trick": [], "trick_seats": [], "table": [],
            "tricks_won": [0, 0, 0, 0], "trick_index": 0, "tricks_total": 2,
            "current_player": 0, "finished": False, "winners": []}


# ------------------------------------------------------------- turn_adapter

def test_follow_suit_is_enforced():
    trick = TrickTool([[0, 2], [1, 3]])
    state = whist_state()
    state["led_suit"] = "H"                      # hearts led
    hand = [card("2", "S", 2), card("9", "H", 9)]
    state["hands"][0] = hand
    assert trick.legal(state, 0) == [1]          # only the heart is legal
    with pytest.raises(ToolError, match="must_follow_suit"):
        trick.play(state, 0, 0)


def test_a_void_hand_may_play_anything():
    trick = TrickTool([[0, 2], [1, 3]])
    state = whist_state()
    state["led_suit"] = "H"
    state["hands"][0] = [card("2", "S", 2), card("5", "D", 5)]
    assert trick.legal(state, 0) == [0, 1]


def test_trick_winner_uses_trump_then_led_suit():
    trick = TrickTool([[0, 2], [1, 3]])
    state = whist_state()
    state["hands"] = [[card("2", "S", 2)], [card("K", "S", 13)],
                      [card("3", "H", 3)], [card("4", "S", 4)]]
    state["tricks_total"] = 1
    trick.play(state, 0, 0)                       # 2S leads spades
    trick.play(state, 1, 0)                       # KS
    trick.play(state, 2, 0)                       # 3H (void in spades)
    result = trick.play(state, 3, 0)              # 4S
    assert result["winner"] == 1                  # highest spade
    assert state["tricks_won"] == [0, 1, 0, 0]


def test_trump_beats_the_led_suit():
    trick = TrickTool([[0, 2], [1, 3]])
    state = whist_state()
    state["hands"] = [[card("A", "S", 14)], [card("2", "S", 2)],
                      [card("3", "H", 3)], [card("4", "S", 4)]]
    state["tricks_total"] = 1
    trick.play(state, 0, 0)
    trick.play(state, 1, 0)
    trick.play(state, 2, 0)
    result = trick.play(state, 3, 0, trump="H")   # hearts are trump
    assert result["winner"] == 2                  # the only trump wins
    assert state["tricks_won"] == [0, 0, 1, 0]


def test_team_winners_split_the_tricks():
    trick = TrickTool([[0, 2], [1, 3]])
    assert trick.team_winners([3, 1, 2, 0], 4) == [0, 2]     # team A: 5 vs 1
    assert trick.team_winners([1, 1, 1, 1], 4) == [0, 1, 2, 3]  # shared
    assert trick.team_winners([0, 4, 1, 0], 4) == [1, 3]     # team B: 4 vs 1


# -------------------------------------------------------------------- team

def test_whist_ir_compiles_and_reports_axes():
    ir = parse_ir(whist_ir())
    assert isinstance(ir, WhistIR)
    assert required_axes(ir) == ["sequential_turn", "turn_adapter", "team", "pattern_lang"]
    assert check_ir(ir).expressible is True
    assert host_compile(ir).game_kind == "whist"


def test_whist_ir_requires_a_valid_team_partition():
    # A different partnership is fine as long as it partitions the seats.
    assert parse_ir(whist_ir(teams=[[0, 1], [2, 3]])).teams == [[0, 1], [2, 3]]
    with pytest.raises(ValueError, match="teams"):
        parse_ir(whist_ir(teams=[[0, 2], [0, 3]]))    # duplicate seat
    with pytest.raises(ValueError):
        parse_ir(whist_ir(cards_each=20))


def test_whist_playtest_passes_for_three_seeds():
    plan = host_compile(parse_ir(whist_ir()))
    report = playtest(plan, core_registry(), card_first, seeds=(0, 7, 23))
    assert report.ok, report.failures


def test_whist_full_game_ends_with_a_team_winner_and_private_hands():
    plan = host_compile(parse_ir(whist_ir(cards_each=4)))
    interpreter = Interpreter(plan, core_registry(), seed=7)
    interpreter.setup()

    view = interpreter.view("player-1")
    assert view["private_hands"] is True
    assert len(view["players"][0]["hand"]) == 4
    assert all(player["hand"] == [] and player["hidden_count"] == 4
               for player in view["players"][1:])

    for _ in range(400):
        if interpreter.state["finished"]:
            break
        action, payload = card_first(policy_context(interpreter))
        interpreter.step(action, **payload)

    assert interpreter.state["finished"] is True
    winners = set(interpreter.state["winners"])
    assert winners in ({0, 2}, {1, 3}, {0, 1, 2, 3})     # a team, or a shared tie
    assert sum(interpreter.state["tricks_won"]) == 4
