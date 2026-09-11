"""Deterministic, game-agnostic tools for the v0.4 core.

Each tool is small and side-effect-bounded. Tools own *mechanism* (exact
arithmetic, state writes, chip ledgers); the plan owns *policy* (when to call
them and what to do with the result).
"""
from __future__ import annotations

import random
from typing import Any

from ..arithmetic import calculate, solve
from .cards import CardRef
from .contracts import ToolError

_RESERVED_STATE_KEYS = {"input", "seed"}
_STANDARD_VALUES = {"A": 1, "J": 11, "Q": 12, "K": 13}


class StateTool:
    """Writes to the shared state dict, refusing reserved/private keys."""

    def update(self, state: dict[str, Any], values: dict[str, Any]) -> list[str]:
        if not isinstance(state, dict) or not isinstance(values, dict):
            raise ToolError("state_update_requires_objects")
        for key in values:
            if key in _RESERVED_STATE_KEYS or str(key).startswith("_"):
                raise ToolError(f"reserved_state_field:{key}")
        state.update(values)
        return sorted(values)


class LogicTool:
    """Tiny total expression language: comparisons and integer bookkeeping."""

    def evaluate(self, expression: Any) -> Any:
        return evaluate_expression(expression)


_BINARY = {"eq": lambda a, b: a == b, "lt": lambda a, b: a < b,
           "le": lambda a, b: a <= b, "gt": lambda a, b: a > b,
           "ge": lambda a, b: a >= b, "add": lambda a, b: a + b}


def evaluate_expression(expression: Any, _depth: int = 0) -> Any:
    """Module-level evaluator shared by LogicTool and contract predicate checks."""
    if _depth > 32:
        raise ToolError("expression_depth_limit")
    if not isinstance(expression, dict):
        return expression
    if len(expression) != 1:
        raise ToolError("invalid_expression")
    operation, arguments = next(iter(expression.items()))
    if not isinstance(arguments, list):
        raise ToolError("expression_arguments_must_be_list")
    if operation in _BINARY and len(arguments) == 2:
        return _BINARY[operation](evaluate_expression(arguments[0], _depth + 1),
                                  evaluate_expression(arguments[1], _depth + 1))
    values = [evaluate_expression(argument, _depth + 1) for argument in arguments]
    if operation == "all":
        return all(values)
    if operation == "any":
        return any(values)
    if operation == "not" and len(values) == 1:
        return not values[0]
    if operation == "count" and len(values) == 1:
        return len(values[0])
    if operation == "join":
        return "".join(str(value) for value in values)
    raise ToolError(f"unknown_expression_operation:{operation}")


class ExactExpressionTool:
    """Exact four-operation puzzle checker; never floating point."""

    def __init__(self, target: int = 24, operations: Any = ("+", "-", "*", "/"),
                 fractional: bool = True, rank_values: dict[str, int] | None = None) -> None:
        self.target = int(target)
        self.operations = tuple(operations)
        self.fractional = bool(fractional)
        self.rank_values = dict(rank_values or {})
        if not self.operations or not set(self.operations) <= {"+", "-", "*", "/"}:
            raise ToolError("invalid_arithmetic_operations")

    def values(self, numbers: Any) -> list[int]:
        result = []
        for number in numbers:
            if isinstance(number, CardRef):
                result.append(int(self.rank_values.get(number.rank, number.value)))
            else:
                result.append(int(number))
        return result

    def solve(self, numbers: Any) -> str | None:
        return solve(tuple(self.values(numbers)), self.target, self.operations, self.fractional)

    def validate(self, expression: str, numbers: Any) -> dict[str, Any]:
        try:
            value = calculate(expression, self.values(numbers), list(self.operations), self.fractional)
        except ValueError as error:  # includes ToolError; keep one rejection type
            raise ToolError(str(error)) from error
        if value != self.target:
            raise ToolError(f"wrong_result:{value}")
        return {"correct": True, "target": self.target}

    def assert_unsolvable(self, numbers: Any) -> dict[str, Any]:
        """Reject a false "no solution" claim; the host verifies, not the player."""
        if self.solve(numbers) is not None:
            raise ToolError("puzzle_is_solvable")
        return {"unsolvable": True}


