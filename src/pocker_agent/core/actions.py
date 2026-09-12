"""Plan-0.5 action input descriptors and how a host satisfies them.

A composed plan declares the *shape* of an action's inputs (which zone, how many
cards, actor-owned or shared) instead of a bare ``card_index``. This module turns
that declaration plus the live state into concrete input values. It is pure
mechanism: no game names, no per-action branches, and no import of the
interpreter, so both the bot policy and ``Interpreter.view`` can share it. A
descriptor kind is explicit, so an unknown kind is refused rather than guessed.
"""
from __future__ import annotations

from typing import Any

from .contracts import ToolError
from .plan import ActionDescriptor, ActionInputDescriptor


def safe_zone(zone_id: str) -> str:
    """The state key a compiled actor-zone instance result is published under."""
    return zone_id.replace("-", "_")


def zone_instance(state: dict[str, Any], zone_id: str) -> str:
    """Resolve a declared player-zone id to the acting seat's instance.

    The compiler publishes the instance as ``state['zone_<id>']``; the
    ``<zone>-<current_player>`` convention is the deterministic fallback.
    """
    resolved = state.get(f"zone_{safe_zone(zone_id)}")
    if isinstance(resolved, str) and resolved:
        return resolved
    current = state.get("current_player")
    if isinstance(current, int) and not isinstance(current, bool):
        return f"{zone_id}-{current}"
    raise ToolError(f"action_zone_unresolved:{zone_id}")


def resolve_zone(state: dict[str, Any], item: ActionInputDescriptor) -> str:
    """The concrete zone id for a descriptor input, given the current state.

    ``actor`` zones are the acting seat's instance; ``shared`` zones are
    addressed by their declared id.
    """
    if item.scope == "shared":
        return item.zone
    return zone_instance(state, item.zone)


def card_ids(state: dict[str, Any], action_id: str, item: ActionInputDescriptor,
             newest: bool = False, offset: int = 0) -> list[str]:
    """The deterministic legal selection from a zone.

    ``min_count`` is a hard floor: a zone that cannot supply it means this action
    is not usable, which the caller treats as "no legal action" rather than a
    crash. ``max_count`` is capped by what is actually there. ``newest`` takes
    from the far end and ``offset`` rotates the window, so independent policies
    can explore different branches without any game-specific knowledge.
    """
    zone_id = resolve_zone(state, item)
    zones = state.get("zones")
    entry = zones.get(zone_id) if isinstance(zones, dict) else None
    cards = entry.get("cards") if isinstance(entry, dict) else None
    if not isinstance(cards, list):
        raise ToolError(f"action_zone_missing:{zone_id}")
    if len(cards) < item.min_count:
        raise ToolError(f"action_input_insufficient_cards:{action_id}.{item.id}")
    take = min(item.max_count, len(cards))
    if take == 0:
        return []
    ordered = list(reversed(cards)) if newest else list(cards)
    shift = offset % len(ordered)
    ordered = ordered[shift:] + ordered[:shift]
    return [card.id for card in ordered[:take]]


def input_value(state: dict[str, Any], action_id: str, item: ActionInputDescriptor,
                newest: bool = False, offset: int = 0) -> Any:
    if item.kind == "card_selection":
        return card_ids(state, action_id, item, newest=newest, offset=offset)
    raise ToolError(f"unsupported_action_input_kind:{item.kind}")


def payload_for(state: dict[str, Any], descriptor: ActionDescriptor, newest: bool = False,
                offset: int = 0) -> dict[str, Any]:
    """Every declared input of one action, as the payload ``Interpreter.step`` takes."""
    return {item.id: input_value(state, descriptor.id, item, newest=newest, offset=offset)
            for item in descriptor.inputs}


def descriptor_for(plan: Any, action: str) -> ActionDescriptor | None:
    """The descriptor a plan declares for ``action``, or ``None`` for a 0.4 plan."""
    return next((item for item in plan.actions if item.id == action), None)
