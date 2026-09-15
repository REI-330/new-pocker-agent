"""M5-1: reading a registered version -- rules, evidence and source-map clauses.

A version is the reviewable unit: the rules the user confirmed, the host
credential that authorised it, and the clause-to-node map that says where each
requirement lives in the compiled plan. The list view stays cheap; the detail
view carries the full rule body and its evidence.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_g2_m2 import scenario_a_ir

from pocker_agent.app import create_app


@pytest.fixture(scope="module")
def published(tmp_path_factory):
    """Verify, confirm and publish scenario A once for the whole module."""
    database = tmp_path_factory.mktemp("m5-version") / "m5.db"
    client = TestClient(create_app(database))
    created = client.post("/api/designs", json={"game_id": "m5-version",
                                                "description": "描述"}).json()
    session_id = created["session_id"]
    updated = client.post(f"/api/designs/{session_id}/update",
                          json={"expected_revision": 0, "ir": scenario_a_ir()})
    assert updated.status_code == 200, updated.text
    verified = client.post(f"/api/designs/{session_id}/verify", json={})
    assert verified.status_code == 200 and verified.json()["ok"], verified.text
    ir_hash = verified.json()["session"]["ir_hash"]
    confirmed = client.post(f"/api/designs/{session_id}/confirm",
                            json={"ir_hash": ir_hash})
    assert confirmed.status_code == 200, confirmed.text
    response = client.post(f"/api/designs/{session_id}/publish", json={})
    assert response.status_code == 200, response.text
    return {"client": client, "session_id": session_id, "ir_hash": ir_hash,
            "artifact": response.json()["artifact"]}


def test_the_version_list_is_a_cheap_summary(published):
    body = published["client"].get("/api/games/m5-version/versions").json()
    assert body["game_id"] == "m5-version"
    versions = body["versions"]
    assert [item["version"] for item in versions] == [1]
    summary = versions[0]
    assert summary["title"] == "三轮公开比较积分"
    assert summary["generation_source"] == "game_rules_1_0"
    assert summary["plan_hash"] and summary["verification_id"]
    assert summary["ir_hash"] == published["ir_hash"]
    assert "plan" not in summary and "ir" not in summary


def test_the_version_detail_exposes_rules_evidence_and_clause_paths(published):
    detail = published["client"].get("/api/games/m5-version/versions/1")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["rules"]["kind"] == "game_rules"
    assert body["rules"]["execution"]["rules"]["requirements"], \
        "the confirmed clauses must be visible"
    assert body["artifact"] == published["artifact"]
    verification = body["verification"]
    assert verification["ok"] is True
    assert verification["verification_id"] == body["artifact"]["verification_id"]
    assert verification["ir_hash"] == published["ir_hash"]
    assert verification["plan_hash"] == body["artifact"]["plan_hash"]
    assert body["source_map"]["clauses"], "each clause must map to plan nodes"
    assert body["source_map"]["paths"], "nodes must map back to IR paths"


def test_a_published_version_can_be_played(published):
    client = published["client"]
    session = client.post("/api/sessions", json={"game_id": "m5-version",
                                                 "seed": 7})
    assert session.status_code == 200, session.text
    view = session.json()
    assert view["game_id"] == "m5-version" and view["version"] == 1
    assert view["session_id"] and view["actions"] is not None


def test_an_unknown_version_or_game_is_a_404(published):
    client = published["client"]
    assert client.get("/api/games/m5-version/versions/99").status_code == 404
    assert client.get("/api/games/m5-version/versions/99").json()["detail"] == "游戏版本不存在"
    assert client.get("/api/games/no-such-game/versions").json()["versions"] == []
