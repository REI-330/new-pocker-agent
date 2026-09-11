from fastapi.testclient import TestClient
from pocker_agent.api import create_app
from pocker_agent.llm import OpenAICompatibleClient


def fixture_payload():
    return {
        "game_id": "api-demo", "title": "API Demo",
        "deck": {"suits": ["S", "H"], "ranks": ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]},
        "players": {"min_players": 2, "max_players": 2, "starting_hand_size": 1},
        "actions": [{"name": "play"}], "phases": [{"name": "main", "actions": ["play"], "max_turns": 2}],
        "scoring": [{"condition": "highest_card", "points": 1}], "max_rounds": 3,
    }


def test_health(local_app):
    _, client, _ = local_app
    assert client.get("/health").json()["status"] == "ok"


def test_validate_and_simulate(local_app):
    _, client, _ = local_app
    assert client.post("/api/rules/validate", json=fixture_payload()).json()["valid"]
    result = client.post("/api/simulations?seed=3", json=fixture_payload())
    assert result.status_code == 200
    assert result.json()["completed"]
    assert len([e for e in result.json()["events"] if e["event"] == "round_finished"]) == 3


def test_saved_config_survives_restart_and_blank_key_updates_model(local_app):
    app, client, vault = local_app
    assert client.post("/api/agent/config", json={"base_url": "https://relay.test", "api_key": "secret-test", "model": "custom-v1"}).status_code == 200
    updated = client.post("/api/agent/config", json={"base_url": "https://relay.test/v1/", "api_key": "", "model": "custom-v2"})
    assert updated.json()["model"] == "custom-v2"
    restarted = TestClient(create_app(app.state.config_store.path, vault))
    saved = restarted.get("/api/agent/config")
    assert saved.json() == {"base_url":"https://relay.test/v1","model":"custom-v2","configured":True,"has_key":True}
    assert "secret-test" not in saved.text
    assert b"secret-test" not in app.state.config_store.path.read_bytes()
    assert app.state.config_store.read().api_key == "secret-test"


def test_discovery_and_test_never_mutate_saved_config(local_app, monkeypatch):
    _, client, _ = local_app
    first = {"base_url": "https://first.test", "api_key": "first-key", "model": "first-model"}
    second = {"base_url": "https://second.test", "api_key": "second-key", "model": "second-model"}
    client.post("/api/agent/config", json=first)
    monkeypatch.setattr(OpenAICompatibleClient, "list_models", lambda self: ["x", "y", "z"])
    monkeypatch.setattr(OpenAICompatibleClient, "complete", lambda self, *a, **kw: "OK")
    assert client.post("/api/agent/models", json=second).json()["models"] == ["x", "y", "z"]
    assert client.post("/api/agent/test-connection", json=second).json()["model"] == "second-model"
    assert client.get("/api/agent/config").json()["model"] == "first-model"
    def fail(self):
        raise RuntimeError("unavailable")
    monkeypatch.setattr(OpenAICompatibleClient, "list_models", fail)
    assert client.post("/api/agent/models", json=second).status_code == 502
    assert client.get("/api/agent/config").json()["base_url"] == "https://first.test/v1"


def test_changed_provider_requires_explicit_key_and_invalid_save_is_atomic(local_app):
    app, client, _ = local_app
    client.post("/api/agent/config", json={"base_url":"https://a.test","api_key":"original","model":"m"})
    for endpoint in ("models", "test-connection", "config"):
        assert client.post("/api/agent/" + endpoint, json={"base_url":"https://b.test","model":"m"}).status_code == 422
    assert client.post("/api/agent/config", json={"base_url":"not-a-url","api_key":"replacement","model":"m"}).status_code == 422
    assert app.state.config_store.read().api_key == "original"


def test_unconfigured_client_requires_key(local_app):
    _, client, _ = local_app
    assert client.post("/api/agent/config",json={"base_url":"https://a.test","model":"m"}).status_code == 422


def test_runtime_bots_rounds_scores_restart_and_revision_conflict(local_app):
    app, client, vault = local_app
    state = client.post("/api/runtime/sessions?seed=7", json=fixture_payload()).json()
    sid = state["session_id"]
    for turn in range(3):
        response = client.post(f"/api/runtime/sessions/{sid}/actions/play", json={"revision":state["revision"]})
        assert response.status_code == 200
        state = response.json()["state"]
        assert state["finished"] or (state["legal_actions"] and state["current_player"] == "player-1")
    assert state["finished"] and state["round"] == 3
    assert sum(p["score"] for p in state["players"]) >= 3
    restarted = TestClient(create_app(app.state.config_store.path, vault))
    assert restarted.get(f"/api/runtime/sessions/{sid}").json() == state
    assert restarted.post(f"/api/runtime/sessions/{sid}/actions/play", json={"revision":0}).status_code == 409


def test_unsupported_construct_is_rejected_before_runtime(local_app):
    _, client, _ = local_app
    rules = fixture_payload()
    rules["actions"] = [{"name":"bet","amount":1}]
    assert client.post("/api/runtime/sessions", json=rules).status_code == 422

def test_vault_write_failure_keeps_previous_configuration(local_app, monkeypatch):
    import keyring.errors
    app, client, vault = local_app
    client.post("/api/agent/config", json={"base_url":"https://a.test","api_key":"first","model":"first"})
    def fail(*args):
        raise keyring.errors.KeyringError("locked")
    monkeypatch.setattr(vault, "set_password", fail)
    response = client.post("/api/agent/config", json={"base_url":"https://a.test","api_key":"second","model":"second"})
    assert response.status_code == 502
    assert app.state.config_store.read().model == "first"
    assert app.state.config_store.read().api_key == "first"


def test_empty_model_is_not_silently_replaced_by_previous_model(local_app):
    _, client, _ = local_app
    client.post("/api/agent/config", json={"base_url":"https://a.test","api_key":"first","model":"first"})
    response = client.post("/api/agent/config", json={"model":""})
    assert response.status_code == 422
    assert client.get("/api/agent/config").json()["model"] == "first"
