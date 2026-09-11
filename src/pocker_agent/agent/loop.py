"""The agent loop: decide -> meta-tool -> observation -> decide.

The loop is bounded and host-controlled. It cannot execute a game, cannot call a
game tool, and cannot finish without a passing playtest. Any model output that is
not a usable decision becomes an observation the model must repair from.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..core.contracts import ToolError, ToolRegistry
from ..core.ir import IR_ADAPTER
from ..core.registry import core_registry
from .meta_tools import TOOL_SCHEMAS, LoopState, _fail, dispatch, observation_text

SYSTEM_PROMPT = f"""你是 Pocker Agent 的规则设计编排器。你只能调用下面的元工具；永远不要执行游戏、不要调用游戏工具、不要写代码、不要返回解释性文字。

规则：
1. 每次只返回一个 JSON 对象：{{"tool": "<name>", "args": {{...}}}}。
2. 信息不足时调用 ask_user（只问一次，并用 missing 列出缺口）。
3. 必须先 propose_ir 提交严谨规则；能力检查通过后用 compose_plan 生成候选计划。
4. 必须调用 playtest；只有 playtest 通过后 finalize 才会成功。
5. capability_check 报 missing 时调用 unsupported 说明缺哪些机制，不得降级成同名简化版。
6. 任何一步失败都会以 observation 返回给你；请据此修复后重试。

元工具：
{json.dumps(TOOL_SCHEMAS, ensure_ascii=False)}

RulesIR JSON Schema（kind = arithmetic | war）：
{json.dumps(IR_ADAPTER.json_schema(), ensure_ascii=False)}
"""


@dataclass
class AgentResult:
    kind: str
    message: str
    ir: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None
    playtest: dict[str, Any] | None = None
    finalized: bool = False
    attempts: int = 0
    observations: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "message": self.message, "ir": self.ir, "plan": self.plan,
                "playtest": self.playtest, "finalized": self.finalized,
                "attempts": self.attempts, "observations": self.observations}


def parse_decision(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str):
        raise RuntimeError("model_output_not_text")
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        value = None
        for match in re.finditer(r"\{", text):
            try:
                candidate, _ = decoder.raw_decode(text[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                value = candidate
                break
        if value is None:
            raise RuntimeError("model_output_invalid_json") from None
    if not isinstance(value, dict):
        raise RuntimeError("model_output_not_object")
    return value


def _summary(state: LoopState) -> str:
    lines = ["已生成可执行游戏，请核对："]
    if state.ir is not None:
        lines.append(f"玩法：{state.ir.title}（kind={state.ir.kind}，players={state.ir.players}，max_rounds={state.ir.max_rounds}）")
    if state.plan is not None:
        lines.append(f"计划：{len(state.plan.get('nodes', {}))} 个节点，工具 {[tool['name'] for tool in state.plan.get('tools', [])]}")
    if state.report:
        lines.append(f"playtest：{'通过' if state.report.get('ok') else '未通过'}，种子 {state.report.get('seeds')}，wait 覆盖 {state.report.get('covered_wait_nodes')}")
    return "\n".join(lines)


def run_loop(goal: str, model, *, registry: ToolRegistry | None = None,
             seeds: tuple[int, ...] = (0, 7, 23), max_steps: int = 12) -> AgentResult:
    registry = registry or core_registry()
    state = LoopState(goal=goal)
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT},
                                      {"role": "user", "content": goal}]
    for step in range(max_steps):
        try:
            raw = model.complete(messages)
        except Exception as error:  # transport/credential failures end the budget
            return AgentResult("error", f"model_failed:{error}",
                               observations=state.observations, attempts=step)

        try:
            decision = parse_decision(raw)
        except RuntimeError as error:
            observation = {"ok": False, "tool": None, "error": str(error)}
            state.observations.append({"step": step + 1, **observation})
            messages.append({"role": "assistant", "content": raw if isinstance(raw, str) else ""})
            messages.append({"role": "user", "content": observation_text(observation)})
            continue

        tool = decision.get("tool")
        args = decision.get("args") or {}
        if not isinstance(tool, str):
            observation = _fail("", "tool_required")
        else:
            try:
                observation = dispatch(tool, args, state, registry, seeds)
            except (ToolError, ValueError) as error:
                observation = _fail(tool, str(error))
        state.observations.append({"step": step + 1, **observation})
        messages.append({"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)})
        messages.append({"role": "user", "content": observation_text(observation)})

        if observation.get("kind") == "question":
            return AgentResult("question", observation["question"],
                               observations=state.observations, attempts=step + 1)
        if observation.get("kind") == "unsupported":
            return AgentResult("unsupported", observation["message"],
                               observations=state.observations, attempts=step + 1)
        if tool == "finalize" and observation.get("ok"):
            ir = state.ir.model_dump(mode="json") if state.ir is not None else None
            return AgentResult("proposal", _summary(state), ir=ir, plan=state.plan,
                               playtest=state.report, finalized=True,
                               observations=state.observations, attempts=step + 1)

    return AgentResult("error", "budget_exhausted: 达到步数上限仍未冻结计划",
                       observations=state.observations, attempts=max_steps)
