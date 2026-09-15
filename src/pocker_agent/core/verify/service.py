"""Host-owned verification and the composed-rules publish service (ADR-0008).

A caller can no longer register a plan by handing over ``{ok: true}``: the host
runs the formal strategies itself, records the resulting
:class:`~pocker_agent.core.artifacts.VerificationResult`, and only that record can
authorise an artifact. :func:`publish_composed` is the ordered pipeline the M3
exit criterion names:

    normalised GameRules -> capability resolution -> compiled artifact
    -> dynamic verification -> required rule confirmation -> immutable artifact

Known-family plans are verified through :func:`verify_plan`; composed rules
through :func:`verify_composed`.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from ..artifacts import GameArtifact, VerificationResult
from ..contracts import ToolRegistry
from ..invariants import (
    never_finishes_without_winners_or_scores,
    non_negative_scores,
    view_is_safe,
    zone_conservation,
)
from ..ir import parse_design_ir
from ..plan import GamePlan, plan_fingerprint
from ..playtest import (
    Invariant,
    Strategy,
    boundary_first,
    goal_first,
    playtest,
    random_legal,
)
from ..registry import core_registry
from ..rules import CompiledRules, ComposedRulesIR, compile_composed
from .contract_check import contract_check

# The formal gate's policies and seeds are host decisions, per ADR-0008 decision 1:
# diagnosis may pick its own seed, the playable gate may not.
VERIFICATION_STRATEGIES: tuple[Strategy, ...] = (random_legal, boundary_first, goal_first)
VERIFICATION_SEEDS: tuple[int, ...] = (0, 1, 7, 23, 42)


def default_invariants() -> tuple[Invariant, ...]:
    """The typed invariants every formal run checks at each step and at the end."""
    return (zone_conservation(), non_negative_scores(),
            never_finishes_without_winners_or_scores(), view_is_safe())


def verify_plan(plan: GamePlan | dict[str, Any], registry: ToolRegistry, *,
                ir_hash: str | None = None, compiler_version: str | None = None,
                strategies: Sequence[Strategy] = VERIFICATION_STRATEGIES,
                seeds: Sequence[int] = VERIFICATION_SEEDS,
                invariants: Sequence[Invariant] = (),
                require_wait_coverage: bool = True) -> VerificationResult:
    """Run the host gate and return a credential bound to its exact inputs."""
    validated = plan if isinstance(plan, GamePlan) else GamePlan.model_validate(plan)
    strategies = tuple(strategies)
    seeds = tuple(int(seed) for seed in seeds)
    report = playtest(validated, registry, strategies, seeds=seeds,
                      invariants=(*default_invariants(), *invariants),
                      require_wait_coverage=require_wait_coverage)
    return VerificationResult.issue(
        plan_hash=plan_fingerprint(validated),
        registry_contract_hash=registry.contract_hash(), ok=report.ok,
        strategies=tuple(strategy.__name__ for strategy in strategies), seeds=seeds,
        ir_hash=ir_hash, compiler_version=compiler_version,
        covered_wait_nodes=tuple(report.covered_wait_nodes),
        failures=tuple(report.failures), checks=tuple(report.checks))


def verify_composed(ir: ComposedRulesIR | dict[str, Any], registry: ToolRegistry | None = None,
                    *, strategies: Sequence[Strategy] = VERIFICATION_STRATEGIES,
                    seeds: Sequence[int] = VERIFICATION_SEEDS,
                    invariants: Sequence[Invariant] = (),
                    require_wait_coverage: bool = True) -> tuple[CompiledRules, VerificationResult]:
    """Compile a composed IR (capability + structure checks) and formally verify it.

    ``compile_composed`` is where the normalised-IR, capability-resolution and
    control-flow checks run; this function only adds the dynamic gate. It returns
    both so a caller can record the credential or inspect the compiled plan.
    """
    registry = registry or core_registry()
    rules = ir if isinstance(ir, ComposedRulesIR) else parse_design_ir(ir)
    compiled = compile_composed(rules, registry)
    result = verify_plan(compiled.plan, registry, ir_hash=compiled.ir_hash,
                         compiler_version=compiled.compiler_version, strategies=strategies,
                         seeds=seeds, invariants=invariants,
                         require_wait_coverage=require_wait_coverage)
    # The dynamic gate is not enough on its own: an independent monitor re-derives
    # the declared clauses from the IR and checks the running product against them.
    contract = contract_check(rules, compiled.plan, registry, strategies, seeds)
    result = replace(result, ok=result.ok and contract.ok,
                     failures=result.failures + tuple(contract.failures()),
                     contract=contract.as_dict())
    return compiled, result


def publish_composed(ir: ComposedRulesIR | dict[str, Any], *, game_id: str, version: int,
                     title: str, registry: ToolRegistry | None = None,
                     approval_ir_hash: str | None = None,
                     strategies: Sequence[Strategy] = VERIFICATION_STRATEGIES,
                     seeds: Sequence[int] = VERIFICATION_SEEDS,
                     invariants: Sequence[Invariant] = (),
                     require_wait_coverage: bool = True) -> tuple[CompiledRules, VerificationResult,
                                                                   GameArtifact]:
    """兼容导入旧 ComposedRulesIR，并立即转入 GameRules 发布链路。

    旧 IR 本身不再是可发布身份。调用者必须确认归一化后的完整
    GameRules 哈希，避免旧入口产生第二套审批与产物语义。
    """
    from ..game_rules import bind_rules_game_id, normalize_rules, publish_rules

    if approval_ir_hash is None:
        raise ValueError("approval_required")
    registry = registry or core_registry()
    rules = bind_rules_game_id(normalize_rules(ir, registry), game_id)
    return publish_rules(
        rules,
        {"rules_hash": approval_ir_hash, "version": version, "title": title},
        registry=registry,
        strategies=strategies,
        seeds=seeds,
        invariants=invariants,
        require_wait_coverage=require_wait_coverage,
    )
