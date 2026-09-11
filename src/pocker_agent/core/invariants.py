"""Reusable invariants for the playtest gate.

An invariant is a pure predicate over the interpreter; raising means the trace
is invalid. They are the only scalable way to check composition correctness,
because a plan can "run" while violating the contract it claims.
"""
from __future__ import annotations

from typing import Any

from .cards import CardRef
from .contracts import ToolError
from .interpreter import Interpreter


def _collect_cards(value: Any) -> list[CardRef]:
    if isinstance(value, CardRef):
        return [value]
    if isinstance(value, dict):
        found: list[CardRef] = []
        for item in value.values():
            found.extend(_collect_cards(item))
        return found
    if isinstance(value, (list, tuple)):
        found = []
        for item in value:
            found.extend(_collect_cards(item))
        return found
    return []


def card_conservation(total: int):
    """Every card exists exactly once in state; nothing is invented or lost."""
    def invariant(interpreter: Interpreter) -> None:
        cards = _collect_cards(interpreter.state)
        ids = [card.id for card in cards]
        if len(ids) != len(set(ids)):
            raise ToolError("card_duplication")
        if len(ids) != total:
            raise ToolError(f"card_conservation:{len(ids)}!={total}")
    return invariant


def never_finishes_without_winners_or_scores():
    """A finished state must be explainable: winners, or a score line."""
    def invariant(interpreter: Interpreter) -> None:
        state = interpreter.state
        if state.get("finished") and not state.get("winners") and not state.get("scores"):
            raise ToolError("finished_without_outcome")
    return invariant
