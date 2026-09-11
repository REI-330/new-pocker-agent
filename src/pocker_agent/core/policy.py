"""Host-owned policies for seats the human does not control.

These are declared and deterministic, not improvised: the plan exposes
``legal_card_indices`` for the active seat, and the policy picks from it. The
host never invents an action that the plan did not offer.
"""
from __future__ import annotations

from .contracts import ToolError
from .interpreter import Interpreter
from .playtest import card_first, resilient_first

HUMAN_INDEX = 0
BOT_STEP_LIMIT = 500
_BETTING_ACTIONS = {"check", "call", "raise", "all_in"}
_CARD_ACTIONS = {"play", "draw"}


def bet_first(interpreter: Interpreter):
    """Conservative betting policy: never fold when checking or calling is legal."""
    actions = interpreter.legal_actions()
    if not actions:
        return None
    for action in ("check", "call", "fold", "all_in"):
        if action in actions:
            return (action, {})
    return (actions[0], {})


def bot_action(interpreter: Interpreter):
    """The single policy used by the host for non-human seats.

    The plan decides the shape of the turn; the host only picks from what the
    plan actually offers. Three shapes are covered: a betting round, a card
    turn, and anything else (probe the actions, take the first the host
    accepts) so a new family is never silently unplayable.
    """
    actions = interpreter.legal_actions()
    if not actions:
        return None
    if _BETTING_ACTIONS.intersection(actions):
        return bet_first(interpreter)
    if _CARD_ACTIONS.intersection(actions):
        return card_first(interpreter)
    return resilient_first(interpreter)


def run_bots(interpreter: Interpreter, human_index: int = HUMAN_INDEX,
             limit: int = BOT_STEP_LIMIT) -> int:
    """Advance until it is the human's turn again or the game is over."""
    steps = 0
    while (not interpreter.state.get("finished")
           and int(interpreter.state.get("current_player", 0)) != human_index):
        action, payload = bot_action(interpreter)
        if action is None:
            raise ToolError("bot_has_no_legal_action")
        interpreter.step(action, **payload)
        steps += 1
        if steps > limit:
            raise ToolError("bot_step_limit")
    return steps
