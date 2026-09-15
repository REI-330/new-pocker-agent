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

import re
from collections.abc import Callable
from copy import deepcopy
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


def one_of(*schemas: dict[str, Any]) -> dict[str, Any]:
    """A tagged union, for an operation whose return shape depends on a branch.

    ``trick.play`` is the concrete case: it returns ``{complete, player}`` while a
    trick is in progress and ``{complete, winner, tricks_won}`` once it closes.
    Declaring a single flat object would promise fields that are not always
    there, so the return contract is the union of the two real shapes.
    """
    if len(schemas) < 2:
        raise ToolError("one_of_requires_two_or_more_schemas")
    return {"oneOf": [deepcopy(schema) for schema in schemas]}


def validate_schema(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Validate the small JSON-Schema subset published by mechanism contracts."""
    if "oneOf" in schema:
        errors: list[str] = []
        for branch in schema["oneOf"]:
            try:
                validate_schema(value, branch, path)
                return
            except ToolError as error:
                errors.append(str(error))
        raise ToolError(f"schema_no_union_branch:{path}:{errors[0] if errors else 'empty'}")
    expected = schema.get("type", "any")
    if isinstance(expected, list):
        for candidate in expected:
            try:
                validate_schema(value, {**schema, "type": candidate}, path)
                return
            except ToolError:
                pass
        raise ToolError(f"schema_type:{path}:{expected}")
    if expected == "any":
        return
    if expected == "null":
        if value is not None:
            raise ToolError(f"schema_type:{path}:null")
        return
    if expected == "integer":
        if type(value) is not int:
            raise ToolError(f"schema_type:{path}:integer")
        return
    if expected == "string":
        if not isinstance(value, str):
            raise ToolError(f"schema_type:{path}:string")
        return
    if expected == "boolean":
        if type(value) is not bool:
            raise ToolError(f"schema_type:{path}:boolean")
        return
    if expected == "array":
        if not isinstance(value, (list, tuple)):
            raise ToolError(f"schema_type:{path}:array")
        for index, item in enumerate(value):
            validate_schema(item, schema.get("items", ANY), f"{path}[{index}]")
        return
    if expected == "object":
        if not isinstance(value, dict):
            if not schema.get("properties") and hasattr(value, "as_dict"):
                return
            raise ToolError(f"schema_type:{path}:object")
        properties = schema.get("properties", {})
        missing = [name for name in schema.get("required", []) if name not in value]
        if missing:
            raise ToolError(f"schema_required:{path}.{missing[0]}")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise ToolError(f"schema_unknown:{path}.{unknown[0]}")
        for name, item in value.items():
            if name in properties:
                validate_schema(item, properties[name], f"{path}.{name}")
        return
    raise ToolError(f"schema_unknown_type:{path}:{expected}")


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
        # Deep-copy the nested schemas: an exporter that aliased the live spec
        # would let a caller mutate `registry.export()` and silently change the
        # contract (and therefore `contract_hash()`) for every other reader.
        return {"name": self.name, "params": list(self.params),
                "requires": [deepcopy(item) if isinstance(item, dict) else item
                             for item in self.requires],
                "ensures": [deepcopy(item) if isinstance(item, dict) else item
                            for item in self.ensures],
                "effects": list(self.effects), "writes": list(self.writes),
                "returns": self.returns, "failure": self.failure,
                "deterministic": self.deterministic,
                "input_schema": deepcopy(self.input_schema),
                "output_schema": deepcopy(self.output_schema),
                "reads": list(self.reads),
                "feature_constraints": list(self.feature_constraints),
                "config_schema": deepcopy(self.config_schema)}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    factory: Callable[..., Any]
    operations: tuple[OperationSpec, ...]
    config_schema: dict[str, Any] = field(default_factory=dict)
    api_version: str = "1.0"
    version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.api_version != "1.0":
            raise ToolError(f"unsupported_mechanism_api:{self.name}:{self.api_version}")
        if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", self.version) is None:
            raise ToolError(f"invalid_mechanism_version:{self.name}")
        # Binding configuration is declared once on the tool; every operation of
        # that tool sees the same config. An operation must not carry a competing
        # schema -- that would let the tool config and the operation disagree.
        for operation in self.operations:
            if operation.config_schema and operation.config_schema != self.config_schema:
                raise ToolError(
                    f"operation_config_conflict:{self.name}.{operation.name}")
        if any(operation.config_schema != self.config_schema for operation in self.operations):
            object.__setattr__(self, "operations", tuple(
                replace(operation, config_schema=self.config_schema)
                for operation in self.operations))

    def operation(self, name: str) -> OperationSpec:
        for spec in self.operations:
            if spec.name == name:
                return spec
        raise ToolError(f"unknown_tool_operation:{self.name}.{name}")

    def export(self) -> dict[str, Any]:
        return {"name": self.name, "api_version": self.api_version,
                "version": self.version, "config_schema": deepcopy(self.config_schema),
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
        validate_schema(spec.config_schema, config, f"{name}.config")
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
