"""Card identity shared by every tool.

A card is a value, never a mutable object: tools move ``CardRef`` instances
between zones but may not invent a rank/suit that the plan's deck declared.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CardRef:
    id: str
    rank: str
    suit: str
    value: int

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "rank": self.rank, "suit": self.suit, "value": self.value}


def encode(value: Any) -> Any:
    """JSON-safe encoding for persisted interpreter state."""
    if isinstance(value, CardRef):
        return {"$card": value.as_dict()}
    if isinstance(value, dict):
        return {str(key): encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    if isinstance(value, set):
        return {"$set": [encode(item) for item in sorted(value, key=repr)]}
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise TypeError(f"state_not_serializable:{type(value).__name__}")


def decode(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$card"}:
            return CardRef(**value["$card"])
        if set(value) == {"$set"}:
            return set(decode(item) for item in value["$set"])
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item) for item in value]
    return value
