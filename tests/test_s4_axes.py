"""S4 axes: betting, ledger and poker hand_rank, proven by a betting showdown."""
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
from pocker_agent.core.ir import PokerIR
from pocker_agent.core.poker_tools import BettingTool, HandRankTool, LedgerTool
from pocker_agent.core.policy import bot_action


def card(rank: str, suit: str, value: int) -> CardRef:
    return C(f"{rank}{suit}", rank, suit, value)


def poker_ir(**overrides) -> dict:
    payload = {"kind": "poker", "game_id": "my-poker", "title": "我的摊牌",
               "stacks": 100, "min_raise": 10}
    payload.update(overrides)
    return payload


def betting_state(stacks=(50, 50)) -> dict:
    return {"stacks": list(stacks), "committed": [0] * len(stacks),
            "hand_committed": [0] * len(stacks), "folded": [], "acted": [],
            "current_bet": 0, "min_raise": 10, "current_player": 0}


# ----------------------------------------------------------------- hand_rank

def test_poker_hand_orders_the_categories():
    hand = HandRankTool()
    royal = [card("A", "S", 14), card("K", "S", 13), card("Q", "S", 12),
             card("J", "S", 11), card("10", "S", 10)]
    quads = [card("9", "S", 9), card("9", "H", 9), card("9", "D", 9), card("9", "C", 9),
             card("2", "S", 2)]
    wheel = [card("A", "S", 14), card("2", "H", 2), card("3", "D", 3), card("4", "C", 4),
             card("5", "S", 5)]
    pair = [card("K", "S", 13), card("K", "H", 13), card("7", "D", 7), card("5", "C", 5),
            card("3", "S", 3)]
    assert hand.best(royal)["category"] == "straight_flush"
    assert hand.best(quads)["category"] == "four_of_a_kind"
    assert hand.best(wheel)["category"] == "straight"
    assert hand.best(wheel)["score"][1] == 5            # the wheel plays as a five
    assert hand.compare(royal, quads)["outcome"] == "left"
    assert hand.compare(pair, pair)["outcome"] == "tie"
    assert hand.compare(quads, royal)["outcome"] == "right"


def test_poker_hand_picks_the_best_five_of_seven():
    hand = HandRankTool()
    seven = [card("A", "S", 14), card("A", "H", 14), card("A", "D", 14), card("K", "S", 13),
             card("K", "H", 13), card("2", "D", 2), card("7", "C", 7)]
    best = hand.best(seven)
    assert best["category"] == "full_house" and len(best["cards"]) == 5
    with pytest.raises(ToolError, match="at_least_five"):
        hand.best([card("A", "S", 14)])


# -------------------------------------------------------------------- ledger

def test_ledger_commit_and_pots_with_a_side_pot():
    ledger = LedgerTool()
    state = betting_state((50, 50, 50))
    ledger.commit(state, 0, 30)
    ledger.commit(state, 1, 50)
    ledger.commit(state, 2, 10)
    pots = ledger.pots(state)
    assert [pot["amount"] for pot in pots] == [30, 40, 20]     # 3 levels
    assert pots[0]["eligible"] == [0, 1, 2]
    assert pots[-1]["refund_to"] is None or pots[-1]["amount"] > 0


def test_ledger_settlement_awards_odd_chips_and_conserves_chips():
    ledger = LedgerTool()
    state = betting_state((50, 50, 50))
    for seat, amount in ((0, 10), (1, 10), (2, 10)):
        ledger.commit(state, seat, amount)
    before_total = sum(state["stacks"]) + sum(state["committed"])
    result = ledger.settle(state, {0: [0, 1, 2]})               # shared, odd chip
    assert sum(result["awards"]) == 30
    assert sum(state["stacks"]) + sum(state["committed"]) == before_total
    assert sorted(result["awards"]) == [10, 10, 10]


def test_ledger_refunds_an_unmatched_contribution():
    ledger = LedgerTool()
    state = betting_state((50, 50))
    ledger.commit(state, 0, 40)
    ledger.commit(state, 1, 10)
    pots = ledger.pots(state)
    assert any(pot["refund_to"] == 0 for pot in pots)
    result = ledger.settle(state, {0: [0]})                     # only the contested pot
    assert sum(result["awards"]) == 50
    assert sum(state["stacks"]) == 100


# ------------------------------------------------------------------- betting

def test_betting_offers_only_legal_actions_and_completes_a_round():
    betting = BettingTool(min_raise=10)
    state = betting_state()
    assert betting.legal(state) == ["fold", "check", "raise", "all_in"]
    betting.act(state, "check")
    assert state["current_player"] == 1 and state["street_done"] is False
    betting.act(state, "check")
    assert state["street_done"] is True


def test_betting_raise_validation_and_call_amount():
    betting = BettingTool(min_raise=10)
    state = betting_state()
    with pytest.raises(ToolError, match="invalid_raise"):
        betting.act(state, "raise", amount=5)                   # below min raise
    betting.act(state, "raise", amount=20)
    assert state["current_bet"] == 20 and state["committed"] == [20, 0]
    assert betting.legal(state) == ["fold", "call", "raise", "all_in"]
    betting.act(state, "call")
    assert state["committed"] == [20, 20] and state["street_done"] is True


def test_betting_all_in_and_fold_end_the_round():
    betting = BettingTool(min_raise=10)
    state = betting_state()
    betting.act(state, "all_in")
    assert state["stacks"][0] == 0 and state["current_bet"] == 50
    betting.act(state, "fold")
    assert 1 in state["folded"] and state["street_done"] is True


# --------------------------------------------------------------------- poker

def test_poker_ir_compiles_and_reports_axes():
    ir = parse_ir(poker_ir())
    assert isinstance(ir, PokerIR)
    assert required_axes(ir) == ["sequential_turn", "betting", "ledger", "hand_rank", "info_set"]
    assert check_ir(ir).expressible is True
    assert host_compile(ir).game_kind == "five_card_poker"


def test_poker_ir_validation():
    with pytest.raises(ValueError, match="min_raise"):
        parse_ir(poker_ir(min_raise=500))
    with pytest.raises(ValueError):
        parse_ir(poker_ir(cards_each=4))


def test_poker_playtest_passes_for_three_seeds():
    plan = host_compile(parse_ir(poker_ir()))
    report = playtest(plan, core_registry(), bot_action, seeds=(0, 7, 23))
    assert report.ok, report.failures


def test_poker_full_game_reaches_showdown_and_conserves_chips():
    plan = host_compile(parse_ir(poker_ir(stacks=100, min_raise=10)))
    interpreter = Interpreter(plan, core_registry(), seed=7)
    interpreter.setup()
    assert interpreter.view("player-1")["players"][1]["hidden_count"] == 5

    for _ in range(200):
        if interpreter.state["finished"]:
            break
        action, payload = bot_action(interpreter)
        interpreter.step(action, **payload)

    assert interpreter.state["finished"] is True
    assert sum(interpreter.state["stacks"]) == 200          # chips are conserved
    assert interpreter.state["winners"]
    assert interpreter.state["pot"] == 0


def test_poker_fold_ends_the_hand_without_a_showdown():
    plan = host_compile(parse_ir(poker_ir()))
    interpreter = Interpreter(plan, core_registry(), seed=3)
    interpreter.setup()
    interpreter.step("fold")
    assert interpreter.state["finished"] is True
    assert interpreter.state["winners"] == [1]              # the non-folding seat
    assert sum(interpreter.state["stacks"]) == 200
