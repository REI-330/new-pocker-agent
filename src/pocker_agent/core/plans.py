"""Host-authored reference plans.

Built-in games live here as data so there is exactly one executable
representation. The agent loop may later *compose* plans from the same tool
contracts; these reference plans are also the golden traces that playtest
compares against.
"""
from __future__ import annotations

from .plan import GamePlan


def arithmetic_plan(*, target: int = 24, max_rounds: int = 3, card_count: int = 4,
                    operations=("+", "-", "*", "/"), fractional: bool = True,
                    rank_values: dict[str, int] | None = None,
                    deck_ranks=("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"),
                    deck_suits=("S", "H", "D", "C"), deal_mode: str = "solvable") -> GamePlan:
    """Solo arithmetic drill.

    Contract this plan actually implements (and playtest enforces):
    - ``max_rounds`` independent puzzles, each freshly dealt;
    - a correct answer *or* a correctly verified no-solution claim scores 1;
    - a wrong answer is rejected transactionally and may be retried;
    - giving up scores 0 and never produces a winner;
    - the drill has no winner, only a score.
    """
    rank_values = dict(rank_values or {})
    solver_config = {"target": target, "operations": list(operations),
                     "fractional": fractional, "rank_values": rank_values}
    deal_config = {"ranks": list(deck_ranks), "suits": list(deck_suits), **solver_config,
                   "deal_mode": deal_mode}
    tools = [
        {"name": "state"},
        {"name": "logic"},
        {"name": "score_settle"},
        {"name": "exact_expression", "config": solver_config},
        {"name": "solvable_deal", "config": deal_config},
    ]
    nodes = {
        "deal": {"kind": "call", "next": "init", "action": {
            "tool": "solvable_deal", "operation": "deal",
            "args": {"seed": "$state.seed", "cards_each": card_count}, "result_key": "deal"}},
        "init": {"kind": "call", "next": "wait", "action": {
            "tool": "state", "operation": "update", "args": {"state": "$state", "values": {
                "table": "$state.deal.hand", "numbers": "$state.deal.numbers",
                "phase": "solve", "reveal": False, "gave_up": False, "solution": None}}}},
        "wait": {"kind": "wait", "inputs": {
            "submit_expression": "submit", "no_solution": "no_solution", "give_up": "give_up"}},
        "submit": {"kind": "call", "next": "score", "action": {
            "tool": "exact_expression", "operation": "validate",
            "args": {"expression": "$state.input.expression", "numbers": "$state.table"},
            "result_key": "validated"}},
        "no_solution": {"kind": "call", "next": "score", "action": {
            "tool": "exact_expression", "operation": "assert_unsolvable",
            "args": {"numbers": "$state.table"}, "result_key": "unsolvable"}},
        "score": {"kind": "call", "next": "reveal", "action": {
            "tool": "score_settle", "operation": "call",
            "args": {"scores": "$state.scores", "winners": [0], "points": 1},
            "result_key": "scores"}},
        "give_up": {"kind": "call", "next": "reveal", "action": {
            "tool": "state", "operation": "update",
            "args": {"state": "$state", "values": {"gave_up": True}}}},
        "reveal": {"kind": "call", "next": "apply_reveal", "action": {
            "tool": "exact_expression", "operation": "solve",
            "args": {"numbers": "$state.table"}, "result_key": "solution"}},
        "apply_reveal": {"kind": "call", "next": "round_check", "action": {
            "tool": "state", "operation": "update",
            "args": {"state": "$state", "values": {"reveal": True, "phase": "reveal"}}}},
        "round_check": {"kind": "call", "next": "round_branch", "action": {
            "tool": "logic", "operation": "evaluate",
            "args": {"expression": {"ge": ["$state.round", "$state.max_rounds"]}},
            "result_key": "at_end"}},
        "round_branch": {"kind": "branch", "value": "$state.at_end",
                         "cases": [{"value": True, "target": "finish"}], "next": "bump"},
        "bump": {"kind": "call", "next": "apply_round", "action": {
            "tool": "logic", "operation": "evaluate",
            "args": {"expression": {"add": ["$state.round", 1]}}, "result_key": "next_round"}},
        "apply_round": {"kind": "call", "next": "deal", "action": {
            "tool": "state", "operation": "update",
            "args": {"state": "$state", "values": {
                "round": "$state.next_round", "phase": "solve",
                "reveal": False, "gave_up": False, "solution": None}}}},
        "finish": {"kind": "call", "next": "end", "action": {
            "tool": "state", "operation": "update",
            "args": {"state": "$state", "values": {
                "finished": True, "winners": [], "phase": "finished"}}}},
        "end": {"kind": "end"},
    }
    initial = {"finished": False, "winners": [], "scores": [0], "current_player": 0,
               "round": 1, "max_rounds": max_rounds, "target": target,
               "phase": "solve", "reveal": False, "gave_up": False,
               "instructions": "每张牌恰好使用一次；允许括号与 " + " ".join(operations) + "；结果必须精确等于目标。"}
    return GamePlan(game_kind="arithmetic", players=1, tools=tools, initial=initial,
                    entry="deal", nodes=nodes, step_limit=512)
