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
from .zones import ZONES_KEY, all_cards


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


def authoritative_cards(state: dict[str, Any]) -> list[CardRef]:
    """The cards a state *owns*, not the cards it merely *observes*.

    A zone table is the authority: a selection stored in ``state.picked`` may
    repeat a ``CardRef`` that already lives in a zone, and counting both would
    report a duplicate that does not exist. Legacy plans have no zone table, so
    they keep the recursive sweep (their intermediate deal results are nulled
    out by the plan, which is why that sweep was sound for them).
    """
    zones = state.get(ZONES_KEY)
    if isinstance(zones, dict):
        return all_cards(zones)
    return _collect_cards(state)


def card_conservation(total: int):
    """Every card exists exactly once in state; nothing is invented or lost."""
    def invariant(interpreter: Interpreter) -> None:
        cards = authoritative_cards(interpreter.state)
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


def zone_conservation():
    """A zone table holds a fixed multiset of card ids for the whole game.

    Unlike :func:`card_conservation`, the total is learned from the first checked
    state (after ``setup``), so a caller does not have to know the deck size. A
    plan with no zone table is skipped: it is governed by the legacy sweep.
    """
    box: dict[str, int] = {}

    def invariant(interpreter: Interpreter) -> None:
        zones = interpreter.state.get(ZONES_KEY)
        if not isinstance(zones, dict):
            return
        ids = [card.id for card in all_cards(zones)]
        if len(ids) != len(set(ids)):
            raise ToolError("zone_card_duplication")
        total = box.setdefault("total", len(ids))
        if len(ids) != total:
            raise ToolError(f"zone_conservation:{len(ids)}!={total}")
    return invariant


def non_negative_scores():
    """Scores are integer counters that never go negative."""
    def invariant(interpreter: Interpreter) -> None:
        scores = interpreter.state.get("scores")
        if not isinstance(scores, list):
            return
        for value in scores:
            if type(value) is not int or value < 0:
                raise ToolError(f"score_out_of_bounds:{value!r}")
    return invariant


def view_is_safe():
    """No viewer's projection may expose a card it does not own.

    This checks the *projection*, not the raw state: a hidden or other-owned zone
    must project to an empty card list until the game reveals or finishes.
    """
    def invariant(interpreter: Interpreter) -> None:
        if interpreter.state.get("finished") or interpreter.state.get("reveal"):
            return
        for index in range(interpreter.plan.players):
            view = interpreter.view(f"player-{index + 1}")
            for zone in view.get("zones", {}).values():
                visibility, owner = zone.get("visibility"), zone.get("owner")
                if visibility == "public":
                    continue
                if owner is not None and owner == index:
                    continue  # an owner may always see their own zone
                if zone.get("visible") or zone.get("cards"):
                    raise ToolError(f"view_leaks_zone:player-{index + 1}")
    return invariant
