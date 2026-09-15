"""End-to-end: real HTTP through the v0.4 app, real playtest gate, real sessions."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pocker_agent.app import create_app
from pocker_agent.arithmetic import solve


def client(tmp_path) -> TestClient:
    return TestClient(create_app(tmp_path / "e2e.db"))


def action_path(session_id: str, action: str) -> str:
    return f"/api/sessions/{session_id}/actions/{action}"


def test_agent_loop_supports_a_multi_turn_conversation_over_http(tmp_path):
    """The chat flow: ask a question, then answer it, then get a playable rule."""
    model = _ScriptedModel(
        _decision("ask_user", question="一共几轮？", missing=["max_rounds"]),
        _decision("propose_ir", ir={"kind": "war", "game_id": "agent-chat-war",
                                     "title": "对话生成比大小", "max_rounds": 5, "players": 2}),
        _decision("compose_plan"),
        _decision("playtest"),
        _decision("finalize"),
    )
    c = TestClient(create_app(tmp_path / "loop.db", model_factory=lambda: model))

    first = c.post("/api/agent/loop", json={"message": "做个比大小的游戏"}).json()
    assert first["kind"] == "question", first
    assert len(first["messages"]) == 2 and first["messages"][1]["content"] == "一共几轮？"

    second = c.post("/api/agent/loop",
                    json={"message": "5 轮", "messages": first["messages"]}).json()
    assert second["finalized"] is True, second
    assert second["ir"]["execution"]["rules"]["max_rounds"] == 5
    assert second["messages"][:2] == first["messages"]      # thread preserved
    assert second["messages"][-1]["role"] == "assistant"

    assert second["registered"] is False
    assert second["approval_required"] is True
    assert not any(game["id"] == "agent-chat-war"
                   for game in c.get("/api/games").json()["games"])


def test_agent_loop_rejects_an_empty_message(tmp_path):
    """The 422 must come from the chat schema, not from `goal` being required.

    A process that started before the chat-thread change answers the very same
    request with `body.goal: Field required`, which is how a stale server shows
    up in the browser as "the design box cannot send".
    """
    c = client(tmp_path)
    response = c.post("/api/agent/loop", json={"message": "   "})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "请输入玩法描述" in detail, detail
    assert "goal" not in detail, detail


def test_health_reports_the_loaded_code_fingerprint(tmp_path):
    """`doctor.py --url` compares this against the working tree to catch stale code."""
    from pocker_agent.app import CODE_FINGERPRINT, code_fingerprint, dist_assets

    health = client(tmp_path).get("/health").json()
    assert health["code"] == CODE_FINGERPRINT == code_fingerprint()
    assert isinstance(health["pid"], int)
    assert health["assets"] == dist_assets()
    assert health["assets_present"] is True


def test_served_page_references_resolve(tmp_path):
    """The single-port page must load its bundle: /assets is the card mount."""
    built = Path(__file__).resolve().parents[1] / "frontend" / "dist" / "index.html"
    if not built.is_file():
        pytest.skip("frontend/dist is not built; run: npm run build --prefix frontend")
    c = client(tmp_path)
    page = c.get("/")
    assert page.status_code == 200
    urls = re.findall(r'(?:src|href)="(/(?:static|assets)[^"]*)"', page.text)
    assert urls, "the built page should reference its bundle and stylesheet"
    for url in urls:
        assert c.get(url).status_code == 200, f"{url} is not reachable from the app"


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


def test_a_repeated_request_id_is_not_applied_twice(tmp_path):
    """A retried submit returns the original response, even at the old revision."""
    c = client(tmp_path)
    state = c.post("/api/sessions", json={"game_id": "arithmetic24", "seed": 7}).json()
    session_id = state["session_id"]
    body = {"revision": 0, "expression": solve(tuple(state["numbers"])),
            "request_id": "retry-1"}
    first = c.post(action_path(session_id, "submit_expression"), json=body)
    assert first.status_code == 200
    second = c.post(action_path(session_id, "submit_expression"), json=body)
    assert second.status_code == 200
    assert second.json() == first.json()
    assert c.get(f"/api/sessions/{session_id}").json()["revision"] == 1


def test_reusing_a_request_id_with_a_different_body_is_a_conflict(tmp_path):
    c = client(tmp_path)
    state = c.post("/api/sessions", json={"game_id": "arithmetic24", "seed": 7}).json()
    session_id = state["session_id"]
    answer = solve(tuple(state["numbers"]))
    assert c.post(action_path(session_id, "submit_expression"),
                  json={"revision": 0, "expression": answer,
                        "request_id": "key"}).status_code == 200
    conflict = c.post(action_path(session_id, "submit_expression"),
                      json={"revision": 0, "expression": "1+2", "request_id": "key"})
    assert conflict.status_code == 409
    assert conflict.json()["detail"].startswith("request_id_conflict")


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


def test_an_in_memory_session_read_is_a_copy():
    """A caller cannot mutate a stored in-memory session through ``get``."""
    from pocker_agent.core import SessionStore

    store = SessionStore()
    session = store.create("arithmetic24", seed=7)
    fetched = store.get(session.id)
    fetched.revision = 999
    fetched.processed["x"] = {"y": 1}
    fetched.interpreter.state["injected"] = True
    again = store.get(session.id)
    assert again.revision == 0 and again.processed == {}
    assert "injected" not in again.interpreter.state


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


def test_a_broken_plan_is_reported_over_http_and_never_becomes_a_500(tmp_path):
    """Structurally valid, semantically broken: the caller must get a result.

    This plan passes GamePlan validation and names real operations, but asks
    `betting` for state it never dealt. Before the interpreter converted lookup
    errors into contract violations, this request answered HTTP 500 and the
    model never learned why.
    """
    broken = {"schema_version": "0.4", "game_kind": "poker", "players": 2,
              "tools": [{"name": "betting", "config": {"min_raise": 10}}],
              "initial": {"stacks": [100, 100]}, "entry": "start",
              "nodes": {"start": {"kind": "wait", "inputs": {"go": "legal"}},
                        "legal": {"kind": "call", "next": "hold", "action": {
                            "tool": "betting", "operation": "legal",
                            "args": {"state": "$state"},
                            "result_key": "legal_bets"}},
                        "hold": {"kind": "wait", "inputs": {"poke": "hold"}}},
              "step_limit": 64}
    model = _ScriptedModel(
        _decision("propose_ir", ir={"kind": "poker", "game_id": "broken-poker",
                                     "title": "坏的扑克", "stacks": 100, "min_raise": 10}),
        _decision("compose_plan", plan=broken),
        _decision("playtest"),
    )
    c = TestClient(create_app(tmp_path / "broken.db", model_factory=lambda: model))
    response = c.post("/api/agent/loop", json={"message": "做个扑克"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["finalized"] is False
    compose_step = next(o for o in body["observations"] if o["tool"] == "compose_plan")
    assert compose_step["ok"] is False
    assert compose_step["error"] == "raw_plan_not_accepted"
    assert not any(game["id"] == "broken-poker" for game in c.get("/api/games").json()["games"])


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
    assert result["registered"] is False
    assert result["approval_required"] is True
    assert result["ir_hash"]
    assert not any(game["id"] == game_id for game in c.get("/api/games").json()["games"])


def test_agent_loop_fails_safely_without_a_model(tmp_path):
    c = client(tmp_path)
    response = c.post("/api/agent/loop", json={"goal": "做一个比大小"})
    assert response.status_code == 422
    assert "模型配置" in response.json()["detail"]


def test_uno_over_http_applies_special_cards_and_hides_hands(tmp_path):
    c = client(tmp_path)
    state = c.post("/api/sessions", json={"game_id": "uno", "seed": 7}).json()
    session_id = state["session_id"]
    assert state["private_hands"] is True
    assert state["players"][1]["hand"] == [] and state["players"][1]["hidden_count"] == 5

    for _ in range(300):
        if state["finished"]:
            break
        if "play" in state["legal_actions"]:
            response = c.post(f"/api/sessions/{session_id}/actions/play",
                              json={"revision": state["revision"],
                                    "card_index": state["legal_card_indices"][0],
                                    "declared_suit": "S"})
        else:
            response = c.post(f"/api/sessions/{session_id}/actions/draw",
                              json={"revision": state["revision"]})
        assert response.status_code == 200, response.text
        state = response.json()["state"]

    assert state["finished"] is True
    assert state["players"][1]["hidden_count"] > 0
    assert state["players"][1]["hand"] == []


def test_go_fish_over_http_asks_concrete_ranks(tmp_path):
    c = client(tmp_path)
    state = c.post("/api/sessions", json={"game_id": "go_fish", "seed": 7}).json()
    session_id = state["session_id"]
    assert state["private_hands"] is True
    assert state["players"][1]["hand"] == [] and state["players"][1]["hidden_count"] == 5
    actions = state["legal_actions"]
    assert actions and all(action.startswith("ask:") and action != "ask:*" for action in actions)
    assert c.post(f"/api/sessions/{session_id}/actions/ask:*",
                  json={"revision": 0}).status_code != 200

    for _ in range(30):
        if state["finished"]:
            break
        response = c.post(f"/api/sessions/{session_id}/actions/{state['legal_actions'][0]}",
                          json={"revision": state["revision"]})
        assert response.status_code == 200, response.text
        state = response.json()["state"]
    assert "pairs" in state


def test_blackjack_over_http_hides_the_dealer(tmp_path):
    c = client(tmp_path)
    state = c.post("/api/sessions", json={"game_id": "blackjack", "seed": 7}).json()
    session_id = state["session_id"]
    assert state["private_hands"] is True
    assert len(state["players"][0]["hand"]) == 2
    assert state["players"][1]["hand"] == [] and state["players"][1]["hidden_count"] == 2

    for _ in range(20):
        if state["finished"]:
            break
        assert "stand" in state["legal_actions"]
        response = c.post(f"/api/sessions/{session_id}/actions/stand",
                          json={"revision": state["revision"]})
        assert response.status_code == 200, response.text
        state = response.json()["state"]

    assert state["finished"] is True
    assert sum(state["scores"]) <= 3
    assert state["players"][1]["hidden_count"] == 0     # dealer revealed at the end


def test_poker_over_http_reaches_showdown_and_conserves_chips(tmp_path):
    c = client(tmp_path)
    state = c.post("/api/sessions", json={"game_id": "five_card_poker", "seed": 7}).json()
    session_id = state["session_id"]
    assert state["private_hands"] is True
    assert state["players"][1]["hidden_count"] == 5
    assert state["stacks"] == [100, 100] and state["pot"] == 0

    for _ in range(100):
        if state["finished"]:
            break
        action = next(choice for choice in state["legal_actions"]
                      if choice in ("check", "call", "fold"))
        payload = {"revision": state["revision"]}
        if action == "raise":
            payload["amount"] = 20
        response = c.post(f"/api/sessions/{session_id}/actions/{action}", json=payload)
        assert response.status_code == 200, response.text
        state = response.json()["state"]

    assert state["finished"] is True
    assert sum(state["stacks"]) == 200          # chips conserved end to end
    assert state["pot"] == 0


def test_whist_over_http_is_a_team_trick_game(tmp_path):
    c = client(tmp_path)
    state = c.post("/api/sessions", json={"game_id": "whist", "seed": 7}).json()
    session_id = state["session_id"]
    assert len(state["players"]) == 4
    assert state["private_hands"] is True
    assert sum(player["hidden_count"] for player in state["players"][1:]) == 15

    for _ in range(300):
        if state["finished"]:
            break
        assert state["legal_card_indices"], "a trick seat always has a legal card"
        response = c.post(f"/api/sessions/{session_id}/actions/play",
                          json={"revision": state["revision"],
                                "card_index": state["legal_card_indices"][0]})
        assert response.status_code == 200, response.text
        state = response.json()["state"]

    assert state["finished"] is True
    assert set(state["winners"]) in ({"player-1", "player-3"}, {"player-2", "player-4"},
                                      {"player-1", "player-2", "player-3", "player-4"})


def test_crazy_eights_over_http_hides_the_opponent_and_is_playable(tmp_path):
    c = client(tmp_path)
    state = c.post("/api/sessions", json={"game_id": "crazy_eights", "seed": 7}).json()
    session_id = state["session_id"]
    assert state["private_hands"] is True
    assert len(state["players"][0]["hand"]) == 5
    assert state["players"][1]["hand"] == []
    assert state["players"][1]["hidden_count"] == 5

    for _ in range(400):
        if state["finished"]:
            break
        if "play" in state["legal_actions"]:
            payload = {"revision": state["revision"],
                       "card_index": state["legal_card_indices"][0],
                       "declared_suit": "S"}
            response = c.post(f"/api/sessions/{session_id}/actions/play", json=payload)
        else:
            response = c.post(f"/api/sessions/{session_id}/actions/draw",
                              json={"revision": state["revision"]})
        assert response.status_code == 200, response.text
        state = response.json()["state"]

    assert state["finished"] is True
    assert state["players"][1]["hidden_count"] > 0
    assert state["players"][1]["hand"] == []
