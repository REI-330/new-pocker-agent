"""Declarative special-card effects over the shared state.

``trigger`` applies a list of effect descriptors (draw N to a seat, skip, reverse,
set the active suit). The plan decides *which* effects fire; the tool owns the
state invariants so a hand-written effect cannot quietly lose or duplicate cards.
"""
from __future__ import annotations

from typing import Any

from .contracts import ToolError

TARGETS = ("current", "next", "previous", "all")
EFFECT_KINDS = ("draw", "skip", "reverse", "set_active_suit")


class TriggerTool:
    @staticmethod
    def _hands(state: dict[str, Any]) -> list[list]:
        hands = state.get("hands")
        if not isinstance(hands, list) or not hands:
            raise ToolError("trigger_requires_hands")
        return hands

    def _target(self, state: dict[str, Any], target: str, players: int) -> list[int]:
        current = state.get("current_player", 0)
        if type(current) is not int or not 0 <= current < players:
            raise ToolError("invalid_current_player")
        if target == "current":
            return [current]
        if target == "next":
            return [(current + 1) % players]
        if target == "previous":
            return [(current - 1) % players]
        if target == "all":
            return list(range(players))
        raise ToolError(f"unknown_effect_target:{target}")

    def _draw(self, state: dict[str, Any], seats: list[int], count: int) -> int:
        hands = self._hands(state)
        stock = state.get("stock")
        if not isinstance(stock, list):
            raise ToolError("trigger_requires_stock")
        drawn = 0
        for _ in range(count):
            for seat in seats:
                if not stock:
                    table = state.get("table") or []
                    if len(table) > 1:                      # recycle, keep the top
                        stock.extend(table[:-1])
                        state["table"] = table[-1:]
                if not stock:
                    return drawn
                hands[seat].append(stock.pop())
                drawn += 1
        return drawn

    def apply(self, state: dict[str, Any], effects: Any) -> dict[str, Any]:
        if not isinstance(effects, list):
            raise ToolError("effects_must_be_a_list")
        hands = self._hands(state)
        players = len(hands)
        applied: list[dict[str, Any]] = []
        for effect in effects:
            if not isinstance(effect, dict):
                raise ToolError("effect_must_be_object")
            kind = effect.get("do")
            if kind not in EFFECT_KINDS:
                raise ToolError(f"unknown_effect:{kind}")
            if kind == "draw":
                target = effect.get("target", "next")
                count = effect.get("count", 1)
                if type(count) is not int or count < 0:
                    raise ToolError("invalid_draw_count")
                seats = self._target(state, target, players)
                applied.append({"do": "draw", "seats": seats,
                                "drawn": self._draw(state, seats, count)})
            elif kind == "skip":
                count = effect.get("count", 1)
                if type(count) is not int or count < 0:
                    raise ToolError("invalid_skip_count")
                state["skip"] = int(state.get("skip", 0)) + count
                applied.append({"do": "skip", "skip": state["skip"]})
            elif kind == "reverse":
                state["direction"] = -int(state.get("direction", 1))
                applied.append({"do": "reverse", "direction": state["direction"]})
            else:
                suit = effect.get("suit")
                if not isinstance(suit, str) or not suit:
                    raise ToolError("set_active_suit_requires_suit")
                state["active_suit"] = suit
                applied.append({"do": "set_active_suit", "suit": suit})
        return {"applied": applied, "count": len(applied)}
