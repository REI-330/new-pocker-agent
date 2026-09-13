"""M4-2: capability/mechanism tools and the durable design context.

The capability and mechanism tools exist so the model can *ask the host* what it
can compile, instead of guessing from the prompt. They read the same capability
matrix and the same tool registry the interpreter enforces, so a description can
never drift from what a plan can run. The design ``context`` (chat, requirements,
compiled summary, verification, budget) is part of the session and is bounded.
"""
from __future__ import annotations

from pocker_agent.agent.design_service import (
    DESIGN_TOOL_NAMES,
    DESIGN_TOOL_SCHEMAS,
    DesignService,
)
from pocker_agent.agent.design_store import DesignStore
from pocker_agent.core import core_registry
from pocker_agent.core.capability import AXES


def _service(tmp_path, game_id: str = "g") -> tuple[DesignService, DesignStore]:
    store = DesignStore(tmp_path / "design.db")
    session = store.create(game_id, "描述")
    return DesignService(store, session.session_id), store


def test_design_context_round_trips_and_is_bounded(tmp_path):
    store = DesignStore(tmp_path / "design.db")
    session = store.create("g")
    chat = [{"role": "user", "content": str(index)} for index in range(120)]
    updated = store.commit(session.session_id, 0, context={"chat": chat, "compiled": None})
    assert len(updated.context["chat"]) == 60                 # most recent kept
    assert updated.context["chat"][-1]["content"] == "119"
    restored = DesignStore(tmp_path / "design.db").get(session.session_id)
    assert len(restored.context["chat"]) == 60
    assert restored.context["compiled"] is None


def test_context_update_merges_at_the_top_level(tmp_path):
    store = DesignStore(tmp_path / "design.db")
    session = store.create("g")
    store.commit(session.session_id, 0, context={"chat": [{"role": "user", "content": "a"}],
                                                 "used": {"decisions": 1}})
    merged = store.commit(session.session_id, 1, context={"questions": ["缺牌数"]})
    assert len(merged.context["chat"]) == 1
    assert merged.context["questions"] == ["缺牌数"]
    assert merged.context["used"] == {"decisions": 1}


def test_a_non_object_context_is_refused_without_writing(tmp_path):
    store = DesignStore(tmp_path / "design.db")
    session = store.create("g")
    try:
        store.commit(session.session_id, 0, context="not-an-object")
        raise AssertionError("expected design_context_must_be_object")
    except ValueError as error:
        assert "design_context_must_be_object" in str(error)
    assert store.get(session.session_id).revision == 0


def test_list_capabilities_reports_the_matrix_and_the_macro_gap(tmp_path):
    service, _ = _service(tmp_path)
    result = service.dispatch("list_capabilities", {})
    assert result["ok"] is True
    assert {axis["id"] for axis in result["axes"]} == {axis.id for axis in AXES}
    macro = next(axis for axis in result["axes"] if axis["id"] == "macro")
    assert macro["status"] == "planned" and macro["covered"] is False
    assert macro["note"]


def test_list_capabilities_filters_one_axis_and_rejects_an_unknown_one(tmp_path):
    service, _ = _service(tmp_path)
    one = service.dispatch("list_capabilities", {"axis": "sequential_turn"})
    assert one["ok"] and one["axis"]["id"] == "sequential_turn"
    assert one["axis"]["status"] == "stable"
    missing = service.dispatch("list_capabilities", {"axis": "no_such_axis"})
    assert missing["ok"] is False and "unknown_axis" in missing["error"]


def test_describe_mechanism_is_sourced_from_the_live_registry(tmp_path):
    service, _ = _service(tmp_path)
    listed = service.dispatch("describe_mechanism", {})
    assert listed["ok"]
    assert {item["name"] for item in listed["tools"]} == set(core_registry().names())

    described = service.dispatch("describe_mechanism", {"tool": "zones", "operation": "move"})
    assert described["ok"]
    operation = described["operation"]
    assert operation["name"] == "move"
    spec = next(item for item in core_registry().export() if item["name"] == "zones")
    live = next(op for op in spec["operations"] if op["name"] == "move")
    assert operation == live                                  # byte-for-byte same source


def test_describe_mechanism_rejects_unknown_tool_and_operation(tmp_path):
    service, _ = _service(tmp_path)
    assert service.dispatch("describe_mechanism", {"tool": "nope"})["ok"] is False
    unknown = service.dispatch("describe_mechanism", {"tool": "zones", "operation": "nope"})
    assert unknown["ok"] is False and "unknown_operation" in unknown["error"]
    by_op = service.dispatch("describe_mechanism", {"operation": "call"})
    assert by_op["ok"] and {item["tool"] for item in by_op["matches"]}


def test_read_only_tools_do_not_touch_the_session(tmp_path):
    service, _ = _service(tmp_path)
    session = service.session()
    service.dispatch("list_capabilities", {})
    service.dispatch("describe_mechanism", {"tool": "state"})
    assert service.session().revision == session.revision == 0


def test_the_design_service_ships_no_macro_tool_this_pass(tmp_path):
    """The ``macro`` axis stays ``planned`` until a tool can author macros."""
    assert not any("macro" in name for name in DESIGN_TOOL_NAMES)
    assert {schema["name"] for schema in DESIGN_TOOL_SCHEMAS} == set(DESIGN_TOOL_NAMES)
