"""G2 / M2 acceptance: ComposedRulesIR, capabilities, deterministic compilation.

Every test maps to an M2 exit criterion:

* the design entry has a ``composed`` branch and rejects unknown fields / code;
* ``derive_requirements`` / ``resolve_composition`` check variants, parameters,
  zones, state reads and interaction inputs -- not just an axis name;
* the compiler lowers by mechanism and never dispatches on a game name;
* a normalised IR + compiler version + contract hash gives the same plan/hash;
* compile errors name the IR path and requirement clause;
* scenario A (the first unfamiliar composition) compiles, diagnoses and plays a
  full game through the real interpreter.
"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from pocker_agent.core import ToolError, core_registry, playtest
from pocker_agent.core.invariants import card_conservation
from pocker_agent.core.ir import DESIGN_ADAPTER, ComposedRulesIR, parse_design_ir
from pocker_agent.core.plan import plan_fingerprint
from pocker_agent.core.rules import (
    COMPILER_VERSION,
    MAX_PLAN_NODES,
    CompileError,
    CompositionReport,
    compile_composed,
    compile_expression,
    derive_requirements,
    expression_depth,
    ir_hash,
    resolve_composition,
    validate_expression,
)


# --------------------------------------------------------------- scenario A IR
def scenario_a_ir(**overrides) -> dict:
    """The hand-authored rule data for acceptance scenario A.

    Two players; ranks 2-9 in S/H; three public hand cards each; select one card
    per turn into the pot; the higher card scores 1; after three rounds the
    highest score wins. Six actor actions, no refill.
    """
    payload: dict = {
        "schema_version": "0.5",
        "kind": "composed",
        "meta": {"title": "三轮公开比较积分", "description": "场景 A 的人工规则数据。"},
        "requirements": [
            {"id": "A2", "text": "每人发 3 张公开手牌，其余留牌堆",
             "nodes": ["setup", "zones.hand", "zones.stock"]},
            {"id": "A3", "text": "每轮双方依次从手牌选择一张放入公共区",
             "nodes": ["actions.play", "zones.pot"]},
            {"id": "A4", "text": "第 1、3 轮 player-1 先，第 2 轮 player-2 先",
             "nodes": ["flow.start_seat"]},
            {"id": "A5", "text": "较大者得 1 分，平局不得分；比较后两张进弃牌区",
             "nodes": ["flow.resolve.0", "flow.resolve.1", "scoring.award_left",
                       "scoring.award_right"]},
            {"id": "A6", "text": "共 3 轮，最高分获胜，平分并列，不补牌",
             "nodes": ["terminal"]},
        ],
        "players": {"count": 2},
        "deck": {"ranks": ["2", "3", "4", "5", "6", "7", "8", "9"], "suits": ["S", "H"],
                 "copies": 1,
                 "values": {str(rank): rank for rank in range(2, 10)}},
        "zones": [
            {"id": "hand", "visibility": "public", "scope": "player"},
            {"id": "pot", "visibility": "public", "scope": "shared"},
            {"id": "discard", "visibility": "public", "scope": "shared"},
            {"id": "stock", "visibility": "hidden", "scope": "shared"},
        ],
        "setup": {"deals": [{"zone": "hand", "count": 3, "per_seat": True}],
                  "stock_zone": "stock"},
        "variables": [],
        "actions": [{
            "id": "play",
            "inputs": [{"id": "card", "kind": "card_selection", "zone": "hand",
                        "scope": "actor", "min_count": 1, "max_count": 1}],
            "effects": [
                {"kind": "select", "input": "card", "result": "picked"},
                {"kind": "move", "from_zone": "hand", "to_zone": "pot",
                 "selection": "picked"},
            ],
        }],
        "flow": {"round_action": "play", "start_seat": "round_parity", "resolve": [
            {"kind": "compare", "zone": "pot", "left": 0, "right": 1,
             "rules": ["award_left", "award_right"]},
            {"kind": "move_top", "from_zone": "pot", "to_zone": "discard", "count": 2},
        ]},
        "scoring": [
            {"id": "award_left", "on_outcome": "left", "points": 1, "recipient": "left"},
            {"id": "award_right", "on_outcome": "right", "points": 1, "recipient": "right"},
        ],
        "terminal": {"max_rounds": 3, "winner": "highest_score", "tie": "allow"},
        "macros": [],
    }
    payload.update(overrides)
    return payload


def smallest_card_strategy(interpreter):
    """The scenario-A fixture policy: play the lowest card in the actor's hand."""
    actions = interpreter.legal_actions()
    if "play" not in actions:
        return None
    seat = interpreter.state["current_player"]
    hand = interpreter.state["zones"][f"hand-{seat}"]["cards"]
    card = min(hand, key=lambda item: item.value)
    return ("play", {"card": [card.id]})


