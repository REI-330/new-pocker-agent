"""Plan identity: a registration must not rewrite history.

An agent game could previously take a built-in id (`arithmetic24`), silently
replace a reference plan, and appear twice in `/api/games`. A running session
could then resume its state against a different control-flow graph. These tests
pin the three rules that stop it.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from pocker_agent.app import create_app
from pocker_agent.core import SessionStore, core_registry, host_compile, parse_ir, playtest
from pocker_agent.core.plan import GamePlan, plan_fingerprint
from pocker_agent.core.playtest import first_legal
from pocker_agent.core.reference import REFERENCE_GAMES


def _passing_plan(game_id: str, max_rounds: int = 3):
    """A real, host-compiled, gated plan -- not a fixture with a faked report."""
    ir = parse_ir({"kind": "war", "game_id": game_id, "title": "测试用",
                   "max_rounds": max_rounds, "players": 2})
    plan = host_compile(ir)
    report = playtest(plan, core_registry(), first_legal, seeds=(0, 7, 23)).as_dict()
    assert report["ok"] is True, report
    return plan, report


class _ScriptedModel:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls = 0

    def complete(self, messages, **kwargs):
        response = self.responses[self.calls]
        self.calls += 1
        return response


def _decision(tool: str, **args) -> str:
    return json.dumps({"tool": tool, "args": args})


def _store(tmp_path) -> SessionStore:
    return SessionStore(tmp_path / "plans.db")


def test_a_reference_id_cannot_be_claimed_by_an_agent_plan(tmp_path):
    store = _store(tmp_path)
    plan, report = _passing_plan("arithmetic24")
    with pytest.raises(ValueError, match="game_id_reserved:arithmetic24"):
        store.register_plan("arithmetic24", plan.model_dump(mode="json"), report)
    assert "arithmetic24" in {game["id"] for game in store.list_games()}


def test_registered_games_never_duplicate_a_reference_id(tmp_path):
    """Even a database written before the rule existed must not shadow a built-in."""
    store = _store(tmp_path)
    plan, report = _passing_plan("war")
    # bypass the guard to simulate a legacy row
    store.plans["war"] = {"plan": plan.model_dump(mode="json"), "playtest": report,
                          "title": "偷来的 War", "fingerprint": plan_fingerprint(plan)}
    ids = [game["id"] for game in store.list_games()]
    assert len(ids) == len(set(ids)), ids
    assert next(g for g in store.list_games() if g["id"] == "war")["title"] == "War 比大小"


def test_re_registering_a_different_plan_for_the_same_id_is_refused(tmp_path):
    store = _store(tmp_path)
    plan, report = _passing_plan("agent-war")
    store.register_plan("agent-war", plan.model_dump(mode="json"), report)
    other, other_report = _passing_plan("agent-war-2", max_rounds=7)   # different plan
    with pytest.raises(ValueError, match="plan_already_registered:agent-war"):
        store.register_plan("agent-war", other.model_dump(mode="json"), other_report)


def test_registering_the_same_plan_again_is_idempotent(tmp_path):
    store = _store(tmp_path)
    plan, report = _passing_plan("agent-war")
    payload = plan.model_dump(mode="json")
    store.register_plan("agent-war", payload, report)
    store.register_plan("agent-war", payload, report)          # same fingerprint
    assert sum(1 for game in store.list_games() if game["id"] == "agent-war") == 1


def test_a_row_written_before_fingerprints_existed_is_compared_by_content(tmp_path):
    """Migration path: old rows have no fingerprint, so compare the plan itself."""
    store = _store(tmp_path)
    plan, report = _passing_plan("agent-legacy")
    payload = plan.model_dump(mode="json")
    store.plans["agent-legacy"] = {"plan": payload, "playtest": report,
                                   "title": "旧记录"}          # no fingerprint key
    store.register_plan("agent-legacy", payload, report)        # same plan: allowed
    assert store._stored_plans()["agent-legacy"]["fingerprint"] == plan_fingerprint(payload)

    other, other_report = _passing_plan("agent-legacy-2", max_rounds=7)
    with pytest.raises(ValueError, match="plan_already_registered:agent-legacy"):
        store.register_plan("agent-legacy", other.model_dump(mode="json"), other_report)


def test_a_plan_that_was_not_playtested_cannot_be_registered(tmp_path):
    store = _store(tmp_path)
    plan, _ = _passing_plan("agent-war")
    with pytest.raises(ValueError, match="plan_not_playtested"):
        store.register_plan("agent-war", plan.model_dump(mode="json"), {"ok": False})


def test_fingerprint_ignores_key_order_but_tracks_content():
    plan, _ = _passing_plan("agent-war")
    dumped = plan.model_dump(mode="json")
    reordered = json.loads(json.dumps(dumped, sort_keys=True))
    assert plan_fingerprint(dumped) == plan_fingerprint(reordered)
    changed = json.loads(json.dumps(dumped))
    changed["game_kind"] = "uno"
    assert plan_fingerprint(dumped) != plan_fingerprint(changed)
    assert plan_fingerprint(dumped) == plan_fingerprint(GamePlan.model_validate(dumped))


def test_a_session_refuses_to_resume_against_a_replaced_plan(tmp_path):
    """Restore must compare the plan it ran against the plan on disk now."""
    store = _store(tmp_path)
    plan, report = _passing_plan("agent-war")
    store.register_plan("agent-war", plan.model_dump(mode="json"), report)
    session = store.create("agent-war", seed=5)
    assert store.get(session.id).revision == 0

    # replace the stored plan behind the session's back
    replacement, _ = _passing_plan("agent-war", max_rounds=7)
    swapped = replacement.model_dump(mode="json")
    swapped["game_kind"] = "uno"
    stored = store._stored_plans()["agent-war"]
    stored["plan"] = swapped
    stored["fingerprint"] = plan_fingerprint(swapped)
    with store.lock:
        from pocker_agent.storage import connect
        with connect(store.path) as db:
            db.execute("INSERT OR REPLACE INTO core_agent_plans VALUES (?, ?)",
                       ("agent-war", json.dumps(stored)))

    with pytest.raises(ValueError, match="plan_changed"):
        store.get(session.id)


def test_a_reserved_id_is_reported_without_throwing_away_the_design(tmp_path):
    """The design is still worth showing; only registration is refused."""
    model = _ScriptedModel(
        _decision("propose_ir", ir={"kind": "war", "game_id": "war",
                                    "title": "假冒内置", "max_rounds": 3, "players": 2}),
        _decision("compose_plan"),
        _decision("playtest"),
        _decision("finalize"),
    )
    c = TestClient(create_app(tmp_path / "reserved.db", model_factory=lambda: model))
    response = c.post("/api/agent/loop", json={"message": "做个比大小"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["finalized"] is True                     # the agent did freeze a plan
    assert body["registered"] is False
    assert body["registration_error"].startswith("game_id_reserved")
    assert body["plan"] is not None                      # ...and the work is not lost
    title = next(g["title"] for g in c.get("/api/games").json()["games"] if g["id"] == "war")
    assert title == REFERENCE_GAMES["war"].title         # the built-in is untouched
