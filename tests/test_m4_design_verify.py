"""M4-5: the formal verification gate and failure inspection.

``verify_game`` is the host gate (multiple seeds x strategies x the independent
contract check); its result is recorded in the session, never taken from the
model. ``inspect_failure`` turns the last stored failure into a located report
with IR clause paths and a bounded state summary, and mutates nothing.
"""
from __future__ import annotations

from test_g2_m2 import scenario_a_ir
from test_g2_m3_scenario_b import scenario_b_ir

from pocker_agent.agent.design_service import DesignService
from pocker_agent.agent.design_store import DesignStore


def _service(tmp_path, ir: dict) -> tuple[DesignService, DesignStore]:
    store = DesignStore(tmp_path / "design.db")
    session = store.create("g", "描述")
    service = DesignService(store, session.session_id)
    service.dispatch("propose_ir", {"ir": ir})
    return service, store


def test_verify_game_records_a_passing_host_credential(tmp_path):
    service, _ = _service(tmp_path, scenario_a_ir())
    result = service.dispatch("verify_game", {})
    assert result["ok"] and result["verified"] is True
    assert result["verification_id"]
    assert set(result["seeds"]) == {0, 1, 7, 23, 42}
    session = service.session()
    assert session.status == "verified"
    assert session.context["verification"]["ok"] is True
    assert session.context["verification"]["verification_id"] == result["verification_id"]
    assert session.context["failure"] is None


def test_a_failing_gate_is_recorded_and_reported(tmp_path):
    # Scenario B's ``pass`` action needs seed 28 for wait coverage; the host
    # default seeds deliberately do not include it, so the gate fails.
    service, _ = _service(tmp_path, scenario_b_ir())
    result = service.dispatch("verify_game", {})
    assert result["ok"] is False
    assert result["verified"] is False
    assert "verification_failed" in result["error"]
    session = service.session()
    assert session.status == "failed"
    assert session.context["verification"]["ok"] is False
    assert session.context["failure"]["stage"] == "verify"
    assert session.context["failure"]["failures"]


def test_verify_game_uses_host_seeds_that_cover_the_pass_action(tmp_path):
    store = DesignStore(tmp_path / "design.db")
    session = store.create("g")
    from test_g2_m3_scenario_b import SCENARIO_B_SEEDS

    service = DesignService(store, session.session_id, seeds=SCENARIO_B_SEEDS)
    service.dispatch("propose_ir", {"ir": scenario_b_ir()})
    result = service.dispatch("verify_game", {})
    assert result["ok"] and result["verified"] is True
    assert service.session().status == "verified"


def test_inspect_failure_returns_clause_paths_and_a_state_summary(tmp_path):
    payload = scenario_b_ir()
    payload["requirements"] = [{"id": "R1", "text": "同点或同花接牌",
                                "nodes": ["actions.play"]}]
    service, _ = _service(tmp_path, payload)
    service.dispatch("verify_game", {})
    before = service.session().revision
    report = service.dispatch("inspect_failure", {})
    assert report["ok"]
    assert report["failure"]["stage"] == "verify"
    assert report["clauses"]["R1"]                          # IR clause -> plan nodes
    assert report["state"] is not None
    assert service.session().revision == before               # inspection never writes


def test_inspect_failure_reports_a_compile_error_without_a_state(tmp_path):
    service, _ = _service(tmp_path, scenario_a_ir(players={"count": 3}))
    service.dispatch("compose_plan", {})
    report = service.dispatch("inspect_failure", {})
    assert report["ok"]
    assert report["failure"]["stage"] == "compose"
    assert report["failure"]["path"] == "flow.resolve.0"
    assert report["failure"]["clause"]
    assert report["state"] is None                            # nothing runnable was built


def test_inspect_failure_says_so_when_there_is_nothing_to_inspect(tmp_path):
    service, _ = _service(tmp_path, scenario_a_ir())
    assert service.dispatch("inspect_failure", {})["error"] == "no_failure"
