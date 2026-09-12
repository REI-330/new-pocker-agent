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
from .plans import (
    arithmetic_plan,
    blackjack_plan,
    crazy_eights_plan,
    five_card_poker_plan,
    go_fish_plan,
    uno_plan,
    war_plan,
    whist_plan,
)
from .rules.composed import ComposedRulesIR

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


class WhistIR(_Strict):
    """Partnership trick-taking with a turn-up trump (Whist family)."""

    schema_version: Literal["0.4"] = "0.4"
    kind: Literal["whist"]
    game_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    players: Literal[4] = 4
    cards_each: int = Field(default=5, ge=1, le=13)
    max_rounds: Literal[1] = 1
    teams: list[list[int]] = Field(default_factory=lambda: [[0, 2], [1, 3]])
    ranks: list[str] = Field(default_factory=lambda: list(DEFAULT_RANKS), min_length=2)
    suits: list[str] = Field(default_factory=lambda: list(DEFAULT_SUITS), min_length=1)

    @model_validator(mode="after")
    def executable(self) -> WhistIR:
        flat = [seat for team in self.teams for seat in team]
        if sorted(flat) != list(range(self.players)):
            raise ValueError("teams 必须正好覆盖全部座位且不重复")
        if len(self.ranks) * len(self.suits) < self.players * self.cards_each + 1:
            raise ValueError("牌组不足以发牌并留下起始牌")
        return self


class PokerIR(_Strict):
    """Heads-up five-card showdown with one no-limit betting round."""

    schema_version: Literal["0.4"] = "0.4"
    kind: Literal["poker"]
    game_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    players: Literal[2] = 2
    cards_each: int = Field(default=5, ge=5, le=7)
    stacks: int = Field(default=100, ge=1, le=1_000_000)
    min_raise: int = Field(default=10, ge=1, le=1_000_000)
    max_rounds: Literal[1] = 1
    ranks: list[str] = Field(default_factory=lambda: list(DEFAULT_RANKS), min_length=5)
    suits: list[str] = Field(default_factory=lambda: list(DEFAULT_SUITS), min_length=1)

    @model_validator(mode="after")
    def executable(self) -> PokerIR:
        if self.min_raise > self.stacks:
            raise ValueError("min_raise 不能超过起始筹码")
        if len(self.ranks) * len(self.suits) < self.players * self.cards_each:
            raise ValueError("牌组不足以发牌")
        return self


class BlackjackIR(_Strict):
    """No-betting 21: you against a dealer, one point per win."""

    schema_version: Literal["0.4"] = "0.4"
    kind: Literal["blackjack"]
    game_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    players: Literal[2] = 2
    max_rounds: int = Field(default=3, ge=1, le=20)
    target: Literal[21] = 21
    dealer_stand_on: Literal[17] = 17
    dealer_hits_soft_17: bool = False
    ranks: list[str] = Field(default_factory=lambda: list(DEFAULT_RANKS), min_length=13)
    suits: list[str] = Field(default_factory=lambda: list(DEFAULT_SUITS), min_length=1)

    @model_validator(mode="after")
    def executable(self) -> BlackjackIR:
        if len(self.ranks) * len(self.suits) < 4:
            raise ValueError("牌组至少需要 4 张牌")
        return self


class GoFishIR(_Strict):
    """Two-player Go Fish: ask for a rank from the hidden hand, pairs score."""

    schema_version: Literal["0.4"] = "0.4"
    kind: Literal["go_fish"]
    game_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    players: Literal[2] = 2
    cards_each: int = Field(default=5, ge=1, le=10)
    max_rounds: Literal[1] = 1
    ranks: list[str] = Field(default_factory=lambda: list(DEFAULT_RANKS), min_length=4)
    suits: list[str] = Field(default_factory=lambda: list(DEFAULT_SUITS), min_length=1)

    @model_validator(mode="after")
    def executable(self) -> GoFishIR:
        if len(self.ranks) * len(self.suits) <= self.players * self.cards_each:
            raise ValueError("牌组必须留出可摸的牌堆")
        return self


