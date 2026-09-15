"""M4-1: design-session persistence, revision locking and idempotency.

The store is the durable state of a design conversation, not a playable game:
a runnable version still comes only from ``publish_composed``. These tests pin
the optimistic-locking contract (stale writes refused, failed writes rolled back),
restart recovery, cross-session isolation and request-id idempotency.
"""
from __future__ import annotations

import threading

import pytest
from test_g2_m3_scenario_b import SCENARIO_B_SEEDS, scenario_b_ir

from pocker_agent.agent.design_store import DesignStore
from pocker_agent.core import SessionStore

CREATED = {"seq": 0, "event": "created", "revision": 0}


def _store(tmp_path) -> DesignStore:
    return DesignStore(tmp_path / "design.db")


def test_create_starts_a_draft_at_revision_zero(tmp_path):
    session = _store(tmp_path).create("my-game", "两人接牌游戏")
    assert session.revision == 0
    assert session.status == "draft"
    assert session.description == "两人接牌游戏"
    assert session.ir is None and session.diagnosis is None
    assert session.history == [CREATED]


def test_reads_return_deep_copies(tmp_path):
    store = _store(tmp_path)
    created = store.create("g", "d")
    created.ir = {"mutated": True}
    created.history.append({"seq": 99})
    stored = store.get(created.session_id)
    assert stored.ir is None
    assert stored.history == [CREATED]
    stored.ir = {"also": "mutated"}
    stored.history.append({"seq": 1})
    assert store.get(created.session_id).ir is None
    assert store.get(created.session_id).history == [CREATED]


def test_commit_normalizes_the_ir_and_bumps_the_revision(tmp_path):
    store = _store(tmp_path)
    session = store.create("g")
    updated = store.commit(session.session_id, 0, ir=scenario_b_ir(),
                           status="diagnosed", diagnosis={"ok": True})
    assert updated.revision == 1
    assert updated.status == "diagnosed"
    assert updated.ir["kind"] == "game_rules"
    assert updated.ir["execution"]["profile"] == "composed-1.0"
    assert updated.ir_hash == updated.ir_hash and updated.ir_hash
    assert updated.diagnosis == {"ok": True}
    assert [event["event"] for event in updated.history] == ["created", "updated"]


def test_stale_revision_is_refused_and_leaves_state_untouched(tmp_path):
    store = _store(tmp_path)
    session = store.create("g")
    store.commit(session.session_id, 0, ir=scenario_b_ir())
    with pytest.raises(ValueError, match="stale_revision"):
        store.commit(session.session_id, 0, status="failed")
    current = store.get(session.session_id)
    assert current.revision == 1
    assert current.status == "draft"


def test_invalid_ir_rolls_back_revision_and_ir(tmp_path):
    store = _store(tmp_path)
    session = store.create("g")
    good = store.commit(session.session_id, 0, ir=scenario_b_ir())
    with pytest.raises(ValueError, match="invalid_ir"):
        store.commit(session.session_id, 1, ir={"schema_version": "0.5", "kind": "composed"})
    current = store.get(session.session_id)
    assert current.revision == 1
    assert current.ir == good.ir                      # byte-identical, not partially patched


def test_unknown_status_and_change_are_refused(tmp_path):
    store = _store(tmp_path)
    session = store.create("g")
    with pytest.raises(ValueError, match="design_status_unknown"):
        store.commit(session.session_id, 0, status="ready")
    with pytest.raises(ValueError, match="design_change_unknown"):
        store.commit(session.session_id, 0, banana=1)
    assert store.get(session.session_id).revision == 0


def test_restart_restores_session_revision_ir_and_diagnosis(tmp_path):
    path = tmp_path / "design.db"
    store = DesignStore(path)
    session = store.create("g", "描述")
    store.commit(session.session_id, 0, ir=scenario_b_ir(),
                 diagnosis={"ok": True, "failures": []}, status="diagnosed")
    reopened = DesignStore(path)                       # a fresh process
    restored = reopened.get(session.session_id)
    assert restored.revision == 1
    assert restored.description == "描述"
    assert restored.ir["kind"] == "game_rules"
    assert restored.diagnosis == {"ok": True, "failures": []}
    assert restored.status == "diagnosed"
    assert restored.history[-1]["revision"] == 1


