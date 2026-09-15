"""S2 axes: pattern_lang and info_set, proven by a real hidden-hand game."""
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
from pocker_agent.core.ir import SheddingIR
from pocker_agent.core.playtest import card_first
from pocker_agent.core.tools import PatternTool


def card(rank: str, suit: str = "S", value: int = 0) -> CardRef:
    return C(f"{rank}{suit}", rank, suit, value or 1)


def shedding_ir(**overrides) -> dict:
    payload = {"kind": "shedding", "game_id": "my-eights", "title": "我的疯狂八",
               "hand_size": 5, "wild_rank": "8"}
    payload.update(overrides)
    return payload


# ------------------------------------------------------------- pattern_lang

def test_pattern_match_by_suit_rank_or_wild():
    pattern = PatternTool()
    top = card("5", "H", 5)
    assert pattern.match(card("9", "H"), top) is True          # same suit
    assert pattern.match(card("5", "C"), top) is True          # same rank
    assert pattern.match(card("9", "C"), top) is False
    assert pattern.match(card("8", "C"), top, wild_ranks=["8"]) is True


def test_pattern_choices_returns_legal_indices():
    pattern = PatternTool()
    hand = [card("9", "C"), card("9", "H"), card("8", "C"), card("2", "S")]
    assert pattern.choices(hand, card("5", "H", 5), wild_ranks=["8"]) == [1, 2]


def test_pattern_classify_and_beats():
    pattern = PatternTool()
    pair = pattern.classify([card("7", "S", 7), card("7", "H", 7)], {"type": "same_rank", "size": 2})
    bigger = pattern.classify([card("9", "S", 9), card("9", "H", 9)], {"type": "same_rank", "size": 2})
    assert pair["kind"] == "same_rank" and pair["length"] == 2 and pair["rank"] == 7
    assert pattern.beats(bigger, pair) is True
    assert pattern.beats(pair, bigger) is False
    bomb = pattern.classify([card("4", "S", 4)] * 1, {"type": "single"})
    assert pattern.beats(bomb, pair, bombs=[]) is False
    with pytest.raises(ToolError, match="pattern_requires_same_rank"):
        pattern.classify([card("7"), card("8", "H", 8)], {"type": "same_rank"})


def test_pattern_run_requires_consecutive_distinct_ranks():
    pattern = PatternTool()
    run = pattern.classify([card("5", "S", 5), card("6", "H", 6), card("7", "D", 7)],
                           {"type": "run", "min_length": 3})
    assert run["kind"] == "run" and run["length"] == 3 and run["rank"] == 7
    with pytest.raises(ToolError, match="run_not_consecutive"):
        pattern.classify([card("5", "S", 5), card("7", "H", 7), card("9", "D", 9)], {"type": "run"})
    with pytest.raises(ToolError, match="run_requires_same_suit"):
        pattern.classify([card("5", "S", 5), card("6", "H", 6), card("7", "D", 7)],
                         {"type": "run", "same_suit": True})


# ---------------------------------------------------------------- info_set

def test_shedding_ir_compiles_and_reports_axes():
    ir = parse_ir(shedding_ir())
    assert isinstance(ir, SheddingIR)
    assert required_axes(ir) == ["sequential_turn", "pattern_lang", "info_set"]
    assert check_ir(ir).expressible is True
    assert host_compile(ir).game_kind == "crazy_eights"


def test_shedding_ir_is_validated():
    with pytest.raises(ValueError, match="wild_rank"):
        parse_ir(shedding_ir(wild_rank="Z"))
    with pytest.raises(ValueError):
        parse_ir(shedding_ir(hand_size=30))


def test_hidden_hands_are_private_per_viewer():
    plan = host_compile(parse_ir(shedding_ir()))
    interpreter = Interpreter(plan, core_registry(), seed=5)
    interpreter.setup()

    mine = interpreter.view("player-1")
    theirs = interpreter.view("player-2")
    assert len(mine["players"][0]["hand"]) == 5        # I see my own hand
    assert mine["players"][1]["hand"] == []            # opponent is private
    assert mine["players"][1]["hidden_count"] == 5
    assert len(theirs["players"][1]["hand"]) == 5      # and vice versa
    assert theirs["players"][0]["hidden_count"] == 5
    assert mine["private_hands"] is True


def test_crazy_eights_playtest_passes_and_finishes():
    plan = host_compile(parse_ir(shedding_ir()))
    report = playtest(plan, core_registry(), card_first, seeds=(0, 7, 23))
    assert report.ok, report.failures


def test_crazy_eights_full_game_and_reveal_at_end():
    plan = host_compile(parse_ir(shedding_ir(hand_size=4)))
    interpreter = Interpreter(plan, core_registry(), seed=3)
    interpreter.setup()
    for _ in range(600):
        if interpreter.state["finished"]:
            break
        action, payload = card_first(policy_context(interpreter))
        interpreter.step(action, **payload)
    assert interpreter.state["finished"] is True
    assert len(interpreter.state["winners"]) == 1
    finished_view = interpreter.view("player-1")
    assert finished_view["players"][1]["hidden_count"] == 0   # hands revealed at the end


def test_opponent_hand_is_never_exposed_before_the_end():
    """The privacy guarantee, not just the field shape."""
    plan = host_compile(parse_ir(shedding_ir()))
    interpreter = Interpreter(plan, core_registry(), seed=11)
    interpreter.setup()
    opponent = {c.id for c in interpreter.state["hands"][1]}
    for _ in range(600):
        if interpreter.state["finished"]:
            break
        view = interpreter.view("player-1")
        leaked = {c["id"] for c in view["players"][1]["hand"]}
        assert not (leaked & opponent)
        action, payload = card_first(policy_context(interpreter))
        interpreter.step(action, **payload)