# ------------------------------------------------------------------ IR entry
def test_the_design_entry_has_a_composed_branch():
    ir = parse_design_ir(scenario_a_ir())
    assert isinstance(ir, ComposedRulesIR)
    assert ir.kind == "composed" and ir.schema_version == "0.5"
    # the known-eight adapter is unchanged and does not advertise composed
    from pocker_agent.core.ir import IR_ADAPTER
    kinds = set(IR_ADAPTER.json_schema()["discriminator"]["mapping"])
    assert "composed" not in kinds
    composed_kinds = set(DESIGN_ADAPTER.json_schema()["discriminator"]["mapping"])
    assert composed_kinds == kinds | {"composed"}


def test_unknown_fields_and_arbitrary_macros_are_rejected():
    payload = scenario_a_ir()
    payload["description"] = "top-level description is not a declared field"
    with pytest.raises(ValidationError):
        parse_design_ir(payload)
    payload = scenario_a_ir()
    payload["macros"] = [{"id": "m", "body": []}]
    with pytest.raises(ValidationError, match="macros_not_supported_in_m2"):
        parse_design_ir(payload)


def test_the_expression_language_rejects_code_and_free_state_paths():
    with pytest.raises(ValidationError):
        parse_design_ir(scenario_a_ir(actions=[{
            "id": "play",
            "guard": {"op": "eval", "code": "__import__('os')"},
            "inputs": scenario_a_ir()["actions"][0]["inputs"],
            "effects": scenario_a_ir()["actions"][0]["effects"]}]))
    with pytest.raises(ValidationError):
        parse_design_ir(scenario_a_ir(actions=[{
            "id": "play",
            "guard": {"op": "ref", "path": "zones.hand-0.cards"},
            "inputs": scenario_a_ir()["actions"][0]["inputs"],
            "effects": scenario_a_ir()["actions"][0]["effects"]}]))

    # declared references compile; undeclared variables do not
    from pocker_agent.core.rules import EXPR_ADAPTER
    guard = EXPR_ADAPTER.validate_python({"op": "gt", "left": {"op": "ref", "path": "scores.0"},
                                          "right": {"op": "lit", "value": 0}})
    assert expression_depth(guard) == 2
    assert compile_expression(guard) == {"gt": ["$state.scores.0", 0]}
    with pytest.raises(ToolError, match="expression_unknown_variable"):
        validate_expression(EXPR_ADAPTER.validate_python(
            {"op": "ref", "path": "variables.missing"}), frozenset())


# -------------------------------------------------------------- capabilities
def test_derive_and_resolve_cover_operations_zones_and_inputs():
    ir = parse_design_ir(scenario_a_ir())
    requirements = derive_requirements(ir)
    operations = {item.detail for item in requirements if item.code == "operation"}
    assert {"zones.select", "zones.move", "zones.top", "rank_compare.call",
            "score_settle.call", "winner_resolve.call", "logic.evaluate",
            "state.update", "deck.deal"} <= operations
    zones = {item.detail for item in requirements if item.code == "zone"}
    assert {"hand", "pot", "discard", "stock"} <= zones
    inputs = {item.detail for item in requirements if item.code == "input"}
    assert "card" in inputs

    report = resolve_composition(ir, core_registry())
    assert isinstance(report, CompositionReport)
    assert report.ok, report.missing
    # the hidden stock zone means a visibility axis is required too
    assert report.axes == ("info_set", "rank_compare", "score_settle", "sequential_turn")


