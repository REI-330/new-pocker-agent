"""M4-7: the bounded design loop and its API adapters.

The loop is host-controlled: one model output is one host tool call, recoverable
failures come back as observations, and host failures (transport, crashes, a
stale revision) stop the turn. A finalized turn still produces only a candidate.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from test_g2_m2 import scenario_a_ir

from pocker_agent.agent import DEFAULT_DESIGN_BUDGET, run_design_loop
from pocker_agent.agent.design_service import DesignService
from pocker_agent.agent.design_store import DesignStore
from pocker_agent.app import create_app
from pocker_agent.core import SessionStore


class ScriptedModel:
    """A transport that replays fixed decisions; it makes no design choices."""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        if not self.decisions:
            raise RuntimeError("script_exhausted")
        return self.decisions.pop(0)


def _decision(tool: str, **args) -> str:
    return json.dumps({"tool": tool, "args": args})


def _service(tmp_path) -> DesignService:
    store = DesignStore(tmp_path / "design.db")
    session = store.create("design-loop", "描述")
    return DesignService(store, session.session_id)


def test_a_scripted_turn_reaches_a_finalized_candidate_without_registering(tmp_path):
    service = _service(tmp_path)
    model = ScriptedModel([
        _decision("propose_ir", ir=scenario_a_ir()),
        _decision("compose_plan"),
        _decision("verify_game"),
        _decision("finalize"),
    ])
    result = run_design_loop(service, service.session_id, "做一个三轮比大小", model)
    assert result.kind == "finalized"
    assert result.status == "awaiting_confirmation"
    assert result.artifact and result.artifact["plan_hash"]
    assert result.used["decisions"] == 4
    assert result.session_id == service.session_id
    session = service.session()
    assert session.context["chat"][0] == {"role": "user", "content": "做一个三轮比大小"}
    assert session.context["chat"][-1]["role"] == "assistant"
    assert SessionStore(tmp_path / "design.db").list_versions("design-loop") == []


def test_the_default_budget_matches_the_phase_two_limits():
    assert DEFAULT_DESIGN_BUDGET["max_decisions"] == 24
    assert DEFAULT_DESIGN_BUDGET["max_repairs"] == 4
    assert DEFAULT_DESIGN_BUDGET["max_tokens"] == 48000
    assert DEFAULT_DESIGN_BUDGET["max_seconds"] == 180


def test_a_recoverable_tool_error_is_refed_then_repaired(tmp_path):
    service = _service(tmp_path)
    model = ScriptedModel([
        _decision("propose_ir", ir={"schema_version": "0.5", "kind": "composed"}),
        _decision("propose_ir", ir=scenario_a_ir()),
        _decision("ask_user", question="还要什么？"),
    ])
    result = run_design_loop(service, service.session_id, "设计", model)
    assert result.kind == "question"
    assert result.used["repairs"] == 1
    assert result.observations[0]["ok"] is False
    assert "invalid_ir" in result.observations[0]["error"]
    assert result.observations[1]["ok"] is True
    assert service.session().context["questions"] == ["还要什么？"]


def test_unparseable_output_is_refed_to_the_model(tmp_path):
    service = _service(tmp_path)
    model = ScriptedModel(["这不是 JSON", _decision("unsupported", message="需要同时行动",
                                                   missing=["simultaneous"])])
    result = run_design_loop(service, service.session_id, "设计", model)
    assert result.kind == "unsupported"
    assert result.used["repairs"] == 1
    assert result.observations[0]["error"] == "model_output_invalid_json"
    assert service.session().status == "failed"


def test_a_host_failure_stops_the_turn(tmp_path):
    service = _service(tmp_path)

    class Broken:
        def complete(self, messages):
            raise RuntimeError("connection refused")

    result = run_design_loop(service, service.session_id, "设计", Broken())
    assert result.kind == "error"
    assert result.message.startswith("model_failed")


def test_a_blocking_model_is_cut_off_at_the_host_deadline(tmp_path):
    service = _service(tmp_path)
    finished = False

    class SlowModel:
        def complete(self, messages):
            nonlocal finished
            time.sleep(0.15)
            finished = True
            return _decision("propose_ir", ir=scenario_a_ir())

    result = run_design_loop(service, service.session_id, "设计", SlowModel(),
                             {"max_seconds": 0.02})
    assert result.kind == "budget_exhausted"
    assert "墙钟" in result.message
    assert service.session().ir is None
    # The late worker may finish, but its response is never dispatched.
    time.sleep(0.17)
    assert finished is True
    assert service.session().ir is None


def test_the_decision_budget_stops_the_turn(tmp_path):
    service = _service(tmp_path)
    model = ScriptedModel([_decision("list_capabilities") for _ in range(10)])
    result = run_design_loop(service, service.session_id, "设计", model,
                             {"max_decisions": 2})
    assert result.kind == "budget_exhausted"
    assert result.used["decisions"] == 2


def test_a_single_over_long_output_is_charged_to_the_repair_budget(tmp_path):
    service = _service(tmp_path)
    model = ScriptedModel(["x" * 5000, _decision("ask_user", question="q")])
    result = run_design_loop(service, service.session_id, "设计", model,
                             {"max_output_chars": 100, "max_repairs": 1})
    assert result.kind == "question"
    assert result.observations[0]["error"] == "output_too_long"


def test_finalize_without_verification_is_a_recoverable_error(tmp_path):
    service = _service(tmp_path)
    model = ScriptedModel([
        _decision("propose_ir", ir=scenario_a_ir()),
        _decision("finalize"),
        _decision("ask_user", question="先验证？"),
    ])
    result = run_design_loop(service, service.session_id, "设计", model)
    assert result.kind == "question"
    assert result.observations[1]["error"] == "verification_required"


def test_the_messages_endpoint_runs_a_design_turn_over_http(tmp_path):
    model = ScriptedModel([
        _decision("propose_ir", ir=scenario_a_ir()),
        _decision("compose_plan"),
        _decision("verify_game"),
        _decision("finalize"),
    ])
    client = TestClient(create_app(tmp_path / "api.db", model_factory=lambda: model))
    created = client.post("/api/designs", json={"game_id": "loop-api",
                                                "description": "描述"}).json()
    response = client.post(f"/api/designs/{created['session_id']}/messages",
                           json={"message": "做一个三轮比大小", "expected_revision": 0})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "finalized" and body["registered"] is False
    assert body["session"]["status"] == "awaiting_confirmation"
    assert body["session"]["revision"] > 0


def test_the_messages_endpoint_rejects_a_stale_revision(tmp_path):
    model = ScriptedModel([_decision("ask_user", question="q")])
    client = TestClient(create_app(tmp_path / "api.db", model_factory=lambda: model))
    created = client.post("/api/designs", json={"game_id": "loop-api"}).json()
    stale = client.post(f"/api/designs/{created['session_id']}/messages",
                        json={"message": "设计", "expected_revision": 3})
    assert stale.status_code == 409
    assert "stale_revision" in stale.json()["detail"]


def test_the_legacy_loop_routes_to_design_when_a_design_id_is_given(tmp_path):
    model = ScriptedModel([_decision("ask_user", question="问题")])
    client = TestClient(create_app(tmp_path / "api.db", model_factory=lambda: model))
    created = client.post("/api/designs", json={"game_id": "loop-api"}).json()
    response = client.post("/api/agent/loop", json={"message": "设计", "goal": "设计",
                                                    "design_id": created["session_id"]})
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "question"
    assert response.json()["registered"] is False


def test_an_unknown_design_id_is_a_404_on_the_loop(tmp_path):
    model = ScriptedModel([_decision("ask_user", question="q")])
    client = TestClient(create_app(tmp_path / "api.db", model_factory=lambda: model))
    response = client.post("/api/agent/loop", json={"message": "设计",
                                                    "design_id": "missing"})
    assert response.status_code == 404


def test_the_repair_budget_is_enforced(tmp_path):
    service = _service(tmp_path)
    model = ScriptedModel([
        _decision("propose_ir", ir={"schema_version": "0.5", "kind": "composed"}),
        _decision("propose_ir", ir={"schema_version": "0.5", "kind": "composed"}),
        _decision("ask_user", question="q"),
    ])
    result = run_design_loop(service, service.session_id, "设计", model,
                             {"max_repairs": 1})
    assert result.kind == "error"
    assert "repair_budget_exhausted" in result.message


@pytest.mark.parametrize("tool", ["ask_user", "unsupported"])
def test_honest_exits_are_distinct_kinds(tmp_path, tool):
    service = _service(tmp_path)
    request = ({"question": "缺牌数"} if tool == "ask_user" else {"message": "做不了",
                                                                 "missing": ["simultaneous"]})
    model = ScriptedModel([_decision(tool, **request)])
    result = run_design_loop(service, service.session_id, "设计", model)
    assert result.kind == ("question" if tool == "ask_user" else "unsupported")


def test_the_loop_claims_the_callers_revision_before_asking_the_model(tmp_path):
    service = _service(tmp_path)
    service.record(event="other_writer")             # revision 0 -> 1
    model = ScriptedModel([_decision("ask_user", question="q")])
    with pytest.raises(ValueError, match="stale_revision"):
        run_design_loop(service, service.session_id, "设计", model,
                        expected_revision=0)
    assert model.calls == 0                            # the conflict is not the model's


def test_a_concurrent_update_during_the_request_is_a_409_not_a_500(tmp_path):
    database = tmp_path / "api.db"
    holder: dict[str, str] = {}

    def model_factory():
        # A second writer bumps the session in the window between the endpoint's
        # revision check and the loop's first commit.
        app.state.design_store.commit(holder["sid"], 0, status="diagnosed")
        return ScriptedModel([_decision("ask_user", question="q")])

    app = create_app(database, model_factory=model_factory)
    client = TestClient(app)
    created = client.post("/api/designs", json={"game_id": "race"}).json()
    holder["sid"] = created["session_id"]
    response = client.post(f"/api/designs/{created['session_id']}/messages",
                           json={"message": "设计", "expected_revision": 0})
    assert response.status_code == 409, response.text
    assert "stale_revision" in response.json()["detail"]
    # The concurrent write is preserved and the racing turn is not applied.
    stored = client.get(f"/api/designs/{created['session_id']}").json()
    assert stored["revision"] == 1 and stored["status"] == "diagnosed"
    assert stored["context"].get("chat") is None


def test_a_tool_level_conflict_is_raised_not_returned_as_a_result(tmp_path):
    service = _service(tmp_path)

    def racing_dispatch(tool, args=None, **kwargs):
        return {"ok": False, "tool": tool, "error": "stale_revision: 并发写入"}

    service.dispatch = racing_dispatch
    model = ScriptedModel([_decision("list_capabilities")])
    with pytest.raises(ValueError, match="stale_revision"):
        run_design_loop(service, service.session_id, "设计", model)
