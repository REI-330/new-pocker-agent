"""G2 / M1 acceptance: typed contracts, generic zones, split matching behaviour.

Each test maps to an M1 exit criterion:

* selection count / ownership errors cannot change state;
* an empty hand does not end a game whose terminal is "reach a score";
* duplicate deck copies stay conserved through a move;
* the same move/score/turn operation serves two combinations (see also
  ``test_architecture_invariants.py``);
* the split did not regress the reference games that used the old behaviour.
"""
from __future__ import annotations

import copy

import pytest

from pocker_agent.core import (
    CardRef,
    GamePlan,
    Interpreter,
    ToolError,
    all_cards,
    apply_moves,
    assert_unique_ownership,
    core_registry,
    crazy_eights_plan,
    ownership_problems,
    playtest,
    uno_plan,
)
from pocker_agent.core.playtest import card_first
from pocker_agent.core.tools import DeckTool, MatchingTool


def card(rank: str, suit: str, value: int) -> CardRef:
    return CardRef(f"{rank}{suit}", rank, suit, value)


# ----------------------------------------------------------------- typed contracts

def test_every_operation_declares_a_typed_contract():
    for tool in core_registry().export():
        for operation in tool["operations"]:
            label = f"{tool['name']}.{operation['name']}"
            assert operation["input_schema"], label
            assert operation["output_schema"], label
            assert isinstance(operation["reads"], list), label
            assert isinstance(operation["feature_constraints"], list), label
            # `writes` is a read-only alias of the enforced write set.
            assert operation["writes"] == operation["effects"], label
            # `params` is the legacy view of the input schema, never a second truth.
            assert set(operation["params"]) == set(
                operation["input_schema"].get("properties", {})), label


def test_tool_config_schema_is_copied_onto_its_operations():
    deck = core_registry().spec("deck")
    assert deck.config_schema["properties"]["ranks"]
    for operation in deck.operations:
        assert operation.config_schema == deck.config_schema
    # An unconfigured tool declares nothing rather than a fake schema.
    assert core_registry().spec("zones").config_schema == {}


def test_feature_constraints_use_the_declared_vocabulary():
    from pocker_agent.core.contracts import FEATURE_TAGS

    for tool in core_registry().export():
        for operation in tool["operations"]:
            assert set(operation["feature_constraints"]) <= FEATURE_TAGS


def test_matching_play_contract_no_longer_claims_a_terminal_write():
    spec = core_registry().spec("matching").operation("play")
    assert "finished" not in spec.writes and "winners" not in spec.writes
    assert spec.failure == "rollback"
    spec_select = core_registry().spec("zones").operation("select")
    assert spec_select.failure == "reject_only" and spec_select.effects == ()


# ------------------------------------------------------------------- zone model

def zones_state():
    return {
        "zones": {
            "hand-0": {"owner": 0, "visibility": "owner_only",
                       "cards": [card("5", "S", 5), card("7", "H", 7)]},
            "market": {"owner": None, "visibility": "public",
                       "cards": [card("9", "S", 9)]},
        },
        "finished": False,
        "winners": [],
    }


def zones_plan() -> GamePlan:
    return GamePlan(
        game_kind="zones_mini", players=1,
        tools=[{"name": "state"}, {"name": "zones"}],
        initial=zones_state(), entry="wait",
        nodes={
            "wait": {"kind": "wait", "inputs": {"pick": "pick", "swap": "swap"}},
            "pick": {"kind": "call", "next": "wait", "action": {
                "tool": "zones", "operation": "select",
                "args": {"state": "$state", "zone": "hand-0",
                         "card_ids": "$state.input.cards", "min_count": 1, "max_count": 1},
                "result_key": "picked"}},
            "swap": {"kind": "call", "next": "wait", "action": {
                "tool": "zones", "operation": "move",
                "args": {"state": "$state", "moves": [
                    {"from": "hand-0", "to": "market",
                     "card_ids": "$state.input.cards", "min_count": 1, "max_count": 1}]},
                "result_key": "moved"}},
        }, step_limit=64)


def test_a_wrong_selection_count_cannot_change_state():
    interpreter = Interpreter(zones_plan(), core_registry(), seed=0)
    interpreter.setup()
    before = copy.deepcopy(interpreter.serialize())
    with pytest.raises(ToolError, match="selection_count_out_of_range:0"):
        interpreter.step("pick", cards=[])
    assert interpreter.serialize() == before
    with pytest.raises(ToolError, match="selection_count_out_of_range:2"):
        interpreter.step("pick", cards=["5S", "7H"])
    assert interpreter.serialize() == before


def test_selecting_a_card_the_zone_does_not_own_cannot_change_state():
    interpreter = Interpreter(zones_plan(), core_registry(), seed=0)
    interpreter.setup()
    before = copy.deepcopy(interpreter.serialize())
    with pytest.raises(ToolError, match="selection_not_owned_by_zone:9S"):
        interpreter.step("pick", cards=["9S"])          # the market's card
    assert interpreter.serialize() == before
    # a valid selection and a valid move both work
    interpreter.step("pick", cards=["5S"])
    assert interpreter.state["picked"]["ids"] == ["5S"]
    interpreter.step("swap", cards=["5S"])
    assert [c.id for c in interpreter.state["zones"]["market"]["cards"]] == ["9S", "5S"]


