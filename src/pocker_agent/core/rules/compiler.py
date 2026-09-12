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
from .composed import (
    MAX_PLAN_NODES,
    MAX_STEP_LIMIT,
    AssignEffect,
    CompareEffect,
    ComposedRulesIR,
    MoveSelectionEffect,
    MoveTopEffect,
    SelectEffect,
)
from .expr import compile_expression
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


class _Compiler:
    def __init__(self, ir: ComposedRulesIR) -> None:
        self.ir = ir
        self.players = ir.players.count
        self.action = ir.action(ir.flow.round_action)
        self.nodes: dict[str, dict[str, Any]] = {}
        self.entries: list[dict[str, str]] = []
        self._has_compare = any(isinstance(effect, CompareEffect)
                                for effect in ir.flow.resolve)
        self._hand_zone = next(deal.zone for deal in ir.setup.deals if deal.per_seat)
        self._shared_deals = [deal for deal in ir.setup.deals if not deal.per_seat]
        self._zone_entry: str | None = None

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

    def _pre_turn_entry(self) -> str:
        return "pre_turn" if self.action.guard is not None else "turn"

    # ------------------------------------------------------------------ build
    def compile(self) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
        self._setup_nodes()
        self._turn_loop()
        return self.nodes, self.entries

    def _actor_zone_ids(self) -> list[str]:
        found: set[str] = set()
        for item in self.action.inputs:
            found.add(item.zone)
        for effect in self.action.effects:
            if isinstance(effect, MoveSelectionEffect):
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
                       f"zone_{_safe(zone_id)}", self._pre_turn_entry(),
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

        self._build_zone_nodes()
        pre_turn = self._zone_entry or self._pre_turn_entry()
        self._update("turn_setup", {"turn_index": 0, "current_player": "$state.first_seat"},
                     pre_turn, "flow.round_action")

        effect_entry = self._compile_action()
        self._add("turn", {"kind": "wait", "inputs": {self.action.id: effect_entry}},
                  f"actions.{self.action.id}")
        if self.action.guard is not None:
            self._eval("pre_turn", compile_expression(self.action.guard), "guard_ok",
                       "guard_branch", f"actions.{self.action.id}.guard")
            self._branch("guard_branch", "$state.guard_ok",
                         [{"value": True, "target": "turn"}], "bump_turn",
                         f"actions.{self.action.id}.guard")
        self._after_turn_nodes()

    def _compile_action(self) -> str:
        if not self.action.effects:
            return "advance_turn"
        bounds = {effect.result: self._input_bounds(effect.input)
                  for effect in self.action.effects if isinstance(effect, SelectEffect)}
        entry: str | None = None
        previous: str | None = None

        def link(node_id: str) -> None:
            nonlocal entry, previous
            if entry is None:
                entry = node_id
            if previous is not None:
                self.nodes[previous]["next"] = node_id
            previous = node_id

        for index, effect in enumerate(self.action.effects):
            path = f"actions.{self.action.id}.effects.{index}"
            if isinstance(effect, SelectEffect):
                node_id = f"act_{self.action.id}_{index}_select"
                low, high = bounds[effect.result]
                self._call(node_id, "zones", "select",
                           {"state": "$state", "zone": self._input_zone_arg(effect.input),
                            "card_ids": f"$state.input.{effect.input}",
                            "min_count": low, "max_count": high},
                           "PENDING", path, result_key=f"sel_{effect.result}")
                link(node_id)
            elif isinstance(effect, MoveSelectionEffect):
                node_id = f"act_{self.action.id}_{index}_move"
                low, high = self._selection_bounds(effect.selection)
                self._call(node_id, "zones", "move",
                           {"state": "$state", "moves": [{
                               "from": self._zone_arg(effect.from_zone),
                               "to": self._zone_arg(effect.to_zone),
                               "card_ids": f"$state.sel_{effect.selection}.ids",
                               "min_count": low, "max_count": high}]},
                           "PENDING", path)
                link(node_id)
            elif isinstance(effect, AssignEffect):
                eval_id, set_id = f"act_{self.action.id}_{index}_eval", f"act_{self.action.id}_{index}_set"
                self._eval(eval_id, compile_expression(effect.value), f"t_assign_{index}",
                           set_id, path)
                self._update(set_id, {f"v_{effect.variable}": f"$state.t_assign_{index}"},
                             "PENDING", path)
                link(eval_id)
                previous = set_id
            else:
                self._fail("unsupported_action_effect", effect.kind, path)
        if previous is not None:
            self.nodes[previous]["next"] = "advance_turn"
        return entry or "advance_turn"

    def _input_bounds(self, input_id: str) -> tuple[int, int]:
        item = next((item for item in self.action.inputs if item.id == input_id), None)
        if item is None:
            self._fail("unknown_input", input_id, f"actions.{self.action.id}")
        return item.min_count, item.max_count

    def _selection_bounds(self, selection: str) -> tuple[int, int]:
        for effect in self.action.effects:
            if isinstance(effect, SelectEffect) and effect.result == selection:
                return self._input_bounds(effect.input)
        return 1, 5

    def _input_zone_arg(self, input_id: str) -> str:
        item = next(item for item in self.action.inputs if item.id == input_id)
        return self._zone_arg(item.zone)

    def _after_turn_nodes(self) -> None:
        self._eval("advance_turn", {"add": ["$state.action_count", 1]}, "next_action_count",
                   "set_action_count", "flow.round_action")
        # ADR-0010: check the action/score terminal gate after the action's own
        # effects, before the seat advances. A round-scoring rule checks the
        # action budget only after resolve, so it is excluded here.
        action_gate = self._terminal_gate("after_action", "bump_turn",
                                          include_action_budget=not self._has_compare)
        self._update("set_action_count", {"action_count": "$state.next_action_count"},
                     action_gate, "flow.round_action")
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
                     self._zone_entry or self._pre_turn_entry(), "flow.round_action")
        self._end_round_nodes()

    # ---------------------------------------------------------------- resolve
    def _compile_resolve(self, resolve_exit: str = "end_round") -> str:
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
