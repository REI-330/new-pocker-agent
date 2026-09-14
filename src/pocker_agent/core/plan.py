"""The single declarative game description: tools + a control-flow graph.

This replaces the branch's two competing representations (family engines and
``ToolPlan`` flows). A plan is data: it can be authored by the host (built-in
games, used as golden references) or by the agent loop, and either way the
same interpreter and the same playtest gate apply.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolCall(_Strict):
    tool: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    operation: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    args: dict[str, Any] = Field(default_factory=dict)
    result_key: str | None = Field(default=None, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")


class ToolBinding(_Strict):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    config: dict[str, Any] = Field(default_factory=dict)


class ActionInputDescriptor(_Strict):
    """Plan-data description of one typed input a wait action needs (ADR-0007).

    A composed plan must not leave the *shape* of an action's input implicit in
    tool code: the host bot and the API both need to know which zone to read,
    whether it is the acting seat's instance or a shared pile, and how many
    cards a legal selection holds. ``zone`` is the declared zone id from the
    rules IR (never a resolved instance), so the same descriptor stays valid
    across seats and rounds. An empty ``actions`` list is exactly a 0.4 plan.
    """

    id: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")
    kind: Literal["card_selection"] = "card_selection"
    zone: str = Field(min_length=1, max_length=64)
    scope: Literal["actor", "shared"] = "actor"
    min_count: int = Field(default=1, ge=0, le=12)
    max_count: int = Field(default=1, ge=0, le=12)

    @model_validator(mode="after")
    def bounds(self) -> ActionInputDescriptor:
        if self.max_count < self.min_count:
            raise ValueError("action_input_bounds_invalid")
        return self


class IntegerRangeInputDescriptor(_Strict):
    """A required integer whose legal bounds are evaluated from live state.

    ``minimum`` and ``maximum`` contain compiled, side-effect-free expressions:
    literals, ``$state`` references, or the same closed expression objects used
    by ``logic.evaluate``. This lets one plan describe a dynamic raise range
    without putting poker-specific fields in the interpreter.
    """

    id: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")
    kind: Literal["integer_range"] = "integer_range"
    minimum: Any
    maximum: Any

    @model_validator(mode="after")
    def static_bounds(self) -> IntegerRangeInputDescriptor:
        if type(self.minimum) is int and type(self.maximum) is int:
            if self.maximum < self.minimum:
                raise ValueError("action_integer_bounds_invalid")
        return self


ActionInput = ActionInputDescriptor | IntegerRangeInputDescriptor


class ActionDescriptor(_Strict):
    """A wait action offered by a plan, plus the inputs it expects."""

    id: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(default="", max_length=120)
    inputs: list[ActionInput] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def unique_inputs(self) -> ActionDescriptor:
        ids = [item.id for item in self.inputs]
        if len(ids) != len(set(ids)):
            raise ValueError("action_input_ids_must_be_unique")
        return self


class FlowCase(_Strict):
    value: Any = None
    target: str


class FlowNode(_Strict):
    kind: Literal["call", "branch", "wait", "end"]
    action: ToolCall | None = None
    next: str | None = None
    value: Any = None
    cases: list[FlowCase] = Field(default_factory=list, max_length=32)
    inputs: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def complete(self) -> FlowNode:
        if self.kind == "call" and (self.action is None or self.next is None):
            raise ValueError("flow_call_requires_action_and_next")
        if self.kind == "branch" and (not self.cases or self.next is None):
            raise ValueError("flow_branch_requires_cases_and_default")
        if self.kind == "wait" and not self.inputs:
            raise ValueError("flow_wait_requires_inputs")
        if self.kind != "call" and self.action is not None:
            raise ValueError("flow_action_only_on_call")
        if self.kind == "branch" and self.value is None:
            raise ValueError("flow_branch_requires_value")
        return self


class GamePlan(_Strict):
    # ADR-0007: plan versions are distinguishable. ``0.4`` remains authoritative
    # for the current field set; ``0.5`` is reserved for the M3 binding split
    # (``binding_id``/``tool_type``). Changing a persisted field's meaning must
    # come with a version bump, and a migrated plan's hash is a *content* hash --
    # it never stands in for an old verification credential.
    schema_version: Literal["0.4", "0.5"] = "0.4"
    game_kind: str = Field(min_length=1, max_length=64)
    players: int = Field(ge=1, le=12)
    tools: list[ToolBinding] = Field(min_length=1, max_length=64)
    # 0.5 addition: the typed inputs of the plan's wait actions. A 0.4 plan
    # serialised before this field existed parses back to ``[]`` unchanged.
    actions: list[ActionDescriptor] = Field(default_factory=list, max_length=32)
    initial: dict[str, Any] = Field(default_factory=dict)
    entry: str
    nodes: dict[str, FlowNode] = Field(min_length=1, max_length=512)
    step_limit: int = Field(default=1024, ge=1, le=8192)

    @model_validator(mode="after")
    def graph(self) -> GamePlan:
        names = [binding.name for binding in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("plan_duplicate_tool")
        action_ids = [descriptor.id for descriptor in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("plan_duplicate_action")
        if self.entry not in self.nodes:
            raise ValueError("plan_entry_missing")
        for node in self.nodes.values():
            targets = list(node.inputs.values()) + [case.target for case in node.cases]
            if node.next is not None:
                targets.append(node.next)
            for target in targets:
                if target not in self.nodes:
                    raise ValueError(f"plan_target_missing:{target}")
            if node.action is not None and node.action.tool not in names:
                raise ValueError(f"plan_action_tool_not_declared:{node.action.tool}")
        return self


def plan_fingerprint(plan: GamePlan | dict[str, Any]) -> str:
    """Stable identity for a plan, so a session can prove which one it ran.

    Sessions store this at creation and re-check it on restore: a plan that was
    replaced under the same id would otherwise resume old state against a new
    control-flow graph.
    """
    payload = plan.model_dump(mode="json") if isinstance(plan, GamePlan) else plan
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