def test_sessions_do_not_pollute_each_other(tmp_path):
    store = _store(tmp_path)
    first = store.create("a")
    second = store.create("b")
    store.commit(first.session_id, 0, ir=scenario_b_ir(), status="diagnosed")
    assert store.get(first.session_id).revision == 1
    other = store.get(second.session_id)
    assert other.revision == 0 and other.ir is None and other.status == "draft"
    assert {item["session_id"] for item in store.list_sessions()} == {
        first.session_id, second.session_id}


def test_request_id_is_idempotent(tmp_path):
    store = _store(tmp_path)
    session = store.create("g")
    first = store.commit(session.session_id, 0, request_id="r1", ir=scenario_b_ir(),
                         status="diagnosed")
    replay = store.commit(session.session_id, 0, request_id="r1", ir=scenario_b_ir(),
                          status="diagnosed")
    assert first.revision == replay.revision == 1
    assert store.get(session.session_id).revision == 1     # applied once
    with pytest.raises(ValueError, match="request_id_conflict"):
        store.commit(session.session_id, 0, request_id="r1", status="failed")


def test_concurrent_stale_writes_are_refused(tmp_path):
    store = _store(tmp_path)
    session = store.create("g")
    barrier = threading.Barrier(8)
    outcomes: list[str] = []
    guard = threading.Lock()

    def worker() -> None:
        barrier.wait()
        try:
            store.commit(session.session_id, 0, ir=scenario_b_ir())
            outcome = "ok"
        except ValueError as error:
            outcome = str(error).split(":", 1)[0]
        with guard:
            outcomes.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("ok") == 1
    assert outcomes.count("stale_revision") == 7
    assert store.get(session.session_id).revision == 1


def test_an_in_memory_store_has_the_same_locking_contract():
    store = DesignStore()
    session = store.create("g")
    store.commit(session.session_id, 0, status="diagnosed")
    with pytest.raises(ValueError, match="stale_revision"):
        store.commit(session.session_id, 0, status="failed")
    assert store.get(session.session_id).revision == 1


def test_context_bounds_text_width_and_depth(tmp_path):
    from pocker_agent.agent.design_store import MAX_CONTEXT_ITEMS, MAX_CONTEXT_TEXT

    store = _store(tmp_path)
    session = store.create("g")
    deep = current = {}
    for _ in range(40):
        current["next"] = {}
        current = current["next"]
    updated = store.commit(session.session_id, 0, context={
        "custom": {"text": "x" * (MAX_CONTEXT_TEXT + 500),
                   "wide": {f"k{index}": index for index in range(MAX_CONTEXT_ITEMS + 50)},
                   "deep": deep}})
    custom = updated.context["custom"]
    assert len(custom["text"]) == MAX_CONTEXT_TEXT
    assert len(custom["wide"]) == MAX_CONTEXT_ITEMS
    assert "<truncated>" in str(custom["deep"])


def test_context_evidence_keys_keep_their_structure(tmp_path):
    store = _store(tmp_path)
    session = store.create("g")
    verification = {"ok": True, "contract": {"checks": ["x" * 100] * 200}}
    updated = store.commit(session.session_id, 0, context={"verification": verification})
    assert updated.context["verification"] == verification


def test_a_design_session_is_not_a_playable_game(tmp_path):
    """Saving a design never registers a runnable version (ADR-0008)."""
    designs = DesignStore(tmp_path / "design.db")
    sessions = SessionStore(tmp_path / "design.db")
    session = designs.create("not-registered", "一个还没验证的玩法")
    designs.commit(session.session_id, 0, ir=scenario_b_ir(), status="diagnosed")
    assert sessions.list_versions("not-registered") == []
    with pytest.raises(ValueError, match=r"unknown_game|game_not_playtested"):
        sessions.create("not-registered")

    # The only path is the host publish service, which produces the artifact.
    from pocker_agent.core import bind_rules_game_id, normalize_rules, rules_fingerprint

    rules = bind_rules_game_id(normalize_rules(scenario_b_ir()), "not-registered")
    artifact = sessions.verify_and_register_rules(
        rules, version=1, title="T", approval_rules_hash=rules_fingerprint(rules),
        seeds=SCENARIO_B_SEEDS,
    )
    assert artifact.generation_source == "game_rules_1_0"
    assert sessions.list_versions("not-registered")[0]["plan_hash"] == artifact.plan_hash
