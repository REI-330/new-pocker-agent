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


# ------------------------------------------------------------------ guard-only
# ADR-0012 section 8: a guard may read the *top* of a declared zone through a
# closed grammar. There is no dynamic path and no function call -- a ``top``
# reference names one declared zone and one fixed field, and the compiler lowers
# it to declared ``zones`` operations (with an emptiness guard, so an empty zone
# is false in boolean context rather than a runtime error). The bounded
# ``has_match`` predicate is the guard form of ``pattern.match`` over a whole
# zone: it reuses ``pattern.choices`` semantics and scans one declared zone.
_ZONE_PATTERN = r"^[a-z][a-z0-9_-]*$"


class TopExpr(_Strict):
    op: Literal["top"]
    zone: str = Field(min_length=1, max_length=32, pattern=_ZONE_PATTERN)
    field: Literal["rank", "suit", "value"] = "rank"


class HasMatchExpr(_Strict):
    """True when any card in ``zone`` matches the top of ``top.zone``.

    Bounded: one declared zone is scanned, using the same same-suit/same-rank
    rule as ``pattern.match``. It never reads an undeclared path and never
    quantifies over the whole state.
    """

    op: Literal["has_match"]
    zone: str = Field(min_length=1, max_length=32, pattern=_ZONE_PATTERN)
    top: TopExpr


class ZoneCountExpr(_Strict):
    op: Literal["zone_count"]
    zone: str = Field(min_length=1, max_length=32, pattern=_ZONE_PATTERN)


Expr = Annotated[
    LiteralExpr | RefExpr | NotExpr | BinaryExpr | BoolExpr | CountExpr | JoinExpr
    | TopExpr | HasMatchExpr | ZoneCountExpr,
    Field(discriminator="op"),
]
EXPR_ADAPTER: Any = _TypeAdapter(Expr)

_GUARD_ONLY_OPS = ("top", "has_match", "zone_count")


def expression_depth(expr: Any) -> int:
    """Maximum nesting depth of an expression tree (a leaf is depth 1)."""
    if not isinstance(expr, BaseModel):
        return 1
    op = getattr(expr, "op", None)
    if op == "lit" or op == "ref" or op == "top" or op == "zone_count":
        return 1
    if op == "has_match":
        return 2
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
    if op == "lit" or op in _GUARD_ONLY_OPS:
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


def guard_mechanisms(expr: Any) -> set[str]:
    """The host operations a guard's top/count references lower to.

    Used by requirement derivation and plan binding selection, so a guard that
    reads a zone top cannot silently compile without the operation it needs.
    """
    found: set[str] = set()
    if not isinstance(expr, BaseModel):
        return found
    op = getattr(expr, "op", None)
    if op == "top":
        found |= {"zones.count_zone", "zones.top"}
    elif op == "has_match":
        found |= {"zones.cards", "pattern.choices"}
    elif op == "zone_count":
        found |= {"zones.count_zone"}
    elif op == "not" or op == "count":
        found |= guard_mechanisms(expr.operand)
    elif op in _ARITHMETIC or op in _COMPARISONS:
        found |= guard_mechanisms(expr.left) | guard_mechanisms(expr.right)
    else:
        for item in getattr(expr, "items", []):
            found |= guard_mechanisms(item)
    return found


def guard_zones(expr: Any) -> set[str]:
    """Every declared zone a guard references, for existence validation."""
    found: set[str] = set()
    if not isinstance(expr, BaseModel):
        return found
    op = getattr(expr, "op", None)
    if op == "top":
        found.add(expr.zone)
    elif op == "has_match":
        found |= {expr.zone, expr.top.zone}
    elif op == "zone_count":
        found.add(expr.zone)
    elif op == "not" or op == "count":
        found |= guard_zones(expr.operand)
    elif op in _ARITHMETIC or op in _COMPARISONS:
        found |= guard_zones(expr.left) | guard_zones(expr.right)
    else:
        for item in getattr(expr, "items", []):
            found |= guard_zones(item)
    return found


def identity_zones_in(expr: Any) -> set[str]:
    """Zones whose card identity, rather than only their count, affects an expression."""
    found: set[str] = set()
    for node in _walk(expr):
        op = getattr(node, "op", None)
        if op == "top":
            found.add(node.zone)
        elif op == "has_match":
            found.update((node.zone, node.top.zone))
    return found


def uses_guard_mechanism(expr: Any, mechanism: str) -> bool:
    return mechanism in guard_mechanisms(expr)