def test_resolve_composition_reports_a_missing_operation_variant():
    """A missing *variant* must fail, not only a missing axis."""
    from pocker_agent.core.contracts import ToolRegistry
    registry = core_registry()
    trimmed = ToolRegistry()
    for name in registry.names():
        spec = registry.spec(name)
        if name == "rank_compare":
            continue  # drop the comparison tool entirely
        trimmed.register(spec)
    report = resolve_composition(parse_design_ir(scenario_a_ir()), trimmed)
    assert report.ok is False
    assert any(item.detail == "rank_compare.call" for item in report.missing)


# --------------------------------------------------------------- compilation
def test_a_normalised_ir_compiles_to_a_stable_plan_and_hash():
    ir = parse_design_ir(scenario_a_ir())
    first = compile_composed(ir, core_registry())
    second = compile_composed(parse_design_ir(scenario_a_ir()), core_registry())
    assert plan_fingerprint(first.plan) == plan_fingerprint(second.plan)
    assert first.ir_hash == second.ir_hash == ir_hash(ir)
    assert first.compiler_version == second.compiler_version == COMPILER_VERSION
    assert first.registry_contract_hash == second.registry_contract_hash
    assert first.plan.game_kind == "composed"
    assert first.source_map["clauses"]["A3"], "clause A3 must map to real nodes"
    assert first.source_map["nodes"]["setup_deal"]["path"] == "setup"


def test_the_same_mechanism_combination_compiles_to_the_same_structure():
    """No dispatch on game id / title: rename the game and the plan is identical.

    The compiler reads the mechanism fields only, so the two fingerprints must
    match exactly -- there is no per-title branch that could change the shape.
    """
    base = scenario_a_ir()
    renamed = scenario_a_ir(meta={"title": "War disguised as something else",
                                  "description": "not the same description"})
    left = compile_composed(parse_design_ir(base), core_registry())
    right = compile_composed(parse_design_ir(renamed), core_registry())
    assert plan_fingerprint(left.plan) == plan_fingerprint(right.plan)
    assert (left.plan.model_dump(mode="json")["nodes"]
            == right.plan.model_dump(mode="json")["nodes"])


def test_compile_errors_name_the_ir_path_and_clause():
    payload = scenario_a_ir()
    payload["flow"]["resolve"][0]["rules"] = ["does_not_exist"]
    with pytest.raises(ValidationError) as excinfo:
        parse_design_ir(payload)
    assert "compare_unknown_scoring_rule" in str(excinfo.value)

    # A structurally valid rule that the compiler cannot lower must fail with the
    # IR path and requirement clause, not a traceback.
    with pytest.raises(CompileError) as excinfo:
        compile_composed(scenario_a_ir(players={"count": 3}), core_registry())
    error = excinfo.value
    assert error.code == "compare_requires_two_players"
    assert error.path == "flow.resolve.0"
    assert error.clause == "A5", error.as_dict()


def test_the_compiler_refuses_to_guess_a_source_clause():
    ir = parse_design_ir(scenario_a_ir(requirements=[]))
    compiled = compile_composed(ir, core_registry())
    assert compiled.source_map["clauses"] == {}
    for entry in compiled.source_map["nodes"].values():
        assert entry["clause"] == "unmapped"


def test_the_plan_schema_versions_are_distinguishable():
    """ADR-0007: widening to 0.5 must not silently redefine 0.4 fields.

    M3's composed plan carries the 0.5 ``actions`` descriptor field, so the
    compiler stamps it 0.5. A 0.4 plan still parses (no ``actions`` -> ``[]``),
    and its fingerprint differs -- a content hash is not a verification
    credential.
    """
    from pocker_agent.core.plan import GamePlan

    compiled = compile_composed(parse_design_ir(scenario_a_ir()), core_registry())
    assert compiled.plan.schema_version == "0.5"
    assert compiled.plan.actions, "a composed plan must publish its action shapes"
    migrated = GamePlan.model_validate(
        {**compiled.plan.model_dump(mode="json"), "schema_version": "0.4",
         "actions": []})
    assert migrated.schema_version == "0.4"
    assert migrated.actions == []
    assert plan_fingerprint(migrated) != plan_fingerprint(compiled.plan)