class SolvableDealTool:
    """Deal a fresh arithmetic hand, optionally filtered to solvable puzzles."""

    def __init__(self, ranks: Any, suits: Any, target: int = 24, operations: Any = ("+", "-", "*", "/"),
                 fractional: bool = True, rank_values: dict[str, int] | None = None,
                 deal_mode: str = "solvable", attempts: int = 64) -> None:
        self.ranks, self.suits = list(ranks), list(suits)
        self.target = int(target)
        self.operations = tuple(operations)
        self.fractional = bool(fractional)
        self.rank_values = dict(rank_values or {})
        self.deal_mode = deal_mode
        self.attempts = int(attempts)
        if self.deal_mode not in {"random", "solvable"}:
            raise ToolError("invalid_deal_mode")

    def catalog(self) -> list[CardRef]:
        cards = []
        for rank in self.ranks:
            for suit in self.suits:
                value = self.rank_values.get(rank, _STANDARD_VALUES.get(rank, 0))
                if not value:
                    try:
                        value = int(rank)
                    except ValueError:
                        value = self.ranks.index(rank) + 1
                cards.append(CardRef(f"{rank}{suit}", rank, suit, int(value)))
        return cards

    def deal(self, seed: Any, cards_each: int = 4) -> dict[str, Any]:
        attempts = 1 if self.deal_mode == "random" else self.attempts
        for attempt in range(attempts):
            deck = self.catalog()
            random.Random(f"{seed}:{attempt}").shuffle(deck)
            hand = deck[:cards_each]
            numbers = [self.rank_values.get(card.rank, card.value) for card in hand]
            if self.deal_mode == "random" or solve(tuple(numbers), self.target, self.operations, self.fractional) is not None:
                return {"hand": hand, "numbers": numbers, "attempt": attempt}
        raise ToolError("no_solvable_deal")


class ScoreSettleTool:
    """Award points to explicit winners; the plan decides *who* won."""

    def call(self, scores: list[int], winners: list[int], points: int = 1) -> list[int]:
        if not isinstance(scores, list):
            raise ToolError("scores_must_be_list")
        if not isinstance(points, int):
            raise ToolError("points_must_be_integer")
        if not winners or any(type(w) is not int or not 0 <= w < len(scores) for w in winners):
            raise ToolError("invalid_winners")
        settled = list(scores)
        for winner in winners:
            settled[winner] += points
        return settled


class DeckTool:
    """Generic deck: deterministic shuffle and deal. Strength = rank order."""

    def __init__(self, ranks: Any, suits: Any, copies: int = 1) -> None:
        self.ranks, self.suits = list(ranks), list(suits)
        self.copies = int(copies)
        if not self.ranks or not self.suits or self.copies < 1:
            raise ToolError("invalid_deck_configuration")

    def catalog(self) -> list[CardRef]:
        return [CardRef(f"{rank}{suit}", rank, suit, index + 1)
                for _ in range(self.copies)
                for index, rank in enumerate(self.ranks)
                for suit in self.suits]

    def shuffled(self, seed: Any) -> list[CardRef]:
        deck = self.catalog()
        random.Random(str(seed)).shuffle(deck)
        return deck

    def deal(self, seed: Any, hands: int, cards_each: int = 1, kitty: int = 0) -> dict[str, Any]:
        if type(hands) is not int or type(cards_each) is not int or hands < 1 or cards_each < 1:
            raise ToolError("invalid_deal_parameters")
        deck = self.shuffled(seed)
        if hands * cards_each + kitty > len(deck):
            raise ToolError("deck_exhausted")
        dealt = [deck[index * cards_each:(index + 1) * cards_each] for index in range(hands)]
        rest = deck[hands * cards_each:]
        return {"hands": dealt, "kitty": rest[:kitty], "deck": rest[kitty:]}


class RankCompareTool:
    """Compare card groups by strength; never knows a game's ranking system."""

    @staticmethod
    def _best(cards: Any) -> CardRef | None:
        if isinstance(cards, CardRef):
            return cards
        if isinstance(cards, list) and cards:
            return max(cards, key=lambda card: card.value)
        return None

    def call(self, left: Any, right: Any) -> dict[str, Any]:
        best_left, best_right = self._best(left), self._best(right)
        if best_left is None or best_right is None:
            raise ToolError("compare_requires_cards_on_both_sides")
        outcome = "tie" if best_left.value == best_right.value else ("left" if best_left.value > best_right.value else "right")
        return {"outcome": outcome, "left": best_left.value, "right": best_right.value}


class WinnerResolveTool:
    """Indices of the maximum value (or minimum when requested)."""

    def call(self, values: list[int], mode: str = "max") -> list[int]:
        if not isinstance(values, list) or not values:
            raise ToolError("winner_resolve_requires_values")
        if mode not in {"max", "min"}:
            raise ToolError("invalid_winner_mode")
        target = max(values) if mode == "max" else min(values)
        return [index for index, value in enumerate(values) if value == target]
