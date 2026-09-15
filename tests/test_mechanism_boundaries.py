"""已确认机制边界的最小复现，防止规则字段被忽略或对象失真。"""
from __future__ import annotations

import pytest

from pocker_agent.core.cards import CardRef
from pocker_agent.core.contracts import ToolError
from pocker_agent.core.hidden_tools import HiddenDrawTool
from pocker_agent.core.point_tools import PointTotalTool
from pocker_agent.core.poker_tools import BettingTool, HandRankTool
from pocker_agent.core.tools import DeckTool, TrickTool, evaluate_expression
from pocker_agent.core.trigger_tools import TriggerTool


def card(card_id: str, rank: str, suit: str, value: int) -> CardRef:
    return CardRef(card_id, rank, suit, value)


def test_deck_rejects_duplicate_identity_dimensions():
    with pytest.raises(ToolError, match="deck_ranks_and_suits_must_be_unique"):
        DeckTool(["A", "A"], ["S"])
    with pytest.raises(ToolError, match="deck_ranks_and_suits_must_be_unique"):
        DeckTool(["A"], ["S", "S"])


def test_trigger_recycle_keeps_the_actual_top_card():
    bottom = card("2S", "2", "S", 2)
    middle = card("3S", "3", "S", 3)
    top = card("4S", "4", "S", 4)
    state = {"hands": [[], []], "stock": [], "table": [bottom, middle, top],
             "current_player": 0}
    TriggerTool().apply(state, [{"do": "draw", "target": "current", "count": 1}])
    assert state["table"] == [top]
    assert state["hands"][0][0] in {bottom, middle}


def test_hand_rank_rejects_the_same_card_twice():
    cards = [card(f"{rank}S", rank, "S", value)
             for rank, value in [("10", 10), ("J", 11), ("Q", 12), ("K", 13), ("A", 14)]]
    with pytest.raises(ToolError, match="poker_hand_requires_unique_cards"):
        HandRankTool().best([*cards, cards[0]])


def test_unsupported_bust_rule_is_not_silently_ignored():
    with pytest.raises(ToolError, match="unsupported_first_bust_loses_false"):
        PointTotalTool().settle(22, 18, first_bust_loses=False)


def test_hidden_draw_can_name_an_opponent_in_multiplayer_games():
    ace = card("AS", "A", "S", 14)
    other_ace = card("AH", "A", "H", 14)
    state = {"hands": [[ace], [], [other_ace]], "stock": [], "current_player": 0}
    result = HiddenDrawTool().ask(state, "ask:A", target=2)
    assert result == {"got": 1, "fished": False, "source": 2}
    assert state["hands"][2] == []


def test_trick_teams_must_partition_all_players():
    with pytest.raises(ToolError, match="teams_must_partition_players"):
        TrickTool([[0, 1], [1, 2]]).team_winners([1, 0, 0], 3)


def test_expression_modulo_by_zero_is_a_contract_error():
    with pytest.raises(ToolError, match="expression_modulo_by_zero"):
        evaluate_expression({"mod": [1, 0]})


@pytest.mark.parametrize("field,value", [("folded", ""), ("acted", {})])
def test_betting_rejects_falsey_non_list_seat_state(field, value):
    state = {
        "stacks": [100, 100], "committed": [0, 0], "hand_committed": [0, 0],
        "folded": [], "acted": [], "current_player": 0, "current_bet": 0,
        "min_raise": 10,
    }
    state[field] = value
    with pytest.raises(ToolError, match=f"betting_{field}_invalid"):
        BettingTool(10).legal(state)
