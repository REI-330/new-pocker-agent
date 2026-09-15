"""G2 / M3 verification acceptance: host credentials and immutable artifacts.

M2 registration trusted a caller-supplied ``{ok: true}`` report. These tests pin
the ADR-0008 rules that make evidence a first-class object:

* the publish service binds a plan to ``ir_hash`` / ``plan_hash`` /
  ``compiler_version`` / ``registry_contract_hash`` and the host's own strategies
  and seeds;
* ``verification_id`` is a deterministic content identity (never a timestamp);
* registration refuses a credential that was never recorded, is stale, or failed;
* a session restores by the immutable version it ran, not by game id alone;
* the formal strategies (legal-random, boundary, target-branch) all supply
  composed payloads instead of failing on a missing one.
"""
from __future__ import annotations

import pytest
from test_g2_m2 import scenario_a_ir

from pocker_agent.core import (
    Interpreter,
    SessionStore,
    ToolError,
    bind_rules_game_id,
    core_registry,
    goal_first,
    normalize_rules,
    publish_rules,
    random_legal,
    rules_fingerprint,
)
from pocker_agent.core.artifacts import build_artifact, verification_id
from pocker_agent.core.decision import policy_context
from pocker_agent.core.invariants import non_negative_scores, zone_conservation
from pocker_agent.core.ir import parse_design_ir
from pocker_agent.core.plan import plan_fingerprint
from pocker_agent.core.playtest import boundary_first
from pocker_agent.core.policy import bot_action
from pocker_agent.core.rules import compile_composed
from pocker_agent.core.verify import VERIFICATION_SEEDS

# A different rule set that still verifies: two rounds instead of three.
ALT_IR = scenario_a_ir(terminal={"max_rounds": 2, "winner": "highest_score", "tie": "allow"})


def _rules(ir, game_id):
    return bind_rules_game_id(normalize_rules(ir), game_id)


def _publish(ir, *, game_id, version, title, approval_ir_hash=None):
    rules = _rules(ir, game_id)
    approved = approval_ir_hash or rules_fingerprint(rules)
    return publish_rules(
        rules, {"rules_hash": approved, "version": version, "title": title}
    )


def _register(store, ir, *, game_id, version, title):
    rules = _rules(ir, game_id)
    return store.verify_and_register_rules(
        rules, version=version, title=title,
        approval_rules_hash=rules_fingerprint(rules),
    )


# --------------------------------------------------------------- publish service
def test_publish_composed_binds_ir_plan_and_compiler():
    compiled, result, artifact = _publish(
        scenario_a_ir(), game_id="duel", version=1, title="公开对局")
    assert result.ok
    assert artifact.generation_source == "game_rules_1_0"
    assert artifact.plan_hash == compiled.plan_hash == plan_fingerprint(compiled.plan)
    assert artifact.ir_hash == compiled.ir_hash
    assert artifact.compiler_version == compiled.compiler_version
    assert artifact.registry_contract_hash == core_registry().contract_hash()
    assert artifact.source_map["ir_hash"] == compiled.ir_hash
    assert result.strategies == ("random_legal", "boundary_first", "goal_first")
    assert result.seeds == VERIFICATION_SEEDS


def test_publish_rejects_a_confirmation_for_a_different_ir():
    with pytest.raises(ValueError, match="approval_mismatch"):
        _publish(scenario_a_ir(), game_id="duel", version=1, title="对局",
                         approval_ir_hash="0" * 16)


def test_verification_id_is_content_bound_not_time_bound():
    first = verification_id("p", "i", "c", "r", ("s",), (1,))
    assert first == verification_id("p", "i", "c", "r", ("s",), (1,))
    assert first != verification_id("p", "i", "c", "r", ("s",), (2,))
    assert first != verification_id("p", "i", "c", "r2", ("s",), (1,))


# ----------------------------------------------------------- registration gate
def test_registration_requires_a_host_recorded_credential(tmp_path):
    _, _, artifact = _publish(scenario_a_ir(), game_id="duel", version=1, title="对局")
    store = SessionStore(tmp_path / "verify.db")
    with pytest.raises(ValueError, match="verification_unknown"):
        store.register_artifact(artifact)


