from __future__ import annotations

from copy import deepcopy

from pocker_agent.core import Interpreter, core_registry, playtest
from pocker_agent.core.decision import PolicyContext, policy_context
from pocker_agent.core.plans import five_card_poker_plan


def _started_poker(seed: int = 3) -> Interpreter:
    interpreter = Interpreter(five_card_poker_plan(), core_registry(), seed=seed)
    interpreter.setup()
    return interpreter


def test_a_playtest_policy_receives_no_interpreter_or_complete_state() -> None:
    seen: list[PolicyContext] = []

    def policy(context: PolicyContext):
        seen.append(context)
        assert not hasattr(context, "state")
        assert not hasattr(context, "tools")
        for action in ("check", "call", "fold", "all_in"):
            if action in context.legal_actions:
                return action, {}
        return None

    report = playtest(five_card_poker_plan(), core_registry(), policy, seeds=(0,))
    assert report.ok
    assert seen


def test_a_non_acting_view_has_no_decision_surface() -> None:
    interpreter = _started_poker()
    acting = interpreter.view("player-1")
    waiting = interpreter.view("player-2")
    assert acting["legal_actions"]
    assert waiting["legal_actions"] == []
    assert waiting["legal_card_indices"] == []
    assert "actions" not in waiting


def test_opponent_private_cards_do_not_enter_the_policy_context() -> None:
    first = _started_poker()
    second = _started_poker()
    second.state["hands"][1] = list(reversed(deepcopy(second.state["hands"][1])))
    left = policy_context(first)
    right = policy_context(second)
    assert left.observation == right.observation
    assert left.legal_actions == right.legal_actions
    assert left.observation["players"][1]["hand"] == []


def test_solution_is_hidden_until_the_plan_reveals_it() -> None:
    interpreter = _started_poker()
    interpreter.state["solution"] = "host-only"
    assert interpreter.view("player-1")["solution"] is None
    interpreter.state["reveal"] = True
    assert interpreter.view("player-1")["solution"] == "host-only"
