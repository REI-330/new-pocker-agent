"""RulesIR: the human-reviewable contract that precedes an executable plan.

RulesIR is what the user confirms; GamePlan is what the interpreter runs.
``host_compile`` deterministically lowers a known family; families without a
host compiler must be composed by the agent and still pass ``playtest``.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .capability import CapabilityReport, capability_check
from .plan import GamePlan
from .plans import arithmetic_plan, crazy_eights_plan, war_plan

DEFAULT_RANK_VALUES = {"A": 1, **{str(n): n for n in range(2, 11)}, "J": 11, "Q": 12, "K": 13}
DEFAULT_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
DEFAULT_SUITS = ["S", "H", "D", "C"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArithmeticIR(_Strict):
    """Solo arithmetic drill (24-point family)."""

    schema_version: Literal["0.4"] = "0.4"
    kind: Literal["arithmetic"]
    game_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    players: Literal[1] = 1
    max_rounds: int = Field(ge=1, le=20)
    target: int = Field(ge=1, le=1000)
    card_count: Literal[4] = 4
    operations: list[Literal["+", "-", "*", "/"]] = Field(
        default_factory=lambda: ["+", "-", "*", "/"], min_length=1, max_length=4)
    fractional_intermediates: bool = True
    rank_values: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_RANK_VALUES))
    deal_mode: Literal["random", "solvable"] = "solvable"
    deck_ranks: list[str] = Field(default_factory=lambda: list(DEFAULT_RANKS), min_length=1)
    deck_suits: list[str] = Field(default_factory=lambda: list(DEFAULT_SUITS), min_length=1)

    @model_validator(mode="after")
    def executable(self) -> ArithmeticIR:
        if len(set(self.operations)) != len(self.operations):
            raise ValueError("operations 不得重复")
        if set(self.rank_values) != set(self.deck_ranks):
            raise ValueError("rank_values 必须覆盖 deck_ranks 的每一种牌点")
        if any(type(value) is not int or not 1 <= value <= 100 for value in self.rank_values.values()):
            raise ValueError("rank_values 必须是 1 到 100 的整数")
        if len(self.deck_ranks) * len(self.deck_suits) < self.card_count:
            raise ValueError("牌组至少需要 card_count 张牌")
        return self


class WarIR(_Strict):
    """Two-player rank duel: each round both draw one card, higher scores."""

    schema_version: Literal["0.4"] = "0.4"
    kind: Literal["war"]
    game_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    players: Literal[2] = 2
    max_rounds: int = Field(ge=1, le=50)
    ranks: list[str] = Field(default_factory=lambda: ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"],
                             min_length=2)
    suits: list[str] = Field(default_factory=lambda: list(DEFAULT_SUITS), min_length=1)


class SheddingIR(_Strict):
    """Two-player matching game with hidden hands (Crazy Eights family)."""

    schema_version: Literal["0.4"] = "0.4"
    kind: Literal["shedding"]
    game_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    players: Literal[2] = 2
    hand_size: int = Field(default=5, ge=1, le=10)
    wild_rank: str | None = "8"
    max_rounds: Literal[1] = 1
    ranks: list[str] = Field(default_factory=lambda: list(DEFAULT_RANKS), min_length=2)
    suits: list[str] = Field(default_factory=lambda: list(DEFAULT_SUITS), min_length=1)

    @model_validator(mode="after")
    def executable(self) -> SheddingIR:
        if self.wild_rank is not None and self.wild_rank not in self.ranks:
            raise ValueError("wild_rank 必须在 ranks 中")
        if len(self.ranks) * len(self.suits) <= 2 * self.hand_size + 1:
            raise ValueError("牌组不足以发牌并留下起始牌")
        return self


RulesIR = Annotated[ArithmeticIR | WarIR | SheddingIR, Field(discriminator="kind")]
IR_ADAPTER = TypeAdapter(RulesIR)

HOST_COMPILED = ("arithmetic", "war", "shedding")
REQUIRED_AXES: dict[str, tuple[str, ...]] = {
    "arithmetic": ("sequential_turn", "exact_expression", "score_settle"),
    "war": ("sequential_turn", "rank_compare", "score_settle"),
    "shedding": ("sequential_turn", "pattern_lang", "info_set"),
}


def parse_ir(payload: dict) -> ArithmeticIR | WarIR:
    return IR_ADAPTER.validate_python(payload)


def required_axes(ir: ArithmeticIR | WarIR) -> list[str]:
    return list(REQUIRED_AXES[ir.kind])


def check_ir(ir: ArithmeticIR | WarIR) -> CapabilityReport:
    return capability_check(required_axes(ir))


def is_host_compiled(ir: ArithmeticIR | WarIR) -> bool:
    return ir.kind in HOST_COMPILED


def host_compile(ir: ArithmeticIR | WarIR) -> GamePlan:
    if isinstance(ir, ArithmeticIR):
        return arithmetic_plan(target=ir.target, max_rounds=ir.max_rounds,
                               card_count=ir.card_count, operations=tuple(ir.operations),
                               fractional=ir.fractional_intermediates,
                               rank_values=ir.rank_values, deck_ranks=ir.deck_ranks,
                               deck_suits=ir.deck_suits, deal_mode=ir.deal_mode)
    if isinstance(ir, WarIR):
        return war_plan(max_rounds=ir.max_rounds, ranks=ir.ranks, suits=ir.suits)
    if isinstance(ir, SheddingIR):
        return crazy_eights_plan(hand_size=ir.hand_size, wild_rank=ir.wild_rank,
                                 ranks=ir.ranks, suits=ir.suits)
    raise ValueError(f"no_host_compiler:{getattr(ir, 'kind', 'unknown')}")