def test_a_recorded_plan_only_credential_cannot_bypass_game_rules(tmp_path):
    from pocker_agent.core.verify import verify_plan

    compiled = compile_composed(parse_design_ir(scenario_a_ir()), core_registry())
    result = verify_plan(compiled.plan, core_registry())
    artifact = build_artifact(
        game_id="duel", version=1, title="对局", plan=compiled.plan,
        verification=result, generation_source="composed_rules",
    )
    store = SessionStore(tmp_path / "verify.db")
    store.record_verification(result)
    with pytest.raises(ValueError, match="artifact_game_rules_required"):
        store.register_artifact(artifact)


def test_a_stale_credential_is_refused():
    _, result, _ = _publish(scenario_a_ir(), game_id="duel", version=1, title="对局")
    other = compile_composed(parse_design_ir(ALT_IR), core_registry())
    with pytest.raises(ValueError, match="verification_stale"):
        build_artifact(game_id="duel", version=2, title="对局", plan=other.plan,
                       verification=result, generation_source="composed_rules")


def test_an_artifact_cannot_claim_a_plan_the_credential_did_not_cover():
    _, result, artifact = _publish(scenario_a_ir(), game_id="duel", version=1, title="对局")
    tampered = {**artifact.plan, "players": 3}
    with pytest.raises(ValueError, match="verification_stale"):
        build_artifact(game_id="duel", version=1, title="对局", plan=tampered,
                       verification=result, generation_source="composed_rules")


# ------------------------------------------------------------- version binding
def test_verify_and_register_makes_a_versioned_game_playable(tmp_path):
    store = SessionStore(tmp_path / "verify.db")
    artifact = _register(store, scenario_a_ir(), game_id="duel", version=1, title="对局")
    game = next(item for item in store.list_games() if item["id"] == "duel")
    assert game["version"] == 1 and game["verification_id"] == artifact.verification_id
    session = store.create("duel", seed=3, version=1)
    assert session.version == 1
    restored = store.get(session.id)
    assert restored.version == 1
    assert restored.interpreter.plan.game_kind == "composed"


def test_a_session_restores_by_the_version_it_ran(tmp_path):
    store = SessionStore(tmp_path / "verify.db")
    _register(store, scenario_a_ir(), game_id="duel", version=1, title="v1")
    session = store.create("duel", seed=3, version=1)
    _register(store, ALT_IR, game_id="duel", version=2, title="v2")
    # the running session still resolves the version it started on
    assert store.get(session.id).version == 1
    # a fresh session without a version takes the latest
    assert store.create("duel", seed=3).version == 2


def test_a_version_cannot_be_overwritten_with_new_content(tmp_path):
    store = SessionStore(tmp_path / "verify.db")
    _register(store, scenario_a_ir(), game_id="duel", version=1, title="v1")
    with pytest.raises(ValueError, match="artifact_version_conflict"):
        _register(store, ALT_IR, game_id="duel", version=1, title="v1-new")


def test_registering_the_same_version_and_content_is_idempotent(tmp_path):
    store = SessionStore(tmp_path / "verify.db")
    first = _register(store, scenario_a_ir(), game_id="duel", version=1, title="v1")
    second = _register(store, scenario_a_ir(), game_id="duel", version=1, title="v1")
    assert first.plan_hash == second.plan_hash
    assert len(store.list_versions("duel")) == 1


def test_a_version_cannot_silently_change_metadata_with_the_same_plan(tmp_path):
    store = SessionStore(tmp_path / "verify.db")
    _register(store, scenario_a_ir(), game_id="duel", version=1, title="v1")
    with pytest.raises(ValueError, match="artifact_version_conflict"):
        _register(store, scenario_a_ir(), game_id="duel", version=1, title="renamed")


def test_a_missing_version_is_an_explicit_error(tmp_path):
    store = SessionStore(tmp_path / "verify.db")
    _register(store, scenario_a_ir(), game_id="duel", version=1, title="v1")
    with pytest.raises(ValueError, match="version_not_found"):
        store.create("duel", seed=1, version=99)


