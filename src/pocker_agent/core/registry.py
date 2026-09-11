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
from .tools import (
    ExactExpressionTool,
    LogicTool,
    ScoreSettleTool,
    SolvableDealTool,
    StateTool,
)

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
    return registry
