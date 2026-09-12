"""Development-only composition samples (G2 / M1).

These are **not** reference games and are not reachable from the app. They exist
to prove one M1 claim mechanically: the *same* generic selection / move /
scoring / turn operations can serve two different rule combinations without a
game-specific tool or plan family.

They are the "real consumers" the tool-budget rule requires for a new mechanism
tool (``docs/engineering-standards.md`` section 6.4 and the M1 tool-budget ADR):
``zones.select`` + ``zones.move`` + ``score_settle.call`` + the ``wait``/branch
turn shape appear in both, while their scoring differs. M2's compiler is
expected to replace them with real composed rules.
"""
from __future__ import annotations

from typing import Any

from .plan import GamePlan

ZONE_RANKS = ("2", "3", "4", "5", "6", "7", "8", "9")
ZONE_SUITS = ("S", "H")
ZONE_VALUES = {rank: int(rank) for rank in ZONE_RANKS}
HAND_SIZE = 3
MARKET_SIZE = 3
MAX_ACTIONS = 4


def _initial_zones() -> dict[str, Any]:
    return {
        "hand-0": {"owner": 0, "visibility": "owner_only", "cards": "$state.deal.hands.0"},
        "hand-1": {"owner": 1, "visibility": "owner_only", "cards": "$state.deal.hands.1"},
        "market": {"owner": None, "visibility": "public", "cards": "$state.deal.kitty"},
        "stock": {"owner": None, "visibility": "hidden", "cards": "$state.deal.deck"},
        "collection-0": {"owner": 0, "visibility": "owner_only", "cards": []},
        "collection-1": {"owner": 1, "visibility": "owner_only", "cards": []},
    }


def _common_nodes() -> dict[str, Any]:
    """Setup + turn + advance + bounded terminal, shared by both samples.

    The scoring entry differs (compare vs. suit), which is exactly the point:
    everything else -- selection, the two-zone exchange, the turn and the
    bounded end -- is literally the same operation sequence.
    """
    return {
        "round_seed": {"kind": "call", "next": "deal", "action": {
            "tool": "logic", "operation": "evaluate",
            "args": {"expression": {"join": ["$state.seed", ":zones"]}},
            "result_key": "round_seed"}},
        "deal": {"kind": "call", "next": "init", "action": {
            "tool": "deck", "operation": "deal",
            "args": {"seed": "$state.round_seed", "hands": 2,
                     "cards_each": HAND_SIZE, "kitty": MARKET_SIZE},
            "result_key": "deal"}},
        "init": {"kind": "call", "next": "turn_setup", "action": {
            "tool": "state", "operation": "update", "args": {"state": "$state", "values": {
                "zones": _initial_zones(), "deal": None, "scores": [0, 0],
                "current_player": 0, "action_count": 0, "max_actions": MAX_ACTIONS,
                "finished": False, "winners": [], "phase": "exchange"}}}},
        "turn_setup": {"kind": "call", "next": "opponent", "action": {
            "tool": "logic", "operation": "evaluate",
            "args": {"expression": {"join": ["hand-", "$state.current_player"]}},
            "result_key": "zone_hand"}},
        "opponent": {"kind": "call", "next": "turn", "action": {
            "tool": "logic", "operation": "evaluate",
            "args": {"expression": {"sub": [1, "$state.current_player"]}},
            "result_key": "opponent"}},
        "turn": {"kind": "wait", "inputs": {"exchange": "sel_hand"}},
        "advance": {"kind": "call", "next": "bump", "action": {
            "tool": "state", "operation": "update",
            "args": {"state": "$state", "values": {"current_player": "$state.opponent"}}}},
        "bump": {"kind": "call", "next": "apply_count", "action": {
            "tool": "logic", "operation": "evaluate",
            "args": {"expression": {"add": ["$state.action_count", 1]}},
            "result_key": "next_count"}},
        "apply_count": {"kind": "call", "next": "limit", "action": {
            "tool": "state", "operation": "update",
            "args": {"state": "$state", "values": {"action_count": "$state.next_count"}}}},
        "limit": {"kind": "call", "next": "limit_branch", "action": {
            "tool": "logic", "operation": "evaluate",
            "args": {"expression": {"ge": ["$state.action_count", "$state.max_actions"]}},
            "result_key": "at_limit"}},
        "limit_branch": {"kind": "branch", "value": "$state.at_limit",
                         "cases": [{"value": True, "target": "resolve"}],
                         "next": "turn_setup"},
        "resolve": {"kind": "call", "next": "finish", "action": {
            "tool": "winner_resolve", "operation": "call",
            "args": {"values": "$state.scores"}, "result_key": "winner_indexes"}},
        "finish": {"kind": "call", "next": "end", "action": {
            "tool": "state", "operation": "update", "args": {"state": "$state", "values": {
                "finished": True, "winners": "$state.winner_indexes",
                "phase": "finished"}}}},
        "end": {"kind": "end"},
    }


