"""Player-facing decision contracts.

Policies are intentionally separated from :class:`Interpreter`.  The host owns
the complete state and gives a policy only the acting seat's projected
observation plus the actions currently offered to that seat.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

PolicyDecision = tuple[str, dict[str, Any]]


@dataclass(frozen=True)
class PolicyContext:
    """All information available to one policy at one decision point."""

    seed: int
    step: int
    player: int
    observation: Mapping[str, Any]
    legal_actions: tuple[str, ...]


Policy = Callable[[PolicyContext], PolicyDecision | None]


def policy_context(interpreter: Any, step: int = 0) -> PolicyContext:
    """Project the live environment into the acting player's decision surface."""
    actor = int(interpreter.state.get("current_player", 0))
    observation = deepcopy(interpreter.view(f"player-{actor + 1}"))
    return PolicyContext(interpreter.seed, step, actor, observation,
                         tuple(interpreter.legal_actions()))


def visible_payload(context: PolicyContext, action: str, *, newest: bool = False,
                    offset: int = 0) -> dict[str, Any] | None:
    """Build one payload exclusively from values in the player observation."""
    descriptors = context.observation.get("actions")
    if isinstance(descriptors, list):
        descriptor = next(
            (item for item in descriptors
             if isinstance(item, Mapping) and item.get("id") == action),
            None,
        )
        if descriptor is not None:
            payload: dict[str, Any] = {}
            for item in descriptor.get("inputs", []):
                if not isinstance(item, Mapping):
                    return None
                input_id = str(item.get("id", ""))
                if not input_id:
                    return None
                if item.get("kind") == "integer_range":
                    minimum, maximum = item.get("minimum"), item.get("maximum")
                    if type(minimum) is not int or type(maximum) is not int or maximum < minimum:
                        return None
                    value = max(minimum, maximum - offset) if newest else min(maximum, minimum + offset)
                    payload[input_id] = value
                    continue
                options = item.get("options")
                if not isinstance(options, list):
                    return None
                minimum = int(item.get("min_count", 0))
                maximum = int(item.get("max_count", minimum))
                if len(options) < minimum:
                    return None
                ordered = list(reversed(options)) if newest else list(options)
                if ordered:
                    shift = offset % len(ordered)
                    ordered = ordered[shift:] + ordered[:shift]
                payload[input_id] = ordered[:min(maximum, len(ordered))]
            return payload

    if action == "play":
        indices = context.observation.get("legal_card_indices")
        if isinstance(indices, list) and indices:
            ordered = list(reversed(indices)) if newest else indices
            return {"card_index": ordered[offset % len(ordered)], "declared_suit": "S"}
    return {}
