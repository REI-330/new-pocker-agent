"""Card identity and rank strength.

Two defects lived here: a deck holding the same card twice produced duplicate
ids (breaking React keys, event replay and card identity), and poker scored on a
13-high positional value while `hand_rank` compares against a 14-high ace, so
the wheel (A-2-3-4-5) came out as `high_card` instead of a straight.
"""
from __future__ import annotations

import pytest

from pocker_agent.core import core_registry, host_compile, parse_ir
from pocker_agent.core.cards import CardRef
from pocker_agent.core.contracts import ToolError
from pocker_agent.core.poker_tools import HandRankTool
from pocker_agent.core.tools import DeckTool

RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
SUITS = ("S", "H", "D", "C")


SUIT_CYCLE = ("S", "H", "D", "C", "S")


def card(rank: str, suit: str, value: int) -> CardRef:
    return CardRef(f"{rank}{suit}", rank, suit, value)


def hand(*pairs: tuple[str, int]) -> list[CardRef]:
    """A mixed-suit hand, so nothing here is accidentally a flush."""
    return [card(rank, SUIT_CYCLE[index], value)
            for index, (rank, value) in enumerate(pairs)]


# ------------------------------------------------------------------ identity

def test_ids_stay_unique_when_a_deck_holds_duplicate_cards():
    single = DeckTool(RANKS, SUITS)
    ids = [item.id for item in single.catalog()]
    assert len(ids) == len(set(ids)) == len(RANKS) * len(SUITS)

    doubled = DeckTool(RANKS, SUITS, copies=2)
    ids = [item.id for item in doubled.catalog()]
    assert len(ids) == len(set(ids)) == 2 * len(RANKS) * len(SUITS), "duplicate card id"


def test_ids_do_not_change_for_a_single_copy_deck():
    """Existing reference plans and oracle fixtures keep their card ids."""
    assert {item.id for item in DeckTool(("A", "K"), ("S", "H")).catalog()} == {
        "AS", "AH", "KS", "KH"}


# -------------------------------------------------------------- rank strength

def test_an_explicit_value_map_overrides_positional_strength():
    deck = DeckTool(RANKS, SUITS, values={"A": 14, "K": 13, "Q": 12, "J": 11})
    by_rank = {item.rank: item.value for item in deck.catalog()}
    assert by_rank["A"] == 14 and by_rank["K"] == 13
    # Unlisted ranks keep the 1-based position, which is a *different* scale.
    # A family that needs ace-high values must map every rank it deals.
    assert by_rank["2"] == 1

    positional = {item.rank: item.value for item in DeckTool(RANKS, SUITS).catalog()}
    assert positional["A"] == 13 and positional["2"] == 1


def test_a_value_map_naming_an_unknown_rank_is_rejected():
    with pytest.raises(ToolError, match="invalid_deck_values:JOKER"):
        DeckTool(RANKS, SUITS, values={"JOKER": 15})


def test_the_poker_plan_deals_ace_high_values():
    plan = host_compile(parse_ir({"kind": "poker", "game_id": "p", "title": "t",
                                  "stacks": 100, "min_raise": 10}))
    binding = next(tool for tool in plan.tools if tool.name == "deck")
    deck = core_registry().create("deck", **binding.config)
    values = {item.rank: item.value for item in deck.catalog()}
    assert values["A"] == 14 and values["K"] == 13
    assert values["2"] == 2 and values["10"] == 10     # whole scale, not just faces


# -------------------------------------------------------------------- scoring

def test_the_wheel_is_the_lowest_straight():
    tool = HandRankTool()
    wheel = hand(("A", 14), ("2", 2), ("3", 3), ("4", 4), ("5", 5))
    assert tool.best(wheel)["category"] == "straight"
    assert tool.best(wheel)["score"] == [4, 5]            # five-high

    six_high = hand(("6", 6), ("5", 5), ("4", 4), ("3", 3), ("2", 2))
    assert tool.best(six_high)["category"] == "straight"
    assert tool.compare(six_high, wheel)["outcome"] == "left"
    assert tool.compare(wheel, six_high)["outcome"] == "right"


def test_broadway_beats_every_other_straight():
    tool = HandRankTool()
    broadway = hand(("A", 14), ("K", 13), ("Q", 12), ("J", 11), ("10", 10))
    wheel = hand(("A", 14), ("2", 2), ("3", 3), ("4", 4), ("5", 5))
    assert tool.best(broadway)["category"] == "straight"
    assert tool.compare(broadway, wheel)["outcome"] == "left"


def test_a_non_straight_gap_is_not_a_straight():
    tool = HandRankTool()
    gapped = hand(("A", 14), ("K", 13), ("Q", 12), ("J", 11), ("9", 9))
    assert tool.best(gapped)["category"] == "high_card"


def test_a_flush_wheel_is_a_straight_flush():
    tool = HandRankTool()
    suited_wheel = [card(rank, "H", value)
                    for rank, value in (("A", 14), ("2", 2), ("3", 3), ("4", 4), ("5", 5))]
    result = tool.best(suited_wheel)
    assert result["category"] == "straight_flush"
    assert result["score"] == [8, 5]
