"""Input-log replay: record actual action inputs and per-operation state.

The M3 gate must be able to replay a game from the *inputs it actually received*,
not merely re-run the same policy, and compare normalized events and state
separately. ``record_trace`` captures exactly that: each step's real
``state['input']`` payload, the event stream, and the state after every accepted
tool operation. ``replay_trace`` then re-drives the interpreter from the recorded
inputs, and ``compare_traces`` reports the first difference.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ..contracts import ToolError, ToolRegistry
from ..interpreter import Interpreter
from ..plan import GamePlan
from ..playtest import Strategy


@dataclass
class Observation:
    """State right after one accepted tool operation."""

    tool: str
    operation: str
    result_key: str | None
    args: dict[str, Any]
    state: dict[str, Any]


@dataclass
class GameTrace:
    seed: int
    inputs: tuple[dict[str, Any], ...] = ()
    events: tuple[dict[str, Any], ...] = ()
    observations: tuple[Observation, ...] = ()
    spans: tuple[tuple[int, int], ...] = ()
    finished: bool = False
    error: str | None = None


def _observer(store: list[Observation]):
    def observe(interpreter: Interpreter, action: Any, args: dict[str, Any]) -> None:
        store.append(Observation(tool=action.tool, operation=action.operation,
                                 result_key=action.result_key, args=deepcopy(args),
                                 state=deepcopy(interpreter.state)))
    return observe


def record_trace(plan: GamePlan, registry: ToolRegistry, strategy: Strategy, seed: int,
                 max_steps: int = 2048) -> GameTrace:
    """Run one game with a policy and record inputs, events and state."""
    interpreter = Interpreter(plan, registry, seed=seed)
    observations: list[Observation] = []
    interpreter.observer = _observer(observations)
    interpreter.setup()
    inputs: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []
    for _ in range(max_steps):
        if interpreter.state.get("finished"):
            break
        choice = strategy(interpreter)
        if choice is None:
            raise ToolError("strategy_returned_none")
        start = len(observations)
        action, payload = choice
        interpreter.step(action, **(payload or {}))
        inputs.append(deepcopy(interpreter.state["input"]))
        spans.append((start, len(observations)))
    else:
        raise ToolError("trace_step_limit")
    return GameTrace(seed=seed, inputs=tuple(inputs),
                     events=tuple(deepcopy(interpreter.events)),
                     observations=tuple(observations), spans=tuple(spans),
                     finished=bool(interpreter.state.get("finished")))


def replay_trace(plan: GamePlan, registry: ToolRegistry, trace: GameTrace) -> GameTrace:
    """Re-drive the plan from the recorded action inputs, not from a policy.

    A recorded input that no longer fits the replayed state is itself a
    divergence, so it is recorded on the trace instead of raised.
    """
    interpreter = Interpreter(plan, registry, seed=trace.seed)
    observations: list[Observation] = []
    interpreter.observer = _observer(observations)
    interpreter.setup()
    spans: list[tuple[int, int]] = []
    error: str | None = None
    for entry in trace.inputs:
        start = len(observations)
        payload = {key: value for key, value in entry.items() if key != "action"}
        try:
            interpreter.step(entry["action"], **payload)
        except ToolError as failure:
            error = str(failure)
            break
        spans.append((start, len(observations)))
    return GameTrace(seed=trace.seed, inputs=trace.inputs,
                     events=tuple(deepcopy(interpreter.events)),
                     observations=tuple(observations), spans=tuple(spans),
                     finished=bool(interpreter.state.get("finished")), error=error)


def compare_traces(recorded: GameTrace, replayed: GameTrace) -> list[str]:
    """Compare the event stream and the per-operation state separately."""
    problems: list[str] = []
    if replayed.error is not None:
        problems.append(f"replay_error:{replayed.error}")
    if list(recorded.events) != list(replayed.events):
        problems.append("events_diverge")
    if len(recorded.observations) != len(replayed.observations):
        return [*problems, f"observation_count:{len(recorded.observations)}"
                f"!={len(replayed.observations)}"]
    for index, (left, right) in enumerate(zip(recorded.observations, replayed.observations)):
        if left.tool != right.tool or left.operation != right.operation:
            problems.append(f"observation_{index}:operation_diverge")
            break
        if left.state != right.state:
            problems.append(f"observation_{index}:state_diverge")
            break
    return problems


def summarize(state: dict[str, Any]) -> dict[str, Any]:
    """A bounded, human-readable state view for failure reports."""
    zones = state.get("zones")
    summary: dict[str, Any] = {"round": state.get("round"),
                               "scores": list(state.get("scores", [])),
                               "finished": bool(state.get("finished")),
                               "winners": list(state.get("winners", []))}
    if isinstance(zones, dict):
        summary["zone_counts"] = {zone_id: len(entry.get("cards", []))
                                  for zone_id, entry in sorted(zones.items())}
    return summary


__all__ = ["GameTrace", "Observation", "compare_traces", "record_trace",
           "replay_trace", "summarize"]
