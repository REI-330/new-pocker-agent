"""Point-total mechanisms for 21-style games.

``point_total`` sums card values with soft aces, runs the dealer's fixed draw
policy, and resolves a showdown against the target. It owns no game grammar:
the target, the stand threshold and whether the dealer hits a soft 17 are
configuration.
"""
from __future__ import annotations

from typing import Any

from .cards import CardRef
from .contracts import ToolError

FACE_VALUES = {"J": 10, "Q": 10, "K": 10}


class PointTotalTool:
    def __init__(self, target: int = 21) -> None:
        if type(target) is not int or target < 1:
            raise ToolError("invalid_point_target")
        self.target = target

    @staticmethod
    def _values(cards: Any) -> list[int]:
        if not isinstance(cards, list) or not all(isinstance(card, CardRef) for card in cards):
            raise ToolError("point_total_requires_cards")
        values = []
        for card in cards:
            if card.rank == "A":
                values.append(1)
            elif card.rank in FACE_VALUES:
                values.append(FACE_VALUES[card.rank])
            else:
                try:
                    values.append(int(card.rank))
                except ValueError as error:
                    raise ToolError(f"invalid_card_rank:{card.rank}") from error
        return values

    def total(self, cards: Any) -> dict[str, Any]:
        values = self._values(cards)
        base = sum(values)
        soft = any(card.rank == "A" for card in cards) and base + 10 <= self.target
        total = base + (10 if soft else 0)
        return {"total": total, "soft": soft, "bust": total > self.target,
                "cards": len(cards)}

    def dealer_play(self, stock: list, hand: list, stand_on: int = 17,
                    hits_soft: bool = True) -> dict[str, Any]:
        if not isinstance(stock, list) or not isinstance(hand, list):
            raise ToolError("dealer_play_requires_lists")
        draws = 0
        while True:
            rank = self.total(hand)
            if rank["total"] > self.target:
                break
            if rank["total"] > stand_on:
                break
            if rank["total"] == stand_on and not (hits_soft and rank["soft"]):
                break
            if not stock:
                raise ToolError("deck_exhausted")
            hand.append(stock.pop())
            draws += 1
            if draws > 52:
                raise ToolError("dealer_draw_limit")
        rank = self.total(hand)
        return {**rank, "draws": draws}

    def settle(self, player: Any, dealer: Any, *, player_natural: bool = False,
               dealer_natural: bool = False, first_bust_loses: bool = True) -> dict[str, Any]:
        if not isinstance(player, int) or not isinstance(dealer, int):
            raise ToolError("settle_requires_totals")
        if player > self.target:
            return {"winner": 1, "reason": "player_bust"}
        if player_natural and dealer_natural:
            return {"winner": None, "reason": "both_natural"}
        if player_natural:
            return {"winner": 0, "reason": "player_natural"}
        if dealer_natural:
            return {"winner": 1, "reason": "dealer_natural"}
        if dealer > self.target:
            return {"winner": 0, "reason": "dealer_bust"}
        if player > dealer:
            return {"winner": 0, "reason": "player_higher"}
        if dealer > player:
            return {"winner": 1, "reason": "dealer_higher"}
        return {"winner": None, "reason": "push"}
