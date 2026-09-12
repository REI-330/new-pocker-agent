"""The core tool whitelist.

Only operations listed here can be reached from a plan. ``export`` is the
description handed to the agent loop, so the model's view of the host is
generated from the same declaration the interpreter enforces.

Contract policy for the core tools:

- mechanism tools (``logic`` / ``exact_expression`` / ``solvable_deal`` /
  ``score_settle`` / ``zones.select`` ...) declare ``effects=()``: they may only
  *return* values, never write shared state. The interpreter rejects any
  out-of-contract write.
- only ``state.update`` may write arbitrary keys, so its ``effects`` is ``"*"``.

Every operation also declares ``input_schema`` / ``output_schema`` / ``reads`` /
``feature_constraints`` (see ``contracts.py``); the tool declares the binding
``config_schema`` once and it is copied onto each of its operations.
"""
from __future__ import annotations

from .contracts import (
    ANY,
    ANY_LIST,
    BOOLEAN,
    CARD_LIST,
    INT_LIST,
    INTEGER,
    OBJECT,
    STR_LIST,
    STRING,
    OperationSpec,
    ToolRegistry,
    ToolSpec,
    array,
    obj,
    one_of,
)
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
from .zone_tools import ZonesTool

# The solver operations read the current table; an empty or missing table is a
# contract violation rather than a silent empty-puzzle answer.
_HAS_TABLE = ({"gt": [{"count": ["$state.table"]}, 0]},)

_DECK_CONFIG = obj({"ranks": STR_LIST, "suits": STR_LIST, "copies": INTEGER,
                    "values": OBJECT}, ("ranks", "suits"))
_SOLVER_CONFIG = obj({"target": INTEGER, "operations": STR_LIST, "fractional": BOOLEAN,
                      "rank_values": OBJECT})
_EXPRESSION_CONFIG = _SOLVER_CONFIG
_SOLVABLE_CONFIG = obj({"ranks": STR_LIST, "suits": STR_LIST, "target": INTEGER,
                        "operations": STR_LIST, "fractional": BOOLEAN,
                        "rank_values": OBJECT, "deal_mode": STRING, "attempts": INTEGER})
_TRICK_CONFIG = obj({"teams": array(INT_LIST)})
_BETTING_CONFIG = obj({"min_raise": INTEGER})
_POINT_CONFIG = obj({"target": INTEGER})


def _match_args() -> dict:
    # `card` and `top` are positional-required in PatternTool.match; declaring
    # them optional let a composition compiler accept a call that always fails.
    return obj({"card": ANY, "top": ANY, "active_suit": STRING, "wild_ranks": STR_LIST},
               ("card", "top"))


