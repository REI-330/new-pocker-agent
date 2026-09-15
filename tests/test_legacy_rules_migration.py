"""八类 0.4 规则经 GameRules 导入后的确定性行为对照。"""
from __future__ import annotations

import pytest

from pocker_agent.core import compile_rules, core_registry, normalize_rules
from pocker_agent.core.ir import host_compile, parse_design_ir
from pocker_agent.core.plan import plan_fingerprint
from pocker_agent.core.playtest import playtest
from pocker_agent.core.policy import bot_action

CASES = [
    {"kind": "arithmetic", "game_id": "m-arithmetic", "title": "24点", "max_rounds": 2,
     "target": 24},
    {"kind": "war", "game_id": "m-war", "title": "比大小", "max_rounds": 3},
    {"kind": "shedding", "game_id": "m-shedding", "title": "出完牌"},
    {"kind": "whist", "game_id": "m-whist", "title": "惠斯特"},
    {"kind": "poker", "game_id": "m-poker", "title": "五张牌下注"},
    {"kind": "blackjack", "game_id": "m-blackjack", "title": "21点"},
    {"kind": "go_fish", "game_id": "m-go-fish", "title": "钓鱼"},
    {"kind": "uno", "game_id": "m-uno", "title": "UNO"},
]


@pytest.mark.parametrize("payload", CASES, ids=[item["kind"] for item in CASES])
def test_legacy_import_keeps_the_same_plan_and_fixed_seed_behavior(payload):
    old_plan = host_compile(parse_design_ir(payload))
    new_plan = compile_rules(normalize_rules(payload)).plan
    assert plan_fingerprint(new_plan) == plan_fingerprint(old_plan)
    old_report = playtest(old_plan, core_registry(), bot_action, seeds=(0, 7))
    new_report = playtest(new_plan, core_registry(), bot_action, seeds=(0, 7))
    assert new_report.ok == old_report.ok
    assert new_report.event_counts == old_report.event_counts
