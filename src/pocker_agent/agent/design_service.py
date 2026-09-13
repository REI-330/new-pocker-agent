"""M4: the design service -- the only writer of a design session.

Every design action is a *host-executed* tool call. The model proposes a JSON
tool call; this service reads the durable :class:`~.design_store.DesignSession`,
applies the change, and writes it back through ``DesignStore.commit`` with the
revision it read. The model never touches the store, never compiles a raw plan
and never registers a runnable version: ``finalize`` only builds a *candidate*
artifact, and registration still requires ``publish_composed`` plus a separate
user confirmation (ADR-0008).

The capability and mechanism tools read from the same registries the interpreter
enforces, so a description cannot drift from what a plan can actually run. There
is no dispatch on a game name, ``game_id`` or description anywhere in this file.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..core.capability import AXIS_BY_ID, capability_matrix
from ..core.ir import parse_design_ir
from ..core.registry import core_registry

#: The tools the design model may call. This is a *separate* table from
#: ``meta_tools.TOOL_SCHEMAS`` on purpose: the macro-authoring tools are deferred
#: (M4 keeps the ``macro`` axis ``planned``), and the legacy loop keeps its own
#: known-family contract. A tool named ``macro`` must not appear here either.
DESIGN_TOOL_SCHEMAS: tuple[dict[str, Any], ...] = (
    {"name": "list_capabilities", "args": {"axis": "string?"},
     "description": "列出能力轴及其状态；给定 axis 时只返回该轴。"},
    {"name": "describe_mechanism", "args": {"tool": "string?", "operation": "string?"},
     "description": "从注册表查询工具/操作的参数、约束与缺口；同源于 Interpreter。"},
    {"name": "propose_ir", "args": {"ir": "RulesIR"},
     "description": "提交或替换规范化规则草案（kind 含 composed）。"},
    {"name": "patch_ir", "args": {"values": "object", "path": "string?"},
     "description": "在现有 IR 上按字段打补丁；不删除已有需求条款。"},
    {"name": "capability_check", "args": {},
     "description": "解析当前 IR 的组合依赖，返回能力轴与缺口。"},
    {"name": "compose_plan", "args": {},
     "description": "对当前 IR 确定性编译；不接受模型提供的 raw plan。"},
    {"name": "validate_plan", "args": {},
     "description": "结构校验候选计划：可达性、有界循环、operation 存在。"},
    {"name": "simulate", "args": {"seed": "int?"},
     "description": "跑一条确定性诊断路径；只作诊断，不等于验证。"},
    {"name": "verify_game", "args": {},
     "description": "运行宿主正式验证（多种子 × 多策略 × 契约检查）。"},
    {"name": "inspect_failure", "args": {},
     "description": "返回最近一次失败的结构化位置、条款与精简状态。"},
    {"name": "finalize", "args": {},
     "description": "仅当当前证据绑定当前 IR/计划时，生成候选产物（不注册）。"},
    {"name": "ask_user", "args": {"question": "string", "missing": "string[]"},
     "description": "规则缺少关键字段时集中提问一次。"},
    {"name": "unsupported", "args": {"message": "string", "missing": "string[]"},
     "description": "表达不了时明确说明缺什么机制。"},
)

DESIGN_TOOL_NAMES: tuple[str, ...] = tuple(schema["name"] for schema in DESIGN_TOOL_SCHEMAS)


def _ok(tool: str, /, **payload: Any) -> dict[str, Any]:
    return {"ok": True, "tool": tool, **payload}


def _fail(tool: str, error: str, /, **payload: Any) -> dict[str, Any]:
    return {"ok": False, "tool": tool, "error": error, **payload}


class DesignService:
    """Host-executed design tools over one durable design session.

    The service is a thin, stateless adapter: it reads the session, computes a
    change, and commits it at the revision it read. A concurrent writer therefore
    yields ``stale_revision`` instead of a silent overwrite.
    """

    def __init__(self, store: Any, session_id: str, *, registry: Any = None) -> None:
        self.store = store
        self.session_id = session_id
        self.registry = registry or core_registry()

    # ------------------------------------------------------------- plumbing
    def session(self):
        return self.store.get(self.session_id)

    def _commit(self, session, *, event: str, request_id: str | None = None,
                **changes: Any):
        return self.store.commit(self.session_id, session.revision,
                                 request_id=request_id, event=event, **changes)

    def dispatch(self, tool: str, args: dict[str, Any] | None = None, *,
                 request_id: str | None = None) -> dict[str, Any]:
        """Run one design tool and return its observation. Never raises on input."""
        if not isinstance(args, dict):
            return _fail(str(tool), "args_must_be_object")
        handler = getattr(self, f"_tool_{tool}", None)
        if handler is None or tool not in DESIGN_TOOL_NAMES:
            return _fail(str(tool), f"unknown_design_tool:{tool}")
        try:
            return handler(args, request_id)
        except KeyError as error:
            return _fail(tool, str(error.args[0]) if error.args else "session_not_found")
        except ValueError as error:
            return _fail(tool, str(error))
        except Exception as error:                                  # pragma: no cover
            return _fail(tool, f"tool_crashed:{type(error).__name__}:{error}")

    # ---------------------------------------------------------- capabilities
    def _tool_list_capabilities(self, args: dict[str, Any], request_id: str | None):
        axis = args.get("axis")
        matrix = capability_matrix()
        if axis:
            found = next((item for item in matrix["axes"] if item["id"] == axis), None)
            if found is None:
                return _fail("list_capabilities", f"unknown_axis:{axis}")
            return _ok("list_capabilities", axis=found)
        return _ok("list_capabilities", status_order=matrix["status_order"],
                   axes=matrix["axes"], count=len(matrix["axes"]))

    def _tool_describe_mechanism(self, args: dict[str, Any], request_id: str | None):
        tool = args.get("tool")
        operation = args.get("operation")
        exported = self.registry.export()
        if tool is None and operation is None:
            return _ok("describe_mechanism", count=len(exported), tools=[
                {"name": item["name"],
                 "operations": [op["name"] for op in item["operations"]]}
                for item in exported])
        if tool is not None:
            spec = next((item for item in exported if item["name"] == tool), None)
            if spec is None:
                return _fail("describe_mechanism", f"unknown_tool:{tool}")
            if operation is None:
                return _ok("describe_mechanism", tool=spec)
            matches = [op for op in spec["operations"] if op["name"] == operation]
            if not matches:
                return _fail("describe_mechanism", f"unknown_operation:{tool}.{operation}")
            return _ok("describe_mechanism", tool=tool, operation=matches[0])
        matches = [{"tool": item["name"], "operation": op}
                   for item in exported for op in item["operations"]
                   if op["name"] == operation]
        if not matches:
            return _fail("describe_mechanism", f"unknown_operation:{operation}")
        return _ok("describe_mechanism", count=len(matches), matches=matches)

    # --------------------------------------------------------------- rules IR
    def _tool_propose_ir(self, args: dict[str, Any], request_id: str | None):
        payload = args.get("ir", args)
        try:
            parsed = parse_design_ir(payload)
        except Exception as error:                          # pydantic ValidationError
            return _fail("propose_ir", f"invalid_ir:{_first_line(error)}")
        normalized = parsed.model_dump(mode="json")
        requirements = _requirement_ids(parsed)
        session = self.session()
        updated = self._commit(
            session, event="propose_ir", request_id=request_id, ir=normalized,
            status="draft", diagnosis=None,
            context={"compiled": None, "verification": None, "failure": None,
                     "requirements": requirements, "macros": []})
        return _ok("propose_ir", kind=parsed.kind, ir_hash=updated.ir_hash,
                   revision=updated.revision, requirements=requirements)

    def _tool_patch_ir(self, args: dict[str, Any], request_id: str | None):
        values = args.get("values", args.get("patch"))
        if not isinstance(values, dict):
            return _fail("patch_ir", "values_must_be_object")
        session = self.session()
        if not session.ir:
            return _fail("patch_ir", "propose_ir_first")
        merged = deepcopy(session.ir)
        path = args.get("path")
        try:
            if path:
                _merge_at_path(merged, str(path), values)
            else:
                merged.update(values)
            parsed = parse_design_ir(merged)
        except Exception as error:
            return _fail("patch_ir", f"invalid_ir:{_first_line(error)}")
        normalized = parsed.model_dump(mode="json")
        previous = list(session.context.get("requirements", []))
        current = _requirement_ids(parsed)
        allowed = args.get("allow_requirement_removal")
        removed = [clause for clause in previous if clause not in current]
        if removed and allowed is not True:
            still_open = [clause for clause in removed
                          if not (isinstance(allowed, list) and clause in allowed)]
            if still_open:
                return _fail("patch_ir", f"requirement_removed:{still_open[0]}")
        updated = self._commit(
            session, event="patch_ir", request_id=request_id, ir=normalized,
            status="draft", diagnosis=None,
            context={"compiled": None, "verification": None, "failure": None,
                     "requirements": current})
        return _ok("patch_ir", kind=parsed.kind, ir_hash=updated.ir_hash,
                   revision=updated.revision, requirements=current, removed=removed)

    # ------------------------------------------------------------- helpers
    def _axis_status(self, axis_id: str) -> str:
        capability = AXIS_BY_ID.get(axis_id)
        return capability.status if capability is not None else "planned"


def _first_line(error: Exception) -> str:
    return str(error).splitlines()[0] if str(error) else type(error).__name__


def _requirement_ids(ir: Any) -> list[str]:
    clauses = getattr(ir, "requirements", None) or []
    return [clause.id for clause in clauses]


def _merge_at_path(root: dict[str, Any], path: str, values: dict[str, Any]) -> None:
    """Merge ``values`` into the container named by a dotted IR ``path``.

    Numeric segments index lists, so a caller can patch one action or effect
    without re-sending the whole rule (``actions.1.guard``). The path must exist;
    ``patch_ir`` repairs a known IR, it does not invent structure.
    """
    parts = [part for part in path.split(".") if part]
    if not parts:
        root.update(values)
        return
    node: Any = root
    for part in parts[:-1]:
        node = _child(node, part)
    last = parts[-1]
    if isinstance(node, list):
        if not last.isdigit() or int(last) >= len(node):
            raise ValueError(f"patch_path_not_found:{last}")
        existing = node[int(last)]
        if isinstance(existing, dict):
            existing.update(values)
        else:
            node[int(last)] = values
        return
    if not isinstance(node, dict) or last not in node:
        raise ValueError(f"patch_path_not_found:{last}")
    existing = node[last]
    if isinstance(existing, dict):
        existing.update(values)
    else:
        node[last] = values


def _child(node: Any, part: str) -> Any:
    if isinstance(node, list):
        if not part.isdigit() or int(part) >= len(node):
            raise ValueError(f"patch_path_not_found:{part}")
        return node[int(part)]
    if not isinstance(node, dict) or part not in node:
        raise ValueError(f"patch_path_not_found:{part}")
    return node[part]