def core_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolSpec("state", lambda **_: StateTool(), (
        OperationSpec("update", params=("state", "values"), effects=("*",),
                      returns="sorted keys written",
                      input_schema=obj({"state": OBJECT, "values": OBJECT}, ("state", "values")),
                      output_schema=STR_LIST, feature_constraints=("explicit_order",)),)))
    registry.register(ToolSpec("logic", lambda **_: LogicTool(), (
        OperationSpec("evaluate", params=("expression",), effects=(), returns="any",
                      input_schema=obj({"expression": ANY}, ("expression",)),
                      output_schema=ANY, feature_constraints=("arithmetic",)),)))
    registry.register(ToolSpec(
        "exact_expression",
        lambda target=24, operations=("+", "-", "*", "/"), fractional=True, rank_values=None:
            ExactExpressionTool(target, operations, fractional, rank_values),
        (OperationSpec("solve", params=("numbers",), effects=(), requires=_HAS_TABLE,
                       returns="str|None", reads=("table",),
                       input_schema=obj({"numbers": ANY_LIST}, ("numbers",)),
                       output_schema={"type": ["string", "null"]},
                       feature_constraints=("arithmetic",)),
         OperationSpec("validate", params=("expression", "numbers"), effects=(),
                       requires=_HAS_TABLE, returns="object", reads=("table",),
                       input_schema=obj({"expression": STRING, "numbers": ANY_LIST},
                                        ("expression", "numbers")),
                       output_schema=obj({"correct": BOOLEAN, "target": INTEGER}),
                       feature_constraints=("arithmetic",)),
         OperationSpec("assert_unsolvable", params=("numbers",), effects=(),
                       requires=_HAS_TABLE, returns="object", reads=("table",),
                       input_schema=obj({"numbers": ANY_LIST}, ("numbers",)),
                       output_schema=obj({"unsolvable": BOOLEAN}),
                       feature_constraints=("arithmetic",))),
        config_schema=_EXPRESSION_CONFIG))
    registry.register(ToolSpec(
        "solvable_deal",
        lambda **config: SolvableDealTool(**config),
        (OperationSpec("deal", params=("seed", "cards_each"), effects=(),
                       returns="hand/numbers/attempt",
                       input_schema=obj({"seed": ANY, "cards_each": INTEGER}, ("seed",)),
                       output_schema=obj({"hand": CARD_LIST, "numbers": INT_LIST,
                                          "attempt": INTEGER}),
                       feature_constraints=("arithmetic",)),),
        config_schema=_SOLVABLE_CONFIG))
    registry.register(ToolSpec("score_settle", lambda **_: ScoreSettleTool(), (
        OperationSpec("call", params=("scores", "winners", "points"), effects=(),
                      returns="scores",
                      input_schema=obj({"scores": INT_LIST, "winners": INT_LIST,
                                        "points": INTEGER}, ("scores", "winners")),
                      output_schema=INT_LIST, feature_constraints=("scoring",)),)))
    registry.register(ToolSpec(
        "deck",
        lambda ranks, suits, copies=1, values=None: DeckTool(ranks, suits, copies, values),
        (OperationSpec("cards", method="catalog", params=(), effects=(), returns="list[CardRef]",
                       input_schema=obj(), output_schema=CARD_LIST,
                       feature_constraints=("card_identity",)),
         OperationSpec("shuffled", params=("seed",), effects=(), returns="list[CardRef]",
                       input_schema=obj({"seed": ANY}, ("seed",)), output_schema=CARD_LIST,
                       feature_constraints=("card_identity",)),
         OperationSpec("deal", params=("seed", "hands", "cards_each", "kitty"), effects=(),
                       returns="hands/kitty/deck",
                       input_schema=obj({"seed": ANY, "hands": INTEGER, "cards_each": INTEGER,
                                         "kitty": INTEGER}, ("seed", "hands")),
                       output_schema=obj({"hands": array(CARD_LIST), "kitty": CARD_LIST,
                                          "deck": CARD_LIST}),
                       feature_constraints=("card_identity",)),
         OperationSpec("draw", params=("stock", "hand", "count"),
                       effects=("stock", "hands"), returns="drawn/hand_size",
                       reads=("stock", "hands"),
                       input_schema=obj({"stock": CARD_LIST, "hand": CARD_LIST,
                                         "count": INTEGER}, ("stock", "hand")),
                       output_schema=obj({"drawn": STR_LIST, "hand_size": INTEGER}),
                       feature_constraints=("card_identity", "deck_exhaustion"))),
        config_schema=_DECK_CONFIG))
    registry.register(ToolSpec("rank_compare", lambda **_: RankCompareTool(), (
        OperationSpec("call", params=("left", "right"), effects=(),
                      returns="outcome/left/right",
                      input_schema=obj({"left": ANY, "right": ANY}, ("left", "right")),
                      output_schema=obj({"outcome": STRING, "left": INTEGER, "right": INTEGER}),
                      feature_constraints=("scoring",)),)))
    registry.register(ToolSpec("winner_resolve", lambda **_: WinnerResolveTool(), (
        OperationSpec("call", params=("values", "mode"), effects=(), returns="winners",
                      input_schema=obj({"values": INT_LIST, "mode": STRING}, ("values",)),
                      output_schema=INT_LIST, feature_constraints=("terminal_rule",)),)))
    registry.register(ToolSpec("pattern", lambda **_: PatternTool(), (
        OperationSpec("match", params=("card", "top", "active_suit", "wild_ranks"), effects=(),
                      returns="bool", input_schema=_match_args(), output_schema=BOOLEAN,
                      feature_constraints=("matching",)),
        OperationSpec("choices", params=("cards", "top", "active_suit", "wild_ranks"), effects=(),
                      returns="list[int]",
                      input_schema=obj({"cards": CARD_LIST, "top": ANY, "active_suit": STRING,
                                        "wild_ranks": STR_LIST}, ("cards",)),
                      output_schema=INT_LIST, feature_constraints=("matching",)),
        OperationSpec("describe", params=("cards",), effects=(),
                      returns="rank/suit/value/id",
                      input_schema=obj({"cards": ANY}, ("cards",)),
                      output_schema=obj({"rank": STRING, "suit": STRING, "value": INTEGER,
                                         "id": STRING}),
                      feature_constraints=("card_identity",)),
        OperationSpec("classify", params=("cards", "pattern"), effects=(),
                      returns="kind/rank/length/cards",
                      input_schema=obj({"cards": CARD_LIST, "pattern": OBJECT},
                                       ("cards", "pattern")),
                      output_schema=obj({"kind": STRING, "rank": INTEGER, "min_rank": INTEGER,
                                         "length": INTEGER, "cards": STR_LIST}),
                      feature_constraints=("matching",)),
        OperationSpec("beats", params=("candidate", "previous", "bombs"), effects=(),
                      returns="bool",
                      input_schema=obj({"candidate": OBJECT, "previous": OBJECT,
                                        "bombs": STR_LIST}, ("candidate", "previous")),
                      output_schema=BOOLEAN, feature_constraints=("matching",)))))
    registry.register(ToolSpec("matching", lambda **_: MatchingTool(), (
        OperationSpec("play", params=("state", "hand_index", "card_index", "declared_suit",
                                      "wild_ranks", "suits"),
                      effects=("hands", "table", "discard", "active_suit"),
                      returns="played/rank/active_suit/hand_size",
                      reads=("hands", "table", "active_suit", "discard"),
                      input_schema=obj({"state": OBJECT, "hand_index": INTEGER,
                                        "card_index": INTEGER, "declared_suit": STRING,
                                        "wild_ranks": STR_LIST, "suits": STR_LIST},
                                       ("state", "hand_index", "card_index")),
                      output_schema=obj({"played": STRING, "rank": STRING,
                                         "active_suit": STRING, "hand_size": INTEGER,
                                         "hand_empty": BOOLEAN}),
                      feature_constraints=("matching", "card_identity", "sequential_turn")),
        OperationSpec("draw", params=("state", "hand_index", "seed", "recycle"),
                      effects=("hands", "stock", "table", "discard"),
                      returns="hand_size",
                      reads=("hands", "stock", "discard", "table"),
                      input_schema=obj({"state": OBJECT, "hand_index": INTEGER, "seed": ANY,
                                        "recycle": BOOLEAN}, ("state", "hand_index")),
                      output_schema=obj({"hand_size": INTEGER, "recycled": BOOLEAN}),
                      feature_constraints=("deck_exhaustion", "explicit_order")))))
    registry.register(ToolSpec("trick", lambda teams=None: TrickTool(teams), (
        OperationSpec("legal", params=("state", "hand_index"), effects=(), returns="list[int]",
                      reads=("hands", "led_suit"),
                      input_schema=obj({"state": OBJECT, "hand_index": INTEGER},
                                       ("state", "hand_index")),
                      output_schema=INT_LIST, feature_constraints=("turn_adapter", "matching")),
        OperationSpec("play", params=("state", "hand_index", "card_index", "trump"),
                      effects=("hands", "trick", "trick_seats", "led_suit", "table",
                               "current_player", "tricks_won", "trick_index", "finished",
                               "winners", "phase"),
                      returns="complete/winner/tricks_won",
                      reads=("hands", "trick", "trick_seats", "led_suit", "tricks_won",
                             "trick_index", "tricks_total"),
                      input_schema=obj({"state": OBJECT, "hand_index": INTEGER,
                                        "card_index": INTEGER, "trump": STRING},
                                       ("state", "hand_index", "card_index")),
                      output_schema=one_of(
                          obj({"complete": BOOLEAN, "player": INTEGER},
                              ("complete", "player")),
                          obj({"complete": BOOLEAN, "winner": INTEGER, "tricks_won": INT_LIST},
                              ("complete", "winner", "tricks_won"))),
                      feature_constraints=("turn_adapter", "card_identity", "scoring"))),
        config_schema=_TRICK_CONFIG))
    registry.register(ToolSpec("ledger", lambda **_: LedgerTool(), (
        OperationSpec("commit", params=("state", "seat", "amount"),
                      effects=("stacks", "committed", "hand_committed"),
                      returns="seat/amount/stacks", reads=("stacks", "committed"),
                      input_schema=obj({"state": OBJECT, "seat": INTEGER, "amount": INTEGER},
                                       ("state", "seat", "amount")),
                      output_schema=obj({"seat": INTEGER, "amount": INTEGER, "stacks": INT_LIST}),
                      feature_constraints=("ledger",)),
        OperationSpec("pots", params=("state",), effects=(), returns="pots",
                      reads=("stacks", "committed", "hand_committed"),
                      input_schema=obj({"state": OBJECT}, ("state",)),
                      output_schema=obj({"pots": INT_LIST}), feature_constraints=("ledger",)),
        OperationSpec("total", params=("state",), effects=(), returns="int",
                      reads=("stacks", "committed"),
                      input_schema=obj({"state": OBJECT}, ("state",)),
                      output_schema=INTEGER, feature_constraints=("ledger",)),
        OperationSpec("settle", params=("state", "winners"),
                      effects=("stacks", "committed", "hand_committed"),
                      returns="awards/pots/stacks", reads=("stacks", "committed", "hand_committed"),
                      input_schema=obj({"state": OBJECT, "winners": OBJECT}, ("state", "winners")),
                      output_schema=obj({"awards": INT_LIST, "pots": INT_LIST,
                                         "stacks": INT_LIST}),
                      feature_constraints=("ledger", "scoring")))))
    registry.register(ToolSpec("betting", lambda min_raise=1: BettingTool(min_raise), (
        OperationSpec("legal", params=("state",), effects=(), returns="list[str]",
                      reads=("stacks", "committed", "folded", "acted", "current_bet",
                             "min_raise", "hand_committed"),
                      input_schema=obj({"state": OBJECT}, ("state",)), output_schema=STR_LIST,
                      feature_constraints=("betting",)),
        OperationSpec("act", params=("state", "action", "amount"),
                      effects=("stacks", "committed", "hand_committed", "folded", "acted",
                               "current_bet", "min_raise", "current_player", "street_done"),
                      returns="action/seat/street_done",
                      reads=("stacks", "committed", "folded", "acted", "current_bet", "min_raise"),
                      input_schema=obj({"state": OBJECT, "action": STRING, "amount": INTEGER},
                                       ("state", "action")),
                      output_schema=obj({"action": STRING, "seat": INTEGER,
                                         "street_done": BOOLEAN}),
                      feature_constraints=("betting", "ledger"))),
        config_schema=_BETTING_CONFIG))
    registry.register(ToolSpec("hand_rank", lambda **_: HandRankTool(), (
        OperationSpec("best", params=("cards",), effects=(), returns="category/score/cards",
                      input_schema=obj({"cards": CARD_LIST}, ("cards",)),
                      output_schema=obj({"category": STRING, "score": INT_LIST,
                                         "cards": STR_LIST}),
                      feature_constraints=("hand_ranking", "scoring")),
        OperationSpec("compare", params=("left", "right"), effects=(), returns="outcome",
                      input_schema=obj({"left": CARD_LIST, "right": CARD_LIST},
                                       ("left", "right")),
                      output_schema=obj({"outcome": STRING}),
                      feature_constraints=("hand_ranking", "scoring")))))
    registry.register(ToolSpec("point_total", lambda target=21: PointTotalTool(target), (
        OperationSpec("total", params=("cards",), effects=(), returns="total/soft/bust/cards",
                      input_schema=obj({"cards": CARD_LIST}, ("cards",)),
                      output_schema=obj({"total": INTEGER, "soft": BOOLEAN, "bust": BOOLEAN,
                                         "cards": STR_LIST}),
                      feature_constraints=("point_total",)),
        OperationSpec("dealer_play", params=("stock", "hand", "stand_on", "hits_soft"),
                      effects=("stock", "hands"), returns="total/soft/bust/draws",
                      reads=("stock", "hands"),
                      input_schema=obj({"stock": CARD_LIST, "hand": CARD_LIST, "stand_on": INTEGER,
                                        "hits_soft": BOOLEAN}, ("stock", "hand")),
                      output_schema=obj({"total": INTEGER, "soft": BOOLEAN, "bust": BOOLEAN,
                                         "draws": INTEGER}),
                      feature_constraints=("point_total", "deck_exhaustion")),
        OperationSpec("settle", params=("player", "dealer", "player_natural", "dealer_natural",
                                         "first_bust_loses"), effects=(),
                      returns="winner/reason",
                      input_schema=obj({"player": INTEGER, "dealer": INTEGER,
                                        "player_natural": BOOLEAN, "dealer_natural": BOOLEAN,
                                        "first_bust_loses": BOOLEAN},
                                       ("player", "dealer")),
                      output_schema=obj({"winner": INTEGER, "reason": STRING}),
                      feature_constraints=("point_total", "scoring"))),
        config_schema=_POINT_CONFIG))
    registry.register(ToolSpec("hidden_draw", lambda **_: HiddenDrawTool(), (
        OperationSpec("askable", params=("state", "seat"), effects=(), returns="list[str]",
                      reads=("hands", "current_player"),
                      input_schema=obj({"state": OBJECT, "seat": INTEGER}, ("state",)),
                      output_schema=STR_LIST,
                      feature_constraints=("hidden_info", "matching")),
        OperationSpec("ask", params=("state", "action", "asker"),
                      effects=("hands", "stock"), returns="got/fished/drawn",
                      reads=("hands", "stock", "current_player"),
                      input_schema=obj({"state": OBJECT, "action": STRING, "asker": INTEGER},
                                       ("state", "action")),
                      output_schema=obj({"got": INTEGER, "fished": BOOLEAN, "drawn": INTEGER}),
                      feature_constraints=("hidden_info", "card_identity")),
        OperationSpec("discard_pairs", params=("state", "seat"),
                      effects=("hands", "pairs"), returns="pairs/hand_size/total",
                      reads=("hands", "pairs", "current_player"),
                      input_schema=obj({"state": OBJECT, "seat": INTEGER}, ("state",)),
                      output_schema=obj({"pairs": INTEGER, "hand_size": INTEGER,
                                         "total": INTEGER}),
                      feature_constraints=("hidden_info", "scoring", "matching")),
        OperationSpec("refill", params=("state", "seat"), effects=("hands", "stock"),
                      returns="drew", reads=("hands", "stock", "current_player"),
                      input_schema=obj({"state": OBJECT, "seat": INTEGER}, ("state",)),
                      output_schema=obj({"drew": INTEGER}),
                      feature_constraints=("hidden_info", "deck_exhaustion")),
        OperationSpec("is_finished", params=("state",), effects=(), returns="bool",
                      reads=("hands", "stock"),
                      input_schema=obj({"state": OBJECT}, ("state",)), output_schema=BOOLEAN,
                      feature_constraints=("terminal_rule", "deck_exhaustion")))))
    registry.register(ToolSpec("trigger", lambda **_: TriggerTool(), (
        OperationSpec("apply", params=("state", "effects"),
                      effects=("hands", "stock", "table", "skip", "direction", "active_suit"),
                      returns="applied/count",
                      reads=("hands", "stock", "table", "skip", "direction", "active_suit",
                             "current_player"),
                      input_schema=obj({"state": OBJECT, "effects": ANY_LIST},
                                       ("state", "effects")),
                      output_schema=obj({"applied": ANY_LIST, "count": INTEGER}),
                      feature_constraints=("special_effect", "explicit_order")),)))
    registry.register(ToolSpec("zones", lambda **_: ZonesTool(), (
        OperationSpec("select", params=("state", "zone", "card_ids", "min_count", "max_count"),
                      effects=(), returns="zone/ids/cards/count", reads=("zones",),
                      input_schema=obj({"state": OBJECT, "zone": STRING, "card_ids": STR_LIST,
                                        "min_count": INTEGER, "max_count": INTEGER},
                                       ("state", "zone", "card_ids")),
                      output_schema=obj({"zone": STRING, "ids": STR_LIST, "cards": CARD_LIST,
                                         "count": INTEGER}),
                      feature_constraints=("card_identity", "multi_zone", "matching")),
        OperationSpec("select_matching",
                      params=("state", "zone", "card_ids", "top_zone", "min_count",
                              "max_count"),
                      effects=(), returns="zone/ids/cards/count", reads=("zones",),
                      input_schema=obj({"state": OBJECT, "zone": STRING, "card_ids": STR_LIST,
                                        "top_zone": STRING, "min_count": INTEGER,
                                        "max_count": INTEGER},
                                       ("state", "zone", "card_ids", "top_zone")),
                      output_schema=obj({"zone": STRING, "ids": STR_LIST, "cards": CARD_LIST,
                                         "count": INTEGER}),
                      feature_constraints=("card_identity", "multi_zone", "matching")),
        OperationSpec("move", params=("state", "moves"), effects=("zones",),
                      returns="moved/sizes", reads=("zones",),
                      input_schema=obj({"state": OBJECT, "moves": ANY_LIST}, ("state", "moves")),
                      output_schema=obj({"moved": INTEGER, "sizes": OBJECT}),
                      feature_constraints=("card_identity", "multi_zone", "explicit_order")),
        OperationSpec("top", params=("state", "zone"), effects=(), returns="zone/id/rank/suit/value",
                      reads=("zones",),
                      input_schema=obj({"state": OBJECT, "zone": STRING}, ("state", "zone")),
                      output_schema=obj({"zone": STRING, "id": STRING, "rank": STRING,
                                         "suit": STRING, "value": INTEGER}),
                      feature_constraints=("card_identity", "multi_zone")),
        OperationSpec("cards", params=("state", "zone"), effects=(), returns="zone/cards/count",
                      reads=("zones",),
                      input_schema=obj({"state": OBJECT, "zone": STRING}, ("state", "zone")),
                      output_schema=obj({"zone": STRING, "cards": CARD_LIST,
                                         "count": INTEGER}),
                      feature_constraints=("card_identity", "multi_zone")),
        OperationSpec("count", params=("state",), effects=(), returns="counts",
                      reads=("zones",),
                      input_schema=obj({"state": OBJECT}, ("state",)),
                      output_schema=obj({"counts": OBJECT}, ("counts",)),
                      feature_constraints=("card_identity", "multi_zone")),
        OperationSpec("count_zone", params=("state", "zone"), effects=(), returns="zone/count",
                      reads=("zones",),
                      input_schema=obj({"state": OBJECT, "zone": STRING}, ("state", "zone")),
                      output_schema=obj({"zone": STRING, "count": INTEGER}, ("zone", "count")),
                      feature_constraints=("card_identity", "multi_zone")),
        OperationSpec("select_duplicates",
                      params=("state", "zone", "key", "min_count", "max_group", "max_total"),
                      effects=(), returns="zone/key/groups/ids/count", reads=("zones",),
                      input_schema=obj({"state": OBJECT, "zone": STRING, "key": STRING,
                                        "min_count": INTEGER, "max_group": INTEGER,
                                        "max_total": INTEGER}, ("state", "zone")),
                      output_schema=obj({"zone": STRING, "key": STRING, "groups": ANY_LIST,
                                         "ids": STR_LIST, "count": INTEGER},
                                        ("zone", "groups", "ids", "count")),
                      feature_constraints=("card_identity", "multi_zone", "matching")),
        OperationSpec("verify", params=("state",), effects=(), returns="zones/cards",
                      reads=("zones",),
                      input_schema=obj({"state": OBJECT}, ("state",)),
                      output_schema=obj({"zones": INTEGER, "cards": INTEGER}),
                      feature_constraints=("card_identity", "multi_zone")))))
    return registry
