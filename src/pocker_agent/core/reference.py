"""Host-compiled reference games.

A reference game is a plan the host owns, plus the policy used to gate it. It
may only be offered as playable after ``playtest`` passes (see
``ensure_playtested``), which is what keeps "unverified" plans out of the app.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

from .interpreter import Interpreter
from .plan import GamePlan
from .plans import arithmetic_plan, crazy_eights_plan, war_plan
from .playtest import PlaytestReport, card_first, first_legal, playtest
from .registry import core_registry

RANK_VALUES = {"A": 1, **{str(n): n for n in range(2, 11)}, "J": 11, "Q": 12, "K": 13}
DECK_RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
DECK_SUITS = ("S", "H", "D", "C")


def _solve_or_claim_none(interpreter: Interpreter):
    """Deterministic policy for the arithmetic drill: answer correctly."""
    actions = interpreter.legal_actions()
    if "submit_expression" not in actions:
        return (actions[0], {}) if actions else None
    answer = interpreter.tools["exact_expression"].solve(interpreter.state["numbers"])
    if answer is None:
        return ("no_solution", {})
    return ("submit_expression", {"expression": answer})


@dataclass(frozen=True)
class ReferenceGame:
    id: str
    title: str
    kind: str
    build: Callable[[], GamePlan]
    strategy: Callable[[Interpreter], tuple[str, dict] | None]


def _arithmetic24() -> GamePlan:
    return arithmetic_plan(target=24, max_rounds=3, card_count=4, deal_mode="solvable",
                           rank_values=RANK_VALUES, deck_ranks=DECK_RANKS, deck_suits=DECK_SUITS)


def _war() -> GamePlan:
    return war_plan(max_rounds=3)


def _crazy_eights() -> GamePlan:
    return crazy_eights_plan(hand_size=5, wild_rank="8")


REFERENCE_GAMES: dict[str, ReferenceGame] = {
    "arithmetic24": ReferenceGame("arithmetic24", "24点 / 四则算式练习", "arithmetic",
                                  _arithmetic24, _solve_or_claim_none),
    "war": ReferenceGame("war", "War 比大小", "war", _war, first_legal),
    "crazy_eights": ReferenceGame("crazy_eights", "疯狂八（隐藏手牌）", "shedding",
                                  _crazy_eights, card_first),
}


def list_reference_games() -> list[dict]:
    return [{"id": game.id, "title": game.title, "kind": game.kind,
             "playtest": ensure_playtested(game.id).as_dict()}
            for game in REFERENCE_GAMES.values()]


def build_plan(game_id: str) -> GamePlan:
    if game_id not in REFERENCE_GAMES:
        raise ValueError(f"unknown_game:{game_id}")
    return REFERENCE_GAMES[game_id].build()


@lru_cache(maxsize=16)
def ensure_playtested(game_id: str) -> PlaytestReport:
    """Gate: a reference game must pass a full playtest before it is playable."""
    game = REFERENCE_GAMES.get(game_id)
    if game is None:
        raise ValueError(f"unknown_game:{game_id}")
    report = playtest(game.build(), core_registry(), game.strategy, seeds=(0, 7, 23))
    return report
