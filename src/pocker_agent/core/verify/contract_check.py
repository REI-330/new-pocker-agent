"""Independent contract monitors generated from the composed IR (ADR-0008).

This is deliberately *not* the compiler. The compiler lowers the IR into a plan;
this module reads the same IR and observes a recorded trace to answer a
different question: did the running product actually implement the declared
clauses? Each monitor re-derives the expected result from IR data -- deck values,
scoring rules, the terminal rule, zone declarations -- rather than reading the
compiler's node arguments, so a plan mutation is caught even though the plan
still runs. That is what turns "the model hard-codes the winner" from a string
match into a rejection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..actions import zone_instance
from ..cards import CardRef
from ..contracts import ToolRegistry
from ..plan import GamePlan
from ..playtest import Strategy
from ..rules import (
    CompareEffect,
    ComposedRulesIR,
    MoveSelectionEffect,
    RefillEffect,
    RemovePairsEffect,
    SelectEffect,
)
from ..rules.requirements import clause_for
from .trace import GameTrace, compare_traces, record_trace, replay_trace

# Monitor name -> the IR clause it is derived from, so a failure names a clause.
MONITOR_CLAUSES: dict[str, str] = {
    "replay": "flow.resolve",
    "card_conservation": "setup",
    "selection_move": "actions",
    "compare_outcome": "flow.resolve",
    "scoring": "scoring",
    "pair_scoring": "scoring",
    "refill": "scoring",
    "terminal": "terminal",
    "zone_visibility": "zones",
}


@dataclass(frozen=True)
class ClauseCheck:
    name: str
    ok: bool
    detail: str = ""
    clause: str = ""


@dataclass(frozen=True)
class ContractReport:
    checks: tuple[ClauseCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def failures(self) -> list[str]:
        return [f"{check.name}:{check.detail}" for check in self.checks if not check.ok]

    def covered(self) -> list[str]:
        return [check.name for check in self.checks if check.ok]

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok,
                "checks": [{"name": check.name, "ok": check.ok, "detail": check.detail,
                            "clause": check.clause} for check in self.checks]}


# --------------------------------------------------------------------- helpers
def _card_id(card: Any) -> str:
    return card.id if isinstance(card, CardRef) else card["id"]


def _card_ids(state: dict[str, Any], zone_id: str) -> list[str]:
    zones = state.get("zones")
    entry = zones.get(zone_id) if isinstance(zones, dict) else None
    cards = entry.get("cards") if isinstance(entry, dict) else None
    return [_card_id(card) for card in cards] if isinstance(cards, list) else []


def _cards_at(state: dict[str, Any] | None, zone_id: str) -> list[Any]:
    zones = state.get("zones") if isinstance(state, dict) else None
    entry = zones.get(zone_id) if isinstance(zones, dict) else None
    cards = entry.get("cards") if isinstance(entry, dict) else None
    return list(cards) if isinstance(cards, list) else []


def _deck_value(ir: ComposedRulesIR, card: Any) -> int:
    rank = card.rank if isinstance(card, CardRef) else card["rank"]
    if rank in ir.deck.values:
        return ir.deck.values[rank]
    return card.value if isinstance(card, CardRef) else card["value"]


def _zone_for(ir: ComposedRulesIR, state: dict[str, Any], zone_id: str) -> str:
    zone = ir.zone(zone_id)
    if zone is not None and zone.scope == "player":
        return zone_instance(state, zone_id)
    return zone_id


# -------------------------------------------------------------------- monitors
def _conservation(trace: GameTrace) -> ClauseCheck:
    total: int | None = None
    for index, observation in enumerate(trace.observations):
        zones = observation.state.get("zones")
        if not isinstance(zones, dict):
            continue
        ids: list[str] = []
        for zone_id in sorted(zones):
            ids.extend(_card_ids(observation.state, zone_id))
        if len(ids) != len(set(ids)):
            return ClauseCheck("card_conservation", False, f"duplicate@{index}")
        if total is None:
            total = len(ids)
        elif len(ids) != total:
            return ClauseCheck("card_conservation", False, f"{len(ids)}!={total}@{index}")
    return ClauseCheck("card_conservation", True, f"total={total}")


def _moved_exactly(trace: GameTrace, index: int, source: str, target: str,
                   expected: list[str]) -> bool:
    before = trace.observations[index - 1].state if index > 0 else None
    after = trace.observations[index].state
    if before is None:
        return False
    left = sorted(set(_card_ids(before, source)) - set(_card_ids(after, source)))
    entered = sorted(set(_card_ids(after, target)) - set(_card_ids(before, target)))
    return left == sorted(expected) and entered == sorted(expected)


def _selection_move(ir: ComposedRulesIR, trace: GameTrace) -> ClauseCheck:
    for step, (start, end) in enumerate(trace.spans):
        entry = trace.inputs[step]
        action = ir.action(entry["action"])
        if action is None:
            return ClauseCheck("selection_move", False, f"unknown_action:{entry['action']}")
        before = trace.observations[start - 1].state if start > 0 else None
        if before is None:
            continue
        selects = {effect.result: effect for effect in action.effects
                   if isinstance(effect, SelectEffect)}
        for effect in action.effects:
            if not isinstance(effect, MoveSelectionEffect):
                continue
            select = selects.get(effect.selection)
            if select is None:
                return ClauseCheck("selection_move", False, f"no_select:{effect.selection}")
            expected = list(entry.get(select.input, []))
            source = _zone_for(ir, before, effect.from_zone)
            target = _zone_for(ir, before, effect.to_zone)
            found = any(_moved_exactly(trace, index, source, target, expected)
                        for index in range(start, end))
            if not found:
                return ClauseCheck("selection_move", False,
                                   f"{action.id}:{effect.selection}:{source}->{target}")
    return ClauseCheck("selection_move", True, f"steps={len(trace.spans)}")


def _compare_outcome(ir: ComposedRulesIR, trace: GameTrace) -> ClauseCheck:
    compares = [effect for effect in ir.flow.resolve if isinstance(effect, CompareEffect)]
    effect = compares[0] if len(compares) == 1 else None
    count = 0
    for index, observation in enumerate(trace.observations):
        if observation.tool != "rank_compare" or observation.operation != "call":
            continue
        count += 1
        left, right = observation.args.get("left"), observation.args.get("right")
        if not isinstance(left, CardRef) or not isinstance(right, CardRef):
            return ClauseCheck("compare_outcome", False, f"bad_args@{index}")
        if effect is not None:
            before = trace.observations[index - 1].state if index > 0 else None
            cards = _cards_at(before, effect.zone)
            if max(effect.left, effect.right) >= len(cards):
                return ClauseCheck("compare_outcome", False, f"positions_missing@{index}")
            if (left.id != _card_id(cards[effect.left])
                    or right.id != _card_id(cards[effect.right])):
                return ClauseCheck("compare_outcome", False, f"positions_mismatch@{index}")
        left_value, right_value = _deck_value(ir, left), _deck_value(ir, right)
        expected = "left" if left_value > right_value else (
            "right" if right_value > left_value else "tie")
        result = observation.state.get(observation.result_key) if observation.result_key else None
        observed = result.get("outcome") if isinstance(result, dict) else None
        if observed != expected:
            return ClauseCheck("compare_outcome", False,
                               f"expected={expected},observed={observed}@{index}")
    return ClauseCheck("compare_outcome", True, f"compares={count}")


def _scoring(ir: ComposedRulesIR, trace: GameTrace) -> ClauseCheck:
    compares = [effect for effect in ir.flow.resolve if isinstance(effect, CompareEffect)]
    if len(compares) != 1:
        return ClauseCheck("scoring", True, f"skipped:compare_count={len(compares)}")
    rules = [rule for rule in ir.scoring if rule.id in set(compares[0].rules)]
    settlements = 0
    outcome: str | None = None
    for index, observation in enumerate(trace.observations):
        if observation.tool == "rank_compare" and observation.operation == "call":
            result = observation.state.get(observation.result_key) if observation.result_key else None
            outcome = result.get("outcome") if isinstance(result, dict) else None
        elif observation.tool == "score_settle" and observation.operation == "call":
            settlements += 1
            if outcome is None:
                return ClauseCheck("scoring", False, f"no_preceding_compare@{index}")
            points = {"left": 0, "right": 0}
            for rule in rules:
                if rule.on_outcome == outcome:
                    points[rule.recipient] += rule.points
            before = trace.observations[index - 1].state
            first = before.get("first_seat", 0)
            second = (ir.players.count - 1 - first) % ir.players.count
            expected = [0] * ir.players.count
            expected[first] += points["left"]
            expected[second] += points["right"]
            deltas = [observation.state["scores"][seat] - before["scores"][seat]
                      for seat in range(ir.players.count)]
            if deltas != expected:
                return ClauseCheck("scoring", False,
                                   f"outcome={outcome},expected={expected},observed={deltas}@{index}")
    return ClauseCheck("scoring", True, f"settlements={settlements}")


def _pair_scoring(ir: ComposedRulesIR, trace: GameTrace) -> ClauseCheck:
    """Independent monitor for ``remove_pairs`` (ADR-0011).

    Re-derives the expected groups from the zone before the selection (every rank
    with at least ``group_size`` cards must appear, the groups must be a
    disjoint partition of the returned ids), and checks the subsequent score is
    exactly ``points_per_pair * number_of_groups``. This runs on the *group
    property*, not by reusing the tool's own return value.
    """
    effects = [effect for action in ir.actions for effect in action.effects
               if isinstance(effect, RemovePairsEffect)]
    if not effects:
        return ClauseCheck("pair_scoring", True, "no_remove_pairs")
    if len(effects) != 1:
        return ClauseCheck("pair_scoring", True, f"skipped:effects={len(effects)}")
    effect = effects[0]
    group_size = effect.group_size
    pending: int | None = None
    settlements = 0
    for index, observation in enumerate(trace.observations):
        if observation.tool == "zones" and observation.operation == "select_duplicates":
            args = observation.args
            if (args.get("key") != "rank" or args.get("min_count") != group_size
                    or args.get("max_group") != group_size):
                return ClauseCheck("pair_scoring", False, f"bounds_mismatch@{index}")
            result = observation.state.get(observation.result_key) if observation.result_key else None
            if not isinstance(result, dict):
                return ClauseCheck("pair_scoring", False, f"missing_result@{index}")
            ids = list(result.get("ids", []))
            groups = result.get("groups", [])
            flat = [card_id for group in groups for card_id in group.get("ids", [])]
            if sorted(flat) != sorted(ids) or len(set(ids)) != len(ids):
                return ClauseCheck("pair_scoring", False, f"not_a_partition@{index}")
            before = trace.observations[index - 1].state if index > 0 else None
            cards = _cards_at(before, args.get("zone"))
            by_rank: dict[str, list[str]] = {}
            for card in cards:
                by_rank.setdefault(card.rank, []).append(card.id)
            expected_ranks = {rank for rank, members in by_rank.items()
                              if len(members) >= group_size}
            observed_ranks = {group.get("key") for group in groups}
            if expected_ranks != observed_ranks:
                return ClauseCheck("pair_scoring", False,
                                   f"groups={sorted(observed_ranks)}"
                                   f",expected={sorted(expected_ranks)}@{index}")
            pending = len(ids)
        elif (observation.tool == "score_settle" and observation.operation == "call"
              and pending is not None):
            settlements += 1
            expected = effect.points_per_pair * (pending // group_size)
            before = trace.observations[index - 1].state
            deltas = [observation.state["scores"][seat] - before["scores"][seat]
                      for seat in range(ir.players.count)]
            if sum(deltas) != expected or any(delta < 0 for delta in deltas):
                return ClauseCheck("pair_scoring", False,
                                   f"expected={expected},observed={deltas}@{index}")
            pending = None
    return ClauseCheck("pair_scoring", True, f"settlements={settlements}")


def _refill(ir: ComposedRulesIR, trace: GameTrace) -> ClauseCheck:
    """Independent monitor: a refill target zone never exceeds its declared size.

    Together with ``card_conservation`` (every drawn card comes from the stock),
    this bounds the refill: it can neither overfill a hand nor invent cards.
    """
    effects = [effect for action in ir.actions for effect in action.effects
               if isinstance(effect, RefillEffect)]
    if not effects:
        return ClauseCheck("refill", True, "no_refill")
    if len(effects) != 1:
        return ClauseCheck("refill", True, f"skipped:effects={len(effects)}")
    effect = effects[0]
    for index, observation in enumerate(trace.observations):
        for seat in range(ir.players.count):
            zone_id = f"{effect.to_zone}-{seat}"
            count = len(_cards_at(observation.state, zone_id))
            if count > effect.target_count:
                return ClauseCheck("refill", False,
                                   f"{zone_id}={count}>{effect.target_count}@{index}")
    return ClauseCheck("refill", True, f"target<={effect.target_count}")


def _terminal(ir: ComposedRulesIR, trace: GameTrace) -> ClauseCheck:
    if not trace.finished or not trace.observations:
        return ClauseCheck("terminal", False, "game_did_not_finish")
    final = trace.observations[-1].state
    scores = list(final.get("scores", []))
    winners = sorted(final.get("winners", []))
    expected = [index for index, value in enumerate(scores) if value == max(scores)] if scores else []
    if winners != expected:
        return ClauseCheck("terminal", False, f"winners={winners},expected={expected}")

    terminal = ir.terminal
    actions = int(final.get("action_count", 0))
    # Completed rounds in the current model: one action per seat per round. The
    # compiled ``round`` counter can lag when a gate fires before ``end_round``,
    # so it is not reliable as the count of *played* rounds.
    rounds = actions // ir.players.count
    if terminal.max_actor_actions is not None and actions > terminal.max_actor_actions:
        return ClauseCheck("terminal", False,
                           f"actions={actions}>{terminal.max_actor_actions}")
    if terminal.max_rounds is not None and rounds > terminal.max_rounds:
        return ClauseCheck("terminal", False, f"rounds={rounds}>{terminal.max_rounds}")
    threshold_met = (terminal.score_reaches is not None and bool(scores)
                     and max(scores) >= terminal.score_reaches)
    action_bound_met = (terminal.max_actor_actions is not None
                        and actions == terminal.max_actor_actions)
    round_bound_met = terminal.max_rounds is not None and rounds == terminal.max_rounds
    if not (threshold_met or action_bound_met or round_bound_met):
        return ClauseCheck("terminal", False,
                           f"no_bound_satisfied:actions={actions},rounds={rounds},scores={scores}")
    return ClauseCheck("terminal", True,
                       f"winners={winners},actions={actions},rounds={rounds}")


def _visibility(ir: ComposedRulesIR, trace: GameTrace) -> ClauseCheck:
    if not trace.observations:
        return ClauseCheck("zone_visibility", True, "no_state")
    zones = trace.observations[-1].state.get("zones")
    if not isinstance(zones, dict):
        return ClauseCheck("zone_visibility", True, "no_zones")
    for zone in ir.zones:
        expected = zone.visibility
        ids = ([f"{zone.id}-{seat}" for seat in range(ir.players.count)]
               if zone.scope == "player" else [zone.id])
        for zone_id in ids:
            entry = zones.get(zone_id)
            if not isinstance(entry, dict):
                return ClauseCheck("zone_visibility", False, f"missing:{zone_id}")
            if entry.get("visibility", "public") != expected:
                return ClauseCheck("zone_visibility", False,
                                   f"{zone_id}:{entry.get('visibility')}!={expected}")
    return ClauseCheck("zone_visibility", True, f"zones={len(zones)}")


# ---------------------------------------------------------------------- driver
def contract_check(ir: ComposedRulesIR, plan: GamePlan, registry: ToolRegistry,
                   strategies: tuple[Strategy, ...] | list[Strategy],
                   seeds: tuple[int, ...] | list[int],
                   max_steps: int = 2048) -> ContractReport:
    """Record one trace per (seed, strategy) and run every IR-derived monitor.

    A failure anywhere short-circuits that monitor but not the others, so the
    report names every violated clause type at once.
    """
    failures: dict[str, str] = {}
    traces: list[GameTrace] = []
    for seed in seeds:
        for strategy in strategies:
            trace = record_trace(plan, registry, strategy, seed, max_steps=max_steps)
            traces.append(trace)
            problems = compare_traces(trace, replay_trace(plan, registry, trace))
            if problems:
                failures.setdefault("replay", f"{seed}:{strategy.__name__}:{problems[0]}")

    monitors = (("card_conservation", lambda t: _conservation(t)),
                ("selection_move", lambda t: _selection_move(ir, t)),
                ("compare_outcome", lambda t: _compare_outcome(ir, t)),
                ("scoring", lambda t: _scoring(ir, t)),
                ("pair_scoring", lambda t: _pair_scoring(ir, t)),
                ("refill", lambda t: _refill(ir, t)),
                ("terminal", lambda t: _terminal(ir, t)),
                ("zone_visibility", lambda t: _visibility(ir, t)))
    for name, monitor in monitors:
        if name in failures:
            continue
        for trace in traces:
            check = monitor(trace)
            if not check.ok:
                failures[name] = check.detail
                break

    names = ["replay", *(name for name, _ in monitors)]
    return ContractReport(tuple(
        ClauseCheck(name, name not in failures, failures.get(name, "ok"),
                    clause_for(ir, MONITOR_CLAUSES[name]))
        for name in names))
