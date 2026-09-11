"""The host macro library and the plans that reuse each macro.

``PLAN_MACROS`` is the declared usage that drives ``promotion_report``: it must
reflect real reuse, so each entry here is a plan that actually expands the macro.
"""
from __future__ import annotations

from .macros import MacroRegistry, MacroSpec
from .registry import core_registry

# "Compute this seat's legal cards, then wait for play or draw."
# Reused by crazy_eights and uno; it is control flow, not a mechanism.
MATCH_TURN = MacroSpec(
    name="match_turn",
    entry="choices",
    exits=("play", "draw"),
    params=("cards",),
    kind="flow",
    note="seats expose only legal actions; the gate branches to the wait nodes",
    nodes={
        "choices": {"kind": "call", "next": "has", "action": {
            "tool": "pattern", "operation": "choices",
            "args": {"cards": "{cards}", "top": "$state.table",
                     "active_suit": "$state.active_suit", "wild_ranks": "$state.wild_ranks"},
            "result_key": "legal_card_indices"}},
        "has": {"kind": "call", "next": "gate", "action": {
            "tool": "logic", "operation": "evaluate",
            "args": {"expression": {"gt": [{"count": ["$state.legal_card_indices"]}, 0]}},
            "result_key": "has_choice"}},
        "gate": {"kind": "branch", "value": "$state.has_choice",
                 "cases": [{"value": True, "target": "wait"}], "next": "drawwait"},
        "wait": {"kind": "wait", "inputs": {"play": "@exit:play"}},
        "drawwait": {"kind": "wait", "inputs": {"draw": "@exit:draw"}},
    },
)

PLAN_MACROS: dict[str, list[str]] = {
    "crazy_eights": ["match_turn"],
    "uno": ["match_turn"],
}


def default_macros() -> MacroRegistry:
    registry = MacroRegistry(core_registry())
    registry.register(MATCH_TURN)
    return registry
