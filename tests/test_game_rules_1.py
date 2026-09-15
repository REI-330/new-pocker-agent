"""GameRules 1.0 的解析、兼容导入、版本绑定和发布门禁。"""
from __future__ import annotations

import copy

import pytest

from pocker_agent.core import (
    compile_rules,
    core_registry,
    normalize_rules,
    publish_rules,
    rules_fingerprint,
    verify_rules,
)
from pocker_agent.core.contracts import ToolError

WAR = {"kind": "war", "game_id": "three-round-compare", "title": "三轮公开比大小",
       "description": "两名玩家每轮各公开一张牌，牌点大者得一分。", "max_rounds": 3}


def test_legacy_rules_are_immediately_normalized_and_compile_through_one_entry():
    rules = normalize_rules(WAR)
    assert rules.schema_version == "1.0"
    assert rules.kind == "game_rules"
    assert rules.compatibility_import is True
    assert rules.execution.profile == "war-0.4"
    assert {item.name for item in rules.mechanisms} == {
        item.name for item in compile_rules(rules).plan.tools}


def test_mechanism_version_is_part_of_the_executable_contract():
    payload = normalize_rules(WAR).model_dump(mode="json")
    payload["mechanisms"][0]["version"] = "9.9.9"
    with pytest.raises(ToolError, match="mechanism_version_mismatch"):
        compile_rules(payload, core_registry())


def test_canonical_state_and_participants_cannot_be_decorative_shells():
    payload = normalize_rules(WAR).model_dump(mode="json")
    payload["participants"]["count"] = 3
    with pytest.raises(ToolError, match="canonical_declaration_mismatch:participants"):
        compile_rules(payload)


def test_mechanism_configuration_cannot_be_a_decorative_shell():
    payload = normalize_rules(WAR).model_dump(mode="json")
    deck = next(item for item in payload["mechanisms"] if item["name"] == "deck")
    deck["config"]["copies"] = 9
    with pytest.raises(ToolError, match="canonical_declaration_mismatch:mechanisms"):
        compile_rules(payload)


def test_rules_hash_changes_when_any_executable_rule_changes():
    original = normalize_rules(WAR)
    changed = copy.deepcopy(original.model_dump(mode="json"))
    changed["execution"]["rules"]["max_rounds"] = 2
    assert rules_fingerprint(original) != rules_fingerprint(normalize_rules(changed))


def test_verification_and_approval_bind_the_same_rules_document():
    rules = normalize_rules(WAR)
    compiled, verification = verify_rules(rules, seeds=(0, 1))
    assert verification.ok
    assert verification.ir_hash == compiled.rules_hash
    with pytest.raises(ValueError, match="approval_mismatch"):
        publish_rules(rules, {"rules_hash": "0" * 16, "version": 1}, seeds=(0, 1))
    _, _, artifact = publish_rules(
        rules, {"rules_hash": compiled.rules_hash, "version": 1}, seeds=(0, 1))
    assert artifact.ir_hash == compiled.rules_hash
    assert artifact.approval_ir_hash == compiled.rules_hash
