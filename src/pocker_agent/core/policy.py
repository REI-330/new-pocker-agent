"""Host-owned policies for seats the human does not control.

These are declared and deterministic, not improvised: the plan exposes
``legal_card_indices`` for the active seat, and the policy picks from it. The
host never invents an action that the plan did not offer.
"""
from __future__ import annotations

from .contracts import ToolError
from .decision import PolicyContext, policy_context, visible_payload
from .interpreter import Interpreter
from .playtest import card_first, resilient_first
from .tools import ExactExpressionTool

HUMAN_INDEX = 0
BOT_STEP_LIMIT = 500
_BETTING_ACTIONS = {"check", "call", "raise", "all_in"}
_CARD_ACTIONS = {"play", "draw"}


def bet_first(context: PolicyContext):
    """Conservative betting policy: never fold when checking or calling is legal."""
    actions = context.legal_actions
    if not actions:
        return None
    for action in ("check", "call", "fold", "all_in"):
        if action in actions:
            return (action, {})
    return (actions[0], {})


def composed_action(context: PolicyContext, newest: bool = False):
    """Generic policy for a composed plan: satisfy each declared input.

    The plan carries :class:`~pocker_agent.core.plan.ActionDescriptor` data, so
    the host no longer has to guess an action's payload from its name. Each
    candidate payload is built from the live state and probed on a throwaway
    copy, so a descriptor the state cannot satisfy (for example an empty zone, or
    a play that must match the discard top) is treated as "this action is not
    usable", not as a crash. It is deterministic given the state, which keeps
    replay byte-exact. ``newest`` selects from the other end of each zone, which
    is how the goal-branch policy reaches the outcomes the default policy never
    does.
    """
    for action in context.legal_actions:
        payload = visible_payload(context, action, newest=newest)
        if payload is not None:
            return action, payload
    return None


def bot_action(context: PolicyContext):
    """The single policy used by the host for non-human seats.

    The plan decides the shape of the turn; the host only picks from what the
    plan actually offers. A composed plan advertises typed action descriptors,
    so it is driven through those; otherwise three shapes are covered: a betting
    round, a card turn, and anything else (probe the actions, take the first the
    host accepts) so a new family is never silently unplayable.
    """
    actions = context.legal_actions
    if not actions:
        return None
    if "submit_expression" in actions:
        numbers = context.observation.get("numbers")
        target = context.observation.get("target")
        if isinstance(numbers, list) and type(target) is int:
            answer = ExactExpressionTool(target=target).solve(numbers)
            return (("submit_expression", {"expression": answer}) if answer is not None
                    else ("no_solution", {}))
    if context.observation.get("actions"):
        return composed_action(context)
    if _BETTING_ACTIONS.intersection(actions):
        return bet_first(context)
    if _CARD_ACTIONS.intersection(actions):
        return card_first(context)
    return resilient_first(context)


def run_bots(interpreter: Interpreter, human_index: int = HUMAN_INDEX,
             limit: int = BOT_STEP_LIMIT) -> int:
    """Advance until it is the human's turn again or the game is over."""
    steps = 0
    while (not interpreter.state.get("finished")
           and int(interpreter.state.get("current_player", 0)) != human_index):
        context = policy_context(interpreter, steps)
        choice = bot_action(context)
        if choice is None:
            raise ToolError("bot_has_no_legal_action")
        action, payload = choice
        interpreter.step(action, **payload)
        steps += 1
        if steps > limit:
            raise ToolError("bot_step_limit")
    return steps
