"""The core tool whitelist.

Only operations listed here can be reached from a plan. ``export`` is the
description handed to the agent loop, so the model's view of the host is
generated from the same declaration the interpreter enforces.

Contract policy for the core tools:

- mechanism tools (``logic`` / ``exact_expression`` / ``solvable_deal`` /
  ``score_settle``) declare ``effects=()``: they may only *return* values,
  never write shared state. The interpreter rejects any out-of-contract write.
- only ``state.update`` may write arbitrary keys, so its ``effects`` is ``"*"``.
"""
from __future__ import annotations

from .contracts import OperationSpec, ToolRegistry, ToolSpec
from .hidden_tools import HiddenDrawTool
from .point_tools import PointTotalTool
from .poker_tools import BettingTool, HandRankTool, LedgerTool
from .tools import (
    DeckTool,
    ExactExpressionTool,
    LogicTool,
    MatchingTool,
    PatternTool,
    RankCompareTool,
    ScoreSettleTool,
    SolvableDealTool,
    StateTool,
    TrickTool,
    WinnerResolveTool,
)
from .trigger_tools import TriggerTool

# The solver operations read the current table; an empty or missing table is a
# contract violation rather than a silent empty-puzzle answer.
_HAS_TABLE = ({"gt": [{"count": ["$state.table"]}, 0]},)


