"""Derive and resolve what a composed rule needs from the host.

Two layers, as required by the plan (section 6 / M2.2):

* :func:`derive_requirements` reads the typed mechanism nodes and lists the
  concrete host operations, feature tags, zones, variables and interaction
  inputs the rule depends on. It never guesses from an axis name.
* :func:`resolve_composition` checks every derived requirement against the live
  registry (the same one the interpreter enforces) and the capability matrix,
  and returns the missing ones with the IR path and requirement clause they
  belong to.

The distinction matters: "the ``rank_compare`` axis exists" is not the same as
"this rule's comparison parameters, zone read/write set and action inputs are all
implemented". Only the second is a resolvable composition.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..capability import capability_check
from ..contracts import FEATURE_TAGS
from .composed import (
    AssignEffect,
    CompareEffect,
    ComposedRulesIR,
    MoveSelectionEffect,
    MoveTopEffect,
    RefillEffect,
    RemovePairsEffect,
    SelectEffect,
)
from .expr import refs_in

# Interaction input kinds the host can currently compile and validate.
SUPPORTED_INPUT_KINDS = frozenset({"card_selection"})

# Operations the compiler always emits around a composed game's turn loop.
_ALWAYS_OPERATIONS = ("deck.deal", "logic.evaluate", "score_settle.call",
                      "state.update", "winner_resolve.call")


@dataclass(frozen=True)
class Requirement:
    code: str          # operation | feature | axis | zone | variable | input
    detail: str
    path: str
    clause: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail, "path": self.path,
                "clause": self.clause}


@dataclass
class CompositionReport:
    ok: bool
    axes: tuple[str, ...]
    operations: tuple[str, ...]
    features: tuple[str, ...]
    requirements: tuple[Requirement, ...] = field(default_factory=tuple)
    missing: tuple[Requirement, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "axes": list(self.axes),
                "operations": list(self.operations), "features": list(self.features),
                "requirements": [item.as_dict() for item in self.requirements],
                "missing": [item.as_dict() for item in self.missing]}


def clause_index(ir: ComposedRulesIR) -> list[tuple[str, str]]:
    """``(ir_path, clause_id)`` pairs, longest path first for prefix matching."""
    pairs: list[tuple[str, str]] = []
    for clause in ir.requirements:
        for path in clause.nodes:
            pairs.append((path, clause.id))
    pairs.sort(key=lambda item: (-len(item[0]), item[0]))
    return pairs


def clause_for(ir: ComposedRulesIR, path: str) -> str:
    for prefix, clause in clause_index(ir):
        if path == prefix or path.startswith(prefix + "."):
            return clause
    return "unmapped"


def _clause(ir: ComposedRulesIR, path: str) -> str:
    return clause_for(ir, path)


def _expr_requirements(ir: ComposedRulesIR, expr: Any, path: str,
                       out: list[Requirement]) -> None:
    for ref in refs_in(expr):
        if ref.startswith("variables."):
            out.append(Requirement("variable", ref[len("variables."):], path,
                                   _clause(ir, f"variables.{ref[len('variables.'):]}")))
        elif ref == "scores" or ref.startswith("scores."):
            out.append(Requirement("variable", "scores", path, _clause(ir, "variables.scores")))
        elif ref.startswith("input."):
            out.append(Requirement("input", ref[len("input."):], path, _clause(ir, path)))


def derive_requirements(ir: ComposedRulesIR) -> tuple[Requirement, ...]:
    """Every host-facing dependency of a composed rule, keyed to an IR path."""
    found: list[Requirement] = []
    for operation in _ALWAYS_OPERATIONS:
        found.append(Requirement("operation", operation, "plan", _clause(ir, "terminal")))

    for zone in ir.zones:
        found.append(Requirement("zone", zone.id, f"zones.{zone.id}", _clause(ir, f"zones.{zone.id}")))
    for variable in ir.variables:
        found.append(Requirement("variable", variable.name, f"variables.{variable.name}",
                                 _clause(ir, f"variables.{variable.name}")))

    for action in ir.actions:
        base = f"actions.{action.id}"
        if action.guard is not None:
            _expr_requirements(ir, action.guard, f"{base}.guard", found)
        for item in action.inputs:
            if item.kind not in SUPPORTED_INPUT_KINDS:
                found.append(Requirement("input", item.kind, f"{base}.inputs.{item.id}",
                                         _clause(ir, base)))
            found.append(Requirement("zone", item.zone, f"{base}.inputs.{item.id}",
                                     _clause(ir, base)))
        _effect_requirements(ir, action.effects, base, found)

    _effect_requirements(ir, ir.flow.resolve, "flow.resolve", found)

    for rule in ir.scoring:
        found.append(Requirement("operation", "score_settle.call", f"scoring.{rule.id}",
                                 _clause(ir, f"scoring.{rule.id}")))

    # Deterministic order, de-duplicated: the same requirement derived twice is
    # one dependency, and a compiler must not reorder dependencies per run.
    unique: dict[tuple[str, str, str, str], Requirement] = {}
    for item in found:
        unique.setdefault((item.code, item.detail, item.path, item.clause), item)
    return tuple(unique.values())


def _effect_requirements(ir: ComposedRulesIR, effects: Any, base: str,
                         out: list[Requirement]) -> None:
    for index, effect in enumerate(effects):
        path = f"{base}.{index}"
        clause = _clause(ir, path)
        if isinstance(effect, SelectEffect):
            out.append(Requirement("operation", "zones.select", path, clause))
            out.append(Requirement("input", effect.input, path, clause))
        elif isinstance(effect, MoveSelectionEffect):
            out.append(Requirement("operation", "zones.move", path, clause))
        elif isinstance(effect, MoveTopEffect):
            out.append(Requirement("operation", "zones.top", path, clause))
            out.append(Requirement("operation", "zones.move", path, clause))
        elif isinstance(effect, CompareEffect):
            out.append(Requirement("operation", "rank_compare.call", path, clause))
            for rule in effect.rules:
                out.append(Requirement("operation", "score_settle.call", path,
                                       _clause(ir, f"scoring.{rule}")))
        elif isinstance(effect, AssignEffect):
            _expr_requirements(ir, effect.value, path, out)
        elif isinstance(effect, RemovePairsEffect):
            out.append(Requirement("operation", "zones.select_duplicates", path, clause))
            out.append(Requirement("operation", "zones.move", path, clause))
            out.append(Requirement("operation", "score_settle.call", path, clause))
        elif isinstance(effect, RefillEffect):
            out.append(Requirement("operation", "zones.count_zone", path, clause))
            out.append(Requirement("operation", "zones.top", path, clause))
            out.append(Requirement("operation", "zones.move", path, clause))


def axes_for(ir: ComposedRulesIR) -> tuple[str, ...]:
    """The capability axes a composed rule depends on (sorted, de-duplicated).

    Zones/finite selection are checked as *operations* (see
    :func:`derive_requirements`), not promoted to an axis claim: there is no
    ``zones`` axis, and mapping ``zones.select`` onto ``pattern_lang`` would be
    the exact over-claim M0 removed (a label standing in for a mechanism).
    """
    axes = {"sequential_turn", "score_settle"}
    if any(isinstance(effect, CompareEffect) for effect in ir.flow.resolve):
        axes.add("rank_compare")
    if any(zone.visibility in {"owner_only", "hidden"} for zone in ir.zones):
        axes.add("info_set")
    return tuple(sorted(axes))


def features_for(ir: ComposedRulesIR) -> tuple[str, ...]:
    features = {"multi_zone", "card_identity", "explicit_order"}
    if any(isinstance(effect, CompareEffect) for effect in ir.flow.resolve):
        features.add("scoring")
    if any(isinstance(effect, RemovePairsEffect)
           for action in ir.actions for effect in action.effects):
        features.add("scoring")
    return tuple(sorted(features))


def _default_catalog() -> Any:
    # Imported lazily so ``core/rules`` does not create an import cycle with
    # ``core/__init__`` while the package is still initialising.
    from ..registry import core_registry
    return core_registry()


def resolve_composition(ir: ComposedRulesIR, catalog: Any = None) -> CompositionReport:
    """Check derived requirements against the registry and capability matrix."""
    registry = catalog if catalog is not None else _default_catalog()
    requirements = derive_requirements(ir)
    operations = tuple(sorted({item.detail for item in requirements
                               if item.code == "operation"}))
    features = features_for(ir)
    axes = axes_for(ir)

    missing: list[Requirement] = []
    names = set(registry.names())
    provided: set[str] = set()
    for item in requirements:
        if item.code != "operation":
            continue
        tool_name, _, operation = item.detail.partition(".")
        if tool_name not in names:
            missing.append(item)
            continue
        spec = registry.spec(tool_name)
        operations_by_name = {op.name: op for op in spec.operations}
        if operation and operation not in operations_by_name:
            missing.append(item)
            continue
        chosen = [operations_by_name[operation]] if operation else list(spec.operations)
        for op in chosen:
            provided |= set(op.feature_constraints)
    # A required feature must be *provided* by a used operation, not merely exist
    # in the vocabulary. This is the difference between a label and a mechanism.
    for feature in features:
        if feature not in FEATURE_TAGS or feature not in provided:
            missing.append(Requirement("feature", feature, "plan", _clause(ir, "plan")))

    capability = capability_check(axes)
    for gap in capability.missing:
        missing.append(Requirement("axis", gap["axis"], "plan", _clause(ir, "plan")))

    return CompositionReport(ok=not missing, axes=axes, operations=operations,
                             features=features, requirements=requirements,
                             missing=tuple(missing))
