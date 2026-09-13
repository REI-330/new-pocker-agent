"""M4-7: the bounded, host-controlled design conversation loop.

Like the known-family loop, this is decide -> host tool -> observation -> decide.
The differences are deliberate:

* the tools are the design-service tools, and every mutation goes through the
  design store's optimistic lock;
* the loop owns the budget (model decisions, repairs, estimated tokens, wall
  clock and per-step output size) and stores the usage in the session context;
* a recoverable failure is re-fed to the model as an observation, while a host
  failure (transport, a crashed tool, or a stale revision from a concurrent
  writer) stops the turn instead of asking the model to repair the host;
* a turn can only end with a question, an explicit "unsupported", a finalized
  candidate, or an error/budget stop -- never with a silent success.

Nothing here can run a game or register a version: the loop only sees the design
service, whose ``finalize`` produces a candidate artifact (ADR-0008).
"""
from __future__ import annotations

import inspect
import json
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .design_service import DESIGN_TOOL_SCHEMAS, DesignService
from .loop import clean_history, parse_decision
from .meta_tools import observation_text

#: Section 7 of the phase-2 plan: one automatic design turn stays under these.
DEFAULT_DESIGN_BUDGET: dict[str, Any] = {
    "max_decisions": 24,
    "max_repairs": 4,
    "max_tokens": 48000,
    "max_seconds": 180,
    "max_output_chars": 8000,
}

#: A crashed host tool ends the turn; a concurrent-writer conflict is raised as a
#: ``stale_revision``/``request_id_conflict`` ValueError so the API maps it to 409.
HOST_ERROR_PREFIXES = ("tool_crashed",)

DESIGN_SYSTEM_PROMPT = f"""你是 Pocker Agent 的规则设计服务。你只能调用下面的设计元工具；不要执行游戏、不要写代码、不要返回解释性文字。

规则：
1. 每次只返回一个 JSON 对象：{{"tool": "<name>", "args": {{...}}}}。
2. 先用 list_capabilities / describe_mechanism 查询宿主能力，再用 propose_ir 提交规则。
3. 信息不足时用 ask_user 一次问清；表达不了时用 unsupported 说明缺口。
4. 修改规则用 patch_ir，不得删除用户已确认的需求条款。
5. compose_plan 之后用 validate_plan / simulate 诊断；正式门槛是 verify_game。
6. verify_game 失败时用 inspect_failure 定位，再修复重试。
7. 只有 verify_game 通过后 finalize 才会成功；finalize 只生成候选产物，不能注册、不能自确认。
8. propose_ir 只提交 kind="composed" 的 ComposedRulesIR（不要提交 arithmetic/war 等内置 kind）。字段名必须精确，不要发明字段；玩家作用域的 zone 会按座位实例化；visibility 取 public/owner_only/hidden；hidden 手牌用 owner_only。最小字段骨架（effects 必须按需求填写，不能留空占位）：
{{{{"schema_version": "0.5", "kind": "composed",
  "meta": {{"title": "名称"}}, "players": {{"count": 2}},
  "deck": {{"ranks": ["2", "3"], "suits": ["S", "H"], "copies": 1, "values": {{}}}},
  "zones": [{{"id": "hand", "visibility": "public", "scope": "player"}},
            {{"id": "stock", "visibility": "hidden", "scope": "shared"}}],
  "setup": {{"deals": [{{"zone": "hand", "count": 3, "per_seat": true}}], "stock_zone": "stock"}},
  "actions": [{{"id": "play", "inputs": [{{"id": "card", "kind": "card_selection", "zone": "hand", "scope": "actor", "min_count": 1, "max_count": 1}}], "effects": []}}],
  "flow": {{"round_action": "play", "turn_actions": ["play"]}},
  "terminal": {{"max_rounds": 3, "winner": "highest_score", "tie": "allow"}}}}}}
9. 失败时先读 repair observation 里的 invalid_ir 字段路径，再用 patch_ir 只改对应字段；不要反复重新提交同一份错误 IR。

设计元工具：
{json.dumps(DESIGN_TOOL_SCHEMAS, ensure_ascii=False)}
"""


