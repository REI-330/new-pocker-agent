"""G2 / M3a acceptance: composed action descriptors and generic bot payloads.

M2 could compile a composed plan, but no host policy could produce the payload a
composed ``wait`` action expects: ``card_first`` sent ``card_index`` while the
compiled plan reads ``input.card``, and the payload-free probe failed with
``state_reference_not_found``. This slice makes the plan publish its input
*shape* (Plan 0.5 ``actions``) and drives bots and sessions from it, so a
composed game is playable end to end without a per-game strategy.

Exit criteria covered here:

* the compiler emits typed ``ActionDescriptor`` data and stamps the plan 0.5;
* a generic bot policy fills each declared input from the live state;
* ``Interpreter.view`` exposes the actions with viewer-filtered options;
* a full composed game finishes through ``run_bots`` and ``SessionStore``;
* a 0.4 plan still parses and routes through the legacy policy unchanged.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError
from test_g2_m2 import scenario_a_ir, shared_move_ir

from pocker_agent.core import (
    ActionDescriptor,
    ActionInputDescriptor,
    CardRef,
    Interpreter,
    SessionStore,
    ToolError,
    compile_composed,
    core_registry,
    playtest,
    run_bots,
)
from pocker_agent.core.actions import descriptor_for, payload_for, resolve_zone
from pocker_agent.core.decision import policy_context
from pocker_agent.core.ir import parse_design_ir
from pocker_agent.core.plan import GamePlan
from pocker_agent.core.policy import bot_action


def compiled_scenario_a():
    return compile_composed(parse_design_ir(scenario_a_ir()), core_registry())


# --------------------------------------------------------- compiler publishing
def test_the_compiler_publishes_typed_action_descriptors():
    compiled = compiled_scenario_a()
    assert compiled.plan.schema_version == "0.5"
    assert [action.id for action in compiled.plan.actions] == ["play"]
    action = compiled.plan.actions[0]
    assert action.label == ""
    (card,) = action.inputs
    assert (card.id, card.kind, card.zone, card.scope, card.min_count, card.max_count) == (
        "card", "card_selection", "hand", "actor", 1, 1)


def test_action_input_bounds_are_validated_in_the_schema():
    with pytest.raises(ValidationError, match="action_input_bounds_invalid"):
        ActionInputDescriptor(id="card", zone="hand", min_count=2, max_count=1)
    with pytest.raises(ValidationError, match="action_input_ids_must_be_unique"):
        ActionDescriptor(id="play", inputs=[{"id": "card", "zone": "hand"},
                                            {"id": "card", "zone": "hand"}])


def test_reference_plans_publish_no_action_descriptors():
    """A 0.4 plan is exactly ``actions == []``; its routing must be untouched."""
    from pocker_agent.core.reference import REFERENCE_GAMES

    for game in REFERENCE_GAMES.values():
        assert game.build().actions == [], game.id


# ----------------------------------------------------------- generic bot policy
def test_the_bot_payload_comes_from_the_live_zone_not_the_action_name():
    compiled = compiled_scenario_a()
    interpreter = Interpreter(compiled.plan, core_registry(), seed=0)
    interpreter.setup()
    action, payload = bot_action(policy_context(interpreter))
    seat = interpreter.state["current_player"]
    hand = interpreter.state["zones"][f"hand-{seat}"]["cards"]
    assert action == "play"
    assert payload == {"card": [hand[0].id]}


def test_run_bots_plays_a_composed_game_to_completion():
    compiled = compiled_scenario_a()
    interpreter = Interpreter(compiled.plan, core_registry(), seed=4)
    interpreter.setup()
    steps = run_bots(interpreter, human_index=-1)
    assert steps == 6
    assert interpreter.state["finished"] is True
    assert interpreter.state["action_count"] == 6
    assert interpreter.state["zones"]["pot"]["cards"] == []
    assert len(interpreter.state["zones"]["discard"]["cards"]) == 6


def test_a_shared_zone_input_resolves_to_the_literal_zone():
    compiled = compile_composed(parse_design_ir(shared_move_ir()), core_registry())
    interpreter = Interpreter(compiled.plan, core_registry(), seed=0)
    interpreter.setup()
    descriptor = descriptor_for(compiled.plan, "play")
    assert resolve_zone(interpreter.state, descriptor.inputs[0]) == "market"
    steps = run_bots(interpreter, human_index=-1)
    assert steps == 6
    assert interpreter.state["finished"] is True


def test_payload_for_caps_at_max_count_and_respects_a_floor():
    descriptor = ActionDescriptor(id="pick", inputs=[
        {"id": "cards", "zone": "hand", "scope": "actor", "min_count": 2, "max_count": 2}])
    cards = [CardRef("2S", "2", "S", 2), CardRef("3S", "3", "S", 3),
             CardRef("4S", "4", "S", 4)]
    state = {"current_player": 1, "zone_hand": "hand-1",
             "zones": {"hand-1": {"cards": cards}}}
    assert payload_for(state, descriptor) == {"cards": ["2S", "3S"]}
    empty = {"current_player": 1, "zone_hand": "hand-1", "zones": {"hand-1": {"cards": []}}}
    with pytest.raises(ToolError, match="action_input_insufficient_cards"):
        payload_for(empty, descriptor)


# ------------------------------------------------------------------------ view
def test_the_view_publishes_actions_with_viewer_filtered_options():
    compiled = compiled_scenario_a()
    interpreter = Interpreter(compiled.plan, core_registry(), seed=0)
    interpreter.setup()
    seat = interpreter.state["current_player"]
    hand_ids = [card.id for card in interpreter.state["zones"][f"hand-{seat}"]["cards"]]
    # 只有当前行动者收到动作面，防止旁观者利用合法动作作为侧信道。
    actor_view = f"player-{seat + 1}"
    action = interpreter.view(actor_view)["actions"][0]
    assert action["id"] == "play"
    assert action["inputs"][0]["options"] == hand_ids
    assert "owner" not in action["inputs"][0]


def hidden_hand_ir() -> dict:
    payload = scenario_a_ir()
    payload["zones"][0]["visibility"] = "owner_only"
    return payload


def test_action_options_are_hidden_from_a_non_owner():
    compiled = compile_composed(parse_design_ir(hidden_hand_ir()), core_registry())
    interpreter = Interpreter(compiled.plan, core_registry(), seed=0)
    interpreter.setup()
    actor = interpreter.state["current_player"]
    other = "player-2" if actor == 0 else "player-1"
    assert interpreter.view(f"player-{actor + 1}")["actions"][0]["inputs"][0]["options"]
    assert "actions" not in interpreter.view(other)


# -------------------------------------------------------------- end-to-end
def test_the_gate_can_use_the_bot_policy_for_a_composed_plan():
    compiled = compiled_scenario_a()
    report = playtest(compiled.plan, core_registry(), bot_action, seeds=(0, 1, 7))
    assert report.ok, report.failures
    assert report.covered_wait_nodes == ["turn"]


def test_a_composed_game_runs_through_the_session_store_with_bot_payloads(tmp_path):
    compiled = compiled_scenario_a()
    report = playtest(compiled.plan, core_registry(), bot_action, seeds=(3,))
    assert report.ok, report.failures
    store = SessionStore(tmp_path / "g2-m3a.sqlite")
    store.register_plan("composed-duel", compiled.plan.model_dump(mode="json"),
                        report.as_dict(), title="组合对局")
    session = store.create("composed-duel", seed=3)
    assert session.revision == 0
    descriptor = descriptor_for(session.plan, "play")
    guard = 0
    while not session.interpreter.state.get("finished"):
        payload = payload_for(session.interpreter.state, descriptor)
        store.act(session.id, "play", session.revision, **payload)
        session = store.get(session.id)
        guard += 1
        assert guard < 12, "the store must drive the game to completion"
    # the human acts once per round; the bots finish the other turns
    assert session.revision == 3
    assert session.interpreter.state["action_count"] == 6
    assert store.get(session.id).interpreter.state["finished"] is True


# ------------------------------------------------------------- 0.4 regression
def test_a_legacy_0_4_plan_parses_without_action_descriptors():
    plan = GamePlan.model_validate({
        "schema_version": "0.4", "game_kind": "war", "players": 2,
        "tools": [{"name": "state"}], "initial": {}, "entry": "w",
        "nodes": {"w": {"kind": "wait", "inputs": {"go": "e"}},
                  "e": {"kind": "end"}}})
    assert plan.schema_version == "0.4"
    assert plan.actions == []


# ------------------------------------------------------- dual-zone selection
def dual_zone_ir() -> dict:
    """One action with two selection inputs (actor hand + shared market).

    Scenario C's acceptance distinction: a fixed ``card_index`` model cannot
    express an action that reads two zones, but the plan-0.5 descriptor can.
    """
    return {
        "schema_version": "0.5", "kind": "composed",
        "meta": {"title": "双区域交换", "description": "两个选牌输入。"},
        "requirements": [],
        "players": {"count": 2},
        "deck": {"ranks": [str(n) for n in range(2, 10)], "suits": ["S", "H"],
                 "copies": 1, "values": {str(n): n for n in range(2, 10)}},
        "zones": [
            {"id": "hand", "visibility": "public", "scope": "player"},
            {"id": "market", "visibility": "public", "scope": "shared"},
            {"id": "discard", "visibility": "public", "scope": "shared"},
            {"id": "stock", "visibility": "hidden", "scope": "shared"},
        ],
        "setup": {"deals": [{"zone": "hand", "count": 3, "per_seat": True},
                              {"zone": "market", "count": 3, "per_seat": False}],
                  "stock_zone": "stock"},
        "variables": [],
        "actions": [{
            "id": "exchange",
            "inputs": [
                {"id": "hand_card", "kind": "card_selection", "zone": "hand",
                 "scope": "actor", "min_count": 1, "max_count": 1},
                {"id": "market_card", "kind": "card_selection", "zone": "market",
                 "scope": "shared", "min_count": 1, "max_count": 1},
            ],
            "effects": [
                {"kind": "select", "input": "hand_card", "result": "from_hand"},
                {"kind": "select", "input": "market_card", "result": "from_market"},
                {"kind": "move", "from_zone": "hand", "to_zone": "market",
                 "selection": "from_hand"},
                {"kind": "move", "from_zone": "market", "to_zone": "hand",
                 "selection": "from_market"},
            ],
        }],
        "flow": {"round_action": "exchange", "start_seat": "seat0", "resolve": [
            {"kind": "compare", "zone": "market", "left": 0, "right": 1,
             "rules": ["award_left", "award_right"]},
        ]},
        "scoring": [
            {"id": "award_left", "on_outcome": "left", "points": 1, "recipient": "left"},
            {"id": "award_right", "on_outcome": "right", "points": 1, "recipient": "right"},
        ],
        "terminal": {"max_rounds": 2, "winner": "highest_score", "tie": "allow"},
        "macros": [],
    }


def test_a_two_zone_action_compiles_and_plays():
    from pocker_agent.core.verify import VERIFICATION_SEEDS, VERIFICATION_STRATEGIES, contract_check

    compiled = compile_composed(parse_design_ir(dual_zone_ir()), core_registry())
    (action,) = compiled.plan.actions
    assert action.id == "exchange"
    assert [(item.id, item.scope, item.zone) for item in action.inputs] == [
        ("hand_card", "actor", "hand"), ("market_card", "shared", "market")]
    report = playtest(compiled.plan, core_registry(), bot_action, seeds=(0, 1, 7))
    assert report.ok, report.failures
    contract = contract_check(parse_design_ir(dual_zone_ir()), compiled.plan,
                              core_registry(), VERIFICATION_STRATEGIES, VERIFICATION_SEEDS)
    assert contract.ok, contract.failures()


def test_the_bot_fills_every_zone_input():
    compiled = compile_composed(parse_design_ir(dual_zone_ir()), core_registry())
    interpreter = Interpreter(compiled.plan, core_registry(), seed=0)
    interpreter.setup()
    action, payload = bot_action(policy_context(interpreter))
    seat = interpreter.state["current_player"]
    hand = [card.id for card in interpreter.state["zones"][f"hand-{seat}"]["cards"]]
    market = [card.id for card in interpreter.state["zones"]["market"]["cards"]]
    assert action == "exchange"
    assert payload == {"hand_card": [hand[0]], "market_card": [market[0]]}


def test_the_market_keeps_its_size_across_the_dual_zone_swap():
    compiled = compile_composed(parse_design_ir(dual_zone_ir()), core_registry())
    interpreter = Interpreter(compiled.plan, core_registry(), seed=5)
    interpreter.setup()
    before = len(interpreter.state["zones"]["market"]["cards"])
    run_bots(interpreter, human_index=-1)
    assert interpreter.state["finished"] is True
    assert len(interpreter.state["zones"]["market"]["cards"]) == before == 3