def validate_guard(expr: Any, declared_zones: frozenset[str]) -> None:
    """Reject a guard that names an undeclared zone or reads a numeric top.

    ADR-0012: an empty zone makes ``top(...)`` false in boolean context, which is
    safe, but ``top(...).value`` used numerically would need a runtime error, so
    it is a compile error (``guard_top_requires_cards``).
    """
    from ..contracts import ToolError

    if not isinstance(expr, BaseModel):
        raise ToolError("expression_must_be_a_typed_node")
    if expression_depth(expr) > MAX_EXPR_DEPTH:
        raise ToolError(f"expression_depth_exceeded:{MAX_EXPR_DEPTH}")
    unknown = sorted(guard_zones(expr) - declared_zones)
    if unknown:
        raise ToolError(f"expression_unknown_zone:{unknown[0]}")
    for node in _walk(expr):
        if getattr(node, "op", None) == "top" and node.field == "value":
            raise ToolError("guard_top_requires_cards")


def _walk(expr: Any):
    """Yield every node in an expression tree (leaves included)."""
    if not isinstance(expr, BaseModel):
        return
    yield expr
    op = getattr(expr, "op", None)
    if op == "not" or op == "count":
        yield from _walk(expr.operand)
    elif op in _ARITHMETIC or op in _COMPARISONS:
        yield from _walk(expr.left)
        yield from _walk(expr.right)
    else:
        for item in getattr(expr, "items", []):
            yield from _walk(item)


# Static expression types. ``any`` is the honest answer when a value's type is
# not knowable at compile time (e.g. an action input); it is never used to reject
# a valid rule, only to avoid a false rejection.
INTEGER = "integer"
BOOLEAN = "boolean"
STRING = "string"
INTEGER_LIST = "integer_list"
ANY_TYPE = "any"


def _literal_type(value: Any) -> str:
    if isinstance(value, bool):
        return BOOLEAN
    if isinstance(value, int):
        return INTEGER
    if isinstance(value, str):
        return STRING
    if isinstance(value, list) and all(isinstance(item, int) and not isinstance(item, bool)
                                       for item in value):
        return INTEGER_LIST
    return ANY_TYPE


def _ref_type(path: str, variable_types: dict[str, str]) -> str:
    if path.startswith("variables."):
        return variable_types.get(path[len("variables."):], ANY_TYPE)
    if path == "scores":
        return INTEGER_LIST
    if path.startswith("scores."):
        return INTEGER
    if path in {"round", "actor", "action_count"}:
        return INTEGER
    return ANY_TYPE  # input.* -- the action input's type is decided at run time


def _merge(*types: str) -> str:
    return ANY_TYPE if ANY_TYPE in types else types[0]


def infer_type(expr: Any, variable_types: dict[str, str] | None = None) -> str:
    """Statically infer an expression's type, or ``any`` when it cannot be known.

    Raises ``ToolError`` when an operator is applied to an operand of the wrong
    type (e.g. arithmetic on a string literal), so a rule cannot silently produce
    an integer variable from ``join``.
    """
    from ..contracts import ToolError

    types = variable_types or {}
    if not isinstance(expr, BaseModel):
        return _literal_type(expr)
    op = getattr(expr, "op", None)
    if op == "lit":
        return _literal_type(expr.value)
    if op == "ref":
        return _ref_type(expr.path, types)
    if op == "top":
        return INTEGER if expr.field == "value" else STRING
    if op == "has_match":
        return BOOLEAN
    if op == "zone_count":
        return INTEGER
    if op == "not":
        operand = infer_type(expr.operand, types)
        if operand not in {BOOLEAN, ANY_TYPE}:
            raise ToolError(f"expression_type_mismatch:not:{operand}")
        return BOOLEAN
    if op in _ARITHMETIC:
        left, right = infer_type(expr.left, types), infer_type(expr.right, types)
        if INTEGER not in {left, ANY_TYPE} or INTEGER not in {right, ANY_TYPE}:
            raise ToolError(f"expression_type_mismatch:{op}:{left}:{right}")
        return INTEGER
    if op in _COMPARISONS:
        left, right = infer_type(expr.left, types), infer_type(expr.right, types)
        if op != "eq" and (INTEGER not in {left, ANY_TYPE} or INTEGER not in {right, ANY_TYPE}):
            raise ToolError(f"expression_type_mismatch:{op}:{left}:{right}")
        return BOOLEAN
    if op in ("all", "any"):
        for item in expr.items:
            if infer_type(item, types) not in {BOOLEAN, ANY_TYPE}:
                raise ToolError(f"expression_type_mismatch:{op}")
        return BOOLEAN
    if op == "count":
        operand = infer_type(expr.operand, types)
        if operand not in {INTEGER_LIST, STRING, ANY_TYPE}:
            raise ToolError(f"expression_type_mismatch:count:{operand}")
        return INTEGER
    if op == "join":
        for item in expr.items:
            infer_type(item, types)
        return STRING
    raise ToolError(f"unknown_expression_operation:{op}")


def check_assignable(actual: str, target: str) -> bool:
    """Whether a value of ``actual`` type may be stored in a ``target`` variable."""
    return actual == ANY_TYPE or actual == target


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
    if op in _GUARD_ONLY_OPS:
        # These need declared host calls and a working state; only the composed
        # compiler's guard lowering may emit them.
        raise ToolError(f"guard_only_expression:{op}")
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