# --------------------------------------------------------------- policy coverage
def test_the_formal_gate_strategies_supply_composed_payloads():
    compiled = compile_composed(parse_design_ir(scenario_a_ir()), core_registry())
    for strategy in (random_legal, boundary_first, goal_first):
        interpreter = Interpreter(compiled.plan, core_registry(), seed=2)
        interpreter.setup()
        action, payload = strategy(policy_context(interpreter))
        assert action == "play", strategy.__name__
        assert payload["card"], f"{strategy.__name__} produced no payload"


def test_the_goal_policy_explores_a_different_branch_than_the_bot():
    compiled = compile_composed(parse_design_ir(scenario_a_ir()), core_registry())
    interpreter = Interpreter(compiled.plan, core_registry(), seed=0)
    interpreter.setup()
    seat = interpreter.state["current_player"]
    hand = [card.id for card in interpreter.state["zones"][f"hand-{seat}"]["cards"]]
    _, bot_payload = bot_action(policy_context(interpreter))
    _, goal_payload = goal_first(policy_context(interpreter))
    assert bot_payload["card"] == [hand[0]]
    assert goal_payload["card"] == [hand[-1]]


# ------------------------------------------------------------ typed invariants
def test_typed_invariants_catch_lost_cards_and_negative_scores():
    compiled = compile_composed(parse_design_ir(scenario_a_ir()), core_registry())
    interpreter = Interpreter(compiled.plan, core_registry(), seed=0)
    interpreter.setup()
    conservation = zone_conservation()
    conservation(interpreter)                       # learns 16
    interpreter.state["zones"]["stock"]["cards"].pop()
    with pytest.raises(ToolError, match="zone_conservation"):
        conservation(interpreter)

    scores = non_negative_scores()
    interpreter.state["scores"][0] = -1
    with pytest.raises(ToolError, match="score_out_of_bounds"):
        scores(interpreter)


# ------------------------------------------------------------- atomic commit
def test_act_rolls_back_when_a_bot_step_fails(tmp_path, monkeypatch):
    store = SessionStore()                          # the in-memory path too
    _register(store, scenario_a_ir(), game_id="duel", version=1, title="对局")
    session = store.create("duel", seed=3)
    interpreter = session.interpreter
    seat = interpreter.state["current_player"]
    hand = interpreter.state["zones"][f"hand-{seat}"]["cards"]
    before = interpreter.serialize()

    from pocker_agent.core import session as session_module

    def boom(*args, **kwargs):
        raise ToolError("bot_exploded")

    monkeypatch.setattr(session_module, "run_bots", boom)
    with pytest.raises(ToolError, match="bot_exploded"):
        store.act(session.id, "play", 0, card=[hand[0].id])
    rolled_back = store.get(session.id)
    assert rolled_back.revision == 0
    assert rolled_back.interpreter.serialize() == before


def test_verify_plan_reports_an_uncovered_wait_instead_of_raising():
    """The gate returns a credential with ``ok=False``; it does not abort."""
    from pocker_agent.core.plan import GamePlan
    from pocker_agent.core.verify import verify_plan

    plan = GamePlan(game_kind="mini", players=1, tools=[{"name": "state"}],
                    initial={"finished": False, "winners": [], "scores": [0]}, entry="wait_a", nodes={
                        "wait_a": {"kind": "wait", "inputs": {"go": "end_ok"}},
                        "end_ok": {"kind": "call", "next": "end", "action": {
                            "tool": "state", "operation": "update",
                            "args": {"state": "$state", "values": {"finished": True}}}},
                        "wait_b": {"kind": "wait", "inputs": {"never": "end_ok"}},
                        "end": {"kind": "end"}})
    result = verify_plan(plan, core_registry())
    assert result.ok is False
    assert any("wait_nodes_uncovered:wait_b" in failure for failure in result.failures)


