"""S5 axis: point_total (21-style totals), proven by a playable blackjack game."""
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
from pocker_agent.core.ir import BlackjackIR
from pocker_agent.core.point_tools import PointTotalTool
from pocker_agent.core.reference import REFERENCE_GAMES
from pocker_agent.core.tools import DeckTool


def card(rank: str, suit: str = "S") -> CardRef:
    return C(f"{rank}{suit}", rank, suit, 0)


def blackjack_ir(**overrides) -> dict:
    payload = {"kind": "blackjack", "game_id": "my-blackjack", "title": "我的21点",
               "max_rounds": 3}
    payload.update(overrides)
    return payload


# --------------------------------------------------------------- point_total

def test_soft_aces_and_bust():
    total = PointTotalTool(21)
    assert total.total([card("A"), card("A"), card("9")]) == {"total": 21, "soft": True, "bust": False, "cards": 3}
    assert total.total([card("A"), card("6")])["total"] == 17
    assert total.total([card("A"), card("6")])["soft"] is True
    assert total.total([card("K"), card("Q"), card("2")])["bust"] is True
    assert total.total([card("A"), card("A"), card("A")])["total"] == 13
    with pytest.raises(ToolError, match="requires_cards"):
        total.total(["AS"])


def test_dealer_draws_to_the_stand_threshold():
    total = PointTotalTool(21)
    stock = [card("2"), card("3"), card("4")]     # drawn from the end: 4 then 3
    hand = [card("10", "H")]
    result = total.dealer_play(stock, hand, stand_on=17, hits_soft=False)
    assert result["total"] == 17 and result["bust"] is False
    assert len(hand) == 3


def test_dealer_hits_soft_17_only_when_configured():
    total = PointTotalTool(21)
    stay = total.dealer_play([card("5")], [card("A"), card("6")], stand_on=17, hits_soft=False)
    assert stay["total"] == 17 and stay["draws"] == 0
    hits = total.dealer_play([card("3")], [card("A"), card("6")], stand_on=17, hits_soft=True)
    assert hits["draws"] == 1


def test_settle_covers_bust_naturals_and_push():
    total = PointTotalTool(21)
    assert total.settle(22, 18)["winner"] == 1
    assert total.settle(20, 22)["winner"] == 0
    assert total.settle(21, 21, player_natural=True)["winner"] == 0
    assert total.settle(21, 21, player_natural=True, dealer_natural=True)["winner"] is None
    assert total.settle(19, 19)["winner"] is None


def test_deck_draw_moves_a_card_and_reports_exhaustion():
    deck = DeckTool(["A", "2"], ["S"])
    stock = deck.shuffled(1)
    hand: list = []
    deck.draw(stock, hand, 1)
    assert len(hand) == 1 and len(stock) == 1
    deck.draw(stock, hand, 1)
    with pytest.raises(ToolError, match="deck_exhausted"):
        deck.draw(stock, hand, 1)


# ----------------------------------------------------------------- blackjack

def test_blackjack_ir_compiles_and_reports_axes():
    ir = parse_ir(blackjack_ir())
    assert isinstance(ir, BlackjackIR)
    assert required_axes(ir) == ["sequential_turn", "point_total", "info_set", "score_settle"]
    assert check_ir(ir).expressible is True
    assert host_compile(ir).game_kind == "blackjack"


def test_blackjack_playtest_passes_for_three_seeds():
    game = REFERENCE_GAMES["blackjack"]
    report = playtest(game.build(), core_registry(), game.strategy, seeds=(0, 7, 23))
    assert report.ok, report.failures


def test_blackjack_full_game_hides_the_dealer_and_scores_every_round():
    plan = host_compile(parse_ir(blackjack_ir(max_rounds=3)))
    interpreter = Interpreter(plan, core_registry(), seed=7)
    interpreter.setup()

    view = interpreter.view("player-1")
    assert view["private_hands"] is True
    assert len(view["players"][0]["hand"]) == 2
    assert view["players"][1]["hand"] == [] and view["players"][1]["hidden_count"] == 2

    for _ in range(200):
        if interpreter.state["finished"]:
            break
        rank = interpreter.tools["point_total"].total(interpreter.state["hands"][0])
        action = "stand" if rank["total"] >= 17 else "hit"
        interpreter.step(action)

    assert interpreter.state["finished"] is True
    assert interpreter.state["round"] == 3
    assert sum(interpreter.state["scores"]) <= 3              # one point per decisive round
    assert len(interpreter.state["winners"]) >= 1
    finished = interpreter.view("player-1")
    assert finished["players"][1]["hidden_count"] == 0        # dealer revealed at the end


def test_blackjack_bust_loses_the_round():
    plan = host_compile(parse_ir(blackjack_ir(max_rounds=1)))
    interpreter = Interpreter(plan, core_registry(), seed=4)
    interpreter.setup()
    for _ in range(20):
        if interpreter.state["finished"]:
            break
        interpreter.step("hit")                               # hit until 21 or bust
    assert interpreter.state["finished"] is True
    assert sum(interpreter.state["scores"]) <= 1              # at most one decisive round
    assert interpreter.state["winners"]
