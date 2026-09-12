"""Machine-readable tool contracts.

The registry is the single source of truth for *what a model may ask the host
to do*. Every operation is declared before a plan can reference it, and
``ToolRegistry.export`` produces the JSON description consumed by the agent
loop (future ``function calling`` tool list). Adding behaviour means adding a
declared operation, never letting a plan call arbitrary Python.

G2/M1 makes each operation *typed*: ``params`` alone (a list of names) and
``returns`` alone (prose) could not tell a compiler what a call accepts, what it
produces, or which state it touches. Operations now also carry:

* ``input_schema`` / ``output_schema`` -- machine-readable JSON-Schema shapes;
* ``reads`` -- the state keys an operation inspects, so a composition compiler
  can check dependencies instead of guessing;
* ``writes`` -- a read-only alias of ``effects`` (the enforced write set);
* ``config_schema`` -- the binding configuration that parameterises the call
  (copied from the tool, because a tool's config applies to all its operations);
* ``feature_constraints`` -- mechanism tags a composition must be able to
  satisfy before this operation may be used.

``params`` and ``returns`` are kept for backward compatibility and are checked
against the typed schemas by the architecture tests.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

# --------------------------------------------------------------------- shapes
# Small JSON-Schema fragments. Deliberately not a full JSON-Schema engine: the
# host only needs enough structure to (a) publish a machine-readable contract
# and (b) let the M2 compiler check that a composition supplies the right shape.

ANY: dict[str, Any] = {"type": "any"}
OBJECT: dict[str, Any] = {"type": "object"}
INTEGER: dict[str, Any] = {"type": "integer"}
STRING: dict[str, Any] = {"type": "string"}
BOOLEAN: dict[str, Any] = {"type": "boolean"}


def array(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


def obj(properties: dict[str, Any] | None = None,
        required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": dict(properties or {}),
            "required": list(required), "additionalProperties": False}


INT_LIST = array(INTEGER)
STR_LIST = array(STRING)
ANY_LIST = array(ANY)
CARD_LIST = array(OBJECT)

NO_ARGS = obj()

# A controlled vocabulary of mechanism tags. A tag is a *claim about the host*
# that a composition compiler can check, so the set is closed and tested.
FEATURE_TAGS = frozenset({
    "arithmetic", "betting", "card_identity", "deck_exhaustion", "explicit_order",
    "hand_ranking", "hidden_info", "ledger", "matching", "multi_zone",
    "point_total", "scoring", "sequential_turn", "special_effect", "terminal_rule",
    "turn_adapter",
})


class ToolError(ValueError):
    """A deterministic tool rejected an operation; callers must roll back."""


@dataclass(frozen=True)
class Observation:
    """Structured result handed back to whoever issued a tool call."""

    ok: bool
    tool: str
    operation: str
    value: Any = None
    error: str | None = None


@dataclass(frozen=True)
class OperationSpec:
    """What a plan may ask the host to do, and what it is allowed to change.

    ``failure`` names the operation's failure *mode*:

    * ``rollback`` -- the operation may write state; if it raises, the caller
      discards every change it made (the interpreter rolls back the whole step).
    * ``reject_only`` -- the operation is a pure check: it must declare no
      ``effects``, so there is nothing of its own to undo. Declaring both
      ``reject_only`` and an ``effects`` list is a contract error, enforced here.

    Either way the interpreter rolls the *step* back on failure, so a rejected
    action can never leave a half-applied board.
    """

    name: str
    method: str = ""
    params: tuple[str, ...] = ()
    requires: tuple[Any, ...] = ()
    ensures: tuple[Any, ...] = ()
    effects: tuple[str, ...] = ()
    returns: str = "any"
    failure: str = "rollback"
    deterministic: bool = True
    # G2/M1 typed contract. Defaults keep hand-written specs and tests valid.
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    reads: tuple[str, ...] = ()
    feature_constraints: tuple[str, ...] = ()
    config_schema: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.method:
            object.__setattr__(self, "method", self.name)
        if self.failure not in {"rollback", "reject_only"}:
            raise ToolError(f"invalid_failure_semantics:{self.name}")
        if self.failure == "reject_only" and self.effects:
            raise ToolError(f"reject_only_must_not_write_state:{self.name}")
        # An operation that writes nothing has nothing to roll back, so the mode
        # follows from `effects`. Deriving it stops the two from drifting apart
        # and keeps the exported contract truthful for readers and validators.
        if self.failure == "rollback" and not self.effects:
            object.__setattr__(self, "failure", "reject_only")
        unknown = set(self.feature_constraints) - FEATURE_TAGS
        if unknown:
            raise ToolError(f"unknown_feature_constraint:{self.name}:{sorted(unknown)}")
        # `params` is the legacy view of the input schema. Derive it when the
        # caller only supplied the typed shape, so the two cannot disagree.
        if not self.params and self.input_schema.get("properties"):
            object.__setattr__(self, "params", tuple(self.input_schema["properties"]))

    @property
    def writes(self) -> tuple[str, ...]:
        """Read-only alias of ``effects`` (the enforced write set)."""
        return self.effects

    def export(self) -> dict[str, Any]:
        return {"name": self.name, "params": list(self.params),
                "requires": [dict(item) if isinstance(item, dict) else item for item in self.requires],
                "ensures": [dict(item) if isinstance(item, dict) else item for item in self.ensures],
                "effects": list(self.effects), "writes": list(self.writes),
                "returns": self.returns, "failure": self.failure,
                "deterministic": self.deterministic,
                "input_schema": self.input_schema, "output_schema": self.output_schema,
                "reads": list(self.reads),
                "feature_constraints": list(self.feature_constraints),
                "config_schema": self.config_schema}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    factory: Callable[..., Any]
    operations: tuple[OperationSpec, ...]
    config_schema: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Binding configuration is declared once on the tool; every operation of
        # that tool sees the same config, so copy it down instead of repeating it.
        if self.config_schema:
            object.__setattr__(self, "operations", tuple(
                operation if operation.config_schema
                else replace(operation, config_schema=self.config_schema)
                for operation in self.operations))

    def operation(self, name: str) -> OperationSpec:
        for spec in self.operations:
            if spec.name == name:
                return spec
        raise ToolError(f"unknown_tool_operation:{self.name}.{name}")

    def export(self) -> dict[str, Any]:
        return {"name": self.name, "config_schema": self.config_schema,
                "operations": [op.export() for op in self.operations]}


class ToolRegistry:
    """Whitelist of instantiable tools with declared operations."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ToolError(f"tool_name_duplicate:{spec.name}")
        if not spec.operations:
            raise ToolError(f"tool_requires_operations:{spec.name}")
        self._specs[spec.name] = spec

    def names(self) -> list[str]:
        return sorted(self._specs)

    def spec(self, name: str) -> ToolSpec:
        if name not in self._specs:
            raise ToolError(f"unknown_tool:{name}")
        return self._specs[name]

    def create(self, name: str, **config: Any) -> Any:
        spec = self.spec(name)
        try:
            return spec.factory(**config)
        except TypeError as error:
            raise ToolError(f"invalid_tool_config:{name}") from error

    def export(self) -> list[dict[str, Any]]:
        return [self._specs[name].export() for name in self.names()]

    def contract_hash(self) -> str:
        """Stable identity of the whole contract surface.

        Evidence is bound to a contract version (ADR-0008): if any operation's
        declared shape, write set or feature constraints change, a stored
        verification result must no longer approve a new artifact.
        """
        import hashlib
        import json

        canonical = json.dumps(self.export(), sort_keys=True, ensure_ascii=False,
                               separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
