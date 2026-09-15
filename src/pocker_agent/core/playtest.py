"""The gate a plan must pass before it may be called playable.

``playtest`` is host-owned: it runs full games across seeds and strategies with
deterministic policies, checks caller-supplied invariants, verifies byte-exact
replay, and (by default) requires every ``wait`` node to be covered.

The gate exists because a plan can "run" yet violate the contract it claims:
the interpreter only knows mechanics, so correctness must be observed.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .contracts import ToolError, ToolRegistry
from .decision import Policy, PolicyContext, policy_context, visible_payload
from .interpreter import Interpreter
from .plan import GamePlan

Strategy = Policy
Invariant = Callable[[Interpreter], None]

# Actions that exercise boundaries rather than the "happy path".
_BOUNDARY_PRIORITY = ("pass", "give_up", "no_solution", "fold", "check", "next_round")

# How many candidate payloads (card rotations) a strategy probes per action. A
# matching rule rejects a non-matching play, so the generic policy must be able
# to try the other cards in the zone -- still bounded and deterministic.
INPUT_CANDIDATE_LIMIT = 16


@dataclass
class PlaytestReport:
    ok: bool
    seeds: tuple[int, ...]
    event_counts: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    covered_wait_nodes: list[str] = field(default_factory=list)

    def raise_if_failed(self) -> PlaytestReport:
        if not self.ok:
            raise ToolError("playtest_failed: " + "; ".join(self.failures))
        return self

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "seeds": list(self.seeds),
                "event_counts": dict(self.event_counts), "failures": list(self.failures),
                "checks": list(self.checks), "covered_wait_nodes": list(self.covered_wait_nodes)}


def descriptor_candidates(context: PolicyContext, action: str, *, newest: bool = False,
                           ) -> list[dict[str, Any]] | None:
    """Candidate payloads for a plan-declared action, or ``None`` for a 0.4 plan.

    A composed action may be guarded so that only *some* selections are legal
    (for example a play that must match the discard top), so the host rotates
    through the cards in each input zone and returns every candidate. The count
    is bounded by the largest input zone (never the whole state), so this stays
    deterministic and finite.
    """
    descriptors = context.observation.get("actions")
    if not isinstance(descriptors, list):
        return None
    descriptor = next((item for item in descriptors
                       if isinstance(item, dict) and item.get("id") == action), None)
    if descriptor is None:
        return None
    widths = [1]
    for item in descriptor.get("inputs", []):
        if item.get("kind") == "integer_range":
            widths.append(int(item.get("maximum", 0)) - int(item.get("minimum", 0)) + 1)
        elif isinstance(item.get("options"), list):
            widths.append(len(item["options"]))
    limit = min(max(widths), INPUT_CANDIDATE_LIMIT)
    candidates: list[dict[str, Any]] = []
    for offset in range(limit):
        payload = visible_payload(context, action, newest=newest, offset=offset)
        if payload is None:
            break
        candidates.append(payload)
    return candidates


def first_legal(context: PolicyContext):
    """First legal action; the minimal deterministic policy."""
    for action in context.legal_actions:
        payload = visible_payload(context, action)
        if payload is not None:
            return action, payload
    return None


def random_legal(context: PolicyContext):
    """Deterministic legal-random: seeded by the interpreter, not global RNG.

    For a composed plan the randomness picks a rotation into each input zone as
    well as an action, so different seeds reach different cards while staying
    reproducible.
    """
    actions = context.legal_actions
    if not actions:
        return None
    tick = context.seed + context.step * 17 + context.player * 31
    for step in range(len(actions)):
        action = actions[(tick + step) % len(actions)]
        candidates = descriptor_candidates(context, action)
        if candidates is None:
            return action, visible_payload(context, action) or {}
        if not candidates:
            continue
        shift = tick % len(candidates)
        ordered = candidates[shift:] + candidates[:shift]
        return action, ordered[0]
    return None


def boundary_first(context: PolicyContext):
    """Prefer boundary actions (give up / pass / fold) before the happy path.

    For a composed plan a boundary action may still need a minimal legal payload,
    so the descriptor is filled with the smallest accepted selection rather than
    skipped.
    """
    actions = context.legal_actions
    if not actions:
        return None
    ordered = [action for action in _BOUNDARY_PRIORITY if action in actions]
    ordered += [action for action in actions if action not in ordered]
    for action in ordered:
        candidates = descriptor_candidates(context, action)
        if candidates is None:
            return action, visible_payload(context, action) or {}
        if candidates:
            return action, candidates[0]
    return None


def card_first(context: PolicyContext):
    """Generic matching-game policy: play a legal card, otherwise draw.

    Used as the playtest policy and as the host bot for non-human seats. It is
    seat-agnostic because the plan exposes ``legal_card_indices`` for whichever
    seat is active.
    """
    actions = context.legal_actions
    if not actions:
        return None
    if "play" in actions:
        indices = context.observation.get("legal_card_indices") or [0]
        return ("play", {"card_index": indices[0], "declared_suit": "S"})
    return (actions[0], {})


def resilient_first(context: PolicyContext):
    """Generic gate policy: the first legal action the host actually accepts.

    Composed games have no game-specific policy, so the gate probes each legal
    action on a throwaway copy and uses the first that does not raise. It is
    deterministic given the state, which keeps replay byte-exact. It proves
    termination and wait coverage, not that the happy path is reachable.
    """
    actions = context.legal_actions
    if not actions:
        return None
    for action in actions:
        payload = visible_payload(context, action)
        if payload is not None:
            return action, payload
    raise ToolError("no_visible_legal_action_payload")


def goal_first(context: PolicyContext):
    """Target-branch policy: pick from the other end so outcomes vary.

    The default bot takes the first card/action, which can leave symmetric
    resolve branches (left/right/tie, or a larger card beating a smaller one)
    permanently uncovered. For a plan with action descriptors this selects the
    newest card in each input zone; for a plan without them it probes forward.
    Deterministic given the state, so replay stays byte-exact.
    """
    for action in context.legal_actions:
        candidates = descriptor_candidates(context, action, newest=True)
        if candidates is None:
            continue
        if candidates:
            return action, candidates[0]
    return resilient_first(context)


def _play_one(plan: GamePlan, registry: ToolRegistry, seed: int, strategy: Strategy,
              invariants: Sequence[Invariant], max_steps: int) -> list[dict[str, Any]]:
    interpreter = Interpreter(plan, registry, seed=seed)
    interpreter.setup()
    for _ in range(max_steps):
        if interpreter.state.get("finished"):
            break
        for invariant in invariants:
            invariant(interpreter)
        context = policy_context(interpreter, _)
        choice = strategy(context)
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