class UnoIR(_Strict):
    """Matching game with declarative special-card effects (UNO family)."""

    schema_version: Literal["0.4"] = "0.4"
    kind: Literal["uno"]
    game_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    players: Literal[2] = 2
    hand_size: int = Field(default=5, ge=1, le=10)
    wild_rank: str | None = "8"
    draw_two_rank: str | None = "2"
    skip_rank: str | None = "K"
    max_rounds: Literal[1] = 1
    ranks: list[str] = Field(default_factory=lambda: list(DEFAULT_RANKS), min_length=2)
    suits: list[str] = Field(default_factory=lambda: list(DEFAULT_SUITS), min_length=1)

    @model_validator(mode="after")
    def executable(self) -> UnoIR:
        for rank in (self.wild_rank, self.draw_two_rank, self.skip_rank):
            if rank is not None and rank not in self.ranks:
                raise ValueError("特殊牌点数必须在 ranks 中")
        if len(self.ranks) * len(self.suits) <= self.players * self.hand_size + 1:
            raise ValueError("牌组不足以发牌并留下起始牌")
        return self


RulesIR = Annotated[
    ArithmeticIR | WarIR | SheddingIR | WhistIR | PokerIR | BlackjackIR | GoFishIR | UnoIR,
    Field(discriminator="kind")]
IR_ADAPTER = TypeAdapter(RulesIR)

# G2/M2: the design entry adds a ``composed`` discriminant branch (ADR-0005). The
# known-eight adapter stays separate so the agent prompt keeps advertising only
# the families it can host-compile; the design entry accepts both and is what the
# M2 compiler reads.
DesignIR = Annotated[
    ArithmeticIR | WarIR | SheddingIR | WhistIR | PokerIR | BlackjackIR | GoFishIR | UnoIR
    | ComposedRulesIR,
    Field(discriminator="kind")]
DESIGN_ADAPTER = TypeAdapter(DesignIR)

HOST_COMPILED = ("arithmetic", "war", "shedding", "whist", "poker", "blackjack", "go_fish",
                 "uno")
REQUIRED_AXES: dict[str, tuple[str, ...]] = {
    "arithmetic": ("sequential_turn", "exact_expression", "score_settle"),
    "war": ("sequential_turn", "rank_compare", "score_settle"),
    "shedding": ("sequential_turn", "pattern_lang", "info_set"),
    "whist": ("sequential_turn", "turn_adapter", "team", "pattern_lang"),
    "poker": ("sequential_turn", "betting", "ledger", "hand_rank", "info_set"),
    "blackjack": ("sequential_turn", "point_total", "info_set", "score_settle"),
    "go_fish": ("sequential_turn", "hidden_draw", "info_set"),
    "uno": ("sequential_turn", "pattern_lang", "info_set", "trigger"),
}


def parse_ir(payload: dict) -> ArithmeticIR | WarIR:
    return IR_ADAPTER.validate_python(payload)


def parse_design_ir(payload: dict) -> ComposedRulesIR | ArithmeticIR | WarIR:
    """Parse any design entry, including the ``composed`` branch (G2/M2)."""
    return DESIGN_ADAPTER.validate_python(payload)


def required_axes(ir: ArithmeticIR | WarIR) -> list[str]:
    if isinstance(ir, ComposedRulesIR):
        from .rules.requirements import axes_for
        return list(axes_for(ir))
    return list(REQUIRED_AXES[ir.kind])


def check_ir(ir: ArithmeticIR | WarIR) -> CapabilityReport:
    if isinstance(ir, ComposedRulesIR):
        from .rules.requirements import axes_for
        return capability_check(axes_for(ir))
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
    if isinstance(ir, WhistIR):
        return whist_plan(cards_each=ir.cards_each, teams=ir.teams,
                          ranks=ir.ranks, suits=ir.suits)
    if isinstance(ir, PokerIR):
        return five_card_poker_plan(stacks=ir.stacks, min_raise=ir.min_raise,
                                    cards_each=ir.cards_each, ranks=ir.ranks, suits=ir.suits)
    if isinstance(ir, BlackjackIR):
        return blackjack_plan(max_rounds=ir.max_rounds, target=ir.target,
                              dealer_stand_on=ir.dealer_stand_on,
                              dealer_hits_soft_17=ir.dealer_hits_soft_17,
                              ranks=ir.ranks, suits=ir.suits)
    if isinstance(ir, GoFishIR):
        return go_fish_plan(cards_each=ir.cards_each, ranks=ir.ranks, suits=ir.suits)
    if isinstance(ir, UnoIR):
        return uno_plan(hand_size=ir.hand_size, wild_rank=ir.wild_rank,
                        draw_two_rank=ir.draw_two_rank, skip_rank=ir.skip_rank,
                        ranks=ir.ranks, suits=ir.suits)
    raise ValueError(f"no_host_compiler:{getattr(ir, 'kind', 'unknown')}")
