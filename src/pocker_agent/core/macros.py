"""Macros: named, parameterized, declarative plan fragments.

A macro is *data* composed of declared operations. The host validates it and
inlines it into a plan at build time, so the interpreter stays unchanged and a
macro can never introduce a new runtime capability.

Promotion rule (engineering-standards section 6.4): once a macro is reused by
two or more plans it should be promoted to a parameterized **axis** (a real
tool). ``promotion_report`` surfaces exactly those candidates instead of leaving
the decision to memory.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .contracts import ToolError, ToolRegistry

EXIT_PREFIX = "@exit:"
PROMOTION_THRESHOLD = 2


@dataclass(frozen=True)
class MacroSpec:
    name: str
    entry: str
    exits: tuple[str, ...]
    params: tuple[str, ...] = ()
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    # "flow" macros are control-flow fragments (gate/wait wiring) and are never
    # promotion candidates: an axis is a mechanism (a tool), not a flow shape.
    kind: str = "flow"
    promoted: bool = False
    note: str = ""


def _render(value: Any, params: dict[str, Any]) -> Any:
    if isinstance(value, str):
        rendered = value
        for key, replacement in params.items():
            rendered = rendered.replace("{" + key + "}", str(replacement))
        return rendered
    if isinstance(value, dict):
        return {key: _render(item, params) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item, params) for item in value]
    return value


def expand_macro(spec: MacroSpec, prefix: str, **params: Any) -> tuple[dict[str, Any], str]:
    """Inline a macro under ``prefix``; returns (nodes, entry_id).

    Exit placeholders (``@exit:name``) are left for :func:`wire`, so the caller
    decides what each exit connects to.
    """
    if not prefix or any(char in prefix for char in ":{}"):
        raise ToolError(f"invalid_macro_prefix:{prefix}")
    unknown = set(params) - set(spec.params)
    if unknown:
        raise ToolError(f"unknown_macro_params:{sorted(unknown)}")
    missing = set(spec.params) - set(params)
    if missing:
        raise ToolError(f"missing_macro_params:{sorted(missing)}")
    nodes: dict[str, Any] = {}
    names = set(spec.nodes)

    def localize(value: Any) -> Any:
        """Point internal references at the prefixed node ids."""
        return f"{prefix}:{value}" if isinstance(value, str) and value in names else value

    for name, node in spec.nodes.items():
        rendered = _render(copy.deepcopy(node), params)
        if rendered.get("next") is not None:
            rendered["next"] = localize(rendered["next"])
        for case in rendered.get("cases", []):
            case["target"] = localize(case["target"])
        if rendered.get("inputs"):
            rendered["inputs"] = {key: localize(value) for key, value in rendered["inputs"].items()}
        nodes[f"{prefix}:{name}"] = rendered
    return nodes, f"{prefix}:{spec.entry}"


def wire(nodes: dict[str, Any], exits: dict[str, str]) -> dict[str, Any]:
    """Replace ``@exit:x`` placeholders with concrete node ids."""

    def fix(value: Any) -> Any:
        if isinstance(value, str) and value.startswith(EXIT_PREFIX):
            name = value[len(EXIT_PREFIX):]
            if name not in exits:
                raise ToolError(f"macro_exit_unwired:{name}")
            return exits[name]
        return value

    for node in nodes.values():
        if node.get("next") is not None:
            node["next"] = fix(node["next"])
        for case in node.get("cases", []):
            case["target"] = fix(case["target"])
        if node.get("inputs"):
            node["inputs"] = {key: fix(value) for key, value in node["inputs"].items()}
    return nodes


def validate_macro(spec: MacroSpec, registry: ToolRegistry | None = None) -> None:
    if spec.entry not in spec.nodes:
        raise ToolError(f"macro_entry_missing:{spec.name}")
    for name, node in spec.nodes.items():
        if node.get("kind") == "end":
            raise ToolError(f"macro_must_not_end:{spec.name}:{name}")
        for key in ("next", "target"):
            value = node.get(key)
            if isinstance(value, str) and value.startswith(EXIT_PREFIX) and value[len(EXIT_PREFIX):] not in spec.exits:
                raise ToolError(f"macro_undeclared_exit:{spec.name}:{value}")
        for case in node.get("cases", []):
            target = case.get("target")
            if isinstance(target, str) and target.startswith(EXIT_PREFIX) and target[len(EXIT_PREFIX):] not in spec.exits:
                raise ToolError(f"macro_undeclared_exit:{spec.name}:{target}")
        for value in (node.get("inputs") or {}).values():
            if isinstance(value, str) and value.startswith(EXIT_PREFIX) and value[len(EXIT_PREFIX):] not in spec.exits:
                raise ToolError(f"macro_undeclared_exit:{spec.name}:{value}")
        if registry is not None and node.get("action"):
            action = node["action"]
            registry.spec(action["tool"]).operation(action["operation"])


class MacroRegistry:
    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.tools = registry
        self._specs: dict[str, MacroSpec] = {}

    def register(self, spec: MacroSpec) -> None:
        if spec.name in self._specs:
            raise ToolError(f"macro_duplicate:{spec.name}")
        validate_macro(spec, self.tools)
        self._specs[spec.name] = spec

    def names(self) -> list[str]:
        return sorted(self._specs)

    def spec(self, name: str) -> MacroSpec:
        if name not in self._specs:
            raise ToolError(f"unknown_macro:{name}")
        return self._specs[name]

    def expand(self, name: str, prefix: str, **params: Any) -> tuple[dict[str, Any], str]:
        return expand_macro(self.spec(name), prefix, **params)


def promotion_report(usage: dict[str, list[str]],
                     registry: MacroRegistry | None = None) -> list[dict[str, Any]]:
    """Mechanism macros reused by >= threshold plans that are not yet axes.

    Flow macros are excluded on purpose: control-flow shapes belong in plans,
    while a repeatedly reused *mechanism* belongs in the tool registry as a
    parameterized axis (a human decision, recorded as an ADR).
    """
    report: list[dict[str, Any]] = []
    for name, plans in sorted(usage.items()):
        if registry is not None and name in registry.names():
            spec = registry.spec(name)
            if spec.kind != "mechanism" or spec.promoted:
                continue
        if len(set(plans)) >= PROMOTION_THRESHOLD:
            report.append({"macro": name, "plans": sorted(set(plans)),
                           "uses": len(set(plans)), "action": "promote_to_axis"})
    return report