def test_the_plan_stays_within_the_expansion_budget():
    compiled = compile_composed(parse_design_ir(scenario_a_ir()), core_registry())
    assert len(compiled.plan.nodes) <= MAX_PLAN_NODES
    assert compiled.plan.step_limit <= 1024


# ------------------------------------------------------------- vertical slice
def test_the_compiled_plan_is_fully_reachable_with_bounded_cycles():
    from pocker_agent.core.rules.structure import analyse_control_flow

    compiled = compile_composed(parse_design_ir(scenario_a_ir()), core_registry())
    report = analyse_control_flow(compiled.plan,
                                  frozenset({"set_round", "set_turn_index"}))
    assert report["unreachable"] == []
    assert report["unbounded_cycles"] == []
    assert report["inconclusive"] is False


def test_control_flow_analysis_flags_unreachable_nodes_and_unbounded_cycles():
    from pocker_agent.core.plan import GamePlan
    from pocker_agent.core.rules.structure import analyse_control_flow

    plan = GamePlan(
        game_kind="loop", players=1, tools=[{"name": "state"}], initial={}, entry="w",
        nodes={
            "w": {"kind": "wait", "inputs": {"go": "c"}},
            "c": {"kind": "call", "next": "w", "action": {
                "tool": "state", "operation": "update",
                "args": {"state": "$state", "values": {}}}},
            "orphan": {"kind": "end"},
        })
    report = analyse_control_flow(plan)
    assert report["unreachable"] == ["orphan"]
    assert report["unbounded_cycles"], "a wait cycle with no counter must be flagged"
    assert report["inconclusive"] is True, "no bounded-counter knowledge means no claim"


def shared_move_ir() -> dict:
    """Scenario A variant whose round action moves from a *shared* source zone.

    Regression for the P1 scope bug: a shared ``from_zone`` must compile to the
    literal zone id, not ``$state.zone_<id>``.
    """
    payload = scenario_a_ir()
    payload["zones"].append({"id": "market", "visibility": "public", "scope": "shared"})
    payload["setup"] = {"deals": [{"zone": "hand", "count": 3, "per_seat": True},
                                   {"zone": "market", "count": 6, "per_seat": False}],
                        "stock_zone": "stock"}
    payload["actions"][0]["inputs"] = [
        {"id": "card", "kind": "card_selection", "zone": "market", "scope": "shared",
         "min_count": 1, "max_count": 1}]
    payload["actions"][0]["effects"] = [
        {"kind": "select", "input": "card", "result": "picked"},
        {"kind": "move", "from_zone": "market", "to_zone": "pot", "selection": "picked"},
    ]
    return payload


def market_strategy(interpreter):
    actions = interpreter.legal_actions()
    if "play" not in actions:
        return None
    market = interpreter.state["zones"]["market"]["cards"]
    return ("play", {"card": [market[0].id]})


def test_a_shared_from_zone_compiles_to_the_literal_zone_id():
    """P1 regression: scope-aware zone references, not a forced actor zone."""
    compiled = compile_composed(parse_design_ir(shared_move_ir()), core_registry())
    move = compiled.plan.nodes["act_play_1_move"].action.args["moves"][0]
    assert move["from"] == "market"
    assert move["to"] == "pot"
    assert "zone_market" not in compiled.plan.nodes, "a shared zone needs no actor lookup"
    report = playtest(compiled.plan, core_registry(), market_strategy, seeds=(0, 1))
    assert report.ok, report.failures


def test_a_player_from_zone_still_uses_the_actor_instance():
    compiled = compile_composed(parse_design_ir(scenario_a_ir()), core_registry())
    move = compiled.plan.nodes["act_play_1_move"].action.args["moves"][0]
    assert move["from"] == "$state.zone_hand"
    assert move["to"] == "pot"


