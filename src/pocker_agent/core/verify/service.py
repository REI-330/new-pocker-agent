"""Host-owned verification and the composed-rules publish service (ADR-0008).

A caller can no longer register a plan by handing over ``{ok: true}``: the host
runs the formal strategies itself, records the resulting
:class:`~pocker_agent.core.artifacts.VerificationResult`, and only that record can
authorise an artifact. :func:`publish_composed` is the ordered pipeline the M3
exit criterion names:

    normalised IR -> capability resolution -> compiled artifact
    -> dynamic verification -> (optional) rule confirmation -> immutable artifact

Known-family plans are verified through :func:`verify_plan`; composed rules
through :func:`verify_composed`.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..artifacts import GameArtifact, VerificationResult, build_artifact
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
from ..rules import CompiledRules, ComposedRulesIR, compile_composed, normalized_ir

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
    return compiled, result


def publish_composed(ir: ComposedRulesIR | dict[str, Any], *, game_id: str, version: int,
                     title: str, registry: ToolRegistry | None = None,
                     approval_ir_hash: str | None = None,
                     strategies: Sequence[Strategy] = VERIFICATION_STRATEGIES,
                     seeds: Sequence[int] = VERIFICATION_SEEDS,
                     invariants: Sequence[Invariant] = (),
                     require_wait_coverage: bool = True) -> tuple[CompiledRules, VerificationResult,
                                                                   GameArtifact]:
    """IR -> capability -> compile -> verify -> immutable artifact.

    Raises ``ValueError("verification_failed")`` when the gate rejects the plan,
    and ``ValueError("approval_mismatch")`` when a user confirmation names a
    different ``ir_hash`` -- a design agent can never self-confirm. The
    credential is returned so the host can persist it before any registration.
    """
    registry = registry or core_registry()
    rules = ir if isinstance(ir, ComposedRulesIR) else parse_design_ir(ir)
    compiled, result = verify_composed(
        rules, registry, strategies=strategies, seeds=seeds, invariants=invariants,
        require_wait_coverage=require_wait_coverage)
    artifact = build_artifact(
        game_id=game_id, version=version, title=title, plan=compiled.plan,
        verification=result, generation_source="composed_rules", ir=normalized_ir(rules),
        source_map=compiled.source_map, approval_ir_hash=approval_ir_hash)
    return compiled, result, artifact
