"""The gate a plan must pass before it may be called playable.

``playtest`` is host-owned: it runs full games across seeds and strategies with
deterministic policies, checks caller-supplied invariants, verifies byte-exact
replay, and (by default) requires every ``wait`` node to be covered.

The gate exists because a plan can "run" yet violate the contract it claims:
the interpreter only knows mechanics, so correctness must be observed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from .contracts import ToolError, ToolRegistry
from .interpreter import Interpreter
from .plan import GamePlan

Strategy = Callable[[Interpreter], "tuple[str, dict[str, Any]] | None"]
Invariant = Callable[[Interpreter], None]

# Actions that exercise boundaries rather than the "happy path".
_BOUNDARY_PRIORITY = ("pass", "give_up", "no_solution", "fold", "check", "next_round")


@dataclass
class PlaytestReport:
    ok: bool
    seeds: tuple[int, ...]
    event_counts: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    covered_wait_nodes: list[str] = field(default_factory=list)

    def raise_if_failed(self) -> "PlaytestReport":
        if not self.ok:
            raise ToolError("playtest_failed: " + "; ".join(self.failures))
        return self


def first_legal(interpreter: Interpreter):
    """First legal action; the minimal deterministic policy."""
    actions = interpreter.legal_actions()
    return (actions[0], {}) if actions else None


def random_legal(interpreter: Interpreter):
    """Deterministic legal-random: seeded by the interpreter, not global RNG."""
    actions = interpreter.legal_actions()
    if not actions:
        return None
    index = (interpreter.seed + len(interpreter.events) * 17) % len(actions)
    return (actions[index], {})


def boundary_first(interpreter: Interpreter):
    """Prefer boundary actions (give up / pass / fold) before the happy path."""
    actions = interpreter.legal_actions()
    if not actions:
        return None
    for action in _BOUNDARY_PRIORITY:
        if action in actions:
            return (action, {})
    return (actions[-1], {})


def _play_one(plan: GamePlan, registry: ToolRegistry, seed: int, strategy: Strategy,
              invariants: Sequence[Invariant], max_steps: int) -> list[dict[str, Any]]:
    interpreter = Interpreter(plan, registry, seed=seed)
    interpreter.setup()
    for _ in range(max_steps):
        if interpreter.state.get("finished"):
            break
        for invariant in invariants:
            invariant(interpreter)
        choice = strategy(interpreter)
        if choice is None:
            raise ToolError("strategy_returned_none")
        action, payload = choice
        interpreter.step(action, **(payload or {}))
    else:
        raise ToolError("playtest_step_limit")
    if not interpreter.state.get("finished"):
        raise ToolError("playtest_not_finished")
    for invariant in invariants:
        invariant(interpreter)
    return interpreter.events


def playtest(plan: GamePlan, registry: ToolRegistry,
             strategies: Strategy | Sequence[Strategy],
             seeds: Iterable[int] = (0, 7, 23), invariants: Sequence[Invariant] = (),
             max_steps: int = 2048, require_wait_coverage: bool = True) -> PlaytestReport:
    """Run full games across seeds x strategies and return a gate report."""
    if callable(strategies):
        strategies = [strategies]
    strategies = list(strategies)
    if not strategies:
        raise ToolError("playtest_requires_a_strategy")
    seeds = tuple(seeds)
    report = PlaytestReport(ok=True, seeds=seeds)
    visited: set[str] = set()
    for seed in seeds:
        for index, strategy in enumerate(strategies):
            label = f"{seed}:{index}"
            try:
                events = _play_one(plan, registry, seed, strategy, invariants, max_steps)
            except (ToolError, ValueError) as error:
                report.failures.append(f"{label}: {error}")
                continue
            report.event_counts[label] = len(events)
            report.checks.append(f"{label}: {len(events)} events")
            visited |= {event["flow_node"] for event in events
                        if isinstance(event.get("flow_node"), str)}
            try:
                replay = _play_one(plan, registry, seed, strategy, invariants, max_steps)
            except (ToolError, ValueError) as error:
                report.failures.append(f"{label} replay: {error}")
                continue
            if replay != events:
                report.failures.append(f"{label}: nondeterministic_replay")
    waits = {node_id for node_id, node in plan.nodes.items() if node.kind == "wait"}
    report.covered_wait_nodes = sorted(waits & visited)
    if require_wait_coverage:
        uncovered = sorted(waits - visited)
        if uncovered:
            report.failures.append("wait_nodes_uncovered:" + ",".join(uncovered))
    report.ok = not report.failures
    return report
