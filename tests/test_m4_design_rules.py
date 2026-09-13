"""M4-3: proposing and patching the rules IR through the design service.

``propose_ir`` is the only way a draft enters the session; ``patch_ir`` repairs
it in place. Both validate before writing, so an invalid IR leaves the stored
session untouched, and neither can silently drop a requirement clause the user
asked for.
"""
from __future__ import annotations

import copy

from test_g2_m3_scenario_b import scenario_b_ir

from pocker_agent.agent.design_service import DesignService
from pocker_agent.agent.design_store import DesignStore


def _service(tmp_path) -> DesignService:
    store = DesignStore(tmp_path / "design.db")
    session = store.create("g", "描述")
    return DesignService(store, session.session_id)


def _with_requirement(payload: dict) -> dict:
    clone = copy.deepcopy(payload)
    clone["requirements"] = [{"id": "R1", "text": "同点或同花接牌", "nodes": ["actions.play"]}]
    return clone


def test_propose_ir_normalizes_records_requirements_and_resets_status(tmp_path):
    service = _service(tmp_path)
    service._commit(service.session(), event="seed", status="compiled",
                    context={"compiled": {"plan_hash": "x"}})
    result = service.dispatch("propose_ir", {"ir": _with_requirement(scenario_b_ir())})
    assert result["ok"]
    assert result["kind"] == "composed" and result["ir_hash"]
    assert result["requirements"] == ["R1"]
    session = service.session()
    assert session.status == "draft"
    assert session.ir["kind"] == "composed"
    assert session.context["requirements"] == ["R1"]
    assert session.context["compiled"] is None
    assert session.context["verification"] is None


def test_an_invalid_proposal_leaves_the_session_untouched(tmp_path):
    service = _service(tmp_path)
    bad = service.dispatch("propose_ir",
                           {"ir": {"schema_version": "0.5", "kind": "composed"}})
    assert bad["ok"] is False and "invalid_ir" in bad["error"]
    assert service.session().revision == 0
    assert service.session().ir is None


def test_patch_ir_merges_top_level_and_invalidates_the_compile(tmp_path):
    service = _service(tmp_path)
    service.dispatch("propose_ir", {"ir": _with_requirement(scenario_b_ir())})
    service._commit(service.session(), event="seed", status="compiled",
                    context={"compiled": {"plan_hash": "x"}})
    result = service.dispatch("patch_ir", {"values": {"terminal": {
        "score_reaches": 8, "max_actor_actions": 20}}})
    assert result["ok"]
    session = service.session()
    assert session.ir["terminal"]["score_reaches"] == 8
    assert session.status == "draft"
    assert session.context["compiled"] is None
    assert session.context["requirements"] == ["R1"]        # not dropped


def test_patch_ir_can_target_a_dotted_path(tmp_path):
    service = _service(tmp_path)
    service.dispatch("propose_ir", {"ir": scenario_b_ir()})
    result = service.dispatch("patch_ir", {"path": "actions.0",
                                           "values": {"trigger": []}})
    assert result["ok"], result
    assert service.session().ir["actions"][0]["trigger"] == []
    deeper = service.dispatch("patch_ir", {"path": "actions.0.inputs.0",
                                           "values": {"min_count": 1, "max_count": 1}})
    assert deeper["ok"], deeper
    assert service.session().ir["actions"][0]["inputs"][0]["min_count"] == 1


def test_patch_ir_refuses_to_drop_a_requirement_unless_explicit(tmp_path):
    service = _service(tmp_path)
    service.dispatch("propose_ir", {"ir": _with_requirement(scenario_b_ir())})
    refused = service.dispatch("patch_ir", {"values": {"requirements": []}})
    assert refused["ok"] is False
    assert refused["error"] == "requirement_removed:R1"
    assert service.session().ir["requirements"][0]["id"] == "R1"

    allowed = service.dispatch("patch_ir", {"values": {"requirements": []},
                                            "allow_requirement_removal": True})
    assert allowed["ok"]
    assert service.session().ir["requirements"] == []
    assert allowed["removed"] == ["R1"]


def test_patch_ir_requires_a_proposal_and_rejects_invalid_values(tmp_path):
    service = _service(tmp_path)
    assert service.dispatch("patch_ir", {"values": {"players": {"count": 2}}})[
        "error"] == "propose_ir_first"
    service.dispatch("propose_ir", {"ir": scenario_b_ir()})
    before = service.session().ir
    invalid = service.dispatch("patch_ir", {"values": {"players": {"count": 9}}})
    assert invalid["ok"] is False and "invalid_ir" in invalid["error"]
    assert service.session().ir == before


def test_patch_ir_rejects_a_missing_dotted_path(tmp_path):
    service = _service(tmp_path)
    service.dispatch("propose_ir", {"ir": scenario_b_ir()})
    result = service.dispatch("patch_ir", {"path": "actions.99", "values": {"id": "x"}})
    assert result["ok"] is False and "patch_path_not_found" in result["error"]