def test_input_scope_must_match_the_zone_scope():
    payload = scenario_a_ir()
    payload["actions"][0]["inputs"][0]["scope"] = "shared"   # hand is a player zone
    with pytest.raises(ValidationError, match="shared_input_requires_shared_zone"):
        parse_design_ir(payload)


def test_a_move_top_cannot_target_a_player_zone():
    payload = scenario_a_ir()
    payload["flow"]["resolve"][1]["to_zone"] = "hand"
    with pytest.raises(ValidationError, match="move_top_requires_shared_zone"):
        parse_design_ir(payload)


def test_assignment_types_are_checked_statically():
    payload = scenario_a_ir(variables=[{"name": "label", "type": "integer", "initial": 0}],
                            actions=[{**scenario_a_ir()["actions"][0], "effects": [
                                *scenario_a_ir()["actions"][0]["effects"],
                                {"kind": "assign", "variable": "label",
                                 "value": {"op": "lit", "value": "nope"}}]}])
    with pytest.raises(ValidationError, match="assign_type_mismatch"):
        parse_design_ir(payload)
    # arithmetic on a string literal is rejected by the type inferer
    with pytest.raises(ValidationError, match="expression_type_mismatch"):
        parse_design_ir(scenario_a_ir(
            variables=[{"name": "n", "type": "integer", "initial": 0}],
            actions=[{**scenario_a_ir()["actions"][0], "effects": [
                *scenario_a_ir()["actions"][0]["effects"],
                {"kind": "assign", "variable": "n", "value": {
                    "op": "add", "left": {"op": "lit", "value": "a"},
                    "right": {"op": "lit", "value": 1}}}]}]))
    # a compatible typed assignment compiles
    compatible = scenario_a_ir(
        variables=[{"name": "n", "type": "integer", "initial": 0}],
        actions=[{**scenario_a_ir()["actions"][0], "effects": [
            *scenario_a_ir()["actions"][0]["effects"],
            {"kind": "assign", "variable": "n", "value": {
                "op": "add", "left": {"op": "ref", "path": "action_count"},
                "right": {"op": "lit", "value": 1}}}]}])
    compile_composed(parse_design_ir(compatible), core_registry())


def test_a_compare_position_must_be_filled_by_the_round_action():
    payload = scenario_a_ir()
    payload["flow"]["resolve"][0]["left"] = 3    # capacity is only 2
    with pytest.raises(ValidationError, match="compare_zone_too_small"):
        parse_design_ir(payload)


def test_zones_count_operations_declare_their_real_shapes():
    """Close the review note: ``count`` is state-only, ``count_zone`` requires a zone."""
    registry = core_registry()
    every = registry.spec("zones").operation("count")
    one = registry.spec("zones").operation("count_zone")
    assert every.input_schema["required"] == ["state"]
    assert "zone" not in every.input_schema["properties"]
    assert every.output_schema["required"] == ["counts"]
    assert one.input_schema["required"] == ["state", "zone"]
    assert one.output_schema["required"] == ["zone", "count"]


def preloaded_compare_ir() -> dict:
    """A compare zone filled at setup: the action plays elsewhere, compare reads it."""
    payload = scenario_a_ir()
    payload["setup"] = {"deals": [{"zone": "hand", "count": 3, "per_seat": True},
                                   {"zone": "pot", "count": 2, "per_seat": False}],
                        "stock_zone": "stock"}
    payload["actions"][0]["effects"] = [
        {"kind": "select", "input": "card", "result": "picked"},
        {"kind": "move", "from_zone": "hand", "to_zone": "discard", "selection": "picked"},
    ]
    payload["flow"]["resolve"] = [
        {"kind": "compare", "zone": "pot", "left": 0, "right": 1,
         "rules": ["award_left", "award_right"]}]
    return payload


def test_a_compare_zone_prefilled_at_setup_is_accepted():
    """P2 regression: the setup stock into the compare zone must count."""
    compiled = compile_composed(parse_design_ir(preloaded_compare_ir()), core_registry())
    report = playtest(compiled.plan, core_registry(), smallest_card_strategy, seeds=(0, 1))
    assert report.ok, report.failures


