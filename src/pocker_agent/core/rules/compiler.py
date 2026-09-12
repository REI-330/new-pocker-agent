"""Deterministic lowering of ``ComposedRulesIR`` into a ``GamePlan``.

The compiler is where ADR-0005 is enforced: it dispatches on *mechanism*, never
on a game title, ``game_id`` or description. Two rules that use the same
operation combination compile to the same structure, whatever they are called
(``test_the_same_mechanism_combination_compiles_to_the_same_structure``).

Lowering works on the semantic nodes the IR declares:

* ``select``   -> ``zones.select``                (validate a finite selection)
* ``move``     -> ``zones.move``                  (move the selection, atomically)
* ``move_top`` -> ``zones.top`` + ``zones.move``  (a bounded, explicit flush)
* ``compare``  -> ``rank_compare.call`` + branch + ``score_settle.call``
* variables   -> ``logic.evaluate`` (value) + ``state.update`` (write)

The turn loop, the round loop and the terminal rule are generated once from the
IR's ``players``/``flow``/``terminal`` fields, not from per-game code. A
``source_map`` links every generated node back to the IR path and requirement
clause that produced it, so a compile error can name the clause it violated.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from ..contracts import ToolError, ToolRegistry
from ..plan import ActionDescriptor, ActionInputDescriptor, GamePlan, plan_fingerprint
from ..zones import MAX_DUPLICATE_TOTAL
from .composed import (
    MAX_PLAN_NODES,
    MAX_STEP_LIMIT,
    AssignEffect,
    CompareEffect,
    ComposedRulesIR,
    DrawEffect,
    MoveSelectionEffect,
    MoveTopEffect,
    RefillEffect,
    RemovePairsEffect,
    SelectEffect,
    SkipEffect,
)
from .expr import compile_expression, guard_mechanisms
from .requirements import CompositionReport, clause_for, resolve_composition
from .structure import analyse_control_flow

COMPILER_VERSION = "m2-0.1"

# The only cycles the compiler emits are the turn loop (bounded by ``players`` via
# ``set_turn_index``) and the round loop (bounded by ``max_rounds`` via
# ``set_round``). ``analyse_control_flow`` requires every cycle to contain one.
_BOUNDED_NODES = frozenset({"set_round", "set_turn_index"})


class CompileError(ToolError):
    """A deterministic compile failure that names its IR path and clause."""

    def __init__(self, code: str, message: str, path: str = "", clause: str = "") -> None:
        super().__init__(f"compiler_error:{code}:{path or '-'}:{message}")
        self.code = code
        self.message = message
        self.path = path
        self.clause = clause

    def as_dict(self) -> dict[str, str]:
        return {"kind": "compiler_error", "code": self.code, "path": self.path,
                "clause": self.clause, "message": self.message}


@dataclass
class CompiledRules:
    plan: GamePlan
    source_map: dict[str, Any]
    ir_hash: str
    compiler_version: str
    registry_contract_hash: str
    composition: CompositionReport
    plan_hash: str = field(default="")

    def __post_init__(self) -> None:
        if not self.plan_hash:
            self.plan_hash = plan_fingerprint(self.plan)

    def as_dict(self) -> dict[str, Any]:
        return {"generation_source": "composed_rules",
                "compiler_version": self.compiler_version,
                "ir_hash": self.ir_hash, "plan_hash": self.plan_hash,
                "registry_contract_hash": self.registry_contract_hash,
                "plan": self.plan.model_dump(mode="json"),
                "source_map": self.source_map,
                "composition": self.composition.as_dict()}


def normalized_ir(ir: ComposedRulesIR) -> dict[str, Any]:
    return ir.model_dump(mode="json")


def ir_hash(ir: ComposedRulesIR) -> str:
    """Content identity of the normalised IR (not a verification credential)."""
    canonical = json.dumps(normalized_ir(ir), sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _safe(zone_id: str) -> str:
    return zone_id.replace("-", "_")


class _GuardBuilder:
    """Lower a guard's closed top/zone references into declared host calls.

    ADR-0012 section 8. The builder emits a straight-line sequence of nodes; a
    ``top`` reference additionally emits an emptiness branch, so an empty zone
    yields a null card (falsey in an equality) instead of a runtime error. The
    caller appends one ``logic.evaluate`` node that stores the final boolean, so
    the guard stays a typed expression over ``$state`` values and the turn loop
    can branch on it unchanged.
    """

    _BINARY_OPS = ("eq", "lt", "le", "gt", "ge", "add", "sub", "mul", "mod")

    def __init__(self, compiler: _Compiler, path: str) -> None:
        self.c = compiler
        self.path = path
        self.first: str | None = None
        self.dangling: list[str] = []

    def _fresh(self) -> int:
        self.c._guard_seq += 1
        return self.c._guard_seq

    def _add(self, node_id: str, node: dict[str, Any]) -> None:
        self.c._add(node_id, node, self.path)
        if self.first is None:
            self.first = node_id

    def _consume(self, node_id: str) -> None:
        """Point every currently-dangling node at ``node_id``."""
        for pending in self.dangling:
            self.c.nodes[pending]["next"] = node_id
        self.dangling = [node_id]

    def _call(self, node_id: str, tool: str, operation: str, args: dict[str, Any],
              next_id: str, result_key: str | None = None) -> None:
        action: dict[str, Any] = {"tool": tool, "operation": operation, "args": args}
        if result_key is not None:
            action["result_key"] = result_key
        self._add(node_id, {"kind": "call", "next": next_id, "action": action})

    def emit(self, expr: Any) -> Any:
        op = getattr(expr, "op", None)
        if op == "lit":
            return expr.value
        if op == "ref":
            return compile_expression(expr)
        if op == "not":
            return {"not": [self.emit(expr.operand)]}
        if op in self._BINARY_OPS:
            return {op: [self.emit(expr.left), self.emit(expr.right)]}
        if op in ("all", "any"):
            return {op: [self.emit(item) for item in expr.items]}
        if op == "count":
            return {"count": [self.emit(expr.operand)]}
        if op == "join":
            return {"join": [self.emit(item) for item in expr.items]}
        if op == "zone_count":
            return self._zone_count(expr.zone)
        if op == "top":
            return self._top(expr.zone, expr.field)
        if op == "has_match":
            return self._has_match(expr.zone, expr.top.zone)
        raise ToolError(f"unsupported_guard_expression:{op}")

    def _zone_count(self, zone_id: str) -> str:
        n = self._fresh()
        key = f"g{n}_count"
        self._call(key, "zones", "count_zone",
                   {"state": "$state", "zone": self.c._zone_arg(zone_id)},
                   "PENDING", result_key=key)
        self._consume(key)
        return f"$state.{key}.count"

    def _top(self, zone_id: str, field: str) -> str:
        n = self._fresh()
        count, has, top, empty, branch = (f"g{n}_count", f"g{n}_has", f"g{n}_top",
                                          f"g{n}_empty", f"g{n}_branch")
        zone_arg = self.c._zone_arg(zone_id)
        self._call(count, "zones", "count_zone", {"state": "$state", "zone": zone_arg},
                   has, result_key=count)
        self._consume(count)
        self._call(has, "logic", "evaluate",
                   {"expression": {"gt": [f"$state.{count}.count", 0]}},
                   branch, result_key=has)
        self._consume(has)
        # Both arms produce ``g{n}_card``: the real top, or a null card so an
        # empty zone is false in a comparison rather than a runtime error (B3).
        self._call(top, "zones", "top", {"state": "$state", "zone": zone_arg},
                   "PENDING", result_key=f"g{n}_card")
        self._call(empty, "state", "update",
                   {"state": "$state", "values": {f"g{n}_card": {
                       "rank": None, "suit": None, "value": None}}}, "PENDING")
        self._add(branch, {"kind": "branch", "value": f"$state.{has}",
                           "cases": [{"value": True, "target": top}], "next": empty})
        self.dangling = [top, empty]
        return f"$state.g{n}_card.{field}"

    def _has_match(self, zone_id: str, top_zone_id: str) -> str:
        n = self._fresh()
        zone, topzone, choices, boolean = (f"g{n}_zone", f"g{n}_topzone",
                                           f"g{n}_choices", f"g{n}_bool")
        self._call(zone, "zones", "cards",
                   {"state": "$state", "zone": self.c._zone_arg(zone_id)},
                   topzone, result_key=zone)
        self._consume(zone)
        self._call(topzone, "zones", "cards",
                   {"state": "$state", "zone": self.c._zone_arg(top_zone_id)},
                   choices, result_key=topzone)
        self._consume(topzone)
        self._call(choices, "pattern", "choices",
                   {"cards": f"$state.{zone}.cards", "top": f"$state.{topzone}.cards"},
                   boolean, result_key=choices)
        self._consume(choices)
        self._call(boolean, "logic", "evaluate",
                   {"expression": {"gt": [{"count": [f"$state.{choices}"]}, 0]}},
                   "PENDING", result_key=boolean)
        self._consume(boolean)
        return f"$state.{boolean}"

    def finish(self, payload: Any, result_key: str, next_id: str,
               plain_node_id: str) -> None:
        node_id = plain_node_id if self.first is None else f"g{self._fresh()}_eval"
        self._add(node_id, {"kind": "call", "next": next_id,
                            "action": {"tool": "logic", "operation": "evaluate",
                                       "args": {"expression": payload},
                                       "result_key": result_key}})
        self._consume(node_id)
        self.dangling = []


class _Compiler:
    def __init__(self, ir: ComposedRulesIR) -> None:
        self.ir = ir
        self.players = ir.players.count
        self.actions = [ir.action(action_id) for action_id in ir.flow.action_sequence]
        self.action = self.actions[0]
        self._has_trigger = any(action.trigger for action in self.actions)
        self.nodes: dict[str, dict[str, Any]] = {}
        self.entries: list[dict[str, str]] = []
        self._has_compare = any(isinstance(effect, CompareEffect)
                                for effect in ir.flow.resolve)
        self._hand_zone = next(deal.zone for deal in ir.setup.deals if deal.per_seat)
        self._shared_deals = [deal for deal in ir.setup.deals if not deal.per_seat]
        self._zone_entry: str | None = None
        self._turn_entry: str | None = None
        self._guard_seq = 0

    # --------------------------------------------------------------- plumbing
    def _add(self, node_id: str, node: dict[str, Any], path: str) -> None:
        if node_id in self.nodes:
            self._fail("duplicate_node", node_id, path)
        self.nodes[node_id] = node
        self.entries.append({"node": node_id, "path": path,
                             "clause": clause_for(self.ir, path)})

    def _fail(self, code: str, message: str, path: str) -> None:
        """Raise a compile error located at an IR path and its clause."""
        raise CompileError(code, message, path, clause_for(self.ir, path))

    def _call(self, node_id: str, tool: str, operation: str, args: dict[str, Any],
              next_id: str, path: str, result_key: str | None = None) -> None:
        action: dict[str, Any] = {"tool": tool, "operation": operation, "args": args}
        if result_key is not None:
            action["result_key"] = result_key
        self._add(node_id, {"kind": "call", "next": next_id, "action": action}, path)

    def _branch(self, node_id: str, value: str, cases: list[dict[str, Any]],
                next_id: str, path: str) -> None:
        self._add(node_id, {"kind": "branch", "value": value, "cases": cases,
                            "next": next_id}, path)

    def _update(self, node_id: str, values: dict[str, Any], next_id: str, path: str) -> None:
        self._call(node_id, "state", "update", {"state": "$state", "values": values},
                   next_id, path)

    def _eval(self, node_id: str, expression: Any, result_key: str, next_id: str,
              path: str) -> None:
        self._call(node_id, "logic", "evaluate", {"expression": expression}, next_id,
                   path, result_key=result_key)

    def _zone_arg(self, zone_id: str) -> str:
        """The plan argument for a zone: the actor's instance, or the shared id.

        The *declared* zone scope decides this. Forcing ``actor`` here (the old
        bug) turned a shared ``from_zone`` into ``$state.zone_pot`` and failed at
        run time; a player zone is always the acting seat's own instance.
        """
        zone = self.ir.zone(zone_id)
        if zone is None:
            self._fail("unknown_zone", zone_id, f"zones.{zone_id}")
        if zone.scope == "player":
            return f"$state.zone_{_safe(zone_id)}"
        return zone_id

    # ------------------------------------------------------------------ build
    def compile(self) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
        self._setup_nodes()
        self._turn_loop()
        return self.nodes, self.entries

    def _actor_zone_ids(self) -> list[str]:
        found: set[str] = set()
        for action in self.actions:
            for item in action.inputs:
                found.add(item.zone)
            for effect in action.effects:
                if isinstance(effect, (MoveSelectionEffect, RemovePairsEffect, RefillEffect,
                                      DrawEffect)):
                    found.add(effect.from_zone)
                    found.add(effect.to_zone)
        return sorted(zone_id for zone_id in found
                      if (zone := self.ir.zone(zone_id)) is not None and zone.scope == "player")

    def _build_zone_nodes(self) -> None:
        previous: str | None = None
        for zone_id in self._actor_zone_ids():
            node_id = f"zone_{_safe(zone_id)}"
            if self._zone_entry is None:
                self._zone_entry = node_id
            if previous is not None:
                self.nodes[previous]["next"] = node_id
            self._eval(node_id, {"join": [f"{zone_id}-", "$state.current_player"]},
                       f"zone_{_safe(zone_id)}", self._turn_entry or "turn",
                       f"zones.{zone_id}")
            previous = node_id

    # ------------------------------------------------------------------ setup
    def _deal_plan(self) -> tuple[int, int]:
        hand = next(deal for deal in self.ir.setup.deals if deal.per_seat)
        return hand.count, sum(deal.count for deal in self._shared_deals)

    def _zone_table(self) -> dict[str, Any]:
        table: dict[str, Any] = {}
        shared_deal_zone = self._shared_deals[0].zone if self._shared_deals else None
        for zone in sorted(self.ir.zones, key=lambda item: item.id):
            if zone.scope == "player":
                for seat in range(self.players):
                    cards: Any = (f"$state.deal.hands.{seat}"
                                  if zone.id == self._hand_zone else [])
                    table[f"{zone.id}-{seat}"] = {"owner": seat,
                                                  "visibility": zone.visibility,
                                                  "cards": cards}
            else:
                if zone.id == self.ir.setup.stock_zone:
                    cards = "$state.deal.deck"
                elif zone.id == shared_deal_zone:
                    cards = "$state.deal.kitty"
                else:
                    cards = []
                table[zone.id] = {"owner": None, "visibility": zone.visibility,
                                  "cards": cards}
        return table

    def _setup_nodes(self) -> None:
        cards_each, kitty = self._deal_plan()
        self._eval("setup_seed", {"join": ["$state.seed", ":composed"]}, "deal_seed",
                   "setup_deal", "setup")
        self._call("setup_deal", "deck", "deal",
                   {"seed": "$state.deal_seed", "hands": self.players,
                    "cards_each": cards_each, "kitty": kitty},
                   "init", "setup", result_key="deal")
        values: dict[str, Any] = {"zones": self._zone_table(), "scores": [0] * self.players,
                                  "round": 1, "action_count": 0, "turn_index": 0,
                                  "finished": False, "winners": [], "phase": "play"}
        if self._has_trigger:
            values["skip_next"] = 0
        for variable in self.ir.variables:
            values[f"v_{variable.name}"] = variable.initial
        self._update("init", values, "round_start", "setup.initialize")

    # -------------------------------------------------------------- turn loop
    def _turn_loop(self) -> None:
        after_start = "second_seat" if self._has_compare else "turn_setup"
        if self.ir.flow.start_seat == "round_parity":
            self._eval("round_start",
                       {"mod": [{"sub": ["$state.round", 1]}, self.players]},
                       "first_seat", after_start, "flow.start_seat")
        else:
            self._update("round_start", {"first_seat": 0}, after_start, "flow.start_seat")
        if self._has_compare:
            if self.players != 2:
                self._fail("compare_requires_two_players", f"got {self.players}",
                           "flow.resolve.0")
            self._eval("second_seat", {"sub": [self.players - 1, "$state.first_seat"]},
                       "second_seat", "turn_setup", "flow.resolve.0")

        # Build the turn's wait/guard chain first so the zone-instance nodes know
        # where to hand off (ADR-0012).
        self._turn_entry = self._build_turn_waits()
        self._build_zone_nodes()
        self._update("turn_setup", {"turn_index": 0, "current_player": "$state.first_seat"},
                     self._zone_entry or self._turn_entry, "flow.round_action")
        self._after_turn_nodes()

    def _build_turn_waits(self) -> str:
        """Emit the wait/guard chain for the turn's ordered actions (ADR-0012).

        The first action whose guard passes is offered; a failing guard falls
        through to the next candidate, and a final failing guard advances the
        turn with no action. Single-action rules keep the M2 node names, so their
        plan fingerprints do not change.
        """
        entries = {action.id: self._compile_action(action) for action in self.actions}
        if len(self.actions) == 1:
            action = self.actions[0]
            self._add("turn", {"kind": "wait", "inputs": {action.id: entries[action.id]}},
                      f"actions.{action.id}")
            if action.guard is None:
                return "turn"
            path = f"actions.{action.id}.guard"
            guard_entry = self._lower_guard(action.guard, "guard_ok", "guard_branch",
                                            path, plain_node_id="pre_turn")
            self._branch("guard_branch", "$state.guard_ok",
                         [{"value": True, "target": "turn"}], "bump_turn", path)
            return guard_entry
        entry: str | None = None
        for action in reversed(self.actions):
            wait_id = f"turn_{action.id}"
            self._add(wait_id, {"kind": "wait", "inputs": {action.id: entries[action.id]}},
                      f"actions.{action.id}")
            if action.guard is None:
                entry = wait_id
                continue
            guard_id, branch_id = f"guard_{action.id}", f"guard_{action.id}_branch"
            path = f"actions.{action.id}.guard"
            guard_entry = self._lower_guard(action.guard, f"guard_ok_{action.id}",
                                            branch_id, path, plain_node_id=guard_id)
            self._branch(branch_id, f"$state.guard_ok_{action.id}",
                         [{"value": True, "target": wait_id}], entry or "bump_turn", path)
            entry = guard_entry
        return entry or "bump_turn"

    def _lower_guard(self, expr: Any, result_key: str, next_id: str, path: str,
                     plain_node_id: str) -> str:
        """Emit a guard expression, returning the entry node of its chain.

        A guard with no top/zone references compiles to one evaluate node whose
        id is ``plain_node_id``, so single-action plans keep their M2 names and
        fingerprint. A guard that reads a zone top emits the extra host calls in
        front of that node (ADR-0012 section 8).
        """
        builder = _GuardBuilder(self, path)
        builder.finish(builder.emit(expr), result_key, next_id, plain_node_id)
        assert builder.first is not None
        return builder.first

    def _compile_action(self, action: Any) -> str:
        if not action.effects:
            return "advance_turn"
        bounds = {effect.result: self._input_bounds(action, effect.input)
                  for effect in action.effects if isinstance(effect, SelectEffect)}
        entry: str | None = None
        previous: str | None = None

        def link(node_id: str) -> None:
            nonlocal entry, previous
            if entry is None:
                entry = node_id
            if previous is not None:
                self.nodes[previous]["next"] = node_id
            previous = node_id

        for index, effect in enumerate(action.effects):
            path = f"actions.{action.id}.effects.{index}"
            if isinstance(effect, SelectEffect):
                node_id = f"act_{action.id}_{index}_select"
                low, high = bounds[effect.result]
                self._call(node_id, "zones", "select",
                           {"state": "$state", "zone": self._input_zone_arg(action, effect.input),
                            "card_ids": f"$state.input.{effect.input}",
                            "min_count": low, "max_count": high},
                           "PENDING", path, result_key=f"sel_{effect.result}")
                link(node_id)
            elif isinstance(effect, MoveSelectionEffect):
                node_id = f"act_{action.id}_{index}_move"
                low, high = self._selection_bounds(action, effect.selection)
                self._call(node_id, "zones", "move",
                           {"state": "$state", "moves": [{
                               "from": self._zone_arg(effect.from_zone),
                               "to": self._zone_arg(effect.to_zone),
                               "card_ids": f"$state.sel_{effect.selection}.ids",
                               "min_count": low, "max_count": high}]},
                           "PENDING", path)
                link(node_id)
            elif isinstance(effect, AssignEffect):
                eval_id, set_id = f"act_{action.id}_{index}_eval", f"act_{action.id}_{index}_set"
                self._eval(eval_id, compile_expression(effect.value), f"t_assign_{index}",
                           set_id, path)
                self._update(set_id, {f"v_{effect.variable}": f"$state.t_assign_{index}"},
                             "PENDING", path)
                link(eval_id)
                previous = set_id
            elif isinstance(effect, RemovePairsEffect):
                first, last = self._remove_pairs_nodes(action, effect, index, path)
                link(first)
                previous = last
            elif isinstance(effect, RefillEffect):
                first, last = self._refill_nodes(action, effect, index, path)
                link(first)
                previous = last
            elif isinstance(effect, DrawEffect):
                first, last = self._draw_nodes(action, effect, index, path)
                link(first)
                previous = last
            else:
                self._fail("unsupported_action_effect", effect.kind, path)
        if previous is not None:
            self.nodes[previous]["next"] = "advance_turn"
        return entry or "advance_turn"

    def _remove_pairs_nodes(self, action: Any, effect: RemovePairsEffect, index: int,
                            path: str) -> tuple[str, str]:
        """Lower ``remove_pairs`` to select_duplicates + move + declared score.

        ADR-0011: the operation only *finds* the groups; the source/target zones
        and the points per group come from the rule. ``max_total`` is the deck
        bound, a static upper limit, and the score uses the group count so a
        group of any declared size works.
        """
        action_id = action.id
        source = self._zone_arg(effect.from_zone)
        target = self._zone_arg(effect.to_zone)
        select_id = f"act_{action_id}_{index}_pairs"
        points_id = f"act_{action_id}_{index}_pair_points"
        move_id = f"act_{action_id}_{index}_pair_move"
        score_id = f"act_{action_id}_{index}_pair_score"
        self._call(select_id, "zones", "select_duplicates",
                   {"state": "$state", "zone": source, "key": "rank",
                    "min_count": effect.group_size, "max_group": effect.group_size,
                    "max_total": MAX_DUPLICATE_TOTAL},
                   points_id, path, result_key=f"sel_pairs_{index}")
        self._eval(points_id,
                   {"mul": [{"count": [f"$state.sel_pairs_{index}.groups"]},
                             effect.points_per_pair]},
                   f"t_pairs_{index}", move_id, path)
        self._call(move_id, "zones", "move",
                   {"state": "$state", "moves": [{
                       "from": source, "to": target,
                       "card_ids": f"$state.sel_pairs_{index}.ids",
                       "min_count": 0, "max_count": MAX_DUPLICATE_TOTAL}]},
                   score_id, path)
        self._call(score_id, "score_settle", "call",
                   {"scores": "$state.scores",
                    "winners": ["$state.current_player"],
                    "points": f"$state.t_pairs_{index}"},
                   "PENDING", path, result_key="scores")
        return select_id, score_id

    def _refill_nodes(self, action: Any, effect: RefillEffect, index: int,
                      path: str) -> tuple[str, str]:
        """Lower ``refill`` to a bounded, unrolled sequence of stock draws.

        Each step checks the target and the stock *before* drawing, so the loop
        is a finite straight line with an exit; a card is only ever moved out of
        the stock, so total card conservation holds.
        """
        action_id = action.id
        source, target = effect.from_zone, self._zone_arg(effect.to_zone)
        done_id = f"act_{action_id}_{index}_refill_done"
        first: str | None = None
        for step in range(effect.max_draw):
            base = f"act_{action_id}_{index}_refill_{step}"
            hand_id, stock_id = f"{base}_hand", f"{base}_stock"
            can_id, branch_id = f"{base}_can", f"{base}_branch"
            top_id, move_id = f"{base}_top", f"{base}_move"
            nxt = (f"act_{action_id}_{index}_refill_{step + 1}_hand"
                   if step + 1 < effect.max_draw else done_id)
            self._call(hand_id, "zones", "count_zone", {"state": "$state", "zone": target},
                       stock_id, path, result_key=f"refill_hand_{index}_{step}")
            self._call(stock_id, "zones", "count_zone", {"state": "$state", "zone": source},
                       can_id, path, result_key=f"refill_stock_{index}_{step}")
            self._eval(can_id, {"all": [
                {"lt": [f"$state.refill_hand_{index}_{step}.count", effect.target_count]},
                {"gt": [f"$state.refill_stock_{index}_{step}.count", 0]}]},
                f"refill_can_{index}_{step}", branch_id, path)
            self._branch(branch_id, f"$state.refill_can_{index}_{step}",
                         [{"value": True, "target": top_id}], done_id, path)
            self._call(top_id, "zones", "top", {"state": "$state", "zone": source},
                       move_id, path, result_key=f"refill_top_{index}_{step}")
            self._call(move_id, "zones", "move",
                       {"state": "$state", "moves": [{
                           "from": source, "to": target,
                           "card_ids": [f"$state.refill_top_{index}_{step}.id"],
                           "min_count": 1, "max_count": 1}]}, nxt, path)
            if first is None:
                first = hand_id
        self._update(done_id, {}, "PENDING", path)
        return first or done_id, done_id

    def _draw_nodes(self, action: Any, effect: DrawEffect, index: int,
                    path: str) -> tuple[str, str]:
        """Lower ``draw`` to a bounded, unrolled sequence of stock draws.

        Each step checks the stock before drawing, so the sequence is a finite
        straight line and a card only ever leaves the stock (conservation).
        """
        action_id = action.id
        source, target = effect.from_zone, self._zone_arg(effect.to_zone)
        done_id = f"act_{action_id}_{index}_draw_done"
        first: str | None = None
        for step in range(effect.count):
            base = f"act_{action_id}_{index}_draw_{step}"
            stock_id, can_id, branch_id = f"{base}_stock", f"{base}_can", f"{base}_branch"
            top_id, move_id = f"{base}_top", f"{base}_move"
            nxt = (f"act_{action_id}_{index}_draw_{step + 1}_stock"
                   if step + 1 < effect.count else done_id)
            self._call(stock_id, "zones", "count_zone", {"state": "$state", "zone": source},
                       can_id, path, result_key=f"draw_stock_{index}_{step}")
            self._eval(can_id, {"gt": [f"$state.draw_stock_{index}_{step}.count", 0]},
                       f"draw_can_{index}_{step}", branch_id, path)
            self._branch(branch_id, f"$state.draw_can_{index}_{step}",
                         [{"value": True, "target": top_id}], done_id, path)
            self._call(top_id, "zones", "top", {"state": "$state", "zone": source},
                       move_id, path, result_key=f"draw_top_{index}_{step}")
            self._call(move_id, "zones", "move",
                       {"state": "$state", "moves": [{
                           "from": source, "to": target,
                           "card_ids": [f"$state.draw_top_{index}_{step}.id"],
                           "min_count": 1, "max_count": 1}]}, nxt, path)
            if first is None:
                first = stock_id
        self._update(done_id, {}, "PENDING", path)
        return first or done_id, done_id

    def _input_bounds(self, action: Any, input_id: str) -> tuple[int, int]:
        item = next((item for item in action.inputs if item.id == input_id), None)
        if item is None:
            self._fail("unknown_input", input_id, f"actions.{action.id}")
        return item.min_count, item.max_count

    def _selection_bounds(self, action: Any, selection: str) -> tuple[int, int]:
        for effect in action.effects:
            if isinstance(effect, SelectEffect) and effect.result == selection:
                return self._input_bounds(action, effect.input)
        return 1, 5

    def _input_zone_arg(self, action: Any, input_id: str) -> str:
        item = next(item for item in action.inputs if item.id == input_id)
        return self._zone_arg(item.zone)

    def _after_turn_nodes(self) -> None:
        self._eval("advance_turn", {"add": ["$state.action_count", 1]}, "next_action_count",
                   "set_action_count", "flow.round_action")
        # ADR-0010/ADR-0012: check the action/score terminal gate after the
        # action's own effects. The trigger phase (skip) runs only on the
        # continue path, so a finished game never skips. A round-scoring rule
        # checks the action budget only after resolve.
        trigger_entry = self._trigger_nodes()
        action_gate = self._terminal_gate("after_action", trigger_entry,
                                          include_action_budget=not self._has_compare)
        self._update("set_action_count", {"action_count": "$state.next_action_count"},
                     action_gate, "flow.round_action")
        # The seat advances by 1 + any pending skip (ADR-0012 section 7); the
        # skipped seat never reaches ``advance_turn`` so it is not counted. A
        # rule without triggers keeps the exact M2 nodes and fingerprint.
        if self._has_trigger:
            self._eval("bump_turn",
                       {"add": [{"add": ["$state.turn_index", 1]}, "$state.skip_next"]},
                       "next_turn_index", "set_turn_index", "flow.round_action")
            self._update("set_turn_index", {"turn_index": "$state.next_turn_index",
                                            "skip_next": 0},
                         "turn_check", "flow.round_action")
        else:
            self._eval("bump_turn", {"add": ["$state.turn_index", 1]}, "next_turn_index",
                       "set_turn_index", "flow.round_action")
            self._update("set_turn_index", {"turn_index": "$state.next_turn_index"},
                         "turn_check", "flow.round_action")
        self._eval("turn_check", {"ge": ["$state.turn_index", self.players]}, "round_done",
                   "turn_check_branch", "flow.round_action")
        self._branch("turn_check_branch", "$state.round_done",
                     [{"value": True, "target": "resolve_start"}], "next_turn",
                     "flow.round_action")
        # A round's scoring happens in resolve, so the gate runs again afterwards.
        round_gate = self._terminal_gate("after_resolve", "end_round",
                                         include_action_budget=True)
        resolve_entry = self._compile_resolve(round_gate)
        self.nodes["turn_check_branch"]["cases"][0]["target"] = resolve_entry
        self._eval("next_turn",
                   {"mod": [{"add": ["$state.first_seat", "$state.turn_index"]}, self.players]},
                   "next_seat", "set_next_seat", "flow.round_action")
        self._update("set_next_seat", {"current_player": "$state.next_seat"},
                     self._zone_entry or self._turn_entry, "flow.round_action")
        self._end_round_nodes()

    # --------------------------------------------------------------- triggers
    def _trigger_nodes(self) -> str:
        """Emit the trigger phase, after the terminal gate and before advancing.

        ADR-0012: a trigger runs only on the terminal gate's continue path, so a
        finished game never skips (B10). ``state.input.action`` records which
        candidate action ran, so one shared phase dispatches each action's
        triggers instead of duplicating the turn loop per action.
        """
        prepared = [(action, self._compile_triggers(action))
                    for action in self.actions if action.trigger]
        if not prepared:
            return "bump_turn"
        target = "bump_turn"
        for action, chain_entry in reversed(prepared):
            path = f"actions.{action.id}.trigger"
            check_id = f"trigger_{action.id}_check"
            branch_id = f"trigger_{action.id}_branch"
            self._eval(check_id, {"eq": ["$state.input.action", action.id]},
                       f"trigger_{action.id}_hit", branch_id, path)
            self._branch(branch_id, f"$state.trigger_{action.id}_hit",
                         [{"value": True, "target": chain_entry}], target, path)
            target = check_id
        return target

    def _compile_triggers(self, action: Any) -> str:
        """Lower one action's trigger list to a bounded skip chain (ADR-0012)."""
        entry: str | None = None
        exits: list[str] = []
        for index, effect in enumerate(action.trigger):
            path = f"actions.{action.id}.trigger.{index}"
            if not isinstance(effect, SkipEffect):
                self._fail("unsupported_trigger_effect", effect.kind, path)
            set_id = f"trig_{action.id}_{index}_set"
            self._update(set_id, {"skip_next": effect.count}, "PENDING", path)
            if effect.condition is None:
                start, new_exits = set_id, [set_id]
            else:
                eval_id = f"trig_{action.id}_{index}_eval"
                branch_id = f"trig_{action.id}_{index}_branch"
                guard_entry = self._lower_guard(effect.condition,
                                                f"trig_ok_{action.id}_{index}",
                                                branch_id, path, plain_node_id=eval_id)
                self._branch(branch_id, f"$state.trig_ok_{action.id}_{index}",
                             [{"value": True, "target": set_id}], "PENDING", path)
                start, new_exits = guard_entry, [branch_id, set_id]
            if entry is None:
                entry = start
            for exit_id in exits:
                self.nodes[exit_id]["next"] = start
            exits = new_exits
        for exit_id in exits:
            self.nodes[exit_id]["next"] = "bump_turn"
        return entry or "bump_turn"

    # ---------------------------------------------------------------- resolve
    def _compile_resolve(self, resolve_exit: str = "end_round") -> str:
        if not self.ir.flow.resolve:
            # A rule can score entirely inside its action; the turn then goes
            # straight to the round gate (ADR-0011).
            return resolve_exit
        segments = [self._resolve_segment(effect, index)
                    for index, effect in enumerate(self.ir.flow.resolve)]
        for index, (_, exits) in enumerate(segments):
            nxt = segments[index + 1][0] if index + 1 < len(segments) else resolve_exit
            for exit_id in exits:
                self.nodes[exit_id]["next"] = nxt
        return segments[0][0]

    def _resolve_segment(self, effect: Any, index: int) -> tuple[str, list[str]]:
        path = f"flow.resolve.{index}"
        if isinstance(effect, CompareEffect):
            return self._segment_compare(effect, index, path)
        if isinstance(effect, MoveTopEffect):
            return self._segment_move_top(effect, index, path)
        self._fail("unsupported_resolve_effect", effect.kind, path)
        raise AssertionError("unreachable")  # pragma: no cover

    def _segment_compare(self, effect: CompareEffect, index: int,
                         path: str) -> tuple[str, list[str]]:
        zone = self.ir.zone(effect.zone)
        if zone is None or zone.scope != "shared":
            self._fail("compare_requires_shared_zone", effect.zone, path)
        compare_id = f"resolve_cmp_{index}"
        branch_id = f"resolve_cmp_branch_{index}"
        self._call(compare_id, "rank_compare", "call",
                   {"left": f"$state.zones.{effect.zone}.cards.{effect.left}",
                    "right": f"$state.zones.{effect.zone}.cards.{effect.right}"},
                   branch_id, path, result_key=f"cmp_{index}")

        by_outcome: dict[str, list[Any]] = {}
        for rule_id in effect.rules:
            rule = next(rule for rule in self.ir.scoring if rule.id == rule_id)
            by_outcome.setdefault(rule.on_outcome, []).append(rule)
        cases: list[dict[str, Any]] = []
        exits: list[str] = []
        for outcome in sorted(by_outcome):
            rules = by_outcome[outcome]
            first = f"resolve_award_{index}_{outcome}_0"
            cases.append({"value": outcome, "target": first})
            for position, rule in enumerate(rules):
                node_id = f"resolve_award_{index}_{outcome}_{position}"
                last = position + 1 == len(rules)
                winner = ("$state.first_seat" if rule.recipient == "left"
                          else "$state.second_seat")
                nxt = "PENDING" if last else f"resolve_award_{index}_{outcome}_{position + 1}"
                self._call(node_id, "score_settle", "call",
                           {"scores": "$state.scores", "winners": [winner],
                            "points": rule.points}, nxt, f"scoring.{rule.id}",
                           result_key="scores")
                if last:
                    exits.append(node_id)
        # A missing outcome (e.g. a tie with no rule) falls through the branch.
        self._branch(branch_id, f"$state.cmp_{index}.outcome", cases, "PENDING", path)
        exits.append(branch_id)
        return compare_id, exits

    def _segment_move_top(self, effect: MoveTopEffect, index: int,
                          path: str) -> tuple[str, list[str]]:
        first = f"resolve_top_{index}_0"
        for step in range(effect.count):
            top_id = f"resolve_top_{index}_{step}"
            move_id = f"resolve_movetop_{index}_{step}"
            nxt = f"resolve_top_{index}_{step + 1}" if step + 1 < effect.count else "PENDING"
            self._call(top_id, "zones", "top", {"state": "$state", "zone": effect.from_zone},
                       move_id, path, result_key=f"t_top_{index}_{step}")
            self._call(move_id, "zones", "move",
                       {"state": "$state", "moves": [{
                           "from": effect.from_zone, "to": effect.to_zone,
                           "card_ids": [f"$state.t_top_{index}_{step}.id"],
                           "min_count": 1, "max_count": 1}]},
                       nxt, path)
        return first, [f"resolve_movetop_{index}_{effect.count - 1}"]

    def _end_round_nodes(self) -> None:
        self._eval("end_round", {"add": ["$state.round", 1]}, "next_round", "set_round",
                   "terminal")
        if self.ir.terminal.max_rounds is None:
            # No round bound: the counter still advances (for round parity), but
            # termination comes from the action/score gate (ADR-0010).
            self._update("set_round", {"round": "$state.next_round"}, "round_start",
                         "terminal")
        else:
            self._update("set_round", {"round": "$state.next_round"}, "round_check",
                         "terminal")
            self._eval("round_check", {"gt": ["$state.round", self.ir.terminal.max_rounds]},
                       "at_end", "round_check_branch", "terminal")
            self._branch("round_check_branch", "$state.at_end",
                         [{"value": True, "target": "finish"}], "round_start", "terminal")
        self._call("finish", "winner_resolve", "call",
                   {"values": "$state.scores", "mode": "max"}, "declare", "terminal",
                   result_key="winner_indexes")
        self._update("declare", {"finished": True, "winners": "$state.winner_indexes",
                                 "phase": "finished"}, "end", "terminal")
        self._add("end", {"kind": "end"}, "terminal")

    def _terminal_gate(self, prefix: str, on_continue: str,
                       include_action_budget: bool) -> str:
        """Emit a threshold/budget check that finishes the game, or pass through.

        ADR-0010: the check runs after the current action's effects (and, for a
        round-scoring rule, after resolve); hitting the boundary ends the game
        immediately, without advancing the seat once more. Multiple conditions
        are OR-ed. ``include_action_budget`` is false for the action gate of a
        round-scoring rule, so an action budget can never skip a round's scoring.
        """
        terminal = self.ir.terminal
        conditions: list[Any] = []
        if terminal.score_reaches is not None:
            threshold = terminal.score_reaches
            conditions.append({"any": [{"ge": [f"$state.scores.{seat}", threshold]}
                                       for seat in range(self.players)]})
        if include_action_budget and terminal.max_actor_actions is not None:
            conditions.append({"ge": ["$state.action_count", terminal.max_actor_actions]})
        if not conditions:
            return on_continue
        expression = conditions[0] if len(conditions) == 1 else {"any": conditions}
        result_key = f"{prefix}_terminal"
        self._eval(prefix, expression, result_key, f"{prefix}_branch", "terminal")
        self._branch(f"{prefix}_branch", f"$state.{result_key}",
                     [{"value": True, "target": "finish"}], on_continue, "terminal")
        return prefix


