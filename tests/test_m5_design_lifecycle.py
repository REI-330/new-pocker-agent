"""M5-1: the design lifecycle -- durable runs, verify, confirm, publish.

The design service still produces only a candidate; this file covers the host
lifecycle around it: every turn is recorded as a durable, bounded *run*, the
formal gate is exposed as its own host endpoint, a *user* confirms one exact
``ir_hash``, and registration happens only when that confirmation and a fresh
host credential both bind the current rules (ADR-0008).
"""
from __future__ import annotations

import copy

from fastapi.testclient import TestClient
from test_g2_m2 import scenario_a_ir
from test_m4_design_loop import ScriptedModel, _decision

from pocker_agent.agent.design_runs import DesignRunStore
from pocker_agent.app import create_app


def _client(tmp_path, decisions=None) -> TestClient:
    model = ScriptedModel(decisions or [])
    return TestClient(create_app(tmp_path / "m5.db", model_factory=lambda: model))


def _design(client: TestClient, game_id: str = "m5-life") -> dict:
    response = client.post("/api/designs", json={"game_id": game_id,
                                                 "description": "描述"})
    assert response.status_code == 200, response.text
    return response.json()


def _with_rules(client: TestClient, session_id: str) -> dict:
    updated = client.post(f"/api/designs/{session_id}/update",
                          json={"expected_revision": 0, "ir": scenario_a_ir()})
    assert updated.status_code == 200, updated.text
    return updated.json()


def _verified_and_confirmed(client: TestClient, session_id: str) -> str:
    """Verify the current rules, confirm the exact hash, return that hash."""
    verified = client.post(f"/api/designs/{session_id}/verify", json={})
    assert verified.status_code == 200, verified.text
    assert verified.json()["ok"] is True, verified.text
    ir_hash = verified.json()["session"]["ir_hash"]
    confirmed = client.post(f"/api/designs/{session_id}/confirm",
                            json={"ir_hash": ir_hash})
    assert confirmed.status_code == 200, confirmed.text
    return ir_hash


# --------------------------------------------------------------------- runs
def test_a_message_turn_records_a_durable_run(tmp_path):
    client = _client(tmp_path, [_decision("ask_user", question="每轮几张？")])
    session_id = _design(client)["session_id"]
    turn = client.post(f"/api/designs/{session_id}/messages",
                       json={"message": "设计", "expected_revision": 0})
    assert turn.status_code == 200, turn.text
    run_id = turn.json()["run_id"]

    listed = client.get(f"/api/designs/{session_id}/runs").json()["runs"]
    assert listed and listed[0]["run_id"] == run_id
    assert listed[0]["status"] == "completed" and listed[0]["kind"] == "question"

    run = client.get(f"/api/designs/{session_id}/runs/{run_id}").json()
    assert run["run_id"] == run_id and run["session_id"] == session_id
    assert run["observations"] and run["attempts"] == 1
    assert run["used"]["decisions"] == 1
    assert run["budget"]["max_decisions"] == 24


def test_a_conflicting_turn_closes_its_run_as_failed(tmp_path):
    holder: dict[str, str] = {}

    def model_factory():
        # A second writer bumps the session between the endpoint's revision
        # check and the loop's first commit, so the turn loses the race.
        app.state.design_store.commit(holder["sid"], 0, status="diagnosed")
        return ScriptedModel([_decision("ask_user", question="q")])

    app = create_app(tmp_path / "m5.db", model_factory=model_factory)
    client = TestClient(app)
    session_id = _design(client)["session_id"]
    holder["sid"] = session_id
    stale = client.post(f"/api/designs/{session_id}/messages",
                        json={"message": "设计", "expected_revision": 0})
    assert stale.status_code == 409, stale.text
    listed = client.get(f"/api/designs/{session_id}/runs").json()["runs"]
    assert listed and listed[0]["status"] == "failed"
    assert "stale_revision" in listed[0]["error"]


def test_an_unknown_or_mismatched_run_is_a_404(tmp_path):
    client = _client(tmp_path)
    first = _design(client, "run-a")["session_id"]
    second = _design(client, "run-b")["session_id"]
    assert client.get(f"/api/designs/{first}/runs/missing").status_code == 404
    assert client.get("/api/designs/missing/runs").status_code == 404

    model = ScriptedModel([_decision("ask_user", question="q")])
    other = TestClient(create_app(tmp_path / "m5.db", model_factory=lambda: model))
    turn = other.post(f"/api/designs/{second}/messages", json={"message": "设计"}).json()
    assert other.get(f"/api/designs/{first}/runs/{turn['run_id']}").status_code == 404


def test_runs_survive_a_restart(tmp_path):
    database = tmp_path / "m5.db"
    model = ScriptedModel([_decision("ask_user", question="q")])
    first = TestClient(create_app(database, model_factory=lambda: model))
    session_id = _design(first)["session_id"]
    run_id = first.post(f"/api/designs/{session_id}/messages",
                        json={"message": "设计"}).json()["run_id"]

    restarted = TestClient(create_app(database))
    run = restarted.get(f"/api/designs/{session_id}/runs/{run_id}")
    assert run.status_code == 200
    assert run.json()["kind"] == "question"


