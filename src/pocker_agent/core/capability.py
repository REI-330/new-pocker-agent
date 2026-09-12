"""Capability status machine and coverage check.

A game can only be called *expressible* when every axis it needs is `stable`.
`planned` must never count as covered, and `experimental` may only produce a
draft (never a finalized, playable session) — see docs/engineering-standards.md
and docs/development-design-v0.4.md §11.

This is the arbiter that answers "which missing category of tool blocks this
game?" without guessing.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

STATUS_ORDER = ("planned", "experimental", "stable", "deprecated")
COVERED_STATUSES = frozenset({"stable"})


@dataclass(frozen=True)
class Capability:
    id: str
    title: str
    status: str
    mechanisms: tuple[str, ...] = ()
    # A gap is a claim about the host, so it must say what is actually missing.
    # Non-stable axes are required to carry one (enforced by an architecture test).
    note: str = ""

    def __post_init__(self) -> None:
        if self.status not in STATUS_ORDER:
            raise ValueError(f"invalid_capability_status:{self.status}")

    @property
    def covered(self) -> bool:
        return self.status in COVERED_STATUSES

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "status": self.status,
                "covered": self.covered, "mechanisms": list(self.mechanisms),
                "note": self.note}


# Axes available to plans. `stable` means the host can compile AND verify a game
# that needs it; everything else is a named gap rather than a silent failure.
AXES: tuple[Capability, ...] = (
    Capability("sequential_turn", "顺序回合（wait / 分支推进）", "stable",
               ("plan.wait", "plan.branch", "state.current_player")),
    Capability("exact_expression", "精确四则算式判定", "stable",
               ("tool.exact_expression.solve", "tool.exact_expression.validate")),
    Capability("score_settle", "显式计分与终局", "stable",
               ("tool.score_settle.call", "tool.winner_resolve.call", "state.finished")),
    Capability("pattern_lang", "参数化牌型与合法性", "stable",
               ("tool.pattern.match", "tool.pattern.choices", "tool.matching.play")),
    Capability("rank_compare", "通用牌力比较（不内置玩法牌型）", "stable",
               ("tool.rank_compare.call", "tool.deck.deal")),
    Capability("hand_rank", "五张牌型评分与比较（扑克类）", "stable",
               ("tool.hand_rank.best", "tool.hand_rank.compare")),
    Capability("point_total", "牌值与软 A 求和（21 点类）", "stable",
               ("tool.point_total.total", "tool.point_total.settle")),
    Capability("info_set", "信息集与按视角可见性", "stable",
               ("state.private_hands", "view(viewer)")),
    Capability("hidden_draw", "抽取对手隐藏牌（Go Fish / 抽乌龟类）", "stable",
               ("tool.hidden_draw.askable", "tool.hidden_draw.ask"),
               note="只覆按点数询问并取牌；不含按隐藏位置盲抽。"),
    Capability("turn_adapter", "墩牌 / 跟牌 / 优先级", "stable",
               ("tool.trick.legal", "tool.trick.play", "state.current_player"),
               note="不含竞叫（bidding/auction）：trick 工具没有叫牌轮与叫品级别。"),
    Capability("trigger", "特殊牌效果 / 连锁触发", "stable",
               ("tool.trigger.apply",)),
    Capability("simultaneous", "同时行动 / 抢牌反应", "planned",
               ("axis.simultaneous",),
               note="缺同时行动原语：wait 只能挂住一个座位，无法表达同时亮牌/抢牌反应。"),
    Capability("team", "队伍与合作胜负", "stable",
               ("tool.trick.play", "state.teams")),
    Capability("betting", "下注轮与边池", "stable",
               ("tool.betting.legal", "tool.betting.act", "tool.ledger.settle")),
    Capability("ledger", "通用资源账本 / 经济", "stable",
               ("tool.ledger.commit", "tool.ledger.settle", "state.stacks")),
    Capability("layout", "耐心 / 目标牌区与自动移动", "planned",
               ("axis.layout",),
               note="缺区域原语：没有 tableau/foundation 这类牌区，也没有自动翻牌与再发牌规则。"),
    Capability("macro", "声明式宏生成与注册", "planned",
               ("axis.macro",),
               note="宏的构建期内联已实现（core/macros.py，match_turn 被 2 个计划复用），"
                    "但模型没有生成/注册宏的元工具，对 agent 而言仍是缺口；且无语料玩法需要它。"),
    Capability("sandbox", "隔离代码逃生口（独立执行面）", "planned",
               ("protocol.sandbox",),
               note="需要独立的隔离执行协议，而不是一个工具：逃生口必须与 Interpreter 分开，且不能绕过契约。"),
)

AXIS_BY_ID = {axis.id: axis for axis in AXES}


def capability_matrix() -> dict[str, Any]:
    return {"version": "0.4",
            "status_order": list(STATUS_ORDER),
            "axes": [axis.as_dict() for axis in AXES]}


def axis_status(axis_id: str) -> str:
    return AXIS_BY_ID[axis_id].status if axis_id in AXIS_BY_ID else "planned"


def mechanism_problems(registry: Any,
                       axes: tuple[Capability, ...] = ()) -> list[dict[str, str]]:
    """Check every axis claim against the registry it claims to describe.

    ``mechanisms`` used to be free-form labels nothing verified, so an axis could
    be marked ``stable`` on a tool that does not exist. A coverage label is a
    claim about the host, so it has to be checkable against the same registry the
    interpreter enforces; otherwise corpus coverage is a label, not a capability.

    Only ``tool.<name>[.<operation>]`` entries are checked here. ``state.*``,
    ``plan.*``, ``axis.*`` and ``protocol.*`` name concepts that are not registry
    entries, and are reported separately by the architecture tests.
    """
    problems: list[dict[str, str]] = []
    names = set(registry.names())
    operations = {name: {op.name for op in registry.spec(name).operations} for name in names}
    for axis in (axes or AXES):
        for mechanism in axis.mechanisms:
            parts = mechanism.split(".")
            if not parts or parts[0] != "tool":
                continue
            if len(parts) < 2 or parts[1] not in names:
                problems.append({"axis": axis.id, "mechanism": mechanism,
                                 "problem": "unknown_tool"})
            elif len(parts) >= 3 and parts[2] not in operations[parts[1]]:
                problems.append({"axis": axis.id, "mechanism": mechanism,
                                 "problem": "unknown_operation"})
    return problems


@dataclass
class CapabilityReport:
    expressible: bool
    required_axes: list[str]
    covered_axes: list[str] = field(default_factory=list)
    missing: list[dict[str, Any]] = field(default_factory=list)
    unknown_axes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"expressible": self.expressible, "required_axes": self.required_axes,
                "covered_axes": self.covered_axes, "missing": self.missing,
                "unknown_axes": self.unknown_axes}


def capability_check(required_axes: Iterable[str]) -> CapabilityReport:
    """Return what is covered and, for each gap, the axis and its status."""
    required = sorted(set(required_axes))
    covered, missing, unknown = [], [], []
    for axis_id in required:
        capability = AXIS_BY_ID.get(axis_id)
        if capability is None:
            unknown.append(axis_id)
            missing.append({"axis": axis_id, "status": "planned",
                            "reason": "unknown_axis_not_in_matrix"})
        elif capability.covered:
            covered.append(axis_id)
        else:
            missing.append({"axis": axis_id, "status": capability.status,
                            "reason": f"axis_status_is_{capability.status}"})
    return CapabilityReport(expressible=not missing, required_axes=required,
                            covered_axes=covered, missing=missing, unknown_axes=unknown)
