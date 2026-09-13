"""M3 review fixes (follow-up to the scenario B package).

Four defects found in review of the scenario B commits, each pinned here so it
cannot regress:

1. the ``suit_scoring`` monitor must check the *awarded seat*, not just the total;
2. it must not skip a rule that declares several ``score_top`` effects;
3. two empty zone tops must not compare equal (``None == None``);
4. a guarded turn with no round bound must be rejected, not spin to
   ``flow_step_limit`` at setup.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError
from test_g2_m3_guard_top import _guard, _started
from test_g2_m3_scenario_b import mutate, scenario_b_ir

from pocker_agent.core import compile_composed, contract_check, core_registry
from pocker_agent.core.ir import parse_design_ir
from pocker_agent.core.playtest import boundary_first


def _check(report, name):
    return next(check for check in report.checks if check.name == name)


def test_score_top_monitor_checks_the_awarded_seat():
    ir = parse_design_ir(scenario_b_ir())
    compiled = compile_composed(ir, core_registry())

    def edit(nodes):
        for node in nodes.values():
            if node.get("kind") == "call" and node["action"]["tool"] == "score_settle":
                node["action"]["args"]["winners"] = [1]

    report = contract_check(ir, mutate(compiled.plan, edit), core_registry(),
                            [boundary_first], (0,))
    assert report.ok is False
    assert _check(report, "suit_scoring").ok is False


def test_multiple_score_top_effects_are_all_checked():
    payload = scenario_b_ir()
    payload["actions"][0]["effects"].append(
        {"kind": "score_top", "zone": "discard", "by": "suit",
         "points": {"H": 5, "S": 4}})
    ir = parse_design_ir(payload)
    compiled = compile_composed(ir, core_registry())

    valid = contract_check(ir, compiled.plan, core_registry(), [boundary_first], (0,))
    assert _check(valid, "suit_scoring").ok is True       # checked, not skipped

    def edit(nodes):
        for node in nodes.values():
            if (node.get("kind") == "call" and node["action"]["tool"] == "score_settle"
                    and node["action"]["args"].get("points") == 5):
                node["action"]["args"]["points"] = 9

    report = contract_check(ir, mutate(compiled.plan, edit), core_registry(),
                            [boundary_first], (0,))
    assert report.ok is False
    assert _check(report, "suit_scoring").ok is False


def test_two_empty_zone_tops_do_not_compare_equal():
    payload = _guard({"op": "eq", "left": {"op": "top", "zone": "empty_a", "field": "rank"},
                      "right": {"op": "top", "zone": "empty_b", "field": "rank"}})
    payload["zones"] = payload["zones"] + [
        {"id": "empty_a", "visibility": "public", "scope": "shared"},
        {"id": "empty_b", "visibility": "public", "scope": "shared"}]
    interpreter = _started(payload)
    assert interpreter.state["zones"]["empty_a"]["cards"] == []
    assert interpreter.state["zones"]["empty_b"]["cards"] == []
    assert interpreter.legal_actions() == []              # false, not None == None


def test_a_guarded_turn_without_a_round_bound_requires_a_fallback():
    payload = _guard({"op": "lit", "value": False})
    payload["terminal"] = {"max_actor_actions": 5}
    with pytest.raises(ValidationError,
                       match="turn_sequence_needs_unconditional_action"):
        parse_design_ir(payload)
