"""G2 / M3 guard top-card references (ADR-0012 section 8).

A guard may read the top of a declared zone through a closed grammar:
``top(zone).rank`` / ``.suit``, the bounded ``has_match(zone, top(other))``
predicate, and ``zone_count(zone)``. An empty zone is false in boolean context,
never a runtime error; ``top(...).value`` used numerically is a compile error.
The guards lower to declared ``zones``/``pattern`` calls with no dynamic path.
"""
from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from pocker_agent.core import Interpreter, compile_composed, core_registry
from pocker_agent.core.ir import parse_design_ir


def _guard(guard: dict, *, discard_deal: bool = True, round_action: str = "play",
           turn_actions: list[str] | None = None) -> dict:
    deals = [{"zone": "hand", "count": 2, "per_seat": True}]
    if discard_deal:
        deals.append({"zone": "discard", "count": 1, "per_seat": False})
    flow: dict = {"round_action": round_action, "start_seat": "seat0", "resolve": []}
    if turn_actions is not None:
        flow["turn_actions"] = turn_actions
    actions = [{"id": "play", "guard": guard, "inputs": [],
                "effects": [{"kind": "refill", "from_zone": "stock",
                             "to_zone": "hand", "target_count": 2, "max_draw": 2}]}]
    if turn_actions is not None:
        actions.append({"id": "idle", "inputs": [],
                        "effects": [{"kind": "refill", "from_zone": "stock",
                                     "to_zone": "hand", "target_count": 2,
                                     "max_draw": 2}]})
    return {
        "schema_version": "0.5", "kind": "composed",
        "meta": {"title": "guard test", "description": "ADR-0012 guard grammar."},
        "requirements": [],
        "players": {"count": 2},
        "deck": {"ranks": [str(n) for n in range(2, 10)], "suits": ["S", "H"],
                 "copies": 1, "values": {str(n): n for n in range(2, 10)}},
        "zones": [
            {"id": "hand", "visibility": "public", "scope": "player"},
            {"id": "discard", "visibility": "public", "scope": "shared"},
            {"id": "stock", "visibility": "hidden", "scope": "shared"},
        ],
        "setup": {"deals": deals, "stock_zone": "stock"},
        "variables": [],
        "actions": actions,
        "flow": flow,
        "scoring": [],
        "terminal": {"max_rounds": 3},
        "macros": [],
    }


def _started(payload: dict, seed: int = 0) -> Interpreter:
    compiled = compile_composed(parse_design_ir(payload), core_registry())
    interpreter = Interpreter(compiled.plan, core_registry(), seed=seed)
    interpreter.setup()
    return interpreter


def _top_expr(zone: str, field: str = "rank") -> dict:
    return {"op": "top", "zone": zone, "field": field}


def _rank_eq(rank: str) -> dict:
    return {"op": "eq", "left": _top_expr("discard"),
            "right": {"op": "lit", "value": rank}}


def _card_matches(card, top) -> bool:
    return card.suit == top.suit or card.rank == top.rank


def test_a_top_rank_guard_offers_the_action_only_for_the_matching_rank():
    discard = _started(_guard(_rank_eq("2")))          # learn the dealt top
    top = discard.state["zones"]["discard"]["cards"][-1]
    other = "3" if top.rank != "3" else "4"

    matching = _started(_guard(_rank_eq(top.rank)))
    assert matching.legal_actions() == ["play"]

    missing = _started(_guard(_rank_eq(other)))
    assert missing.legal_actions() == []


def test_an_empty_zone_top_is_false_not_an_error():
    interpreter = _started(_guard(_rank_eq("7"), discard_deal=False))
    assert interpreter.state["zones"]["discard"]["cards"] == []
    assert interpreter.legal_actions() == []
    assert interpreter.state["finished"] is True          # all guards failed


