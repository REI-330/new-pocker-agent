"""End-to-end: real HTTP through the v0.4 app, real playtest gate, real sessions."""
from __future__ import annotations

from fastapi.testclient import TestClient

from pocker_agent.app import create_app
from pocker_agent.arithmetic import solve


def client(tmp_path) -> TestClient:
    return TestClient(create_app(tmp_path / "e2e.db"))


def action_path(session_id: str, action: str) -> str:
    return f"/api/sessions/{session_id}/actions/{action}"


def test_capabilities_and_games_report_the_playtest_gate(tmp_path):
    c = client(tmp_path)
    capabilities = c.get("/api/capabilities").json()
    assert capabilities["matrix"]["axes"]
    assert capabilities["coverage"]["total"] >= 20
    games = c.get("/api/games").json()
    arithmetic = next(game for game in games["games"] if game["id"] == "arithmetic24")
    assert arithmetic["playtest"]["ok"] is True
    assert arithmetic["playtest"]["covered_wait_nodes"] == ["wait"]


def test_full_arithmetic_game_via_http(tmp_path):
    c = client(tmp_path)
    state = c.post("/api/sessions", json={"game_id": "arithmetic24", "seed": 7}).json()
    session_id = state["session_id"]
    assert state["revision"] == 0
    assert state["target"] == 24
    assert state["legal_actions"] == ["submit_expression", "no_solution", "give_up"]
    assert state["players"][0]["score"] == 0

    rounds = 0
    for _ in range(32):
        if state["finished"]:
            break
        answer = solve(tuple(state["numbers"]))
        if answer is None:
            response = c.post(action_path(session_id, "no_solution"),
                              json={"revision": state["revision"]})
        else:
            response = c.post(action_path(session_id, "submit_expression"),
                              json={"revision": state["revision"], "expression": answer})
        assert response.status_code == 200, response.text
        state = response.json()["state"]
        rounds += 1

    assert state["finished"] is True
    assert rounds == 3
    assert state["players"][0]["score"] == 3
    assert state["winners"] == []          # a drill has a score, not a winner
    assert state["revision"] == 3


def test_wrong_answer_is_rejected_without_advancing(tmp_path):
    c = client(tmp_path)
    state = c.post("/api/sessions", json={"game_id": "arithmetic24", "seed": 9}).json()
    session_id = state["session_id"]
    response = c.post(action_path(session_id, "submit_expression"),
                      json={"revision": 0, "expression": "24"})
    assert response.status_code == 422
    after = c.get(f"/api/sessions/{session_id}").json()
    assert after["revision"] == 0 and after["players"][0]["score"] == 0


def test_stale_revision_is_a_conflict(tmp_path):
    c = client(tmp_path)
    state = c.post("/api/sessions", json={"game_id": "arithmetic24", "seed": 7}).json()
    session_id = state["session_id"]
    answer = solve(tuple(state["numbers"]))
    assert c.post(action_path(session_id, "submit_expression"),
                  json={"revision": 0, "expression": answer}).status_code == 200
    stale = c.post(action_path(session_id, "give_up"), json={"revision": 0})
    assert stale.status_code == 409
    assert stale.json()["detail"].startswith("stale_revision")


def test_session_survives_an_app_restart(tmp_path):
    database = tmp_path / "persist.db"
    first = TestClient(create_app(database))
    state = first.post("/api/sessions", json={"game_id": "arithmetic24", "seed": 11}).json()
    session_id = state["session_id"]
    answer = solve(tuple(state["numbers"]))
    after = first.post(action_path(session_id, "submit_expression"),
                       json={"revision": 0, "expression": answer}).json()["state"]

    second = TestClient(create_app(database))
    assert second.get(f"/api/sessions/{session_id}").json() == after


def test_unknown_game_and_missing_session_are_reported(tmp_path):
    c = client(tmp_path)
    unknown = c.post("/api/sessions", json={"game_id": "not-a-game"})
    assert unknown.status_code == 422
    assert "unknown_game" in unknown.json()["detail"]
    assert c.get("/api/sessions/deadbeef").status_code == 404


def test_playtest_gate_blocks_a_failing_game(tmp_path, monkeypatch):
    """A game whose gate fails must not become playable."""
    import pocker_agent.core.session as session_module
    from pocker_agent.core.playtest import PlaytestReport

    failing = PlaytestReport(ok=False, seeds=(0,), failures=["nondeterministic_replay"])
    monkeypatch.setattr(session_module, "ensure_playtested", lambda game_id: failing)
    c = client(tmp_path)
    blocked = c.post("/api/sessions", json={"game_id": "arithmetic24", "seed": 7})
    assert blocked.status_code == 422
    assert "game_not_playtested" in blocked.json()["detail"]


# ------------------------------------------------------- agent loop over HTTP

class _ScriptedModel:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls = 0

    def complete(self, messages, **kwargs):
        response = self.responses[self.calls]
        self.calls += 1
        return response


def _decision(tool: str, **args) -> str:
    import json
    return json.dumps({"tool": tool, "args": args})


def test_agent_composes_a_game_and_it_becomes_playable_over_http(tmp_path):
    model = _ScriptedModel(
        _decision("propose_ir", ir={"kind": "war", "game_id": "agent-war",
                                     "title": "Agent 比大小", "max_rounds": 3, "players": 2}),
        _decision("capability_check"),
        _decision("compose_plan"),
        _decision("playtest"),
        _decision("finalize"),
    )
    c = TestClient(create_app(tmp_path / "agent.db", model_factory=lambda: model))

    tools = c.get("/api/agent/tools").json()
    assert any(tool["name"] == "finalize" for tool in tools["meta_tools"])

    result = c.post("/api/agent/loop", json={"goal": "做一个两人各抽一张比大小的游戏"}).json()
    assert result["kind"] == "proposal" and result["finalized"] is True, result
    assert result["playtest"]["ok"] is True

    game_id = result["ir"]["game_id"]
    games = c.get("/api/games").json()["games"]
    assert any(game["id"] == game_id and game.get("source") == "agent_compose" for game in games)

    state = c.post("/api/sessions", json={"game_id": game_id, "seed": 3}).json()
    session_id = state["session_id"]
    for _ in range(20):
        if state["finished"]:
            break
        response = c.post(f"/api/sessions/{session_id}/actions/play",
                          json={"revision": state["revision"]})
        assert response.status_code == 200, response.text
        state = response.json()["state"]
    assert state["finished"] is True
    assert state["scores"] and sum(state["scores"]) > 0


def test_agent_loop_fails_safely_without_a_model(tmp_path):
    c = client(tmp_path)
    response = c.post("/api/agent/loop", json={"goal": "做一个比大小"})
    assert response.status_code == 422
    assert "模型配置" in response.json()["detail"]