# ---------------------------------------------------------------- lifecycle
def test_verify_confirm_publish_registers_one_immutable_version(tmp_path):
    client = _client(tmp_path)
    session_id = _design(client)["session_id"]
    _with_rules(client, session_id)
    ir_hash = _verified_and_confirmed(client, session_id)

    published = client.post(f"/api/designs/{session_id}/publish", json={})
    assert published.status_code == 200, published.text
    body = published.json()
    artifact = body["artifact"]
    assert artifact["version"] == 1 and artifact["game_id"] == "m5-life"
    assert artifact["ir_hash"] == ir_hash
    assert artifact["approval_ir_hash"] == ir_hash
    assert artifact["title"] == "三轮公开比较积分"
    assert body["session"]["context"]["published"] == artifact

    versions = client.get("/api/games/m5-life/versions").json()["versions"]
    assert [item["version"] for item in versions] == [1]


def test_publish_is_idempotent_for_the_same_ir(tmp_path):
    client = _client(tmp_path)
    session_id = _design(client)["session_id"]
    _with_rules(client, session_id)
    _verified_and_confirmed(client, session_id)

    first = client.post(f"/api/designs/{session_id}/publish", json={}).json()
    retry = client.post(f"/api/designs/{session_id}/publish", json={}).json()
    assert first["idempotent"] is False
    assert retry["idempotent"] is True
    assert retry["artifact"] == first["artifact"]
    assert len(client.get("/api/games/m5-life/versions").json()["versions"]) == 1


def test_publish_requires_a_user_confirmation(tmp_path):
    client = _client(tmp_path)
    session_id = _design(client)["session_id"]
    _with_rules(client, session_id)
    client.post(f"/api/designs/{session_id}/verify", json={})
    rejected = client.post(f"/api/designs/{session_id}/publish", json={})
    assert rejected.status_code == 422
    assert "confirmation_required" in rejected.json()["detail"]


def test_publish_requires_a_bound_verification(tmp_path):
    client = _client(tmp_path)
    session_id = _design(client)["session_id"]
    _with_rules(client, session_id)
    ir_hash = client.get(f"/api/designs/{session_id}").json()["ir_hash"]
    client.post(f"/api/designs/{session_id}/confirm", json={"ir_hash": ir_hash})
    rejected = client.post(f"/api/designs/{session_id}/publish", json={})
    assert rejected.status_code == 422
    assert "verification_required" in rejected.json()["detail"]


def test_confirming_a_different_hash_is_refused(tmp_path):
    client = _client(tmp_path)
    session_id = _design(client)["session_id"]
    _with_rules(client, session_id)
    rejected = client.post(f"/api/designs/{session_id}/confirm",
                           json={"ir_hash": "deadbeefdeadbeef"})
    assert rejected.status_code == 422
    assert "approval_mismatch" in rejected.json()["detail"]
    assert client.get(f"/api/designs/{session_id}").json()["context"].get("confirmation") is None


def test_a_changed_ir_invalidates_the_confirmation(tmp_path):
    client = _client(tmp_path)
    session_id = _design(client)["session_id"]
    _with_rules(client, session_id)
    _verified_and_confirmed(client, session_id)

    current = client.get(f"/api/designs/{session_id}").json()
    changed = copy.deepcopy(current["ir"])
    changed["terminal"]["max_rounds"] = 2
    updated = client.post(f"/api/designs/{session_id}/update",
                          json={"expected_revision": current["revision"], "ir": changed})
    assert updated.status_code == 200, updated.text
    rejected = client.post(f"/api/designs/{session_id}/publish", json={})
    assert rejected.status_code == 422
    assert "confirmation_required" in rejected.json()["detail"]


# --------------------------------------------------------------- run store
def test_the_run_store_is_bounded_per_session(tmp_path):
    from pocker_agent.agent.design_runs import MAX_RUNS_PER_SESSION

    store = DesignRunStore(tmp_path / "runs.db")
    for _ in range(MAX_RUNS_PER_SESSION + 5):
        store.start("s1", "消息")
    assert len(store.list_runs("s1", limit=100)) == MAX_RUNS_PER_SESSION
    assert store.latest("s1").seq > MAX_RUNS_PER_SESSION
    # Another session's runs are untouched and invisible to the first.
    store.start("s2", "别的")
    assert all(run["session_id"] == "s1" for run in store.list_runs("s1"))


def test_the_run_store_keeps_only_the_newest_observations(tmp_path):
    from pocker_agent.agent.design_runs import MAX_RUN_OBSERVATIONS

    store = DesignRunStore(tmp_path / "runs.db")
    run = store.start("s1", "消息")
    finished = store.finish(run.run_id, status="completed", kind="finalized",
                            observations=[{"step": index} for index in range(200)])
    assert len(finished.observations) == MAX_RUN_OBSERVATIONS
    assert finished.observations[-1]["step"] == 199


def test_the_run_store_works_without_a_path():
    store = DesignRunStore()
    run = store.start("s1", "消息")
    assert store.get(run.run_id).status == "running"
    store.finish(run.run_id, status="completed", kind="question")
    assert store.get(run.run_id).kind == "question"