def test_has_match_agrees_with_an_independent_matching_check():
    # Learn the state, then assert the predicate against a manual computation.
    learned = _started(_guard({"op": "gt", "left": {"op": "lit", "value": 1},
                               "right": {"op": "lit", "value": 0}}))
    top = learned.state["zones"]["discard"]["cards"][-1]
    seats = [seat for seat in range(2)
             if any(_card_matches(card, top)
                    for card in learned.state["zones"][f"hand-{seat}"]["cards"])]

    interpreter = _started(_guard({"op": "has_match", "zone": "hand",
                                   "top": _top_expr("discard")}))
    if seats:
        assert interpreter.legal_actions() == ["play"]
        assert interpreter.state["current_player"] == seats[0]
    else:                                                # no seat can match
        assert interpreter.legal_actions() == []
        assert interpreter.state["finished"] is True


def test_zone_count_reads_a_shared_zone():
    non_empty = _started(_guard({"op": "gt", "left": {"op": "zone_count", "zone": "stock"},
                                 "right": {"op": "lit", "value": 0}}))
    assert non_empty.state["zones"]["stock"]["cards"]
    assert non_empty.legal_actions() == ["play"]

    impossible = _started(_guard({"op": "gt", "left": {"op": "zone_count", "zone": "stock"},
                                  "right": {"op": "lit", "value": 99}}))
    assert impossible.legal_actions() == []


def test_a_guard_with_top_references_emits_the_declared_host_calls():
    compiled = compile_composed(
        parse_design_ir(_guard({"op": "has_match", "zone": "hand",
                                "top": _top_expr("discard")})), core_registry())
    calls = {(node.action.tool, node.action.operation)
             for node in compiled.plan.nodes.values() if node.action is not None}
    assert ("zones", "cards") in calls
    assert ("pattern", "choices") in calls
    assert any(binding.name == "pattern" for binding in compiled.plan.tools)


def test_a_plain_guard_keeps_the_m2_node_names():
    compiled = compile_composed(
        parse_design_ir(_guard({"op": "lt", "left": {"op": "lit", "value": 1},
                                "right": {"op": "lit", "value": 2}})), core_registry())
    assert "pre_turn" in compiled.plan.nodes
    assert "guard_branch" in compiled.plan.nodes
    assert not [node_id for node_id in compiled.plan.nodes if re.match(r"g\d", node_id)]


def test_top_value_in_a_guard_is_a_compile_error():
    with pytest.raises(ValidationError, match="guard_top_requires_cards"):
        parse_design_ir(_guard({"op": "gt", "left": _top_expr("discard", "value"),
                                "right": {"op": "lit", "value": 3}}))


def test_a_guard_naming_an_undeclared_zone_is_rejected():
    with pytest.raises(ValidationError, match="expression_unknown_zone:nope"):
        parse_design_ir(_guard({"op": "eq", "left": _top_expr("nope"),
                                "right": {"op": "lit", "value": "7"}}))
    with pytest.raises(ValidationError, match="expression_unknown_zone:nope"):
        parse_design_ir(_guard({"op": "zone_count", "zone": "nope"}))
    with pytest.raises(ValidationError, match="expression_unknown_zone:nope"):
        parse_design_ir(_guard({"op": "has_match", "zone": "hand",
                                "top": _top_expr("nope")}))


def test_top_guards_work_inside_a_multi_action_sequence():
    """The ADR-0012 scenario-B shape: play when a match exists, otherwise idle."""
    learned = _started(_guard({"op": "gt", "left": {"op": "lit", "value": 1},
                               "right": {"op": "lit", "value": 0}}))
    top = learned.state["zones"]["discard"]["cards"][-1]
    can_play = any(_card_matches(card, top)
                   for card in learned.state["zones"]["hand-0"]["cards"])

    payload = _guard({"op": "has_match", "zone": "hand", "top": _top_expr("discard")},
                     turn_actions=["play", "idle"])
    interpreter = _started(payload)
    assert interpreter.state["current_player"] == 0
    assert interpreter.legal_actions() == (["play"] if can_play else ["idle"])
