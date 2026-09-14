"""Simulation-driven diagnostics for a compiled game plan.

Formal verification answers whether a plan is safe enough to publish.  This
module answers a different question: what behaviour do repeated games exhibit
under declared policies and seeds?  The resulting seat parity and starter
advantage are observations about those policies, not proofs of game balance.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean, median
from typing import Any

from .contracts import ToolError, ToolRegistry
from .interpreter import Interpreter
from .plan import GamePlan, plan_fingerprint
from .playtest import Invariant, Strategy, boundary_first, goal_first, random_legal

DEFAULT_EVALUATION_STRATEGIES: Mapping[str, Strategy] = {
    "random_legal": random_legal,
    "boundary_first": boundary_first,
    "goal_first": goal_first,
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


@dataclass(frozen=True)
class SimulationRun:
    seed: int
    strategy: str
    starting_player: int | None
    finished: bool
    winners: tuple[int, ...]
    actions: int
    events: int
    rounds: int | None
    scores: tuple[float, ...]
    error: str | None = None

    @property
    def completed(self) -> bool:
        return self.finished and self.error is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "strategy": self.strategy,
            "starting_player": self.starting_player,
            "finished": self.finished,
            "winners": [f"player-{seat + 1}" for seat in self.winners],
            "actions": self.actions,
            "events": self.events,
            "rounds": self.rounds,
            "scores": list(self.scores),
            "error": self.error,
        }


@dataclass(frozen=True)
class SimulationEvaluation:
    plan_hash: str
    seeds: tuple[int, ...]
    strategies: tuple[str, ...]
    runs: tuple[SimulationRun, ...]
    completion_rate: float
    draw_rate: float | None
    seat_win_shares: tuple[float, ...]
    seat_parity_score: float | None
    starter_advantage: float | None
    mean_scores: tuple[float, ...]
    action_counts: Distribution
    event_counts: Distribution
    round_counts: Distribution
    strategy_completion_rates: Mapping[str, float]
    failure_counts: Mapping[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_hash": self.plan_hash,
            "seeds": list(self.seeds),
            "strategies": list(self.strategies),
            "policy_dependent": True,
            "run_count": len(self.runs),
            "completed_runs": sum(run.completed for run in self.runs),
            "completion_rate": self.completion_rate,
            "draw_rate": self.draw_rate,
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
            "strategy_completion_rates": dict(self.strategy_completion_rates),
            "failure_counts": dict(self.failure_counts),
            "runs": [run.as_dict() for run in self.runs],
        }


def _strategy_items(
    strategies: Mapping[str, Strategy] | Sequence[Strategy] | Strategy | None,
) -> tuple[tuple[str, Strategy], ...]:
    if strategies is None:
        return tuple(DEFAULT_EVALUATION_STRATEGIES.items())
    if callable(strategies):
        return ((getattr(strategies, "__name__", "strategy"), strategies),)
    if isinstance(strategies, Mapping):
        items = tuple((str(name), strategy) for name, strategy in strategies.items())
    else:
        counts: Counter[str] = Counter()
        named: list[tuple[str, Strategy]] = []
        for strategy in strategies:
            base = getattr(strategy, "__name__", "strategy")
            counts[base] += 1
            suffix = f"_{counts[base]}" if counts[base] > 1 else ""
            named.append((f"{base}{suffix}", strategy))
        items = tuple(named)
    if not items or any(not callable(strategy) for _, strategy in items):
        raise ToolError("simulation_requires_strategies")
    if len({name for name, _ in items}) != len(items):
        raise ToolError("simulation_strategy_names_must_be_unique")
    return items


def _seat(value: Any, players: int) -> int | None:
    try:
        seat = int(value)
    except (TypeError, ValueError):
        return None
    return seat if 0 <= seat < players else None


def _winners(state: Mapping[str, Any], players: int) -> tuple[int, ...]:
    result: list[int] = []
    for value in state.get("winners") or ():
        seat = _seat(value, players)
        if seat is None:
            raise ToolError(f"simulation_winner_out_of_range:{value}")
        if seat not in result:
            result.append(seat)
    return tuple(result)


def _scores(state: Mapping[str, Any], players: int) -> tuple[float, ...]:
    raw = state.get("scores")
    if isinstance(raw, Mapping):
        values = [raw.get(index, raw.get(str(index), 0)) for index in range(players)]
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        values = list(raw[:players]) + [0] * max(0, players - len(raw))
    else:
        values = [0] * players
    try:
        return tuple(float(value) for value in values)
    except (TypeError, ValueError) as error:
        raise ToolError("simulation_scores_must_be_numeric") from error


def _round(state: Mapping[str, Any]) -> int | None:
    try:
        return int(state["round"])
    except (KeyError, TypeError, ValueError):
        return None


def _run_once(
    plan: GamePlan,
    registry: ToolRegistry,
    seed: int,
    strategy_name: str,
    strategy: Strategy,
    invariants: Sequence[Invariant],
    max_steps: int,
) -> SimulationRun:
    interpreter = Interpreter(plan, registry, seed=seed)
    actions = 0
    starting_player: int | None = None
    error: str | None = None
    try:
        interpreter.setup()
        starting_player = _seat(interpreter.state.get("current_player"), plan.players)
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
            actions += 1
        else:
            raise ToolError("simulation_step_limit")
        if not interpreter.state.get("finished"):
            raise ToolError("simulation_not_finished")
        for invariant in invariants:
            invariant(interpreter)
        winners = _winners(interpreter.state, plan.players)
        scores = _scores(interpreter.state, plan.players)
    except (ToolError, ValueError) as failure:
        error = str(failure)
        winners = ()
        try:
            scores = _scores(interpreter.state, plan.players)
        except ToolError:
            scores = tuple(0.0 for _ in range(plan.players))
    return SimulationRun(
        seed=seed,
        strategy=strategy_name,
        starting_player=starting_player,
        finished=bool(interpreter.state.get("finished")),
        winners=winners,
        actions=actions,
        events=len(interpreter.events),
        rounds=_round(interpreter.state),
        scores=scores,
        error=error,
    )


def _failure_code(error: str) -> str:
    return error.split(":", 1)[0]


def evaluate_simulations(
    plan: GamePlan,
    registry: ToolRegistry,
    strategies: Mapping[str, Strategy] | Sequence[Strategy] | Strategy | None = None,
    *,
    seeds: Sequence[int] = (0, 1, 7, 23, 42),
    invariants: Sequence[Invariant] = (),
    max_steps: int = 2048,
) -> SimulationEvaluation:
    """Run a seed-by-policy matrix and summarize observed game behaviour.

    ``seat_parity_score`` is ``1 - (max win share - min win share)`` over runs
    that named at least one winner.  Tied winners split one unit of credit.  A
    high score can therefore coexist with a high draw rate; callers must report
    both.  All outcome metrics are policy-dependent diagnostics and do not
    replace :func:`verify_plan` or prove balance for human players.
    """
    strategy_items = _strategy_items(strategies)
    normalized_seeds = tuple(int(seed) for seed in seeds)
    if not normalized_seeds:
        raise ToolError("simulation_requires_seeds")
    if max_steps < 1:
        raise ToolError("simulation_max_steps_must_be_positive")

    runs = tuple(
        _run_once(plan, registry, seed, name, strategy, invariants, max_steps)
        for seed in normalized_seeds
        for name, strategy in strategy_items
    )
    completed = tuple(run for run in runs if run.completed)
    completion_rate = len(completed) / len(runs)
    draws = tuple(run for run in completed if not run.winners)
    draw_rate = len(draws) / len(completed) if completed else None

    winner_runs = tuple(run for run in completed if run.winners)
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
        fmean(run.scores[seat] for run in completed) if completed else 0.0
        for seat in range(plan.players)
    )
    strategy_completion_rates = {
        name: sum(run.completed for run in runs if run.strategy == name) / len(normalized_seeds)
        for name, _ in strategy_items
    }
    failure_counts = Counter(
        _failure_code(run.error) for run in runs if run.error is not None
    )
    return SimulationEvaluation(
        plan_hash=plan_fingerprint(plan),
        seeds=normalized_seeds,
        strategies=tuple(name for name, _ in strategy_items),
        runs=runs,
        completion_rate=completion_rate,
        draw_rate=draw_rate,
        seat_win_shares=seat_win_shares,
        seat_parity_score=seat_parity_score,
        starter_advantage=starter_advantage,
        mean_scores=mean_scores,
        action_counts=Distribution.of([run.actions for run in completed]),
        event_counts=Distribution.of([run.events for run in completed]),
        round_counts=Distribution.of(
            [run.rounds for run in completed if run.rounds is not None]
        ),
        strategy_completion_rates=strategy_completion_rates,
        failure_counts=dict(failure_counts),
    )


__all__ = [
    "DEFAULT_EVALUATION_STRATEGIES",
    "Distribution",
    "SimulationEvaluation",
    "SimulationRun",
    "evaluate_simulations",
]