# ------------------------------------------------------------ idempotent commits
def test_a_repeated_request_id_returns_the_original_response(tmp_path):
    from pocker_agent.core.actions import descriptor_for, payload_for

    store = SessionStore(tmp_path / "dedup.db")
    _register(store, scenario_a_ir(), game_id="duel", version=1, title="对局")
    session = store.create("duel", seed=3)
    descriptor = descriptor_for(session.plan, "play")
    payload = payload_for(session.interpreter.state, descriptor)
    first = store.act(session.id, "play", 0, request_id="retry-1", **payload)
    # the same key at the now-stale revision must replay, not re-apply
    second = store.act(session.id, "play", 0, request_id="retry-1", **payload)
    assert second == first
    assert store.get(session.id).revision == 1
    # a *different* key at the stale revision is still a conflict
    with pytest.raises(ValueError, match="stale_revision"):
        store.act(session.id, "play", 0, request_id="retry-2", **payload)


def test_processed_request_ids_survive_a_reload(tmp_path):
    from pocker_agent.core.actions import descriptor_for, payload_for

    path = tmp_path / "dedup.db"
    store = SessionStore(path)
    _register(store, scenario_a_ir(), game_id="duel", version=1, title="对局")
    session = store.create("duel", seed=3)
    descriptor = descriptor_for(session.plan, "play")
    payload = payload_for(session.interpreter.state, descriptor)
    first = store.act(session.id, "play", 0, request_id="retry-1", **payload)

    reopened = SessionStore(path)
    replay = reopened.act(session.id, "play", 0, request_id="retry-1", **payload)
    assert replay == first
    assert reopened.get(session.id).revision == 1


def test_a_request_id_with_a_different_body_conflicts(tmp_path):
    from pocker_agent.core.actions import descriptor_for, payload_for

    store = SessionStore(tmp_path / "dedup.db")
    _register(store, scenario_a_ir(), game_id="duel", version=1, title="对局")
    session = store.create("duel", seed=3)
    descriptor = descriptor_for(session.plan, "play")
    payload = payload_for(session.interpreter.state, descriptor)
    store.act(session.id, "play", 0, request_id="key", **payload)
    with pytest.raises(ValueError, match="request_id_conflict"):
        store.act(session.id, "play", 0, request_id="key", card=["9H"])


# ------------------------------------------------- immutability of the artifact
def test_an_artifact_cannot_be_edited_through_its_output(tmp_path):
    store = SessionStore(tmp_path / "artifact.db")
    artifact = _register(store, scenario_a_ir(), game_id="duel", version=1, title="对局")
    exposed = artifact.as_dict()
    exposed["plan"]["players"] = 99
    exposed["title"] = "tampered"
    exposed["ir"]["meta"]["title"] = "tampered"
    stored = store.list_versions("duel")[0]
    assert stored["plan"]["players"] != 99
    assert stored["title"] == "对局"
    assert stored["ir"]["meta"]["title"] != "tampered"
    # ...and mutating the dataclass's own plan dict must not reach the store
    artifact.plan["players"] = 99
    assert store.list_versions("duel")[0]["plan"]["players"] != 99


# ---------------------------------------------- host-verified agent registration
def test_the_raw_agent_plan_registration_path_is_closed(tmp_path):
    from pocker_agent.core.plans import war_plan

    store = SessionStore(tmp_path / "agent.db")
    plan = war_plan(max_rounds=3).model_dump(mode="json")
    with pytest.raises(ValueError, match="raw_plan_registration_not_supported"):
        store.verify_and_register_plan("agent-war", plan, "Agent War")
    assert not store.list_versions("agent-war")


def test_the_agent_registration_path_refuses_a_reserved_id(tmp_path):
    from pocker_agent.core.plans import war_plan

    store = SessionStore(tmp_path / "agent.db")
    with pytest.raises(ValueError, match="raw_plan_registration_not_supported"):
        store.verify_and_register_plan("war", war_plan(max_rounds=3).model_dump(mode="json"))


def test_the_agent_registration_path_rejects_an_unplayable_plan(tmp_path):
    store = SessionStore(tmp_path / "agent.db")
    broken = {"schema_version": "0.4", "game_kind": "war", "players": 2,
              "tools": [{"name": "deck"}], "initial": {}, "entry": "end",
              "nodes": {"end": {"kind": "end"}}}
    with pytest.raises(ValueError, match="raw_plan_registration_not_supported"):
        store.verify_and_register_plan("agent-broken", broken)
