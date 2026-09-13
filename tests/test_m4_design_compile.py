"""M4-4: capability resolution, deterministic compilation and diagnostics.

``compose_plan`` never accepts a model-written raw plan: it lowers the current
IR with the same compiler the publish path uses. ``validate_plan`` and
``simulate`` are diagnostics -- they read the deterministic compile and do not
mutate the session. A compile failure is stored as a located failure, not a
traceback, so ``inspect_failure`` (M4-5) has something to report.
"""
from __future__ import annotations

from test_g2_m2 import scenario_a_ir
from test_g2_m3_scenario_b import scenario_b_ir

from pocker_agent.agent.design_service import DesignService
from pocker_agent.agent.design_store import DesignStore


def _service(tmp_path, ir: dict | None = None) -> DesignService:
    store = DesignStore(tmp_path / "design.db")
    session = store.create("g", "描述")
    service = DesignService(store, session.session_id)
    if ir is not None:
        service.dispatch("propose_ir", {"ir": ir})
    return service


def test_capability_check_resolves_a_composed_rules_dependencies(tmp_path):
    service = _service(tmp_path, scenario_a_ir())
    result = service.dispatch("capability_check", {})
    assert result["ok"] and result["expressible"] is True
    assert {"sequential_turn", "score_settle", "rank_compare"} <= set(result["axes"])
    assert result["missing"] == []
    assert service.session().status == "diagnosed"


def test_compose_plan_rejects_a_model_supplied_raw_plan(tmp_path):
    service = _service(tmp_path, scenario_a_ir())
    result = service.dispatch("compose_plan", {"plan": {"game_kind": "custom", "nodes": {}}})
    assert result["ok"] is False and result["error"] == "raw_plan_not_accepted"
    assert service.session().revision == 1                 # only the proposal landed


def test_compose_plan_lowers_the_ir_and_records_a_bound_summary(tmp_path):
    service = _service(tmp_path, scenario_a_ir())
    result = service.dispatch("compose_plan", {})
    assert result["ok"], result
    assert result["generation_source"] == "composed_rules"
    assert result["ir_hash"] == service.session().ir_hash
    assert result["plan_hash"] and result["compiler_version"]
    assert result["nodes"] > 0 and "zones" in result["tools"]
    assert result["composition"]["ok"] is True
    session = service.session()
    assert session.status == "compiled"
    assert session.context["compiled"]["plan_hash"] == result["plan_hash"]


def test_a_compile_failure_is_located_and_leaves_the_session_uncompiled(tmp_path):
    # Three seats with a compare resolve is valid IR data but has no lowering.
    service = _service(tmp_path, scenario_a_ir(players={"count": 3}))
    result = service.dispatch("compose_plan", {})
    assert result["ok"] is False
    assert "compare_requires_two_players" in result["error"]
    assert result["path"] == "flow.resolve.0"
    session = service.session()
    assert session.context["compiled"] is None
    assert session.context["failure"]["stage"] == "compose"
    assert session.context["failure"]["path"] == "flow.resolve.0"
    assert session.status == "draft"


def test_validate_plan_requires_a_compile_and_reports_structure(tmp_path):
    service = _service(tmp_path, scenario_a_ir())
    assert service.dispatch("validate_plan", {})["error"] == "compose_plan_first"
    service.dispatch("compose_plan", {})
    before = service.session().revision
    result = service.dispatch("validate_plan", {})
    assert result["ok"], result
    assert result["reachable"] == result["nodes"]
    assert result["unreachable"] == []
    assert service.session().revision == before             # diagnostics do not write


def test_simulate_runs_one_bounded_path_without_writing(tmp_path):
    service = _service(tmp_path, scenario_b_ir())
    service.dispatch("compose_plan", {})
    before = service.session().revision
    result = service.dispatch("simulate", {"seed": 0})
    assert result["ok"], result
    assert result["steps"] > 0 and result["events"] > 0
    assert result["state"]["finished"] is True
    assert service.session().revision == before


def test_compose_requires_a_proposal_first(tmp_path):
    service = _service(tmp_path)
    assert service.dispatch("compose_plan", {})["error"] == "propose_ir_first"
    assert service.dispatch("capability_check", {})["error"] == "propose_ir_first"
    assert service.dispatch("simulate", {})["error"] == "propose_ir_first"