@dataclass
class DesignResult:
    kind: str
    message: str
    session_id: str
    revision: int = 0
    status: str = "draft"
    artifact: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    observations: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, str]] = field(default_factory=list)
    attempts: int = 0
    used: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "message": self.message,
                "session_id": self.session_id, "revision": self.revision,
                "status": self.status, "artifact": self.artifact,
                "verification": self.verification, "attempts": self.attempts,
                "observations": self.observations, "messages": self.messages,
                "used": self.used}


def normalize_budget(budget: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_DESIGN_BUDGET)
    if budget:
        merged.update({key: value for key, value in budget.items() if value is not None})
    return merged


def _usage() -> dict[str, Any]:
    return {"decisions": 0, "repairs": 0, "tokens": 0, "seconds": 0.0,
            "output_chars": 0}


class ModelDeadlineExceeded(TimeoutError):
    """The model call did not return before the host-owned turn deadline."""


def _model_accepts_timeout(model: Any) -> bool:
    """Keep compatibility with injected test models while enforcing a host cap."""
    try:
        signature = inspect.signature(model.complete)
    except (TypeError, ValueError):
        return False
    parameters = signature.parameters.values()
    return ("timeout_seconds" in signature.parameters
            or any(parameter.kind is inspect.Parameter.VAR_KEYWORD
                   for parameter in parameters))


def _complete_with_deadline(model: Any, messages: list[dict[str, str]], remaining: float) -> str:
    """Call a model with a hard host deadline and discard late results.

    OpenAI-compatible clients receive the remaining timeout so their socket is
    bounded too. Legacy/injected models that do not accept the keyword run in a
    daemon thread; once the deadline expires their return value is never observed
    and therefore cannot dispatch a late tool call or mutate the design store.
    """
    if remaining <= 0:
        raise ModelDeadlineExceeded("model_deadline_exceeded")
    result: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
    accepts_timeout = _model_accepts_timeout(model)

    def invoke() -> None:
        try:
            if accepts_timeout:
                value = model.complete(messages, timeout_seconds=max(0.1, remaining))
            else:
                value = model.complete(messages)
            result.put_nowait(("ok", value))
        except Exception as error:  # propagated on the caller thread
            try:
                result.put_nowait(("error", error))
            except queue.Full:
                pass

    worker = threading.Thread(target=invoke, name="pocker-model-call", daemon=True)
    worker.start()
    worker.join(remaining)
    if worker.is_alive():
        raise ModelDeadlineExceeded("model_deadline_exceeded")
    try:
        kind, value = result.get_nowait()
    except queue.Empty as error:  # defensive: a completed thread must report
        raise RuntimeError("model_call_no_result") from error
    if kind == "error":
        raise value
    return value