def test_a_compare_zone_drained_before_the_compare_is_still_rejected():
    payload = preloaded_compare_ir()
    payload["flow"]["resolve"].insert(
        0, {"kind": "move_top", "from_zone": "pot", "to_zone": "discard", "count": 2})
    with pytest.raises(ValidationError, match="compare_zone_too_small"):
        parse_design_ir(payload)


def test_scenario_a_compiles_and_simulates_a_full_game():
    compiled = compile_composed(parse_design_ir(scenario_a_ir()), core_registry())
    report = playtest(compiled.plan, core_registry(), smallest_card_strategy,
                      seeds=(0, 1, 7, 42), invariants=(card_conservation(16),))
    assert report.ok, report.failures

    from pocker_agent.core.interpreter import Interpreter
    interpreter = Interpreter(compiled.plan, core_registry(), seed=3)
    interpreter.setup()
    actions = 0
    while not interpreter.state.get("finished"):
        action, payload = smallest_card_strategy(interpreter)
        interpreter.step(action, **(payload or {}))
        actions += 1
    assert actions == 6, "three rounds x two actors"
    assert interpreter.state["action_count"] == 6
    assert interpreter.state["round"] == 4, "the round counter advanced past 3"
    assert interpreter.state["finished"] is True
    assert interpreter.state["phase"] == "finished"
    scores = interpreter.state["scores"]
    assert len(scores) == 2 and sum(scores) <= 3
    assert interpreter.state["winners"] == [index for index, value in enumerate(scores)
                                            if value == max(scores)]
    # the two played cards left the pot for the discard each round
    zones = interpreter.state["zones"]
    assert zones["pot"]["cards"] == []
    assert len(zones["discard"]["cards"]) == 6


def test_scenario_a_cards_are_conserved_across_every_round():
    compiled = compile_composed(parse_design_ir(scenario_a_ir()), core_registry())
    report = playtest(compiled.plan, core_registry(), smallest_card_strategy, seeds=(5,),
                      invariants=(card_conservation(16),))
    assert report.ok, report.failures


def test_the_compiled_plan_registers_and_restores_through_the_real_session_store(tmp_path):
    """The compiled plan is reachable through the registry/session path the API uses.

    A passing playtest registers the plan; the store records its fingerprint and
    refuses a silent replacement; the persisted interpreter restores to the same
    finished state. (Bot payload generation for composed actions is M3 work; this
    slice drives the human seat through the real interpreter.)
    """
    from pocker_agent.core.interpreter import Interpreter
    from pocker_agent.core.session import SessionStore

    compiled = compile_composed(parse_design_ir(scenario_a_ir()), core_registry())
    report = playtest(compiled.plan, core_registry(), smallest_card_strategy, seeds=(3,))
    assert report.ok, report.failures

    store = SessionStore(tmp_path / "g2-m2.sqlite")
    store.register_plan("scenario-a", compiled.plan.model_dump(mode="json"),
                        report.as_dict(), title="场景 A")
    session = store.create("scenario-a", seed=3)
    assert plan_fingerprint(session.plan) == compiled.plan_hash
    restored = store.get(session.id)
    assert plan_fingerprint(restored.plan) == compiled.plan_hash

    interpreter = session.interpreter
    actions = 0
    while not interpreter.state.get("finished"):
        action, payload = smallest_card_strategy(interpreter)
        interpreter.step(action, **(payload or {}))
        actions += 1
    assert actions == 6
    replay = Interpreter.restore(interpreter.serialize(), core_registry())
    assert replay.state["finished"] is True
    assert replay.state["action_count"] == 6


def test_an_invalid_selection_does_not_advance_the_game():
    from pocker_agent.core.contracts import ToolError
    from pocker_agent.core.interpreter import Interpreter

    compiled = compile_composed(parse_design_ir(scenario_a_ir()), core_registry())
    interpreter = Interpreter(compiled.plan, core_registry(), seed=0)
    interpreter.setup()
    before = copy.deepcopy(interpreter.serialize())
    with pytest.raises(ToolError, match="selection_not_owned_by_zone"):
        interpreter.step("play", card=["9H"])   # not in the actor's hand
    assert interpreter.serialize() == before
