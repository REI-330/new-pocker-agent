"""M6 flow check: a scripted model can drive the whole design lifecycle.

This is deliberately *flow only* (ADR-0020): a deterministic model proposes a
known-good composed IR, the host gate verifies it, the user confirmation and
publish endpoints register it, and the registered game is played to the end over
the generic action endpoint. It proves the wiring the real blind benchmark
depends on; it is never reported as a generation-capability result.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient
from test_g2_m2 import scenario_a_ir

from pocker_agent.app import create_app


def decision(tool: str, **args) -> str:
    return json.dumps({"tool": tool, "args": args})


class ScriptedModel:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def complete(self, messages, **kwargs):
        if self.calls >= len(self.responses):
            raise RuntimeError("scripted responses exhausted")
        response = self.responses[self.calls]
        self.calls += 1
        return response


def _play_to_end(client: TestClient, session_id: str, max_steps: int = 240) -> dict:
    state = client.get(f"/api/sessions/{session_id}").json()
    for _ in range(max_steps):
        if state["finished"]:
            return state
        actions = state.get("actions") or []
        descriptor = next((item for item in actions if item["inputs"]), actions[0])
        values = {}
        for item in descriptor["inputs"]:
            if item["min_count"] > 0:
                values[item["id"]] = item["options"][: item["min_count"]]
        response = client.post(f"/api/sessions/{session_id}/actions",
                               json={"action_id": descriptor["id"], "input_values": values,
                                     "revision": state["revision"]})
        assert response.status_code == 200, response.text
        state = response.json()["state"]
    raise AssertionError(f"game {session_id} did not finish in {max_steps} steps")


def test_a_scripted_design_becomes_a_registered_playable_game(tmp_path):
    script = [decision("propose_ir", ir=scenario_a_ir()),
              decision("compose_plan"), decision("verify_game"), decision("finalize")]
    app = create_app(tmp_path / "m6-flow.db", model_factory=lambda: ScriptedModel(script))
    client = TestClient(app)

    created = client.post("/api/designs",
                          json={"game_id": "m6-flow", "description": "公开比较积分"}).json()
    session_id = created["session_id"]
    turn = client.post(f"/api/designs/{session_id}/messages", json={"message": "按描述设计"}).json()
    assert turn["kind"] == "finalized", turn
    session = turn["session"]
    assert session["status"] == "awaiting_confirmation"
    assert session["ir_hash"] and session["context"]["verification"]["ok"]

    confirmed = client.post(f"/api/designs/{session_id}/confirm",
                            json={"ir_hash": session["ir_hash"],
                                  "expected_revision": session["revision"]}).json()
    assert confirmed["confirmed"] is True
    published = client.post(f"/api/designs/{session_id}/publish",
                            json={"expected_revision": confirmed["session"]["revision"]}).json()
    assert published["published"] is True and published["idempotent"] is False
    assert published["artifact"]["generation_source"] == "game_rules_1_0"
    version = published["artifact"]["version"]

    created_session = client.post("/api/sessions",
                                  json={"game_id": "m6-flow", "version": version, "seed": 7})
    assert created_session.status_code == 200, created_session.text
    view = created_session.json()
    assert view["version"] == version and view["zones"] and view["actions"]

    state = _play_to_end(client, view["session_id"])
    assert state["finished"] is True

    # The summary DTO exposes the registered version's evidence (ADR-0019).
    game = next(item for item in client.get("/api/games").json()["games"]
                if item["id"] == "m6-flow")
    assert game["version"] == version
    assert game["playtest"]["evidence"] == "verification"
    assert game["playtest"]["ok"] is True


def test_an_invalid_ir_reports_field_level_errors(tmp_path):
    """A repair loop needs the field path, not just "15 validation errors"."""
    broken = scenario_a_ir()
    broken.pop("players", None)
    script = [decision("propose_ir", ir=broken)]
    app = create_app(tmp_path / "m6-invalid.db",
                     model_factory=lambda: ScriptedModel(script))
    client = TestClient(app)
    created = client.post("/api/designs",
                          json={"game_id": "m6-invalid", "description": "x"}).json()
    turn = client.post(f"/api/designs/{created['session_id']}/messages",
                       json={"message": "x"}).json()
    first = turn["observations"][0]
    assert first["ok"] is False
    assert first["error"].startswith("invalid_ir:")
    assert "players" in first["error"], first["error"]


def test_a_verbose_model_output_is_parsed_not_rejected(tmp_path):
    """A long reasoning preamble must not hide a valid JSON decision.

    The first M6 blind run failed with ``output_too_long`` on a model that wraps
    its decision in prose; that was a host defect (a size gate before parsing),
    so a decision inside a long message is now accepted up to a hard cap.
    """
    preamble = "推理过程：" + "逐步分析规则与机制。" * 1200          # > 8000 chars
    script = [preamble + "\n" + decision("propose_ir", ir=scenario_a_ir()),
              decision("compose_plan"), decision("verify_game"), decision("finalize")]
    app = create_app(tmp_path / "m6-verbose.db",
                     model_factory=lambda: ScriptedModel(script))
    client = TestClient(app)
    created = client.post("/api/designs",
                          json={"game_id": "m6-verbose", "description": "公开比较"}).json()
    turn = client.post(f"/api/designs/{created['session_id']}/messages",
                       json={"message": "按描述设计"}).json()
    assert turn["kind"] == "finalized", turn
    assert turn["observations"][0]["ok"] is True
    assert turn["used"]["repairs"] == 0, turn["used"]
