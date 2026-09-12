"""Typed expression AST for :class:`ComposedRulesIR`.

A composed rule may not run arbitrary code (ADR-0005): expressions are a small,
typed, side-effect-free language that the compiler lowers into the *existing*
``logic.evaluate`` node. The AST deliberately mirrors that evaluator's total
operation set, so lowering is a structural translation with no hidden power:

* literals;
* references to declared variables / scores / round / actor / action inputs;
* integer arithmetic (``add sub mul mod``) and comparisons (``eq lt le gt ge``);
* boolean logic (``all any not``) and finite collection queries (``count join``).

Refused by construction (``extra="forbid"`` + a strict ``op`` literal + a strict
``path`` pattern): unknown operations, ``eval``-style fields, dynamic imports,
reflection, dotted paths into card objects and recursion beyond ``MAX_EXPR_DEPTH``.

The reference path vocabulary is intentionally *small*. Card semantics (rank,
suit, top of a pile) are reached through declared effects such as ``compare`` and
``move``, never through a raw state path, so a rule cannot peek at internals.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic import TypeAdapter as _TypeAdapter

MAX_EXPR_DEPTH = 12

# Reserved state keys the compiler owns. A user variable may not shadow one of
# these; otherwise a rule could silently rebind the game's score list or turn.
RESERVED_STATE_NAMES = frozenset({
    "input", "seed", "zones", "scores", "round", "current_player", "action_count",
    "finished", "winners", "phase", "deal", "deal_seed", "first_seat", "second_seat",
    "turn_index", "next_turn_index", "next_seat", "next_round", "round_done", "at_end",
    "reveal", "legal_card_indices",
})

# ``variables.x`` -> the compiled top-level key ``v_x``; ``actor`` -> the seat.
_PATH_PATTERN = (
    r"^(variables\.[a-z][a-z0-9_]*"
    r"|scores|scores\.[0-9]+"
    r"|round|actor|action_count"
    r"|input\.[a-z][a-z0-9_]*)$"
)

_ARITHMETIC = ("add", "sub", "mul", "mod")
_COMPARISONS = ("eq", "lt", "le", "gt", "ge")
_BOOLEAN = ("all", "any")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LiteralExpr(_Strict):
    op: Literal["lit"]
    value: int | str | bool | None = None


class RefExpr(_Strict):
    op: Literal["ref"]
    path: str = Field(pattern=_PATH_PATTERN, max_length=80)


class NotExpr(_Strict):
    op: Literal["not"]
    operand: Expr


class BinaryExpr(_Strict):
    op: Literal["eq", "lt", "le", "gt", "ge", "add", "sub", "mul", "mod"]
    left: Expr
    right: Expr


class BoolExpr(_Strict):
    op: Literal["all", "any"]
    items: list[Expr] = Field(min_length=1, max_length=16)


class CountExpr(_Strict):
    op: Literal["count"]
    operand: Expr


class JoinExpr(_Strict):
    op: Literal["join"]
    items: list[Expr] = Field(min_length=1, max_length=8)


Expr = Annotated[
    LiteralExpr | RefExpr | NotExpr | BinaryExpr | BoolExpr | CountExpr | JoinExpr,
    Field(discriminator="op"),
]
EXPR_ADAPTER: Any = _TypeAdapter(Expr)


def expression_depth(expr: Any) -> int:
    """Maximum nesting depth of an expression tree (a leaf is depth 1)."""
    if not isinstance(expr, BaseModel):
        return 1
    op = getattr(expr, "op", None)
    if op == "lit" or op == "ref":
        return 1
    if op == "not" or op == "count":
        return 1 + expression_depth(expr.operand)
    if op in _ARITHMETIC or op in _COMPARISONS:
        return 1 + max(expression_depth(expr.left), expression_depth(expr.right))
    children = list(getattr(expr, "items", []))
    return 1 + max((expression_depth(item) for item in children), default=0)


def _path_to_state(path: str) -> str:
    """Map an IR reference to the concrete state path the interpreter resolves."""
    if path.startswith("variables."):
        return "v_" + path[len("variables."):]
    if path == "actor":
        return "current_player"
    return path


def refs_in(expr: Any) -> list[str]:
    """Every reference path an expression reads, in source order."""
    if not isinstance(expr, BaseModel):
        return []
    op = getattr(expr, "op", None)
    if op == "lit":
        return []
    if op == "ref":
        return [expr.path]
    if op == "not" or op == "count":
        return refs_in(expr.operand)
    if op in _ARITHMETIC or op in _COMPARISONS:
        return [*refs_in(expr.left), *refs_in(expr.right)]
    found: list[str] = []
    for item in getattr(expr, "items", []):
        found.extend(refs_in(item))
    return found


def validate_expression(expr: Any, declared_variables: frozenset[str]) -> None:
    """Reject an expression that reads an undeclared variable or nests too deep.

    This is the static half of "no arbitrary state paths": the path shape was
    already fixed by the model, and here the *semantic* reference must resolve to
    a variable the IR actually declared.
    """
    from ..contracts import ToolError

    if not isinstance(expr, BaseModel):
        raise ToolError("expression_must_be_a_typed_node")
    if expression_depth(expr) > MAX_EXPR_DEPTH:
        raise ToolError(f"expression_depth_exceeded:{MAX_EXPR_DEPTH}")
    for path in refs_in(expr):
        if path.startswith("variables."):
            name = path[len("variables."):]
            if name not in declared_variables:
                raise ToolError(f"expression_unknown_variable:{name}")


def compile_expression(expr: Any) -> Any:
    """Lower a typed AST into the ``logic.evaluate`` payload.

    References become ``$state.<path>`` strings, which the interpreter resolves
    without evaluating code; every operator maps 1:1 onto a declared evaluator
    operation. Computed values are only ever produced by ``logic.evaluate``.
    """
    from ..contracts import ToolError

    if not isinstance(expr, BaseModel):
        raise ToolError("expression_must_be_a_typed_node")
    op = getattr(expr, "op", None)
    if op == "lit":
        return expr.value
    if op == "ref":
        return "$state." + _path_to_state(expr.path)
    if op == "not":
        return {"not": [compile_expression(expr.operand)]}
    if op in _ARITHMETIC or op in _COMPARISONS:
        return {op: [compile_expression(expr.left), compile_expression(expr.right)]}
    if op in _BOOLEAN:
        return {op: [compile_expression(item) for item in expr.items]}
    if op == "count":
        return {"count": [compile_expression(expr.operand)]}
    if op == "join":
        return {"join": [compile_expression(item) for item in expr.items]}
    raise ToolError(f"unknown_expression_operation:{op}")
