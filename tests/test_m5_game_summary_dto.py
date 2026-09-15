"""ADR-0019: every game exposes the same, complete playtest summary.

The old ``/api/games`` returned a full report for a reference game but only
``{ok: true}`` for a published artifact, which let the frontend hide the drift
behind optional fields (and then crash). The DTO is now strict: all seven keys
are always present, ``evidence`` names the origin, and an unexpected field is a
validation error rather than a silently dropped key.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_g2_m2 import scenario_a_ir

from pocker_agent.app import GameSummaryDTO, create_app

PLAYTEST_KEYS = {"ok", "seeds", "checks", "failures", "covered_wait_nodes",
                 "event_counts", "evidence"}


@pytest.fixture()
def client(tmp_path):
    app = create_app(tmp_path / "dto.db")
    test_client = TestClient(app)
    created = test_client.post("/api/designs",
                               json={"game_id": "dto-composed", "description": "描述"}).json()
    session_id = created["session_id"]
    updated = test_client.post(f"/api/designs/{session_id}/update",
                               json={"expected_revision": 0, "ir": scenario_a_ir()})
    assert updated.status_code == 200, updated.text
    verified = test_client.post(f"/api/designs/{session_id}/verify", json={})
    assert verified.status_code == 200 and verified.json()["ok"], verified.text
    ir_hash = verified.json()["session"]["ir_hash"]
    assert test_client.post(f"/api/designs/{session_id}/confirm",
                            json={"ir_hash": ir_hash}).status_code == 200
    published = test_client.post(f"/api/designs/{session_id}/publish", json={})
    assert published.status_code == 200, published.text

    return test_client


def _games(client) -> dict:
    return {game["id"]: game for game in client.get("/api/games").json()["games"]}


def test_every_game_has_the_complete_playtest_shape(client):
    games = _games(client)
    assert {"arithmetic24", "dto-composed"} <= set(games)
    for game in games.values():
        assert set(game["playtest"]) == PLAYTEST_KEYS, game["id"]
        assert game["playtest"]["evidence"] in ("reference", "verification")


def test_a_reference_game_reports_reference_evidence(client):
    report = _games(client)["arithmetic24"]["playtest"]
    assert report["evidence"] == "reference"
    assert report["ok"] is True and report["seeds"]


def test_a_published_artifact_summarises_its_verification_credential(client):
    game = _games(client)["dto-composed"]
    report = game["playtest"]
    assert report["evidence"] == "verification"
    assert report["ok"] is True
    assert game["version"] == 1 and game["verification_id"]
    detail = client.get("/api/games/dto-composed/versions/1").json()
    verification = detail["verification"]
    assert report["seeds"] == verification["seeds"]
    assert report["checks"] == verification["checks"]
    assert report["covered_wait_nodes"] == verification["covered_wait_nodes"]


def test_optional_artifact_fields_are_explicit_nulls(client):
    game = _games(client)["arithmetic24"]
    assert game["version"] is None and game["verification_id"] is None and game["source"] is None


def test_an_unexpected_field_is_a_validation_error():
    with pytest.raises(ValidationError):
        GameSummaryDTO.model_validate(
            {"id": "x", "title": "x", "kind": "k", "drift": 1,
             "playtest": {"ok": True, "seeds": [], "checks": [], "failures": [],
                          "covered_wait_nodes": [], "event_counts": {},
                          "evidence": "reference"}})
    with pytest.raises(ValidationError):
        GameSummaryDTO.model_validate(
            {"id": "x", "title": "x", "kind": "k",
             "playtest": {"ok": True, "evidence": "made_up"}})
