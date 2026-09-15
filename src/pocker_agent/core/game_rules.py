"""GameRules 1.0：规则的语言无关边界与唯一正式入口。

JSON 文档保存用户确认的规则、状态可见性、机制版本和执行预算；Python
模型只负责实现同一份约束。旧 0.4 IR 和 ComposedRulesIR 只能经
``normalize_rules`` 导入，解释器仍只执行编译后的 ``GamePlan``。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .artifacts import GameArtifact, VerificationResult, build_artifact
from .contracts import ToolError, ToolRegistry
from .ir import ComposedRulesIR, host_compile, parse_design_ir
from .plan import GamePlan, plan_fingerprint
from .registry import core_registry
from .rules import CompileError, compile_composed
from .rules.requirements import derive_requirements


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuleMeta(_Strict):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=32)


class ParticipantSpec(_Strict):
    count: int = Field(ge=1, le=16)
    roles: list[str] = Field(default_factory=list, max_length=16)
    teams: list[list[int]] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def valid_teams(self) -> ParticipantSpec:
        if self.roles and len(self.roles) != self.count:
            raise ValueError("participant_roles_must_match_count")
        if self.teams:
            flat = [seat for team in self.teams for seat in team]
            if any(not team for team in self.teams) or sorted(flat) != list(range(self.count)):
                raise ValueError("participant_teams_must_partition_seats")
        return self


class StateFieldSpec(_Strict):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    value_type: Literal["any", "integer", "number", "boolean", "string", "card", "cards",
                        "array", "object"] = "any"
    scope: Literal["global", "player", "team"] = "global"
    visibility: Literal["public", "owner", "team", "host"] = "host"
    initial: Any = None


class ComponentSpec(_Strict):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    kind: Literal["card_deck", "object_pool", "board", "resource", "zone"]
    config: dict[str, Any] = Field(default_factory=dict)


class MechanismBinding(_Strict):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    config: dict[str, Any] = Field(default_factory=dict)


class ExecutionSpec(_Strict):
    """确定性编译配置；旧 profile 只用于兼容导入。"""

    profile: Literal[
        "composed-1.0", "arithmetic-0.4", "war-0.4", "shedding-0.4", "whist-0.4",
        "poker-0.4", "blackjack-0.4", "go_fish-0.4", "uno-0.4"
    ]
    rules: dict[str, Any]


class ExecutionBudget(_Strict):
    step_limit: int = Field(default=2048, ge=1, le=100_000)


class GameRules(_Strict):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["game_rules"] = "game_rules"
    game_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    rules_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    meta: RuleMeta
    participants: ParticipantSpec
    components: list[ComponentSpec] = Field(default_factory=list, max_length=64)
    state: list[StateFieldSpec] = Field(default_factory=list, max_length=128)
    mechanisms: list[MechanismBinding] = Field(min_length=1, max_length=64)
    execution: ExecutionSpec
    budget: ExecutionBudget = Field(default_factory=ExecutionBudget)
    compatibility_import: bool = False

    @model_validator(mode="after")
    def unique_names(self) -> GameRules:
        for label, names in (
            ("component", [item.id for item in self.components]),
            ("state", [item.name for item in self.state]),
            ("mechanism", [item.name for item in self.mechanisms]),
        ):
            if len(names) != len(set(names)):
                raise ValueError(f"duplicate_{label}_name")
        if self.execution.profile.endswith("-0.4") and not self.compatibility_import:
            raise ValueError("legacy_profile_requires_compatibility_import")
        source_kind = self.execution.rules.get("kind")
        source_version = self.execution.rules.get("schema_version")
        if self.execution.profile == "composed-1.0":
            if source_kind != "composed" or source_version != "0.5":
                raise ValueError("execution_profile_rules_mismatch")
            if self.compatibility_import:
                raise ValueError("composed_profile_is_not_compatibility_import")
        else:
            expected_kind = self.execution.profile.removesuffix("-0.4")
            if source_kind != expected_kind or source_version != "0.4":
                raise ValueError("execution_profile_rules_mismatch")
        source_game_id = self.execution.rules.get("game_id")
        if source_game_id is not None and source_game_id != self.game_id:
            raise ValueError("execution_rules_game_id_mismatch")
        return self


class RuleApproval(_Strict):
    rules_hash: str = Field(pattern=r"^[0-9a-f]{16}$")
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=120)


@dataclass(frozen=True)
class CompiledGameRules:
    rules: GameRules
    plan: GamePlan
    rules_hash: str
    plan_hash: str
    registry_contract_hash: str
    source_map: dict[str, Any]
    composition: dict[str, Any] | None

    @property
    def ir_hash(self) -> str:
        """兼容只读名称；发布凭据实际绑定完整 GameRules 哈希。"""
        return self.rules_hash

    @property
    def compiler_version(self) -> str:
        return "game-rules-1.0"


def rules_fingerprint(rules: GameRules) -> str:
    canonical = json.dumps(rules.model_dump(mode="json"), sort_keys=True,
                           ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def bind_rules_game_id(rules: GameRules, game_id: str) -> GameRules:
    """把外层身份和兼容源规则身份作为一次原子变更绑定。"""
    payload = rules.model_dump(mode="json")
    payload["game_id"] = game_id
    source = payload["execution"]["rules"]
    if "game_id" in source:
        source["game_id"] = game_id
    return GameRules.model_validate(payload)


def _title(ir: Any) -> tuple[str, str]:
    if isinstance(ir, ComposedRulesIR):
        return ir.meta.title, ir.meta.description
    return ir.title, ir.description


def _components(ir: Any) -> list[ComponentSpec]:
    result: list[ComponentSpec] = []
    deck = getattr(ir, "deck", None)
    ranks = list(deck.ranks) if deck is not None else list(getattr(ir, "ranks", [])
                                                          or getattr(ir, "deck_ranks", []))
    suits = list(deck.suits) if deck is not None else list(getattr(ir, "suits", [])
                                                          or getattr(ir, "deck_suits", []))
    if ranks and suits:
        config: dict[str, Any] = {"ranks": ranks, "suits": suits,
                                  "copies": int(getattr(deck, "copies", 1))}
        values = getattr(deck, "values", None) or getattr(ir, "rank_values", None)
        if values:
            config["values"] = dict(values)
        result.append(ComponentSpec(id="deck", kind="card_deck", config=config))
    if isinstance(ir, ComposedRulesIR):
        result.extend(ComponentSpec(id=zone.id, kind="zone", config={
            "scope": zone.scope, "visibility": zone.visibility}) for zone in ir.zones)
    if hasattr(ir, "stacks"):
        result.append(ComponentSpec(id="chips", kind="resource",
                                    config={"initial_per_player": ir.stacks}))
    return result


def _state(ir: Any) -> list[StateFieldSpec]:
    result = [
        StateFieldSpec(name="current_player", value_type="integer", visibility="public"),
        StateFieldSpec(name="finished", value_type="boolean", visibility="public", initial=False),
        StateFieldSpec(name="winners", value_type="array", visibility="public", initial=[]),
    ]
    if isinstance(ir, ComposedRulesIR):
        visibility = {"public": "public", "owner_only": "owner", "hidden": "host"}
        result.extend(StateFieldSpec(name=zone.id, value_type="cards",
                                     scope="player" if zone.scope == "player" else "global",
                                     visibility=visibility[zone.visibility]) for zone in ir.zones)
        result.extend(StateFieldSpec(name=item.name,
                                     value_type="array" if item.type == "integer_list" else item.type,
                                     scope="global", visibility=item.visibility, initial=item.initial)
                      for item in ir.variables)
    elif getattr(ir, "kind", "") != "arithmetic":
        result.append(StateFieldSpec(name="hands", value_type="cards", scope="player",
                                     visibility="owner"))
    if getattr(ir, "kind", "") == "arithmetic":
        result.extend([StateFieldSpec(name="numbers", value_type="array", visibility="public"),
                       StateFieldSpec(name="target", value_type="integer", visibility="public")])
    if hasattr(ir, "stacks"):
        result.extend([StateFieldSpec(name="stacks", value_type="array", visibility="public"),
                       StateFieldSpec(name="pot", value_type="integer", visibility="public")])
    return result


def normalize_rules(payload: GameRules | dict[str, Any],
                    registry: ToolRegistry | None = None) -> GameRules:
    """把 0.4/0.5 输入立即归一为 GameRules 1.0。"""
    if isinstance(payload, GameRules):
        return payload
    if payload.get("kind") == "game_rules":
        return GameRules.model_validate(payload)
    registry = registry or core_registry()
    ir = parse_design_ir(payload)
    plan: GamePlan | None = None
    if isinstance(ir, ComposedRulesIR):
        try:
            plan = compile_composed(ir, registry).plan
        except CompileError:
            # 设计草案可以在结构合法但能力/降层仍失败时进入修复流程。
            plan = None
    else:
        plan = host_compile(ir)
    title, description = _title(ir)
    teams = list(getattr(ir, "teams", []))
    if plan is not None:
        bindings = [(binding.name, dict(binding.config)) for binding in plan.tools]
    else:
        names = {item.detail.split(".", 1)[0] for item in derive_requirements(ir)
                 if item.code == "operation"}
        deck_config = {"ranks": list(ir.deck.ranks), "suits": list(ir.deck.suits),
                       "copies": ir.deck.copies}
        if ir.deck.values:
            deck_config["values"] = dict(ir.deck.values)
        bindings = [(name, deck_config if name == "deck" else {}) for name in sorted(names)]
    mechanisms = [MechanismBinding(name=name, version=registry.spec(name).version, config=config)
                  for name, config in bindings]
    normalized = ir.model_dump(mode="json")
    if isinstance(ir, ComposedRulesIR):
        profile = "composed-1.0"
        compatibility = False
    else:
        profile = f"{ir.kind}-0.4"
        compatibility = True
    return GameRules(
        game_id=getattr(ir, "game_id", re.sub(r"[^a-z0-9_-]+", "-", title.lower()).strip("-")
                        or "imported-game"),
        rules_version="1.0.0", meta=RuleMeta(title=title, description=description),
        participants=ParticipantSpec(count=(ir.players.count if isinstance(ir, ComposedRulesIR)
                                            else ir.players), teams=teams),
        components=_components(ir), state=_state(ir), mechanisms=mechanisms,
        execution=ExecutionSpec(profile=profile, rules=normalized),
        budget=ExecutionBudget(step_limit=plan.step_limit if plan is not None else 2048),
        compatibility_import=compatibility,
    )


def parse_rules(payload: GameRules | dict[str, Any]) -> GameRules:
    return normalize_rules(payload)


def _source_ir(rules: GameRules) -> Any:
    payload = dict(rules.execution.rules)
    if rules.execution.profile == "composed-1.0":
        payload["schema_version"] = "0.5"
        payload["kind"] = "composed"
    return parse_design_ir(payload)


def source_ir(rules: GameRules | dict[str, Any]) -> Any:
    """返回兼容编译器读取的内部源规则；业务入口不应直接调用它。"""
    return _source_ir(parse_rules(rules))


def compile_rules(rules: GameRules | dict[str, Any],
                  registry: ToolRegistry | None = None) -> CompiledGameRules:
    registry = registry or core_registry()
    parsed = parse_rules(rules)
    for binding in parsed.mechanisms:
        spec = registry.spec(binding.name)
        if binding.version != spec.version:
            raise ToolError(f"mechanism_version_mismatch:{binding.name}:{binding.version}:{spec.version}")
    projected = normalize_rules(parsed.execution.rules, registry)
    for field in ("participants", "components", "state", "mechanisms"):
        if getattr(parsed, field) != getattr(projected, field):
            raise ToolError(f"canonical_declaration_mismatch:{field}")
    ir = _source_ir(parsed)
    lowered = compile_composed(ir, registry) if isinstance(ir, ComposedRulesIR) else None
    plan = lowered.plan if lowered is not None else host_compile(ir)
    declared = {(item.name, item.version, json.dumps(item.config, sort_keys=True))
                for item in parsed.mechanisms}
    actual = {(item.name, registry.spec(item.name).version,
               json.dumps(item.config, sort_keys=True)) for item in plan.tools}
    if declared != actual:
        raise ToolError("mechanism_bindings_do_not_match_compiled_plan")
    if plan.step_limit > parsed.budget.step_limit:
        raise ToolError("compiled_plan_exceeds_rule_budget")
    fingerprint = rules_fingerprint(parsed)
    source_map = dict(lowered.source_map) if lowered is not None else {}
    if "ir_hash" in source_map:
        source_map["source_ir_hash"] = source_map["ir_hash"]
    source_map["ir_hash"] = fingerprint
    return CompiledGameRules(
        parsed, plan, fingerprint, plan_fingerprint(plan), registry.contract_hash(),
        source_map,
        lowered.composition.as_dict() if lowered is not None else None,
    )


def verify_rules(rules: GameRules | dict[str, Any], registry: ToolRegistry | None = None,
                 **kwargs: Any) -> tuple[CompiledGameRules, VerificationResult]:
    from .verify.contract_check import contract_check
    from .verify.service import verify_plan

    registry = registry or core_registry()
    compiled = compile_rules(rules, registry)
    result = verify_plan(compiled.plan, registry, ir_hash=compiled.rules_hash,
                         compiler_version="game-rules-1.0", **kwargs)
    ir = _source_ir(compiled.rules)
    if isinstance(ir, ComposedRulesIR):
        strategies = kwargs.get("strategies")
        seeds = kwargs.get("seeds")
        if strategies is None or seeds is None:
            from .verify.service import VERIFICATION_SEEDS, VERIFICATION_STRATEGIES
            strategies = strategies or VERIFICATION_STRATEGIES
            seeds = seeds or VERIFICATION_SEEDS
        contract = contract_check(ir, compiled.plan, registry, strategies, seeds)
        result = replace(result, ok=result.ok and contract.ok,
                         failures=result.failures + tuple(contract.failures()),
                         contract=contract.as_dict())
    return compiled, result


def publish_rules(rules: GameRules | dict[str, Any], approval: RuleApproval | dict[str, Any],
                  registry: ToolRegistry | None = None, **kwargs: Any
                  ) -> tuple[CompiledGameRules, VerificationResult, GameArtifact]:
    registry = registry or core_registry()
    approved = approval if isinstance(approval, RuleApproval) else RuleApproval.model_validate(approval)
    compiled, verification = verify_rules(rules, registry, **kwargs)
    if approved.rules_hash != compiled.rules_hash:
        raise ValueError("approval_mismatch")
    if not verification.ok:
        raise ValueError("verification_failed")
    artifact = build_artifact(
        game_id=compiled.rules.game_id, version=approved.version,
        title=approved.title or compiled.rules.meta.title, plan=compiled.plan,
        verification=verification, generation_source="game_rules_1_0",
        ir=compiled.rules.model_dump(mode="json"), source_map=compiled.source_map,
        approval_ir_hash=approved.rules_hash,
    )
    return compiled, verification, artifact