# --------------------------------------------------------------------- public
def _tool_bindings(ir: ComposedRulesIR) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = [{"name": "state"}, {"name": "logic"},
                                      {"name": "zones"}, {"name": "score_settle"},
                                      {"name": "winner_resolve"}]
    deck_config: dict[str, Any] = {"ranks": list(ir.deck.ranks), "suits": list(ir.deck.suits),
                                   "copies": ir.deck.copies}
    if ir.deck.values:
        deck_config["values"] = dict(ir.deck.values)
    bindings.append({"name": "deck", "config": deck_config})
    if any(isinstance(effect, CompareEffect) for effect in ir.flow.resolve):
        bindings.append({"name": "rank_compare"})
    if any("pattern.choices" in guard_mechanisms(action.guard)
           for action in ir.actions if action.guard is not None):
        bindings.append({"name": "pattern"})
    return bindings


def _action_descriptors(ir: ComposedRulesIR) -> list[ActionDescriptor]:
    """The plan-data description of each action's typed inputs.

    This is the M3/Plan-0.5 half of the IR: the compiler publishes the input
    *shape* (zone, scope, bounds) so the host bot and the API can build a legal
    payload without reading compiled node arguments. The declared zone id and
    scope are kept verbatim -- ``actor`` resolution happens against the live
    state, which is what lets one descriptor serve both seats.
    """
    return [ActionDescriptor(id=action.id, label="", inputs=[
        ActionInputDescriptor(id=item.id, kind=item.kind, zone=item.zone,
                              scope=item.scope, min_count=item.min_count,
                              max_count=item.max_count)
        for item in action.inputs]) for action in ir.actions]


