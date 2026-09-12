"""The ``zones`` operation surface.

The generic selection/move mechanism lives in :mod:`pocker_agent.core.zones`;
this tool is only the plan-facing adapter. ``select`` is a pure check (no
effects), so an illegal count or a card the zone does not own is rejected with
nothing written. ``move`` is the only writer, and it can only touch ``zones``.
"""
from __future__ import annotations

from typing import Any

from .zones import (
    ZONES_KEY,
    apply_moves,
    assert_unique_ownership,
    select_cards,
    top_card,
    zone_cards,
    zone_table,
)


class ZonesTool:
    def select(self, state: dict[str, Any], zone: Any, card_ids: Any,
               min_count: int = 1, max_count: int = 1) -> dict[str, Any]:
        zones = zone_table(state)
        cards = select_cards(zones, zone, card_ids, min_count, max_count)
        return {"zone": zone, "ids": [card.id for card in cards],
                "cards": list(cards), "count": len(cards)}

    def move(self, state: dict[str, Any], moves: Any) -> dict[str, Any]:
        zones = zone_table(state)
        updated = apply_moves(zones, moves)
        state[ZONES_KEY] = updated
        return {"moved": sum(len(move["card_ids"]) for move in moves),
                "sizes": {zone_id: len(entry["cards"]) for zone_id, entry in updated.items()}}

    def top(self, state: dict[str, Any], zone: Any) -> dict[str, Any]:
        card = top_card(zone_table(state), zone)
        return {"zone": zone, "id": card.id, "rank": card.rank,
                "suit": card.suit, "value": card.value}

    def count(self, state: dict[str, Any], zone: Any = None) -> dict[str, Any]:
        zones = zone_table(state)
        if zone is None:
            return {"counts": {zone_id: len(entry["cards"])
                               for zone_id, entry in sorted(zones.items())}}
        return {"zone": zone, "count": len(zone_cards(zones, zone))}

    def verify(self, state: dict[str, Any]) -> dict[str, Any]:
        """A pure ownership audit, used by invariants and diagnostics."""
        zones = zone_table(state)
        assert_unique_ownership(zones)
        return {"zones": len(zones),
                "cards": sum(len(entry["cards"]) for entry in zones.values())}
