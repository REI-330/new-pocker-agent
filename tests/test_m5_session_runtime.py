"""M5-2: the generic session runtime -- version pinning, actions and events.

The composed-plan workspace must not special-case an action name. These tests
drive the descriptor-based endpoint (``POST /api/sessions/{id}/actions``), the
bounded incremental event read, and the ADR-0007 adapter that keeps the legacy
per-action route on the very same service.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_g2_m2 import scenario_a_ir
from test_m4_design_loop import ScriptedModel, _decision

from pocker_agent.agent.design_runs import DesignRunStore
from pocker_agent.app import create_app
from pocker_agent.arithmetic import solve


@pytest.fixture(scope="module")
def game(tmp_path_factory):
    """Publish scenario A once, then drive it through the generic runtime."""
    database = tmp_path_factory.mktemp("m5-runtime") / "m5.db"
    client = TestClient(create_app(database))
    created = client.post("/api/designs", json={"game_id": "m5-runtime"}).json()
    session_id = created["session_id"]
    client.post(f"/api/designs/{session_id}/update",
                json={"expected_revision": 0, "ir": scenario_a_ir()})
    verified = client.post(f"/api/designs/{session_id}/verify", json={})
    assert verified.status_code == 200 and verified.json()["ok"], verified.text
    ir_hash = verified.json()["session"]["ir_hash"]
    client.post(f"/api/designs/{session_id}/confirm", json={"ir_hash": ir_hash})
    published = client.post(f"/api/designs/{session_id}/publish", json={})
    assert published.status_code == 200, published.text
    return client


def _new_game(client: TestClient, *, seed: int = 7, version: int | None = None) -> dict:
    payload: dict = {"game_id": "m5-runtime", "seed": seed}
    if version is not None:
        payload["version"] = version
    response = client.post("/api/sessions", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _play_input(state: dict) -> tuple[str, str]:
    play = next(item for item in state["actions"] if item["id"] == "play")
    card_input = play["inputs"][0]
    assert card_input["id"] == "card" and card_input["options"]
    return play["id"], card_input["options"][0]


# ----------------------------------------------------------- version pinning
def test_a_session_can_pin_an_explicit_version(game):
    pinned = _new_game(game, version=1)
    assert pinned["version"] == 1 and pinned["game_id"] == "m5-runtime"
    missing = game.post("/api/sessions", json={"game_id": "m5-runtime", "version": 99})
    assert missing.status_code == 404
    assert missing.json()["detail"] == "游戏版本不存在"


def test_unknown_request_fields_are_rejected(game):
    response = game.post("/api/sessions",
                         json={"game_id": "m5-runtime", "bogus": True})
    assert response.status_code == 422


# ------------------------------------------------------------- generic action
def test_a_descriptor_driven_action_advances_the_game(game):
    state = _new_game(game)
    session_id = state["session_id"]
    action_id, card = _play_input(state)
    response = game.post(f"/api/sessions/{session_id}/actions",
                         json={"action_id": action_id, "input_values": {"card": [card]},
                               "revision": state["revision"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action_id"] == action_id
    assert body["state"]["revision"] == 1
    assert game.get(f"/api/sessions/{session_id}").json()["revision"] == 1


def test_an_unavailable_card_is_rejected_without_advancing(game):
    state = _new_game(game)
    session_id = state["session_id"]
    response = game.post(f"/api/sessions/{session_id}/actions",
                         json={"action_id": "play", "input_values": {"card": ["ZZ"]},
                               "revision": 0})
    assert response.status_code == 422
    assert "card_not_available" in response.json()["detail"]
    assert game.get(f"/api/sessions/{session_id}").json()["revision"] == 0


def test_missing_and_unknown_inputs_are_rejected(game):
    state = _new_game(game)
    session_id = state["session_id"]
    missing = game.post(f"/api/sessions/{session_id}/actions",
                        json={"action_id": "play", "input_values": {}, "revision": 0})
    assert missing.status_code == 422
    assert "missing_action_input" in missing.json()["detail"]
    unknown = game.post(f"/api/sessions/{session_id}/actions",
                        json={"action_id": "play",
                              "input_values": {"nope": ["x"]}, "revision": 0})
    assert unknown.status_code == 422
    assert "unknown_action_input" in unknown.json()["detail"]
    illegal = game.post(f"/api/sessions/{session_id}/actions",
                        json={"action_id": "nope", "input_values": {}, "revision": 0})
    assert illegal.status_code == 422
    assert "illegal_action" in illegal.json()["detail"]


def test_a_stale_revision_is_a_409(game):
    state = _new_game(game)
    session_id = state["session_id"]
    action_id, card = _play_input(state)
    body = {"action_id": action_id, "input_values": {"card": [card]}, "revision": 0}
    assert game.post(f"/api/sessions/{session_id}/actions", json=body).status_code == 200
    stale = game.post(f"/api/sessions/{session_id}/actions", json=body)
    assert stale.status_code == 409
    assert stale.json()["detail"].startswith("stale_revision")


def test_a_repeated_generic_request_id_is_idempotent(game):
    state = _new_game(game)
    session_id = state["session_id"]
    action_id, card = _play_input(state)
    body = {"action_id": action_id, "input_values": {"card": [card]}, "revision": 0,
            "request_id": "generic-1"}
    first = game.post(f"/api/sessions/{session_id}/actions", json=body)
    second = game.post(f"/api/sessions/{session_id}/actions", json=body)
    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert game.get(f"/api/sessions/{session_id}").json()["revision"] == 1


def test_reusing_a_generic_request_id_with_different_input_is_a_conflict(game):
    state = _new_game(game)
    session_id = state["session_id"]
    action_id, card = _play_input(state)
    other = next(c for c in state["actions"][0]["inputs"][0]["options"] if c != card)
    assert game.post(f"/api/sessions/{session_id}/actions",
                     json={"action_id": action_id, "input_values": {"card": [card]},
                           "revision": 0, "request_id": "same"}).status_code == 200
    conflict = game.post(f"/api/sessions/{session_id}/actions",
                         json={"action_id": action_id, "input_values": {"card": [other]},
                               "revision": 0, "request_id": "same"})
    assert conflict.status_code == 409
    assert conflict.json()["detail"].startswith("request_id_conflict")


# --------------------------------------------------------------------- events
def test_events_are_incremental_bounded_and_safe(game):
    state = _new_game(game)
    session_id = state["session_id"]
    first = game.get(f"/api/sessions/{session_id}/events",
                     params={"after": 0, "limit": 2}).json()
    assert first["cursor"] == len(first["events"]) <= 2
    assert first["total"] >= first["cursor"]
    for event in first["events"]:
        assert "seed" not in event and "state" not in event

    second = game.get(f"/api/sessions/{session_id}/events",
                      params={"after": first["cursor"], "limit": 2}).json()
    assert second["cursor"] >= first["cursor"]
    assert second["total"] == first["total"]
    assert len(second["events"]) <= 2

    tail = game.get(f"/api/sessions/{session_id}/events",
                    params={"after": 10 ** 6}).json()
    assert tail["events"] == [] and tail["has_more"] is False
    assert game.get(f"/api/sessions/{session_id}/events",
                    params={"after": -1}).status_code == 422


# ------------------------------------------------------- ADR-0007 adapter
def test_legacy_and_generic_action_paths_agree(tmp_path):
    client = TestClient(create_app(tmp_path / "legacy.db"))
    state = client.post("/api/sessions", json={"game_id": "arithmetic24",
                                               "seed": 7}).json()
    answer = solve(tuple(state["numbers"]))
    legacy = client.post(f"/api/sessions/{state['session_id']}/actions/submit_expression",
                         json={"revision": 0, "expression": answer})
    assert legacy.status_code == 200, legacy.text

    other = client.post("/api/sessions", json={"game_id": "arithmetic24",
                                               "seed": 7}).json()
    generic = client.post(f"/api/sessions/{other['session_id']}/actions",
                          json={"action_id": "submit_expression",
                                "input_values": {"expression": answer}, "revision": 0})
    assert generic.status_code == 200, generic.text
    legacy_state = {key: value for key, value in legacy.json()["state"].items()
                    if key != "session_id"}
    generic_state = {key: value for key, value in generic.json()["state"].items()
                     if key != "session_id"}
    assert generic_state == legacy_state


# --------------------------------------------------------- design-turn replay
def test_a_retried_design_turn_replays_its_run(tmp_path):
    model = ScriptedModel([_decision("ask_user", question="q")])
    client = TestClient(create_app(tmp_path / "retry.db", model_factory=lambda: model))
    created = client.post("/api/designs", json={"game_id": "m5-retry"}).json()
    session_id = created["session_id"]
    body = {"message": "设计", "expected_revision": 0, "request_id": "turn-1"}
    first = client.post(f"/api/designs/{session_id}/messages", json=body)
    assert first.status_code == 200, first.text
    # The first turn advanced the revision; the retry must still replay, not 409.
    second = client.post(f"/api/designs/{session_id}/messages", json=body)
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert model.calls == 1


# ------------------------------------------------------------- run recovery
def test_a_stale_running_run_is_reaped(tmp_path):
    store = DesignRunStore(tmp_path / "runs.db")
    run = store.start("s1", "消息")
    assert store.get(run.run_id).status == "running"
    reaped = store.recover_stale(max_age_seconds=0, now=run.started_at + 1)
    assert reaped == 1
    recovered = store.get(run.run_id)
    assert recovered.status == "failed" and "host_restarted" in recovered.error
