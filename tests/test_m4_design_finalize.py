"""M4-6: finalize is a host-gated freeze, and never a registration.

``finalize`` only builds a *candidate* artifact from evidence that is bound to
the current IR and plan. It does not write to ``SessionStore`` and it is not a
user confirmation: a runnable version still requires ``publish_composed`` plus a
separate confirmation (ADR-0008). ``ask_user`` and ``unsupported`` are the
honest exits when a requirement cannot be met.
"""
from __future__ import annotations

import copy

from test_g2_m2 import scenario_a_ir
from test_g2_m3_scenario_b import SCENARIO_B_SEEDS, scenario_b_ir

from pocker_agent.agent.design_service import DesignService
from pocker_agent.agent.design_store import DesignStore
from pocker_agent.core import SessionStore


def _service(tmp_path, ir: dict, **kwargs) -> tuple[DesignService, DesignStore]:
    store = DesignStore(tmp_path / "design.db")
    session = store.create("design-only", "描述")
    service = DesignService(store, session.session_id, **kwargs)
    service.dispatch("propose_ir", {"ir": ir})
    return service, store


def test_finalize_requires_a_passing_verification(tmp_path):
    service, _ = _service(tmp_path, scenario_a_ir())
    assert service.dispatch("finalize", {})["error"] == "verification_required"
    service.dispatch("compose_plan", {})
    assert service.dispatch("finalize", {})["error"] == "verification_required"


def test_finalize_refuses_after_a_failed_verification(tmp_path):
    service, _ = _service(tmp_path, scenario_b_ir())
    service.dispatch("verify_game", {})                     # fails with default seeds
    assert service.dispatch("finalize", {})["error"] == "verification_failed"


def test_finalize_builds_a_candidate_but_never_registers(tmp_path):
    service, store = _service(tmp_path, scenario_a_ir())
    service.dispatch("verify_game", {})
    result = service.dispatch("finalize", {})
    assert result["ok"] and result["finalized"] is True
    assert result["registered"] is False
    artifact = result["artifact"]
    session = service.session()
    assert session.status == "awaiting_confirmation"
    assert session.context["artifact"] == artifact
    assert artifact["plan_hash"] == session.context["compiled"]["plan_hash"]
    assert artifact["verification_id"] == session.context["verification"]["verification_id"]
    assert artifact["title"] == "三轮公开比较积分"

    # The design store has no registration path: no runnable version exists yet.
    sessions = SessionStore(tmp_path / "design.db")
    assert sessions.list_versions("design-only") == []
    assert store.get(session.session_id).status == "awaiting_confirmation"


def test_finalize_refuses_evidence_that_does_not_bind_the_current_rules(tmp_path):
    service, store = _service(tmp_path, scenario_a_ir())
    service.dispatch("verify_game", {})
    session = service.session()
    changed = copy.deepcopy(session.ir)
    changed["execution"]["rules"]["terminal"]["max_rounds"] = 2
    store.commit(session.session_id, session.revision, ir=changed)   # keeps old evidence
    stale = service.dispatch("finalize", {})
    assert stale["ok"] is False
    assert stale["error"].startswith("verification_stale")


def test_ask_user_is_a_persisted_question(tmp_path):
    service, _ = _service(tmp_path, scenario_a_ir())
    result = service.dispatch("ask_user", {"question": "每轮几张牌？", "missing": ["card_count"]})
    assert result["ok"] and result["kind"] == "question"
    assert result["missing"] == ["card_count"]
    assert service.session().context["questions"] == ["每轮几张牌？"]
    assert service.dispatch("ask_user", {"question": "  "})["error"] == "question_required"


def test_unsupported_marks_the_design_failed(tmp_path):
    service, _ = _service(tmp_path, scenario_a_ir())
    result = service.dispatch("unsupported", {"message": "需要同时行动", "missing": ["simultaneous"]})
    assert result["ok"] and result["kind"] == "unsupported"
    session = service.session()
    assert session.status == "failed"
    assert session.context["failure"]["missing"] == ["simultaneous"]
    assert service.dispatch("unsupported", {"message": ""})["error"] == "message_required"


def test_the_candidate_can_be_published_only_through_the_host_service(tmp_path):
    service, _ = _service(tmp_path, scenario_b_ir(), seeds=SCENARIO_B_SEEDS)
    service.dispatch("verify_game", {})
    candidate = service.dispatch("finalize", {})["artifact"]
    sessions = SessionStore(tmp_path / "design.db")
    assert sessions.list_versions("design-only") == []
    artifact = sessions.verify_and_register(scenario_b_ir(), game_id="design-only", version=1,
                                            title=candidate["title"],
                                            seeds=SCENARIO_B_SEEDS)
    assert artifact.plan_hash == candidate["plan_hash"]
