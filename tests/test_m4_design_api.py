"""M4-1: the design-session API over real HTTP (create / read / update / conflict)."""
from __future__ import annotations

from fastapi.testclient import TestClient
from test_g2_m3_scenario_b import scenario_b_ir

from pocker_agent.app import create_app


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(tmp_path / "design-api.db"))


def _create(client: TestClient, game_id: str = "api-design") -> dict:
    response = client.post("/api/designs", json={"game_id": game_id,
                                                 "description": "两人接牌游戏"})
    assert response.status_code == 200, response.text
    return response.json()


def test_create_read_and_update_over_http(tmp_path):
    client = _client(tmp_path)
    created = _create(client)
    assert created["revision"] == 0 and created["status"] == "draft"
    assert created["description"] == "两人接牌游戏"

    read = client.get(f"/api/designs/{created['session_id']}")
    assert read.status_code == 200
    assert read.json()["revision"] == 0

    updated = client.post(f"/api/designs/{created['session_id']}/update",
                          json={"expected_revision": 0, "ir": scenario_b_ir(),
                                "status": "diagnosed", "diagnosis": {"ok": True}})
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["revision"] == 1 and body["status"] == "diagnosed"
    assert body["ir"]["kind"] == "composed"
    assert body["ir_hash"]


def test_a_stale_revision_is_a_409_conflict(tmp_path):
    client = _client(tmp_path)
    created = _create(client)
    first = client.post(f"/api/designs/{created['session_id']}/update",
                        json={"expected_revision": 0, "ir": scenario_b_ir()})
    assert first.status_code == 200
    stale = client.post(f"/api/designs/{created['session_id']}/update",
                        json={"expected_revision": 0, "status": "failed"})
    assert stale.status_code == 409
    assert "stale_revision" in stale.json()["detail"]
    assert client.get(f"/api/designs/{created['session_id']}").json()["revision"] == 1


def test_an_invalid_ir_is_a_422_and_the_session_is_unchanged(tmp_path):
    client = _client(tmp_path)
    created = _create(client)
    bad = client.post(f"/api/designs/{created['session_id']}/update",
                      json={"expected_revision": 0,
                            "ir": {"schema_version": "0.5", "kind": "composed"}})
    assert bad.status_code == 422
    assert "invalid_ir" in bad.json()["detail"]
    assert client.get(f"/api/designs/{created['session_id']}").json()["revision"] == 0


def test_expected_revision_is_required_by_the_schema(tmp_path):
    client = _client(tmp_path)
    created = _create(client)
    missing = client.post(f"/api/designs/{created['session_id']}/update",
                          json={"ir": scenario_b_ir()})
    assert missing.status_code == 422


def test_request_id_makes_an_http_retry_idempotent(tmp_path):
    client = _client(tmp_path)
    created = _create(client)
    payload = {"expected_revision": 0, "request_id": "retry-1", "ir": scenario_b_ir()}
    first = client.post(f"/api/designs/{created['session_id']}/update", json=payload)
    retry = client.post(f"/api/designs/{created['session_id']}/update", json=payload)
    assert first.status_code == retry.status_code == 200
    assert first.json()["revision"] == retry.json()["revision"] == 1
    assert client.get(f"/api/designs/{created['session_id']}").json()["revision"] == 1


def test_unknown_design_session_is_a_404(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/designs/missing").status_code == 404
    assert client.get("/api/designs/missing").json()["detail"] == "设计会话不存在"
    update = client.post("/api/designs/missing/update",
                         json={"expected_revision": 0, "status": "failed"})
    assert update.status_code == 404


def test_designs_are_listed_and_isolated(tmp_path):
    client = _client(tmp_path)
    first = _create(client, "game-a")
    second = _create(client, "game-b")
    client.post(f"/api/designs/{first['session_id']}/update",
                json={"expected_revision": 0, "ir": scenario_b_ir()})
    listed = client.get("/api/designs").json()["designs"]
    assert {item["session_id"] for item in listed} == {first["session_id"], second["session_id"]}
    assert client.get(f"/api/designs/{second['session_id']}").json()["revision"] == 0


def test_a_restarted_app_restores_design_sessions(tmp_path):
    database = tmp_path / "design-api.db"
    first_client = TestClient(create_app(database))
    created = _create(first_client)
    first_client.post(f"/api/designs/{created['session_id']}/update",
                      json={"expected_revision": 0, "ir": scenario_b_ir(),
                            "status": "diagnosed", "diagnosis": {"ok": True}})

    restarted = TestClient(create_app(database))
    restored = restarted.get(f"/api/designs/{created['session_id']}").json()
    assert restored["revision"] == 1
    assert restored["status"] == "diagnosed"
    assert restored["ir"]["kind"] == "composed"
    assert restored["diagnosis"] == {"ok": True}


def test_the_design_tool_table_is_exposed_over_http(tmp_path):
    client = _client(tmp_path)
    response = client.get("/api/agent/design-tools")
    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["design_tools"]}
    assert {"list_capabilities", "describe_mechanism", "propose_ir", "finalize"} <= names
    assert not any("macro" in name for name in names)