def _select_and_move_nodes(score_entry: str) -> dict[str, Any]:
    """Two-zone card selection and an atomic swap; identical in both samples."""
    return {
        "sel_hand": {"kind": "call", "next": "sel_market", "action": {
            "tool": "zones", "operation": "select",
            "args": {"state": "$state", "zone": "$state.zone_hand",
                     "card_ids": "$state.input.hand_cards", "min_count": 1, "max_count": 1},
            "result_key": "picked_hand"}},
        "sel_market": {"kind": "call", "next": "move", "action": {
            "tool": "zones", "operation": "select",
            "args": {"state": "$state", "zone": "market",
                     "card_ids": "$state.input.market_cards", "min_count": 1, "max_count": 1},
            "result_key": "picked_market"}},
        "move": {"kind": "call", "next": score_entry, "action": {
            "tool": "zones", "operation": "move",
            "args": {"state": "$state", "moves": [
                {"from": "$state.zone_hand", "to": "market",
                 "card_ids": "$state.picked_hand.ids", "min_count": 1, "max_count": 1},
                {"from": "market", "to": "$state.zone_hand",
                 "card_ids": "$state.picked_market.ids", "min_count": 1, "max_count": 1}]},
            "result_key": "moved"}},
    }


def _tools(extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"name": "state"},
        {"name": "logic"},
        {"name": "deck", "config": {"ranks": list(ZONE_RANKS), "suits": list(ZONE_SUITS),
                                      "values": ZONE_VALUES}},
        {"name": "zones"},
        {"name": "score_settle"},
        {"name": "winner_resolve"},
        *extra,
    ]


def _initial() -> dict[str, Any]:
    return {"finished": False, "winners": [], "scores": [0, 0], "current_player": 0,
            "round": 1, "max_rounds": 1, "phase": "exchange", "action_count": 0,
            "max_actions": MAX_ACTIONS,
            "instructions": "交换一张手牌与一张市场牌；按本规则的计分方式记分。"}


def exchange_compare_composition() -> GamePlan:
    """Exchange two cards, then let ``rank_compare`` decide who scores.

    The same ``zones.select`` / ``zones.move`` sequence as the suit sample; only
    the scoring node differs.
    """
    nodes = dict(_common_nodes())
    nodes.update(_select_and_move_nodes("compare"))
    nodes.update({
        "compare": {"kind": "call", "next": "cmp_branch", "action": {
            "tool": "rank_compare", "operation": "call",
            "args": {"left": "$state.picked_hand.cards", "right": "$state.picked_market.cards"},
            "result_key": "compare"}},
        "cmp_branch": {"kind": "branch", "value": "$state.compare.outcome",
                       "cases": [{"value": "left", "target": "award_actor"},
                                 {"value": "right", "target": "award_opponent"}],
                       "next": "advance"},
        "award_actor": {"kind": "call", "next": "advance", "action": {
            "tool": "score_settle", "operation": "call",
            "args": {"scores": "$state.scores", "winners": ["$state.current_player"],
                     "points": 1}, "result_key": "scores"}},
        "award_opponent": {"kind": "call", "next": "advance", "action": {
            "tool": "score_settle", "operation": "call",
            "args": {"scores": "$state.scores", "winners": ["$state.opponent"],
                     "points": 1}, "result_key": "scores"}},
    })
    return GamePlan(game_kind="zones_exchange_compare", players=2,
                    tools=_tools([{"name": "rank_compare"}]),
                    initial=_initial(), entry="round_seed", nodes=nodes, step_limit=256)


def exchange_suit_score_composition() -> GamePlan:
    """Exchange two cards, then score the received card by suit (H=2, else 1).

    Shares the move sequence with the compare sample but scores a different way,
    which is what "one mechanism, two combinations" means.
    """
    nodes = dict(_common_nodes())
    nodes.update(_select_and_move_nodes("describe"))
    nodes.update({
        "describe": {"kind": "call", "next": "suit_branch", "action": {
            "tool": "pattern", "operation": "describe",
            "args": {"cards": "$state.picked_market.cards"}, "result_key": "picked_desc"}},
        "suit_branch": {"kind": "branch", "value": "$state.picked_desc.suit",
                        "cases": [{"value": "H", "target": "score_two"}],
                        "next": "score_one"},
        "score_two": {"kind": "call", "next": "advance", "action": {
            "tool": "score_settle", "operation": "call",
            "args": {"scores": "$state.scores", "winners": ["$state.current_player"],
                     "points": 2}, "result_key": "scores"}},
        "score_one": {"kind": "call", "next": "advance", "action": {
            "tool": "score_settle", "operation": "call",
            "args": {"scores": "$state.scores", "winners": ["$state.current_player"],
                     "points": 1}, "result_key": "scores"}},
    })
    return GamePlan(game_kind="zones_exchange_suit_score", players=2,
                    tools=_tools([{"name": "pattern"}]),
                    initial=_initial(), entry="round_seed", nodes=nodes, step_limit=256)


def composition_samples() -> dict[str, GamePlan]:
    return {"zones_exchange_compare": exchange_compare_composition(),
            "zones_exchange_suit_score": exchange_suit_score_composition()}


def exchange_strategy(interpreter: Any):
    """Deterministic policy for the samples: first hand card for first market card."""
    actions = interpreter.legal_actions()
    if "exchange" not in actions:
        return (actions[0], {}) if actions else None
    zones = interpreter.state["zones"]
    hand = zones[f"hand-{interpreter.state['current_player']}"]["cards"]
    market = zones["market"]["cards"]
    return ("exchange", {"hand_cards": [hand[0].id], "market_cards": [market[0].id]})
