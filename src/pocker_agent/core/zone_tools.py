"""The ``zones`` operation surface.

The generic selection/move mechanism lives in :mod:`pocker_agent.core.zones`;
this tool is only the plan-facing adapter. ``select`` is a pure check (no
effects), so an illegal count or a card the zone does not own is rejected with
nothing written. ``move`` is the only writer, and it can only touch ``zones``.
"""
from __future__ import annotations

from typing import Any

from .contracts import ToolError
from .zones import (
    ZONES_KEY,
    apply_moves,
    assert_unique_ownership,
    duplicate_groups,
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

    def select_matching(self, state: dict[str, Any], zone: Any, card_ids: Any,
                        top_zone: Any, min_count: int = 1,
                        max_count: int = 1) -> dict[str, Any]:
        """A selection where every chosen card matches ``top_zone``'s top card.

        Same-suit or same-rank, the same rule as ``pattern.match`` with no wild
        ranks. Pure and side-effect free: an illegal play is rejected before any
        card moves (ADR-0012 B4).
        """
        zones = zone_table(state)
        cards = select_cards(zones, zone, card_ids, min_count, max_count)
        top = top_card(zones, top_zone)
        for card in cards:
            if card.suit != top.suit and card.rank != top.rank:
                raise ToolError(f"selection_does_not_match:{card.id}")
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

    def cards(self, state: dict[str, Any], zone: Any) -> dict[str, Any]:
        """The zone's cards, in order; a pure read for bounded zone scans."""
        cards = zone_cards(zone_table(state), zone)
        return {"zone": zone, "cards": list(cards), "count": len(cards)}

    def count(self, state: dict[str, Any]) -> dict[str, Any]:
        """Card counts for every zone, with no optional branch in the shape."""
        zones = zone_table(state)
        return {"counts": {zone_id: len(entry["cards"])
                           for zone_id, entry in sorted(zones.items())}}

    def count_zone(self, state: dict[str, Any], zone: Any) -> dict[str, Any]:
        """Card count for one named zone; ``zone`` is always required."""
        return {"zone": zone, "count": len(zone_cards(zone_table(state), zone))}

    def select_duplicates(self, state: dict[str, Any], zone: Any, key: str = "rank",
                          min_count: int = 2, max_group: int = 2,
                          max_total: int = 108) -> dict[str, Any]:
        """A pure partition of a zone into same-``key`` groups (ADR-0011)."""
        return duplicate_groups(zone_table(state), zone, key, min_count, max_group,
                                max_total)

    def verify(self, state: dict[str, Any]) -> dict[str, Any]:
        """A pure ownership audit, used by invariants and diagnostics."""
        zones = zone_table(state)
        assert_unique_ownership(zones)
        return {"zones": len(zones),
                "cards": sum(len(entry["cards"]) for entry in zones.values())}
