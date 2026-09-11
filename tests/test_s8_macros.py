"""S8: macros are declarative, validated, inlined, and tracked for promotion."""
from __future__ import annotations

import pytest

from pocker_agent.core import core_registry, host_compile, parse_ir
from pocker_agent.core.contracts import ToolError
from pocker_agent.core.macro_library import MATCH_TURN, PLAN_MACROS, default_macros
from pocker_agent.core.macros import MacroRegistry, MacroSpec, expand_macro, promotion_report, validate_macro, wire


def tiny_macro(**overrides) -> MacroSpec:
    payload = {
        "name": "tiny", "entry": "start", "exits": ("done",),
        "params": ("cards",), "kind": "mechanism",
        "nodes": {
            "start": {"kind": "call", "next": "@exit:done", "action": {
                "tool": "pattern", "operation": "choices",
                "args": {"cards": "{cards}", "top": "$state.table"},
                "result_key": "legal_card_indices"}},
        }}
    payload.update(overrides)
    return MacroSpec(**payload)


# --------------------------------------------------------------- expansion

def test_expansion_prefixes_ids_and_localizes_internal_references():
    nodes, entry = expand_macro(MATCH_TURN, "seat0", cards="$state.hands.0")
    assert entry == "seat0:choices"
    assert set(nodes) == {"seat0:choices", "seat0:has", "seat0:gate", "seat0:wait", "seat0:drawwait"}
    assert nodes["seat0:choices"]["next"] == "seat0:has"          # internal ref prefixed
    assert nodes["seat0:choices"]["action"]["args"]["cards"] == "$state.hands.0"
    assert nodes["seat0:gate"]["cases"][0]["target"] == "seat0:wait"
    assert nodes["seat0:wait"]["inputs"]["play"] == "@exit:play"  # exits left for the caller


def test_expansion_requires_exactly_the_declared_params():
    with pytest.raises(ToolError, match="missing_macro_params"):
        expand_macro(MATCH_TURN, "x")
    with pytest.raises(ToolError, match="unknown_macro_params"):
        expand_macro(MATCH_TURN, "x", cards="a", bogus=1)
    with pytest.raises(ToolError, match="invalid_macro_prefix"):
        expand_macro(MATCH_TURN, "bad:prefix", cards="a")


def test_wiring_replaces_every_exit_or_fails():
    nodes, _ = expand_macro(MATCH_TURN, "seat1", cards="$state.hands.1")
    wire(nodes, {"play": "play1", "draw": "draw1"})
    assert nodes["seat1:wait"]["inputs"]["play"] == "play1"
    assert nodes["seat1:drawwait"]["inputs"]["draw"] == "draw1"

    nodes, _ = expand_macro(MATCH_TURN, "seat2", cards="$state.hands.0")
    with pytest.raises(ToolError, match="macro_exit_unwired:draw"):
        wire(nodes, {"play": "play2"})


# -------------------------------------------------------------- validation

def test_validation_rejects_a_broken_macro():
    with pytest.raises(ToolError, match="macro_entry_missing"):
        validate_macro(tiny_macro(entry="nope"))
    with pytest.raises(ToolError, match="macro_must_not_end"):
        validate_macro(tiny_macro(nodes={"start": {"kind": "end"}}))
    with pytest.raises(ToolError, match="macro_undeclared_exit"):
        validate_macro(tiny_macro(nodes={
            "start": {"kind": "call", "next": "@exit:other", "action": {
                "tool": "pattern", "operation": "choices",
                "args": {"cards": "{cards}", "top": "$state.table"}}}}))
    with pytest.raises(ToolError, match="unknown_tool_operation"):
        validate_macro(tiny_macro(nodes={
            "start": {"kind": "call", "next": "@exit:done", "action": {
                "tool": "pattern", "operation": "teleport",
                "args": {"cards": "{cards}"}}}}), core_registry())


def test_registry_is_a_whitelist():
    registry = default_macros()
    assert registry.names() == ["match_turn"]
    nodes, entry = registry.expand("match_turn", "z", cards="$state.hands.0")
    assert entry == "z:choices" and "z:gate" in nodes
    with pytest.raises(ToolError, match="unknown_macro"):
        registry.expand("nope", "z", cards="a")
    with pytest.raises(ToolError, match="macro_duplicate"):
        registry.register(MATCH_TURN)


# --------------------------------------------------- promotion rule (6.4)

def test_flow_macros_are_not_promotion_candidates():
    usage = {"match_turn": ["crazy_eights", "uno"]}       # reused twice, but control flow
    assert promotion_report(usage, default_macros()) == []


def test_a_reused_mechanism_macro_is_flagged_for_promotion():
    registry = MacroRegistry(core_registry())
    registry.register(tiny_macro())
    usage = {"tiny": ["game_a", "game_b"]}
    report = promotion_report(usage, registry)
    assert report == [{"macro": "tiny", "plans": ["game_a", "game_b"], "uses": 2,
                       "action": "promote_to_axis"}]


def test_a_promoted_macro_is_no_longer_flagged():
    registry = MacroRegistry(core_registry())
    registry.register(tiny_macro(promoted=True))
    assert promotion_report({"tiny": ["a", "b"]}, registry) == []


def test_declared_usage_matches_reality():
    """PLAN_MACROS must reflect plans that actually expand the macro."""
    shedding = host_compile(parse_ir({"kind": "shedding", "game_id": "m1", "title": "t",
                                      "hand_size": 5, "wild_rank": "8"}))
    uno = host_compile(parse_ir({"kind": "uno", "game_id": "m2", "title": "t", "hand_size": 5}))
    for plan in (shedding, uno):
        assert any(node.startswith("seat0:") for node in plan.nodes), plan.game_kind
    assert PLAN_MACROS == {"crazy_eights": ["match_turn"], "uno": ["match_turn"]}
