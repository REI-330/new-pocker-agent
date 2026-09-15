"""Taking cards from another player's hidden hand (Go Fish / Old Maid family).

The tool owns no game grammar: it asks for a rank, transfers the matching hidden
cards, fishes the stock when the opponent has none, removes completed pairs and
reports when the stock can no longer keep the game alive. Hidden identities are
never copied into an event or a public view.
"""
from __future__ import annotations

from typing import Any

from .contracts import ToolError


class HiddenDrawTool:
    @staticmethod
    def _hands(state: dict[str, Any]) -> list[list]:
        hands = state.get("hands")
        if not isinstance(hands, list) or len(hands) < 2:
            raise ToolError("hidden_draw_requires_at_least_two_hands")
        return hands

    @staticmethod
    def _seat(state: dict[str, Any], seat: int | None, hands: list) -> int:
        resolved = state.get("current_player", 0) if seat is None else seat
        if type(resolved) is not int or not 0 <= resolved < len(hands):
            raise ToolError("invalid_seat")
        return resolved

    def askable(self, state: dict[str, Any], seat: int | None = None) -> list[str]:
        hands = self._hands(state)
        player = self._seat(state, seat, hands)
        ranks: list[str] = []
        for card in hands[player]:
            if card.rank not in ranks:
                ranks.append(card.rank)
        return [f"ask:{rank}" for rank in ranks]

    def ask(self, state: dict[str, Any], action: str, asker: int | None = None,
            target: int | None = None) -> dict[str, Any]:
        if not isinstance(action, str) or not action.startswith("ask:") or len(action) <= 4:
            raise ToolError("invalid_ask")
        rank = action.split(":", 1)[1]
        hands = self._hands(state)
        player = self._seat(state, asker, hands)
        source = ((player + 1) % len(hands) if target is None
                  else self._seat(state, target, hands))
        if source == player:
            raise ToolError("no_opponent")
        if not any(card.rank == rank for card in hands[player]):
            raise ToolError("must_hold_the_rank")
        taken = [card for card in hands[source] if card.rank == rank]
        if taken:
            hands[source] = [card for card in hands[source] if card.rank != rank]
            hands[player].extend(taken)
            return {"got": len(taken), "fished": False, "source": source}
        stock = state.get("stock")
        if not isinstance(stock, list):
            raise ToolError("hidden_draw_requires_stock")
        if stock:
            hands[player].append(stock.pop())
            state["stock"] = stock
            return {"got": 0, "fished": True, "drawn": 1}
        return {"got": 0, "fished": True, "drawn": 0}

    def discard_pairs(self, state: dict[str, Any], seat: int | None = None) -> dict[str, Any]:
        hands = self._hands(state)
        player = self._seat(state, seat, hands)
        counts: dict[str, int] = {}
        for card in hands[player]:
            counts[card.rank] = counts.get(card.rank, 0) + 1
        removed = 0
        for rank, count in counts.items():
            pairs = count // 2
            if not pairs:
                continue
            removed += pairs
            dropped = 0
            kept = []
            for card in hands[player]:
                if card.rank == rank and dropped < pairs * 2:
                    dropped += 1
                    continue
                kept.append(card)
            hands[player] = kept
        scores = state.setdefault("pairs", [0] * len(hands))
        if (not isinstance(scores, list) or len(scores) != len(hands)
                or any(type(value) is not int or value < 0 for value in scores)):
            raise ToolError("hidden_draw_pairs_invalid")
        scores[player] += removed
        return {"pairs": removed, "hand_size": len(hands[player]), "total": scores[player]}

    def refill(self, state: dict[str, Any], seat: int | None = None) -> dict[str, Any]:
        hands = self._hands(state)
        player = self._seat(state, seat, hands)
        stock = state.get("stock")
        if not isinstance(stock, list):
            raise ToolError("hidden_draw_requires_stock")
        if hands[player] or not stock:
            return {"drew": 0}
        hands[player].append(stock.pop())
        state["stock"] = stock
        return {"drew": 1}

    def is_finished(self, state: dict[str, Any]) -> bool:
        hands = self._hands(state)
        stock = state.get("stock")
        if not isinstance(stock, list):
            raise ToolError("hidden_draw_requires_stock")
        return not stock and any(not hand for hand in hands)
