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

    def __post_init__(self) -> None:
        if self.status not in STATUS_ORDER:
            raise ValueError(f"invalid_capability_status:{self.status}")

    @property
    def covered(self) -> bool:
        return self.status in COVERED_STATUSES

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "status": self.status,
                "covered": self.covered, "mechanisms": list(self.mechanisms)}


# Axes available to plans. `stable` means the host can compile AND verify a game
# that needs it; everything else is a named gap rather than a silent failure.
AXES: tuple[Capability, ...] = (
    Capability("sequential_turn", "顺序回合（wait / 分支推进）", "stable",
               ("plan.wait", "plan.branch", "state.current_player")),
    Capability("exact_expression", "精确四则算式判定", "stable",
               ("tool.exact_expression",)),
    Capability("score_settle", "显式计分与终局", "stable",
               ("tool.score_settle", "state.finished")),
    Capability("pattern_lang", "参数化牌型与合法性", "stable",
               ("tool.pattern", "tool.shedding")),
    Capability("rank_compare", "通用牌力比较（不内置玩法牌型）", "stable",
               ("tool.rank_compare", "tool.deck")),
    Capability("point_total", "牌值与软 A 求和（21 点类）", "planned",
               ("axis.point_total",)),
    Capability("info_set", "信息集与按视角可见性", "stable",
               ("state.private_hands", "view(viewer)")),
    Capability("hidden_draw", "抽取对手隐藏牌（Go Fish / 抽乌龟类）", "planned",
               ("axis.hidden_draw",)),
    Capability("turn_adapter", "墩牌 / 跟牌 / 竞叫 / 优先级", "stable",
               ("tool.trick", "state.current_player")),
    Capability("trigger", "通用触发器与效果", "planned",
               ("axis.trigger",)),
    Capability("team", "队伍与合作胜负", "stable",
               ("tool.trick.team_winners", "state.teams")),
    Capability("betting", "下注轮与边池", "planned",
               ("axis.betting",)),
    Capability("ledger", "通用资源账本 / 经济", "planned",
               ("axis.ledger",)),
    Capability("layout", "耐心 / 目标牌区与自动移动", "planned",
               ("axis.layout",)),
    Capability("macro", "声明式宏生成与注册", "planned",
               ("axis.macro",)),
    Capability("sandbox", "隔离代码逃生口（独立执行面）", "planned",
               ("protocol.sandbox",)),
)

AXIS_BY_ID = {axis.id: axis for axis in AXES}


def capability_matrix() -> dict[str, Any]:
    return {"version": "0.4",
            "status_order": list(STATUS_ORDER),
            "axes": [axis.as_dict() for axis in AXES]}


def axis_status(axis_id: str) -> str:
    return AXIS_BY_ID[axis_id].status if axis_id in AXIS_BY_ID else "planned"


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
