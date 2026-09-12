"""Stable card zones: named piles with exactly one authoritative owner per card.

A zone is a small record::

    {"owner": int | None, "visibility": "public" | "owner_only", "cards": [CardRef]}

The point of G2/M1 is that *where a card is* becomes an addressable reference
(``hand-0``, ``market``, ``stock``) instead of an ad-hoc top-level state key per
game family, while every card still has exactly one place it lives. A card may
be referenced by several results (a selection, a comparison), but it *belongs*
to one zone; moving it never clones or drops it.

This module is pure mechanism. It never decides *when* to move a card, *what* to
score or *when* the game ends -- the plan does that. It reuses the shared
``CardRef`` value type, so there is no second card or pile model.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .cards import CardRef
from .contracts import ToolError

ZONES_KEY = "zones"
VISIBILITIES = ("public", "owner_only", "hidden")


def zone_table(state: dict[str, Any]) -> dict[str, Any]:
    """The zone mapping, or a contract violation when the plan never set it up."""
    zones = state.get(ZONES_KEY)
    if not isinstance(zones, dict):
        raise ToolError("zones_not_initialized")
    return zones


def zone_entry(zones: dict[str, Any], zone_id: Any) -> dict[str, Any]:
    if not isinstance(zone_id, str) or not zone_id:
        raise ToolError("invalid_zone_id")
    entry = zones.get(zone_id)
    if not isinstance(entry, dict):
        raise ToolError(f"unknown_zone:{zone_id}")
    if not isinstance(entry.get("cards"), list):
        raise ToolError(f"zone_missing_cards:{zone_id}")
    visibility = entry.get("visibility", "public")
    if visibility not in VISIBILITIES:
        raise ToolError(f"invalid_zone_visibility:{zone_id}:{visibility}")
    return entry


def zone_cards(zones: dict[str, Any], zone_id: Any) -> list[CardRef]:
    return zone_entry(zones, zone_id)["cards"]


def all_cards(zones: dict[str, Any]) -> list[CardRef]:
    found: list[CardRef] = []
    for zone_id in zones:
        found.extend(zone_entry(zones, zone_id)["cards"])
    return found


def ownership_problems(zones: dict[str, Any]) -> list[str]:
    """Every card id must appear in at most one zone, and as a real ``CardRef``.

    Duplicate ``copies`` of the same rank/suit are legitimate *cards*; what is
    not legitimate is the same card id existing twice, because then a move can
    silently duplicate or lose a card.
    """
    problems: list[str] = []
    seen: dict[str, str] = {}
    for zone_id in sorted(zones):
        cards = zone_entry(zones, zone_id)["cards"]
        for card in cards:
            if not isinstance(card, CardRef):
                problems.append(f"not_a_card:{zone_id}")
            elif card.id in seen:
                problems.append(f"duplicate_card:{card.id}")
            else:
                seen[card.id] = zone_id
    return problems


def assert_unique_ownership(zones: dict[str, Any]) -> None:
    problems = ownership_problems(zones)
    if problems:
        raise ToolError("zone_ownership_violation:" + ",".join(problems))


def find_zone_of(zones: dict[str, Any], card_id: str) -> str | None:
    for zone_id in sorted(zones):
        if any(card.id == card_id for card in zone_entry(zones, zone_id)["cards"]):
            return zone_id
    return None


def select_cards(zones: dict[str, Any], zone_id: Any, card_ids: Any,
                 min_count: int = 1, max_count: int = 1) -> list[CardRef]:
    """Validate a selection: right count, no duplicates, all owned by the zone.

    Validation is side-effect free, so a bad selection is rejected before any
    card moves. The returned cards keep the order the caller asked for.
    """
    if type(min_count) is not int or type(max_count) is not int:
        raise ToolError("selection_bounds_must_be_integers")
    if min_count < 0 or max_count < min_count:
        raise ToolError("invalid_selection_bounds")
    if not isinstance(card_ids, list) or any(not isinstance(card_id, str) for card_id in card_ids):
        raise ToolError("selection_requires_card_ids")
    if len(set(card_ids)) != len(card_ids):
        raise ToolError("selection_has_duplicate_ids")
    if not min_count <= len(card_ids) <= max_count:
        raise ToolError(f"selection_count_out_of_range:{len(card_ids)}")
    owned = {card.id: card for card in zone_cards(zones, zone_id)}
    missing = [card_id for card_id in card_ids if card_id not in owned]
    if missing:
        raise ToolError("selection_not_owned_by_zone:" + ",".join(sorted(missing)))
    return [owned[card_id] for card_id in card_ids]


def apply_moves(zones: dict[str, Any], moves: Any) -> dict[str, Any]:
    """Validate and apply a list of moves atomically.

    Runs on a copy: if *any* move is invalid the original table is untouched, so
    a bad multi-move can never leave a card half-transferred. Later moves see
    the effect of earlier ones, which lets a plan express a swap as two moves.
    """
    if not isinstance(moves, list) or not moves:
        raise ToolError("move_requires_moves")
    updated = deepcopy(zones)
    for move in moves:
        if not isinstance(move, dict):
            raise ToolError("move_must_be_object")
        source, target = move.get("from"), move.get("to")
        if not isinstance(source, str) or not isinstance(target, str) or source == target:
            raise ToolError("move_requires_distinct_zones")
        zone_entry(updated, source)
        zone_entry(updated, target)
        selected = select_cards(updated, source, move.get("card_ids"),
                                move.get("min_count", 1), move.get("max_count", 1))
        from_cards = zone_cards(updated, source)
        moved_ids = {card.id for card in selected}
        updated[source]["cards"] = [card for card in from_cards if card.id not in moved_ids]
        updated[target]["cards"].extend(selected)
    assert_unique_ownership(updated)
    return updated


def top_card(zones: dict[str, Any], zone_id: Any) -> CardRef:
    cards = zone_cards(zones, zone_id)
    if not cards:
        raise ToolError(f"zone_is_empty:{zone_id}")
    return cards[-1]