def run_design_loop(service: DesignService, session_id: str, message: str, model,
                    budget: dict[str, Any] | None = None, *,
                    history: Any = None, expected_revision: int | None = None) -> DesignResult:
    """One bounded design turn, persisted through ``service``.

    ``history`` overrides the stored chat (used when a caller supplies the thread
    explicitly); otherwise the session's own chat is the source of truth.

    ``expected_revision`` claims the turn at the revision the caller read. The
    first write commits at that revision, so a concurrent update fails with
    ``stale_revision`` (mapped to HTTP 409) instead of the turn silently rebasing
    onto newer state. Once the turn has claimed its revision, later writes use the
    freshly-read one.
    """
    limits = normalize_budget(budget)
    started = time.monotonic()
    session = service.session()
    prior = history if history is not None else session.context.get("chat")
    chat: list[dict[str, str]] = [*clean_history(prior),
                                  {"role": "user", "content": message}]
    session = service.record(event="user_turn", expected_revision=expected_revision,
                             context={"chat": chat, "budget": limits})
    messages: list[dict[str, str]] = [{"role": "system", "content": DESIGN_SYSTEM_PROMPT},
                                      *chat]
    observations: list[dict[str, Any]] = []
    used = _usage()

    def finish(kind: str, text: str, *, artifact: dict[str, Any] | None = None,
               verification: dict[str, Any] | None = None,
               assistant: str | None = None) -> DesignResult:
        if assistant is not None:
            chat.append({"role": "assistant", "content": assistant})
        used["seconds"] = round(time.monotonic() - started, 3)
        latest = service.record(event="turn_end",
                                context={"chat": chat, "used": used})
        return DesignResult(kind, text, session_id, revision=latest.revision,
                            status=latest.status, artifact=artifact,
                            verification=verification or latest.context.get("verification"),
                            observations=observations, messages=chat,
                            attempts=used["decisions"], used=used)

    for _ in range(int(limits["max_decisions"])):
        if time.monotonic() - started > limits["max_seconds"]:
            return finish("budget_exhausted", "budget_exhausted: 达到墙钟时间上限")
        if used["tokens"] > limits["max_tokens"]:
            return finish("budget_exhausted", "budget_exhausted: 达到 token 上限")
        remaining = float(limits["max_seconds"]) - (time.monotonic() - started)
        try:
            raw = _complete_with_deadline(model, messages, remaining)
        except ModelDeadlineExceeded:
            return finish("budget_exhausted", "budget_exhausted: 模型调用超过墙钟时间上限")
        except Exception as error:                          # transport/credential
            return finish("error", f"model_failed:{error}")

        used["decisions"] += 1
        text = raw if isinstance(raw, str) else ""
        used["output_chars"] += len(text)
        used["tokens"] += max(1, len(text) // 4)

        # A verbose model often wraps its decision in reasoning prose. Parse
        # first: a valid JSON decision is accepted up to a hard cap, so the size
        # gate only rejects output that carries no usable decision. (The first
        # M6 blind run hit exactly this: output_too_long before any parse
        # attempt, which is a host defect, not a model failure.)
        hard_limit = int(limits["max_output_chars"]) * 4
        parse_error: RuntimeError | None = None
        decision = None
        if len(text) <= hard_limit:
            try:
                decision = parse_decision(text)
            except RuntimeError as error:
                parse_error = error

        if decision is None:
            used["repairs"] += 1
            if len(text) > limits["max_output_chars"]:
                error_code = "output_too_long"
            else:
                error_code = str(parse_error or "model_output_invalid_json")
            observation = {"ok": False, "tool": None, "error": error_code}
            observations.append({"step": used["decisions"], **observation})
            messages.append({"role": "assistant",
                             "content": text[: int(limits["max_output_chars"])]})
            messages.append({"role": "user", "content": observation_text(observation)})
            if used["repairs"] > limits["max_repairs"]:
                message = ("repair_budget_exhausted: 输出过长次数过多"
                           if error_code == "output_too_long"
                           else "repair_budget_exhausted: 无法解析模型输出")
                return finish("error", message)
            continue

        tool = decision.get("tool")
        args = decision.get("args") or {}
        if not isinstance(tool, str):
            observation = {"ok": False, "tool": "", "error": "tool_required"}
        else:
            observation = service.dispatch(tool, args)
        observations.append({"step": used["decisions"], **observation})
        messages.append({"role": "assistant",
                         "content": json.dumps(decision, ensure_ascii=False)})
        messages.append({"role": "user", "content": observation_text(observation)})

        error = str(observation.get("error") or "")
        if error.startswith(("stale_revision", "request_id_conflict")):
            # A concurrent writer owns the session; surface it as a conflict
            # (HTTP 409) rather than a turn result the model could "repair".
            raise ValueError(error)
        if error.startswith(HOST_ERROR_PREFIXES):
            return finish("error", f"host_error:{error}")

        if observation.get("kind") == "question":
            return finish("question", observation["question"],
                          assistant=observation["question"])
        if observation.get("kind") == "unsupported":
            return finish("unsupported", observation["message"],
                          assistant=observation["message"])
        if tool == "finalize" and observation.get("ok"):
            artifact = observation.get("artifact")
            text_message = "已生成候选产物：" + json.dumps(artifact, ensure_ascii=False)
            return finish("finalized", text_message, artifact=artifact,
                          assistant=text_message)

        if not observation.get("ok"):
            used["repairs"] += 1
            if used["repairs"] > limits["max_repairs"]:
                return finish("error", "repair_budget_exhausted: 自动修复次数达到上限")

    return finish("budget_exhausted", "budget_exhausted: 达到决策步数上限")
