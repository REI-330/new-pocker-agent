from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .executors import create_engine
from .game_rules import PlayableRule


@dataclass(frozen=True)
class SimulationResult:
    seed: int
    events: list[dict[str, Any]]
    winner: str | None
    completed: bool


def simulate(rules: PlayableRule, seed: int = 0, player_count: int | None = None, max_steps: int = 1000) -> SimulationResult:
    engine = create_engine(rules, seed=seed, player_count=player_count)
    events = engine.run(max_steps=max_steps)
    finish_events = [event for event in events if event["event"] == "game_finished"]
    return SimulationResult(
        seed=seed,
        events=events,
        winner=finish_events[-1].get("winner") if finish_events else None,
        completed=bool(finish_events),
    )