def core_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolSpec("state", lambda **_: StateTool(), (
        OperationSpec("update", params=("state", "values"), effects=("*",),
                      returns="sorted keys written"),)))
    registry.register(ToolSpec("logic", lambda **_: LogicTool(), (
        OperationSpec("evaluate", params=("expression",), effects=(), returns="any"),)))
    registry.register(ToolSpec(
        "exact_expression",
        lambda target=24, operations=("+", "-", "*", "/"), fractional=True, rank_values=None:
            ExactExpressionTool(target, operations, fractional, rank_values),
        (OperationSpec("solve", params=("numbers",), effects=(), requires=_HAS_TABLE, returns="str|None"),
         OperationSpec("validate", params=("expression", "numbers"), effects=(), requires=_HAS_TABLE, returns="object"),
         OperationSpec("assert_unsolvable", params=("numbers",), effects=(), requires=_HAS_TABLE, returns="object"))))
    registry.register(ToolSpec(
        "solvable_deal",
        lambda **config: SolvableDealTool(**config),
        (OperationSpec("deal", params=("seed", "cards_each"), effects=(), returns="hand/numbers/attempt"),)))
    registry.register(ToolSpec("score_settle", lambda **_: ScoreSettleTool(), (
        OperationSpec("call", params=("scores", "winners", "points"), effects=(), returns="scores"),)))
    registry.register(ToolSpec(
        "deck",
        lambda ranks, suits, copies=1, values=None: DeckTool(ranks, suits, copies, values),
        (OperationSpec("cards", params=(), effects=(), returns="list[CardRef]"),
         OperationSpec("shuffled", params=("seed",), effects=(), returns="list[CardRef]"),
         OperationSpec("deal", params=("seed", "hands", "cards_each", "kitty"), effects=(),
                       returns="hands/kitty/deck"),
         OperationSpec("draw", params=("stock", "hand", "count"),
                       effects=("stock", "hands"), returns="drawn/hand_size"),)))
    registry.register(ToolSpec("rank_compare", lambda **_: RankCompareTool(), (
        OperationSpec("call", params=("left", "right"), effects=(),
                      returns="outcome/left/right"),)))
    registry.register(ToolSpec("winner_resolve", lambda **_: WinnerResolveTool(), (
        OperationSpec("call", params=("values", "mode"), effects=(), returns="winners"),)))
    registry.register(ToolSpec("pattern", lambda **_: PatternTool(), (
        OperationSpec("match", params=("card", "top", "active_suit", "wild_ranks"), effects=(),
                      returns="bool"),
        OperationSpec("choices", params=("cards", "top", "active_suit", "wild_ranks"), effects=(),
                      returns="list[int]"),
        OperationSpec("describe", params=("cards",), effects=(), returns="rank/suit/value/id"),
        OperationSpec("classify", params=("cards", "pattern"), effects=(),
                      returns="kind/rank/length/cards"),
        OperationSpec("beats", params=("candidate", "previous", "bombs"), effects=(),
                      returns="bool"))))
    registry.register(ToolSpec("matching", lambda **_: MatchingTool(), (
        OperationSpec("play", params=("state", "hand_index", "card_index", "declared_suit", "wild_ranks", "suits"),
                      effects=("hands", "table", "discard", "active_suit", "finished", "winners", "phase"),
                      returns="played/rank/active_suit/hand_size"),
        OperationSpec("draw", params=("state", "hand_index", "seed", "recycle"),
                      effects=("hands", "stock", "table", "discard"), returns="hand_size"),)))
    registry.register(ToolSpec("trick", lambda teams=None: TrickTool(teams), (
        OperationSpec("legal", params=("state", "hand_index"), effects=(), returns="list[int]"),
        OperationSpec("play", params=("state", "hand_index", "card_index", "trump"),
                      effects=("hands", "trick", "trick_seats", "led_suit", "table",
                               "current_player", "tricks_won", "trick_index", "finished",
                               "winners", "phase"),
                      returns="complete/winner/tricks_won"),)))
    registry.register(ToolSpec("ledger", lambda **_: LedgerTool(), (
        OperationSpec("commit", params=("state", "seat", "amount"),
                      effects=("stacks", "committed", "hand_committed"), returns="seat/amount/stacks"),
        OperationSpec("pots", params=("state",), effects=(), returns="pots"),
        OperationSpec("total", params=("state",), effects=(), returns="int"),
        OperationSpec("settle", params=("state", "winners"),
                      effects=("stacks", "committed", "hand_committed"), returns="awards/pots/stacks"),)))
    registry.register(ToolSpec("betting", lambda min_raise=1: BettingTool(min_raise), (
        OperationSpec("legal", params=("state",), effects=(), returns="list[str]"),
        OperationSpec("act", params=("state", "action", "amount"),
                      effects=("stacks", "committed", "hand_committed", "folded", "acted",
                               "current_bet", "min_raise", "current_player", "street_done"),
                      returns="action/seat/street_done"),)))
    registry.register(ToolSpec("hand_rank", lambda **_: HandRankTool(), (
        OperationSpec("best", params=("cards",), effects=(), returns="category/score/cards"),
        OperationSpec("compare", params=("left", "right"), effects=(), returns="outcome"),)))
    registry.register(ToolSpec("point_total", lambda target=21: PointTotalTool(target), (
        OperationSpec("total", params=("cards",), effects=(), returns="total/soft/bust/cards"),
        OperationSpec("dealer_play", params=("stock", "hand", "stand_on", "hits_soft"),
                      effects=("stock", "hands"), returns="total/soft/bust/draws"),
        OperationSpec("settle", params=("player", "dealer", "player_natural", "dealer_natural",
                                         "first_bust_loses"), effects=(), returns="winner/reason"),)))
    registry.register(ToolSpec("hidden_draw", lambda **_: HiddenDrawTool(), (
        OperationSpec("askable", params=("state", "seat"), effects=(), returns="list[str]"),
        OperationSpec("ask", params=("state", "action", "asker"),
                      effects=("hands", "stock"), returns="got/fished/drawn"),
        OperationSpec("discard_pairs", params=("state", "seat"),
                      effects=("hands", "pairs"), returns="pairs/hand_size/total"),
        OperationSpec("refill", params=("state", "seat"), effects=("hands", "stock"),
                      returns="drew"),
        OperationSpec("is_finished", params=("state",), effects=(), returns="bool"),)))
    registry.register(ToolSpec("trigger", lambda **_: TriggerTool(), (
        OperationSpec("apply", params=("state", "effects"),
                      effects=("hands", "stock", "table", "skip", "direction", "active_suit"),
                      returns="applied/count"),)))
    return registry
