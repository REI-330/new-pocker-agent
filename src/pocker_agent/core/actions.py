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
from .plan import (
    ActionDescriptor,
    ActionInputDescriptor,
    IntegerRangeInputDescriptor,
)
from .tools import evaluate_expression


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


def _resolve_state_value(state: dict[str, Any], value: Any) -> Any:
    if isinstance(value, str) and (value == "$state" or value.startswith("$state.")):
        path = value[len("$state"):].lstrip(".")
        current: Any = state
        for part in path.split(".") if path else ():
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            else:
                raise ToolError(f"state_reference_not_found:{value}")
        return current
    if isinstance(value, dict):
        return {key: _resolve_state_value(state, item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_state_value(state, item) for item in value]
    return value


def integer_bounds(
    state: dict[str, Any], item: IntegerRangeInputDescriptor
) -> tuple[int, int]:
    """Evaluate one integer input's legal range against the live state."""
    minimum = evaluate_expression(_resolve_state_value(state, item.minimum))
    maximum = evaluate_expression(_resolve_state_value(state, item.maximum))
    if type(minimum) is not int or type(maximum) is not int:
        raise ToolError(f"action_integer_bounds_not_integer:{item.id}")
    if maximum < minimum:
        raise ToolError(f"action_integer_bounds_invalid:{item.id}")
    return minimum, maximum


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


def input_value(state: dict[str, Any], action_id: str,
                item: ActionInputDescriptor | IntegerRangeInputDescriptor,
                newest: bool = False, offset: int = 0) -> Any:
    if item.kind == "card_selection":
        return card_ids(state, action_id, item, newest=newest, offset=offset)
    if item.kind == "integer_range":
        minimum, maximum = integer_bounds(state, item)
        return maximum if newest else min(minimum + offset, maximum)
    raise ToolError(f"unsupported_action_input_kind:{item.kind}")


def normalize_action_payload(
    state: dict[str, Any], descriptor: ActionDescriptor, values: dict[str, Any],
    *, unavailable_error: str = "selection_not_owned_by_zone",
) -> dict[str, Any]:
    """Validate and normalize one structured action payload.

    This is the single action-contract boundary used by the interpreter and the
    session API. Callers cannot bypass range or card-visibility constraints by
    invoking the interpreter directly.
    """
    declared = {item.id: item for item in descriptor.inputs}
    unknown = sorted(set(values) - set(declared))
    if unknown:
        raise ToolError(f"unknown_action_input:{unknown[0]}")
    result: dict[str, Any] = {}
    for input_id, item in declared.items():
        if input_id not in values:
            if item.kind == "card_selection" and item.min_count == 0:
                continue
            raise ToolError(f"missing_action_input:{input_id}")
        value = values[input_id]
        if isinstance(item, ActionInputDescriptor):
            if isinstance(value, str):
                cards = [value]
            elif isinstance(value, (list, tuple)):
                cards = [str(entry) for entry in value]
            else:
                raise ToolError(f"input_must_be_card_ids:{item.id}")
            if len(cards) != len(set(cards)):
                raise ToolError(f"duplicate_cards:{item.id}")
            if not item.min_count <= len(cards) <= item.max_count:
                raise ToolError(f"selection_count_out_of_range:{item.id}")
            zone_id = resolve_zone(state, item)
            zones = state.get("zones")
            entry = zones.get(zone_id) if isinstance(zones, dict) else None
            available = entry.get("cards") if isinstance(entry, dict) else None
            if not isinstance(available, list):
                raise ToolError(f"action_zone_missing:{zone_id}")
            visibility = entry.get("visibility")
            owner = entry.get("owner")
            actor = state.get("current_player")
            visible = visibility in {None, "public"} or owner == actor
            available_ids = {card.id for card in available} if visible else set()
            unavailable = [card for card in cards if card not in available_ids]
            if unavailable:
                raise ToolError(
                    f"{unavailable_error}:{item.id}:{unavailable[0]}"
                )
            result[input_id] = cards
            continue
        if type(value) is not int:
            raise ToolError(f"input_must_be_integer:{item.id}")
        minimum, maximum = integer_bounds(state, item)
        if not minimum <= value <= maximum:
            raise ToolError(
                f"integer_out_of_range:{item.id}:{value}:{minimum}:{maximum}"
            )
        result[input_id] = value
    return result


def payload_for(state: dict[str, Any], descriptor: ActionDescriptor, newest: bool = False,
                offset: int = 0) -> dict[str, Any]:
    """Every declared input of one action, as the payload ``Interpreter.step`` takes."""
    return {item.id: input_value(state, descriptor.id, item, newest=newest, offset=offset)
            for item in descriptor.inputs}


def descriptor_for(plan: Any, action: str) -> ActionDescriptor | None:
    """The descriptor a plan declares for ``action``, or ``None`` for a 0.4 plan."""
    return next((item for item in plan.actions if item.id == action), None)
