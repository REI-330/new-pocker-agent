"""Immutable game artifacts and host-issued verification credentials (ADR-0008).

The M2 registration path trusted a caller-supplied ``{ok: true}`` report. This
module makes the evidence a first-class object: a :class:`VerificationResult`
binds a plan/IR/compiler/registry to the strategies and seeds the *host* ran,
and a :class:`GameArtifact` names the exact verified product by hash. A stale or
forged credential is mechanically distinguishable from a valid one because the
binding fields are content, not prose.

Nothing here consults a clock or a random source: ``verification_id`` is a pure
function of the bound fields, so the same host verification is reproducible from
the recorded data alone.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .plan import GamePlan, plan_fingerprint

ARTIFACT_SCHEMA_VERSION = "1"


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def verification_id(plan_hash: str, ir_hash: str | None, compiler_version: str | None,
                    registry_contract_hash: str, strategies: tuple[str, ...],
                    seeds: tuple[int, ...]) -> str:
    """A content identity for one host-run verification (never a timestamp).

    Two verifications of the same plan with the same mechanism versions, policies
    and seeds are the same evidence; changing any of them yields a new id, which
    is what makes ``verification_stale`` mechanical rather than a convention.
    """
    binding = {"plan_hash": plan_hash, "ir_hash": ir_hash,
               "compiler_version": compiler_version,
               "registry_contract_hash": registry_contract_hash,
               "strategies": list(strategies), "seeds": list(seeds)}
    return hashlib.sha256(_canonical(binding).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class VerificationResult:
    """The host's record that a specific plan, under specific policies, passed."""

    verification_id: str
    ok: bool
    plan_hash: str
    registry_contract_hash: str
    strategies: tuple[str, ...] = ()
    seeds: tuple[int, ...] = ()
    ir_hash: str | None = None
    compiler_version: str | None = None
    covered_wait_nodes: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()

    @classmethod
    def issue(cls, *, plan_hash: str, registry_contract_hash: str, ok: bool,
              strategies: tuple[str, ...], seeds: tuple[int, ...],
              ir_hash: str | None = None, compiler_version: str | None = None,
              covered_wait_nodes: tuple[str, ...] = (), failures: tuple[str, ...] = (),
              checks: tuple[str, ...] = ()) -> VerificationResult:
        strategies, seeds = tuple(strategies), tuple(int(seed) for seed in seeds)
        return cls(
            verification_id=verification_id(plan_hash, ir_hash, compiler_version,
                                            registry_contract_hash, strategies, seeds),
            ok=ok, plan_hash=plan_hash, registry_contract_hash=registry_contract_hash,
            strategies=strategies, seeds=seeds, ir_hash=ir_hash,
            compiler_version=compiler_version,
            covered_wait_nodes=tuple(covered_wait_nodes),
            failures=tuple(failures), checks=tuple(checks))

    def as_dict(self) -> dict[str, Any]:
        return {"verification_id": self.verification_id, "ok": self.ok,
                "plan_hash": self.plan_hash,
                "registry_contract_hash": self.registry_contract_hash,
                "strategies": list(self.strategies), "seeds": list(self.seeds),
                "ir_hash": self.ir_hash, "compiler_version": self.compiler_version,
                "covered_wait_nodes": list(self.covered_wait_nodes),
                "failures": list(self.failures), "checks": list(self.checks)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationResult:
        return cls(verification_id=data["verification_id"], ok=bool(data["ok"]),
                   plan_hash=data["plan_hash"],
                   registry_contract_hash=data["registry_contract_hash"],
                   strategies=tuple(data.get("strategies", ())),
                   seeds=tuple(data.get("seeds", ())), ir_hash=data.get("ir_hash"),
                   compiler_version=data.get("compiler_version"),
                   covered_wait_nodes=tuple(data.get("covered_wait_nodes", ())),
                   failures=tuple(data.get("failures", ())),
                   checks=tuple(data.get("checks", ())))


@dataclass(frozen=True)
class GameArtifact:
    """An immutable, verified product: the plan is fixed by hash, not by name."""

    game_id: str
    version: int
    title: str
    generation_source: str
    plan: dict[str, Any]
    plan_hash: str
    verification_id: str
    registry_contract_hash: str
    ir: dict[str, Any] | None = None
    ir_hash: str | None = None
    compiler_version: str | None = None
    source_map: dict[str, Any] | None = None
    approval_ir_hash: str | None = None
    schema_version: str = ARTIFACT_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "game_id": self.game_id,
                "version": self.version, "title": self.title,
                "generation_source": self.generation_source, "plan": self.plan,
                "plan_hash": self.plan_hash,
                "verification_id": self.verification_id,
                "registry_contract_hash": self.registry_contract_hash,
                "ir": self.ir, "ir_hash": self.ir_hash,
                "compiler_version": self.compiler_version,
                "source_map": self.source_map,
                "approval_ir_hash": self.approval_ir_hash}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GameArtifact:
        return cls(game_id=data["game_id"], version=int(data["version"]),
                   title=data.get("title", data["game_id"]),
                   generation_source=data["generation_source"], plan=data["plan"],
                   plan_hash=data["plan_hash"], verification_id=data["verification_id"],
                   registry_contract_hash=data["registry_contract_hash"],
                   ir=data.get("ir"), ir_hash=data.get("ir_hash"),
                   compiler_version=data.get("compiler_version"),
                   source_map=data.get("source_map"),
                   approval_ir_hash=data.get("approval_ir_hash"),
                   schema_version=data.get("schema_version", ARTIFACT_SCHEMA_VERSION))

    def matches(self, verification: VerificationResult) -> bool:
        """Whether a credential actually describes this artifact's content."""
        return (self.verification_id == verification.verification_id
                and self.plan_hash == verification.plan_hash
                and self.registry_contract_hash == verification.registry_contract_hash
                and self.ir_hash == verification.ir_hash
                and self.compiler_version == verification.compiler_version)


def build_artifact(*, game_id: str, version: int, title: str, plan: GamePlan | dict[str, Any],
                   verification: VerificationResult, generation_source: str,
                   ir: dict[str, Any] | None = None, source_map: dict[str, Any] | None = None,
                   approval_ir_hash: str | None = None) -> GameArtifact:
    """Assemble an artifact from an already-verified plan, or refuse.

    The plan's hash is recomputed here, so an artifact can never claim a plan the
    credential did not cover. A caller that changed the plan after verifying it
    gets ``verification_stale``; a caller that never verified gets
    ``verification_failed``. This is the only constructor the registration path
    should use.
    """
    validated = plan if isinstance(plan, GamePlan) else GamePlan.model_validate(plan)
    actual = plan_fingerprint(validated)
    if actual != verification.plan_hash:
        raise ValueError("verification_stale")
    if not verification.ok:
        raise ValueError("verification_failed")
    if approval_ir_hash is not None and approval_ir_hash != verification.ir_hash:
        raise ValueError("approval_mismatch")
    return GameArtifact(
        game_id=game_id, version=int(version), title=title,
        generation_source=generation_source, plan=validated.model_dump(mode="json"),
        plan_hash=actual, verification_id=verification.verification_id,
        registry_contract_hash=verification.registry_contract_hash, ir=ir,
        ir_hash=verification.ir_hash, compiler_version=verification.compiler_version,
        source_map=source_map, approval_ir_hash=approval_ir_hash)
