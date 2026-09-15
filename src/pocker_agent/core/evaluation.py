"""Policy-isolated simulation of a compiled game plan.

The public concepts follow OpenSpiel's game-state boundary and PettingZoo's
agent-environment cycle: the environment owns hidden state and transitions;
each acting policy receives only its player observation and currently exposed
legal actions. Formal publish verification remains a separate host gate.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from statistics import fmean, median
from typing import Any, Literal

from .contracts import ToolError, ToolRegistry
from .decision import Policy, PolicyContext, PolicyDecision, visible_payload
from .interpreter import Interpreter
from .plan import GamePlan, plan_fingerprint
from .playtest import Invariant


@dataclass(frozen=True)
class PolicyProfile:
    """One policy per seat, equivalent to a strategy profile in game theory."""

    name: str
    policies: tuple[Policy, ...]


def shared_policy_profile(name: str, policy: Policy, players: int) -> PolicyProfile:
    if players < 1:
        raise ToolError("simulation_requires_players")
    return PolicyProfile(name=name, policies=tuple(policy for _ in range(players)))


def seat_rotations(name: str, policies: Sequence[Policy]) -> tuple[PolicyProfile, ...]:
    """Rotate a policy lineup through every seat for paired comparisons."""
    lineup = tuple(policies)
    if not lineup or any(not callable(policy) for policy in lineup):
        raise ToolError("simulation_requires_policies")
    return tuple(
        PolicyProfile(
            name=f"{name}:rotation-{offset}",
            policies=lineup[offset:] + lineup[:offset],
        )
        for offset in range(len(lineup))
    )


def first_observed_legal(context: PolicyContext) -> PolicyDecision | None:
    for action in context.legal_actions:
        payload = visible_payload(context, action)
        if payload is not None:
            return action, payload
    return None


def boundary_observed_first(context: PolicyContext) -> PolicyDecision | None:
    priorities = ("pass", "give_up", "no_solution", "fold", "check", "next_round")
    ordered = [action for action in priorities if action in context.legal_actions]
    ordered += [action for action in context.legal_actions if action not in ordered]
    for action in ordered:
        payload = visible_payload(context, action)
        if payload is not None:
            return action, payload
    return None


def seeded_observed_legal(context: PolicyContext) -> PolicyDecision | None:
    actions = context.legal_actions
    if not actions:
        return None
    start = (context.seed + context.step * 17 + context.player * 31) % len(actions)
    for offset in range(len(actions)):
        action = actions[(start + offset) % len(actions)]
        payload = visible_payload(context, action, newest=bool((start + offset) % 2))
        if payload is not None:
            return action, payload
    return None


DEFAULT_EVALUATION_POLICIES: Mapping[str, Policy] = {
    "seeded_observed_legal": seeded_observed_legal,
    "boundary_observed_first": boundary_observed_first,
    "first_observed_legal": first_observed_legal,
}


@dataclass(frozen=True)
class Distribution:
    count: int
    minimum: float | None
    maximum: float | None
    mean: float | None
    median: float | None

    @classmethod
    def of(cls, values: Sequence[int | float]) -> Distribution:
        if not values:
            return cls(0, None, None, None, None)
        numbers = [float(value) for value in values]
        return cls(len(numbers), min(numbers), max(numbers), fmean(numbers), median(numbers))

    def as_dict(self) -> dict[str, int | float | None]:
        return {
            "count": self.count,
            "min": self.minimum,
            "max": self.maximum,
            "mean": self.mean,
            "median": self.median,
        }


ResultKind = Literal[
    "declared_winner", "declared_draw", "score_only", "no_result", "failed"
]


@dataclass(frozen=True)
class SimulationRun:
    seed: int
    profile: str
    starting_player: int | None
    finished: bool
    result_kind: ResultKind
    winners: tuple[int, ...]
    actions: int
    events: int
    rounds: int | None
    scores: tuple[float, ...]
    actors: tuple[int, ...]
    inputs: tuple[Mapping[str, Any], ...]
    error: str | None = None

    @property
    def completed(self) -> bool:
        return self.finished and self.error is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "profile": self.profile,
            "starting_player": self.starting_player,
            "finished": self.finished,
            "result_kind": self.result_kind,
            "winners": [f"player-{seat + 1}" for seat in self.winners],
            "actions": self.actions,
            "events": self.events,
            "rounds": self.rounds,
            "scores": list(self.scores),
            "actors": [f"player-{seat + 1}" for seat in self.actors],
            "inputs": [dict(item) for item in self.inputs],
            "error": self.error,
        }


@dataclass(frozen=True)
class SimulationEvaluation:
    plan_hash: str
    seeds: tuple[int, ...]
    profiles: tuple[str, ...]
    runs: tuple[SimulationRun, ...]
    completion_rate: float
    declared_draw_rate: float | None
    score_only_rate: float | None
    no_result_rate: float | None
    seat_win_shares: tuple[float, ...]
    seat_parity_score: float | None
    starter_advantage: float | None
    mean_scores: tuple[float | None, ...]
    action_counts: Distribution
    event_counts: Distribution
    round_counts: Distribution
    profile_completion_rates: Mapping[str, float]
    failure_counts: Mapping[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_hash": self.plan_hash,
            "seeds": list(self.seeds),
            "profiles": list(self.profiles),
            "policy_dependent": True,
            "run_count": len(self.runs),
            "completed_runs": sum(run.completed for run in self.runs),
            "completion_rate": self.completion_rate,
            "declared_draw_rate": self.declared_draw_rate,
            "score_only_rate": self.score_only_rate,
            "no_result_rate": self.no_result_rate,
            "seat_win_shares": {
                f"player-{seat + 1}": share
                for seat, share in enumerate(self.seat_win_shares)
            },
            "seat_parity_score": self.seat_parity_score,
            "starter_advantage": self.starter_advantage,
            "mean_scores": {
                f"player-{seat + 1}": score for seat, score in enumerate(self.mean_scores)
            },
            "action_counts": self.action_counts.as_dict(),
            "event_counts": self.event_counts.as_dict(),
            "round_counts": self.round_counts.as_dict(),
            "profile_completion_rates": dict(self.profile_completion_rates),
            "failure_counts": dict(self.failure_counts),
            "runs": [run.as_dict() for run in self.runs],
        }


def _profiles(
    plan: GamePlan,
    profiles: Mapping[str, Policy | Sequence[Policy]] | Sequence[PolicyProfile] | None,
) -> tuple[PolicyProfile, ...]:
    if profiles is None:
        result = tuple(
            shared_policy_profile(name, policy, plan.players)
            for name, policy in DEFAULT_EVALUATION_POLICIES.items()
        )
    elif isinstance(profiles, Mapping):
        normalized: list[PolicyProfile] = []
        for name, value in profiles.items():
            if callable(value):
                normalized.append(shared_policy_profile(str(name), value, plan.players))
            else:
                normalized.append(PolicyProfile(str(name), tuple(value)))
        result = tuple(normalized)
    else:
        result = tuple(profiles)
    if not result:
        raise ToolError("simulation_requires_profiles")
    names = [profile.name for profile in result]
    if len(names) != len(set(names)):
        raise ToolError("simulation_profile_names_must_be_unique")
    for profile in result:
        if len(profile.policies) != plan.players:
            raise ToolError(f"simulation_profile_seat_count:{profile.name}")
        if any(not callable(policy) for policy in profile.policies):
            raise ToolError(f"simulation_profile_policy_invalid:{profile.name}")
    return result


def _seat(value: Any, players: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        seat = int(value)
    except (TypeError, ValueError):
        return None
    return seat if 0 <= seat < players else None


def _winners(state: Mapping[str, Any], players: int) -> tuple[int, ...]:
    raw = state.get("winners")
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ToolError("simulation_winners_must_be_a_list")
    result: list[int] = []
    for value in raw:
        seat = _seat(value, players)
        if seat is None:
            raise ToolError(f"simulation_winner_out_of_range:{value}")
        if seat not in result:
            result.append(seat)
    return tuple(result)


def _scores(state: Mapping[str, Any], players: int) -> tuple[float, ...]:
    raw = state.get("scores")
    if raw is None:
        return ()
    if isinstance(raw, Mapping):
        values = [raw.get(index, raw.get(str(index), 0)) for index in range(players)]
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        values = list(raw[:players]) + [0] * max(0, players - len(raw))
    else:
        raise ToolError("simulation_scores_must_be_a_list_or_map")
    try:
        return tuple(float(value) for value in values)
    except (TypeError, ValueError) as error:
        raise ToolError("simulation_scores_must_be_numeric") from error


def _round(state: Mapping[str, Any]) -> int | None:
    try:
        return int(state["round"])
    except (KeyError, TypeError, ValueError):
        return None


def _result_kind(
    state: Mapping[str, Any], winners: tuple[int, ...], scores: tuple[float, ...]
) -> ResultKind:
    if state.get("draw") is True:
        return "declared_draw"
    if winners:
        return "declared_winner"
    if scores:
        return "score_only"
    return "no_result"


def _run_once(
    plan: GamePlan,
    registry: ToolRegistry,
    seed: int,
    profile: PolicyProfile,
    invariants: Sequence[Invariant],
    max_steps: int,
) -> SimulationRun:
    interpreter = Interpreter(plan, registry, seed=seed)
    actors: list[int] = []
    inputs: list[Mapping[str, Any]] = []
    starting_player: int | None = None
    error: str | None = None
    winners: tuple[int, ...] = ()
    scores: tuple[float, ...] = ()
    result_kind: ResultKind = "failed"
    try:
        interpreter.setup()
        starting_player = _seat(interpreter.state.get("current_player"), plan.players)
        for step in range(max_steps):
            if interpreter.state.get("finished"):
                break
            for invariant in invariants:
                invariant(interpreter)
            actor = _seat(interpreter.state.get("current_player"), plan.players)
            if actor is None:
                raise ToolError("simulation_current_player_invalid")
            observation = deepcopy(interpreter.view(f"player-{actor + 1}"))
            context = PolicyContext(
                seed, step, actor, observation, tuple(interpreter.legal_actions())
            )
            choice = profile.policies[actor](context)
            if choice is None:
                raise ToolError("policy_returned_none")
            action, payload = choice
            interpreter.step(action, **(payload or {}))
            actors.append(actor)
            inputs.append(deepcopy(interpreter.state["input"]))
        else:
            raise ToolError("simulation_step_limit")
        if not interpreter.state.get("finished"):
            raise ToolError("simulation_not_finished")
        for invariant in invariants:
            invariant(interpreter)
        winners = _winners(interpreter.state, plan.players)
        scores = _scores(interpreter.state, plan.players)
        result_kind = _result_kind(interpreter.state, winners, scores)
    except (ToolError, ValueError) as failure:
        error = str(failure)
        try:
            scores = _scores(interpreter.state, plan.players)
        except ToolError:
            scores = ()
    return SimulationRun(
        seed=seed,
        profile=profile.name,
        starting_player=starting_player,
        finished=bool(interpreter.state.get("finished")),
        result_kind=result_kind,
        winners=winners,
        actions=len(inputs),
        events=len(interpreter.events),
        rounds=_round(interpreter.state),
        scores=scores,
        actors=tuple(actors),
        inputs=tuple(inputs),
        error=error,
    )


def _failure_code(error: str) -> str:
    return error.split(":", 1)[0]


def evaluate_simulations(
    plan: GamePlan,
    registry: ToolRegistry,
    profiles: Mapping[str, Policy | Sequence[Policy]] | Sequence[PolicyProfile] | None = None,
    *,
    seeds: Sequence[int] = (0, 1, 7, 23, 42),
    invariants: Sequence[Invariant] = (),
    max_steps: int = 2048,
) -> SimulationEvaluation:
    """Run a seed-by-profile matrix and summarize empirical game behaviour."""
    profile_items = _profiles(plan, profiles)
    normalized_seeds = tuple(int(seed) for seed in seeds)
    if not normalized_seeds:
        raise ToolError("simulation_requires_seeds")
    if len(normalized_seeds) != len(set(normalized_seeds)):
        raise ToolError("simulation_seeds_must_be_unique")
    if max_steps < 1:
        raise ToolError("simulation_max_steps_must_be_positive")

    runs = tuple(
        _run_once(plan, registry, seed, profile, invariants, max_steps)
        for seed in normalized_seeds
        for profile in profile_items
    )
    completed = tuple(run for run in runs if run.completed)
    completion_rate = len(completed) / len(runs)
    declared_draw_rate = (
        sum(run.result_kind == "declared_draw" for run in completed) / len(completed)
        if completed
        else None
    )
    score_only_rate = (
        sum(run.result_kind == "score_only" for run in completed) / len(completed)
        if completed
        else None
    )
    no_result_rate = (
        sum(run.result_kind == "no_result" for run in completed) / len(completed)
        if completed
        else None
    )

    winner_runs = tuple(run for run in completed if run.result_kind == "declared_winner")
    credits = [0.0] * plan.players
    for run in winner_runs:
        share = 1.0 / len(run.winners)
        for seat in run.winners:
            credits[seat] += share
    seat_win_shares = tuple(
        credit / len(winner_runs) if winner_runs else 0.0 for credit in credits
    )
    seat_parity_score = (
        1.0 - (max(seat_win_shares) - min(seat_win_shares)) if winner_runs else None
    )

    starter_runs = tuple(
        run for run in winner_runs if run.starting_player is not None and plan.players > 1
    )
    if starter_runs:
        starter_credit = 0.0
        nonstarter_credit = 0.0
        for run in starter_runs:
            share = 1.0 / len(run.winners)
            for seat in run.winners:
                if seat == run.starting_player:
                    starter_credit += share
                else:
                    nonstarter_credit += share
        starter_rate = starter_credit / len(starter_runs)
        nonstarter_rate = nonstarter_credit / (len(starter_runs) * (plan.players - 1))
        starter_advantage = starter_rate - nonstarter_rate
    else:
        starter_advantage = None

    mean_scores = tuple(
        fmean(run.scores[seat] for run in completed if len(run.scores) > seat)
        if any(len(run.scores) > seat for run in completed)
        else None
        for seat in range(plan.players)
    )
    profile_completion_rates = {
        profile.name: sum(run.completed for run in runs if run.profile == profile.name)
        / len(normalized_seeds)
        for profile in profile_items
    }
    failure_counts = Counter(
        _failure_code(run.error) for run in runs if run.error is not None
    )
    return SimulationEvaluation(
        plan_hash=plan_fingerprint(plan),
        seeds=normalized_seeds,
        profiles=tuple(profile.name for profile in profile_items),
        runs=runs,
        completion_rate=completion_rate,
        declared_draw_rate=declared_draw_rate,
        score_only_rate=score_only_rate,
        no_result_rate=no_result_rate,
        seat_win_shares=seat_win_shares,
        seat_parity_score=seat_parity_score,
        starter_advantage=starter_advantage,
        mean_scores=mean_scores,
        action_counts=Distribution.of([run.actions for run in completed]),
        event_counts=Distribution.of([run.events for run in completed]),
        round_counts=Distribution.of(
            [run.rounds for run in completed if run.rounds is not None]
        ),
        profile_completion_rates=profile_completion_rates,
        failure_counts=dict(failure_counts),
    )


__all__ = [
    "DEFAULT_EVALUATION_POLICIES",
    "Distribution",
    "Policy",
    "PolicyContext",
    "PolicyDecision",
    "PolicyProfile",
    "SimulationEvaluation",
    "SimulationRun",
    "boundary_observed_first",
    "evaluate_simulations",
    "first_observed_legal",
    "seat_rotations",
    "seeded_observed_legal",
    "shared_policy_profile",
]
