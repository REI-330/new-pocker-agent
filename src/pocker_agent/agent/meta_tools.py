"""Meta-tools the agent may call while designing a game.

The model never calls a game tool. It calls these tools, which operate on the
IR and the candidate plan; every result is returned as a structured
observation. ``finalize`` is host-gated: it refuses unless a full playtest has
passed, so the model cannot self-certify a game.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..core.contracts import ToolError, ToolRegistry
from ..core.interpreter import Interpreter
from ..core.ir import (
    REQUIRED_AXES,
    ArithmeticIR,
    WarIR,
    check_ir,
    host_compile,
    is_host_compiled,
    parse_ir,
    required_axes,
)
from ..core.plan import GamePlan
from ..core.playtest import playtest
from ..core.policy import bot_action
from ..core.registry import core_registry

# Derived, not hand-written: this string advertised "arithmetic | war" until S8
# and it is also served to the design UI through /api/agent/tools.
IR_KIND_LIST = ", ".join(sorted(REQUIRED_AXES))


@dataclass
class LoopState:
    goal: str
    ir: ArithmeticIR | WarIR | None = None
    plan: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    observations: list[dict[str, Any]] = field(default_factory=list)


TOOL_SCHEMAS: tuple[dict[str, Any], ...] = (
    {"name": "ask_user", "args": {"question": "string", "missing": "string[]"},
     "description": "规则缺少关键字段时集中提问一次。"},
    {"name": "propose_ir", "args": {"ir": "RulesIR"},
     "description": f"提交严谨规则草案（kind = {IR_KIND_LIST}）。"},
    {"name": "patch_ir", "args": {"values": "object"},
     "description": "在现有 IR 上按字段打补丁（例如 {\"max_rounds\": 5}）。"},
    {"name": "capability_check", "args": {},
     "description": "检查当前 IR 需要的能力轴是否都已 stable。"},
    {"name": "compose_plan", "args": {"plan": "GamePlan?"},
     "description": "生成候选计划；不传 plan 时由宿主确定性编译（已知族）。"},
    {"name": "validate_plan", "args": {},
     "description": "校验候选计划：结构、工具、operation。"},
    {"name": "simulate", "args": {"seed": "int?"}, "description": "跑一条确定性路径看事件数。"},
    {"name": "playtest", "args": {"seeds": "int[]?"},
     "description": "发货门槛：完整对局 + wait 覆盖 + 字节重放。"},
    {"name": "finalize", "args": {}, "description": "冻结计划（仅当 playtest 通过）。"},
    {"name": "unsupported", "args": {"message": "string", "missing": "string[]"},
     "description": "表达不了时明确说明缺什么机制。"},
)


def _ok(tool: str, **payload: Any) -> dict[str, Any]:
    return {"ok": True, "tool": tool, **payload}


def _fail(tool: str, error: str) -> dict[str, Any]:
    return {"ok": False, "tool": tool, "error": error}


def _plan_from(state: LoopState) -> GamePlan:
    if state.plan is None:
        raise ToolError("plan_missing")
    return GamePlan.model_validate(state.plan)


def dispatch(tool: str, args: dict[str, Any], state: LoopState,
             registry: ToolRegistry | None = None,
             seeds: tuple[int, ...] = (0, 7, 23)) -> dict[str, Any]:
    registry = registry or core_registry()
    if not isinstance(args, dict):
        return _fail(tool, "args_must_be_object")

    if tool == "ask_user":
        question = str(args.get("question", "")).strip()
        if not question:
            return _fail(tool, "question_required")
        return _ok(tool, kind="question", question=question, missing=list(args.get("missing", [])))

    if tool == "unsupported":
        message = str(args.get("message", "")).strip()
        if not message:
            return _fail(tool, "message_required")
        return _ok(tool, kind="unsupported", message=message, missing=list(args.get("missing", [])))

    if tool == "propose_ir":
        payload = args.get("ir", args)
        try:
            state.ir = parse_ir(payload)
        except Exception as error:  # pydantic ValidationError is a ValueError
            return _fail(tool, f"invalid_ir:{error}")
        state.plan = None
        state.report = None
        report = check_ir(state.ir)
        return _ok(tool, kind=state.ir.kind, required_axes=required_axes(state.ir),
                   expressible=report.expressible, missing=report.missing,
                   host_compiled=is_host_compiled(state.ir))

    if tool == "patch_ir":
        if state.ir is None:
            return _fail(tool, "propose_ir_first")
        values = args.get("values", args)
        merged = {**state.ir.model_dump(mode="json"), **values}
        try:
            state.ir = parse_ir(merged)
        except Exception as error:
            return _fail(tool, f"invalid_ir:{error}")
        state.plan = None
        state.report = None
        report = check_ir(state.ir)
        return _ok(tool, kind=state.ir.kind, expressible=report.expressible, missing=report.missing)

    if tool == "capability_check":
        if state.ir is None:
            return _fail(tool, "propose_ir_first")
        report = check_ir(state.ir)
        return _ok(tool, expressible=report.expressible, missing=report.missing,
                   required_axes=report.required_axes)

    if tool == "compose_plan":
        provided = args.get("plan")
        try:
            if provided is not None:
                plan = GamePlan.model_validate(provided)
            elif state.ir is not None and is_host_compiled(state.ir):
                plan = host_compile(state.ir)
            else:
                return _fail(tool, "no_host_compiler: 需要模型提供 plan（agent_compose）")
            Interpreter(plan, registry)  # validates operations exist
        except Exception as error:
            return _fail(tool, f"invalid_plan:{error}")
        state.plan = plan.model_dump(mode="json")
        state.report = None
        return _ok(tool, game_kind=plan.game_kind, nodes=len(plan.nodes),
                   tools=[binding.name for binding in plan.tools],
                   source="host_compile" if provided is None else "agent_compose")

    if tool == "validate_plan":
        try:
            plan = _plan_from(state)
            Interpreter(plan, registry)
        except Exception as error:
            return _fail(tool, f"invalid_plan:{error}")
        return _ok(tool, game_kind=plan.game_kind, nodes=len(plan.nodes))

    if tool == "simulate":
        try:
            plan = _plan_from(state)
            seed = int(args.get("seed", 7))
            interpreter = Interpreter(plan, registry, seed=seed)
            interpreter.setup()
            steps = 0
            for _ in range(256):
                if interpreter.state.get("finished"):
                    break
                action, payload = bot_action(interpreter)
                interpreter.step(action, **payload)
                steps += 1
            if not interpreter.state.get("finished"):
                return _fail(tool, "simulate_did_not_finish")
        except ToolError as error:
            return _fail(tool, str(error))
        return _ok(tool, seed=seed, steps=steps, events=len(interpreter.events))

    if tool == "playtest":
        try:
            plan = _plan_from(state)
            seeds_arg = args.get("seeds")
            chosen = tuple(int(seed) for seed in seeds_arg) if seeds_arg else seeds
            report = playtest(plan, registry, bot_action, seeds=chosen)
        except ToolError as error:
            return _fail(tool, str(error))
        state.report = report.as_dict()
        return _ok(tool, **report.as_dict())

    if tool == "finalize":
        if state.plan is None:
            return _fail(tool, "plan_missing")
        if not state.report:
            return _fail(tool, "playtest_required")
        if not state.report.get("ok"):
            return _fail(tool, "playtest_failed")
        plan = GamePlan.model_validate(state.plan)
        game_id = state.ir.game_id if state.ir is not None else plan.game_kind
        return _ok(tool, game_id=game_id, playable=True, plan=state.plan,
                   playtest=state.report)

    return _fail(tool, f"unknown_meta_tool:{tool}")


def observation_text(observation: dict[str, Any]) -> str:
    return "observation: " + json.dumps(observation, ensure_ascii=False, default=str)