def test_a_zone_selection_result_survives_serialize_and_restore():
    """A stored selection holds real ``CardRef`` values, so encode/decode must round-trip."""
    interpreter = Interpreter(zones_plan(), core_registry(), seed=0)
    interpreter.setup()
    interpreter.step("pick", cards=["5S"])
    restored = Interpreter.restore(interpreter.serialize(), core_registry())
    assert restored.serialize() == interpreter.serialize()
    assert restored.state["picked"]["cards"][0].id == "5S"


def test_duplicate_copies_are_conserved_through_a_move():
    copies = DeckTool(("5",), ("S",), copies=2).catalog()
    zones = {"a": {"owner": 0, "visibility": "public", "cards": copies},
             "b": {"owner": None, "visibility": "public", "cards": []}}
    applied = apply_moves(zones, [{"from": "a", "to": "b", "card_ids": [copies[0].id]}])
    ids = sorted(c.id for c in all_cards(applied))
    assert ids == sorted(c.id for c in copies), "a copy was duplicated or lost"
    assert len(set(ids)) == 2
    assert ownership_problems(applied) == []


def test_the_same_card_id_in_two_zones_is_an_ownership_violation():
    duplicated = card("5", "S", 5)
    zones = {"a": {"owner": 0, "visibility": "public", "cards": [duplicated]},
             "b": {"owner": 1, "visibility": "public", "cards": [duplicated]}}
    assert ownership_problems(zones) == ["duplicate_card:5S"]
    with pytest.raises(ToolError, match="zone_ownership_violation:duplicate_card:5S"):
        assert_unique_ownership(zones)


def test_move_is_atomic_when_one_of_several_moves_is_invalid():
    before = zones_state()
    with pytest.raises(ToolError, match="selection_not_owned_by_zone"):
        apply_moves(before["zones"], [
            {"from": "hand-0", "to": "market", "card_ids": ["5S"]},
            {"from": "market", "to": "hand-0", "card_ids": ["nope"]}])
    assert before == zones_state(), "the caller's zone table must be untouched"


# ------------------------------------------------- empty hand is not a terminal

def matching_plan() -> GamePlan:
    top = card("5", "S", 5)
    return GamePlan(
        game_kind="matching_mini", players=1,
        tools=[{"name": "state"}, {"name": "logic"}, {"name": "matching"}],
        initial={"hands": [[card("5", "H", 5)], [card("7", "H", 7)]],
                 "table": [top], "discard": [top], "active_suit": "S",
                 "finished": False, "winners": []},
        entry="loop",
        nodes={
            "loop": {"kind": "wait", "inputs": {"play": "play"}},
            "play": {"kind": "call", "next": "check", "action": {
                "tool": "matching", "operation": "play",
                "args": {"state": "$state", "hand_index": 0,
                         "card_index": "$state.input.card_index"},
                "result_key": "played"}},
            "check": {"kind": "call", "next": "branch", "action": {
                "tool": "logic", "operation": "evaluate",
                "args": {"expression": {"eq": [{"count": ["$state.hands.0"]}, 99]}},
                "result_key": "should_end"}},
            "branch": {"kind": "branch", "value": "$state.should_end",
                       "cases": [{"value": True, "target": "finish"}], "next": "loop"},
            "finish": {"kind": "call", "next": "end", "action": {
                "tool": "state", "operation": "update",
                "args": {"state": "$state", "values": {
                    "finished": True, "winners": [0], "phase": "finished"}}}},
            "end": {"kind": "end"},
        }, step_limit=64)


def test_matching_play_does_not_declare_a_winner_for_an_empty_hand():
    tool = MatchingTool()
    state = {"hands": [[card("5", "H", 5)], [card("7", "H", 7)]],
             "table": [card("5", "S", 5)], "discard": [card("5", "S", 5)], "active_suit": "S"}
    result = tool.play(state, 0, 0)
    assert state["hands"][0] == [] and result["hand_empty"] is True
    assert "finished" not in state and "winners" not in state


def test_a_score_terminal_keeps_running_after_the_hand_empties():
    """Scenario B's rule: emptying a hand is not the end of the game."""
    interpreter = Interpreter(matching_plan(), core_registry(), seed=0)
    interpreter.setup()
    interpreter.step("play", card_index=0)
    assert interpreter.state["hands"][0] == []
    assert interpreter.state.get("finished", False) is False
    assert interpreter.state["played"]["hand_empty"] is True
    assert interpreter.legal_actions() == ["play"], "the plan kept its own terminal"


# ------------------------------------------------------- reference-game regression

def test_crazy_eights_still_finishes_with_a_plan_declared_terminal():
    report = playtest(crazy_eights_plan(hand_size=4), core_registry(), card_first,
                      seeds=(0, 7))
    assert report.ok, report.failures


def test_uno_still_finishes_with_a_plan_declared_terminal():
    report = playtest(uno_plan(hand_size=4), core_registry(), card_first, seeds=(0, 7))
    assert report.ok, report.failures
