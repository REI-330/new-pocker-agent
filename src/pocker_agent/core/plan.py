"""The single declarative game description: tools + a control-flow graph.

This replaces the branch's two competing representations (family engines and
``ToolPlan`` flows). A plan is data: it can be authored by the host (built-in
games, used as golden references) or by the agent loop, and either way the
same interpreter and the same playtest gate apply.
"""
from __future__ import annotations

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
    schema_version: Literal["0.4"] = "0.4"
    game_kind: str = Field(min_length=1, max_length=64)
    players: int = Field(ge=1, le=12)
    tools: list[ToolBinding] = Field(min_length=1, max_length=64)
    initial: dict[str, Any] = Field(default_factory=dict)
    entry: str
    nodes: dict[str, FlowNode] = Field(min_length=1, max_length=512)
    step_limit: int = Field(default=1024, ge=1, le=8192)

    @model_validator(mode="after")
    def graph(self) -> GamePlan:
        names = [binding.name for binding in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("plan_duplicate_tool")
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