def build_source_map(ir: ComposedRulesIR, entries: list[dict[str, str]]) -> dict[str, Any]:
    by_node: dict[str, dict[str, str]] = {}
    by_path: dict[str, list[str]] = {}
    by_clause: dict[str, list[str]] = {}
    for entry in entries:
        node, path, clause = entry["node"], entry["path"], entry["clause"]
        by_node[node] = {"path": path, "clause": clause}
        by_path.setdefault(path, []).append(node)
        if clause != "unmapped":
            by_clause.setdefault(clause, []).append(node)
    for mapping in (by_path, by_clause):
        for key in mapping:
            mapping[key] = sorted(set(mapping[key]))
    return {"version": "0.1", "compiler_version": COMPILER_VERSION,
            "ir_hash": ir_hash(ir), "nodes": by_node, "paths": by_path,
            "clauses": by_clause}


def compile_composed(ir: ComposedRulesIR | dict[str, Any],
                     registry: ToolRegistry | None = None,
                     catalog: Any = None) -> CompiledRules:
    """Compile a composed rule into a plan plus its provenance.

    Raises :class:`CompileError` (a ``ToolError``) carrying ``code``/``path``/
    ``clause`` on any deterministic failure, so a caller gets a located error, not
    a bare traceback.
    """
    if isinstance(ir, dict):
        try:
            ir = ComposedRulesIR.model_validate(ir)
        except ValidationError as error:
            first = error.errors()[0]
            path = ".".join(str(part) for part in first["loc"])
            raise CompileError("invalid_ir", first["msg"], path) from error

    composition = resolve_composition(ir, catalog if catalog is not None else registry)
    if not composition.ok:
        first = composition.missing[0]
        raise CompileError("missing_requirement", f"{first.code}:{first.detail}",
                           first.path, first.clause)

    nodes, entries = _Compiler(ir).compile()
    if len(nodes) > MAX_PLAN_NODES:
        raise CompileError("plan_node_budget_exceeded", f"{len(nodes)}>{MAX_PLAN_NODES}",
                           "flow", clause_for(ir, "flow"))

    plan = GamePlan(schema_version="0.5", game_kind="composed", players=ir.players.count,
                    tools=_tool_bindings(ir), actions=_action_descriptors(ir),
                    initial={"reveal": False}, entry="setup_seed", nodes=nodes,
                    step_limit=MAX_STEP_LIMIT)
    flow = analyse_control_flow(plan, _BOUNDED_NODES)
    if flow["unreachable"]:
        raise CompileError("unreachable_plan_nodes", ",".join(flow["unreachable"]),
                           "plan", clause_for(ir, "plan"))
    if flow["unbounded_cycles"]:
        raise CompileError("unbounded_cycle", ";".join(flow["unbounded_cycles"][0]),
                           "plan", clause_for(ir, "plan"))
    contract_hash = (registry or _default_registry()).contract_hash()
    return CompiledRules(plan=plan, source_map=build_source_map(ir, entries),
                         ir_hash=ir_hash(ir), compiler_version=COMPILER_VERSION,
                         registry_contract_hash=contract_hash, composition=composition)


def _default_registry() -> ToolRegistry:
    from ..registry import core_registry
    return core_registry()
