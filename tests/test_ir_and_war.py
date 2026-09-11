"""RulesIR -> host_compile -> playable second family (rank duel)."""
from __future__ import annotations

import pytest

from pocker_agent.core import Interpreter, check_ir, core_registry, host_compile, parse_ir, playtest, required_axes
from pocker_agent.core.ir import ArithmeticIR, WarIR


def war_ir(**overrides) -> dict:
    payload = {"kind": "war", "game_id": "my-war", "title": "我的比大小", "max_rounds": 4, "players": 2}
    payload.update(overrides)
    return payload


def test_parse_and_compile_war_ir():
    ir = parse_ir(war_ir())
    assert isinstance(ir, WarIR)
    assert required_axes(ir) == ["sequential_turn", "rank_compare", "score_settle"]
    assert check_ir(ir).expressible is True
    plan = host_compile(ir)
    assert plan.game_kind == "war" and plan.players == 2


def test_parse_and_compile_arithmetic_ir():
    ir = parse_ir({"kind": "arithmetic", "game_id": "a24", "title": "24点", "max_rounds": 2,
                   "target": 24})
    assert isinstance(ir, ArithmeticIR)
    assert check_ir(ir).expressible is True
    assert host_compile(ir).game_kind == "arithmetic"


def test_ir_is_strict_and_validated():
    with pytest.raises(ValueError):
        parse_ir(war_ir(bogus=1))
    with pytest.raises(ValueError):
        parse_ir(war_ir(max_rounds=0))
    with pytest.raises(ValueError):
        parse_ir({"kind": "arithmetic", "game_id": "a", "title": "t", "max_rounds": 1,
                  "target": 24, "rank_values": {"A": 1}, "deck_ranks": ["A", "2"]})


def test_war_plan_completes_and_awards_points():
    plan = host_compile(parse_ir(war_ir(max_rounds=4)))
    report = playtest(plan, core_registry(), lambda interpreter: ("play", {}), seeds=(0, 7))
    assert report.ok, report.failures

    interpreter = Interpreter(plan, core_registry(), seed=3)
    interpreter.setup()
    for _ in range(50):
        if interpreter.state["finished"]:
            break
        interpreter.step("play")
    assert interpreter.state["finished"] is True
    assert interpreter.state["round"] == 4
    assert len([event for event in interpreter.events if event.get("operation") == "deal"]) == 4
    assert sum(interpreter.state["scores"]) <= 4
    assert interpreter.state["winners"]


def test_war_rounds_use_different_deals():
    plan = host_compile(parse_ir(war_ir(max_rounds=3)))
    interpreter = Interpreter(plan, core_registry(), seed=5)
    interpreter.setup()
    first = [card.id for card in interpreter.state["hands"][0]]
    interpreter.step("play")
    second = [card.id for card in interpreter.state["hands"][0]]
    assert first != second, "the round must be part of the deal seed"
