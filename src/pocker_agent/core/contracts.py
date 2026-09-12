"""Machine-readable tool contracts.

The registry is the single source of truth for *what a model may ask the host
to do*. Every operation is declared before a plan can reference it, and
``ToolRegistry.export`` produces the JSON description consumed by the agent
loop (future ``function calling`` tool list). Adding behaviour means adding a
declared operation, never letting a plan call arbitrary Python.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


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

    def export(self) -> dict[str, Any]:
        return {"name": self.name, "params": list(self.params),
                "requires": [dict(item) if isinstance(item, dict) else item for item in self.requires],
                "ensures": [dict(item) if isinstance(item, dict) else item for item in self.ensures],
                "effects": list(self.effects), "returns": self.returns,
                "failure": self.failure, "deterministic": self.deterministic}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    factory: Callable[..., Any]
    operations: tuple[OperationSpec, ...]

    def operation(self, name: str) -> OperationSpec:
        for spec in self.operations:
            if spec.name == name:
                return spec
        raise ToolError(f"unknown_tool_operation:{self.name}.{name}")

    def export(self) -> dict[str, Any]:
        return {"name": self.name, "operations": [op.export() for op in self.operations]}


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
