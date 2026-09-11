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


def crazy_eights_plan(*, hand_size: int = 5, wild_rank: str | None = "8",
                      ranks=("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"),
                      suits=("S", "H", "D", "C")) -> GamePlan:
    """Two-player matching game with hidden hands and draw-until-playable.

    Contract: match suit or rank (``wild_rank`` also matches anything and
    declares the next suit); no legal card means keep drawing; the first empty
    hand wins; opponent hands are private (``private_hands``).
    """
    wild = [wild_rank] if wild_rank else []
    tools = [
        {"name": "state"},
        {"name": "logic"},
        {"name": "deck", "config": {"ranks": list(ranks), "suits": list(suits)}},
        {"name": "pattern"},
        {"name": "matching"},
    ]
    match = {"wild_ranks": wild}

    def play_node(player: int) -> dict:
        return {"kind": "call", "next": "check_end", "action": {
            "tool": "matching", "operation": "play",
            "args": {"state": "$state", "hand_index": player,
                     "card_index": "$state.input.card_index",
                     "declared_suit": "$state.input.declared_suit",
                     "suits": list(suits), **match}}}

    def draw_node(player: int) -> dict:
        return {"kind": "call", "next": "turn", "action": {
            "tool": "matching", "operation": "draw",
            "args": {"state": "$state", "hand_index": player, "seed": "$state.round_seed"}}}

    def turn_nodes(player: int) -> dict:
        """A seat's turn: expose the real choice set, then wait accordingly.

        Offering ``play`` only when a legal card exists is what makes
        ``legal_actions`` truthful; a generic policy can then trust it.
        """
        return {
            f"turn{player}": {"kind": "call", "next": f"has{player}", "action": {
                "tool": "pattern", "operation": "choices",
                "args": {"cards": f"$state.hands.{player}", "top": "$state.table",
                         "active_suit": "$state.active_suit", **match},
                "result_key": "legal_card_indices"}},
            f"has{player}": {"kind": "call", "next": f"gate{player}", "action": {
                "tool": "logic", "operation": "evaluate",
                "args": {"expression": {"gt": [{"count": ["$state.legal_card_indices"]}, 0]}},
                "result_key": "has_choice"}},
            f"gate{player}": {"kind": "branch", "value": "$state.has_choice",
                              "cases": [{"value": True, "target": f"wait{player}"}],
                              "next": f"drawwait{player}"},
            f"wait{player}": {"kind": "wait", "inputs": {"play": f"play{player}"}},
            f"drawwait{player}": {"kind": "wait", "inputs": {"draw": f"draw{player}"}},
            f"play{player}": play_node(player),
            f"draw{player}": draw_node(player),
        }

    nodes = {
        "round_seed": {"kind": "call", "next": "deal", "action": {
            "tool": "logic", "operation": "evaluate",
            "args": {"expression": {"join": ["$state.seed", ":ce"]}},
            "result_key": "round_seed"}},
        "deal": {"kind": "call", "next": "init", "action": {
            "tool": "deck", "operation": "deal",
            "args": {"seed": "$state.round_seed", "hands": 2, "cards_each": hand_size, "kitty": 1},
            "result_key": "deal"}},
        "init": {"kind": "call", "next": "top_card", "action": {
            "tool": "state", "operation": "update", "args": {"state": "$state", "values": {
                "hands": "$state.deal.hands", "stock": "$state.deal.deck",
                "table": "$state.deal.kitty", "deal": None,
                "suits": list(suits),
                "current_player": 0, "phase": "play", "finished": False, "winners": [],
                "private_hands": True, "wild_ranks": wild}}}},
        "top_card": {"kind": "call", "next": "set_suit", "action": {
            "tool": "pattern", "operation": "describe",
            "args": {"cards": "$state.table"}, "result_key": "top_desc"}},
        "set_suit": {"kind": "call", "next": "turn", "action": {
            "tool": "state", "operation": "update",
            "args": {"state": "$state", "values": {"active_suit": "$state.top_desc.suit"}}}},
        "turn": {"kind": "branch", "value": "$state.current_player",
                 "cases": [{"value": 0, "target": "turn0"}, {"value": 1, "target": "turn1"}],
                 "next": "turn0"},
        **turn_nodes(0),
        **turn_nodes(1),
        "check_end": {"kind": "branch", "value": "$state.finished",
                      "cases": [{"value": True, "target": "finish"}], "next": "advance"},
        "advance": {"kind": "call", "next": "apply_turn", "action": {
            "tool": "logic", "operation": "evaluate",
            "args": {"expression": {"sub": [1, "$state.current_player"]}},
            "result_key": "next_player"}},
        "apply_turn": {"kind": "call", "next": "turn", "action": {
            "tool": "state", "operation": "update",
            "args": {"state": "$state", "values": {"current_player": "$state.next_player"}}}},
        "finish": {"kind": "call", "next": "end", "action": {
            "tool": "state", "operation": "update",
            "args": {"state": "$state", "values": {"phase": "finished"}}}},
        "end": {"kind": "end"},
    }
    initial = {"finished": False, "winners": [], "current_player": 0, "round": 1, "max_rounds": 1,
               "phase": "play", "private_hands": True, "suits": list(suits), "wild_ranks": wild,
               "instructions": "接同花色或同点数；万能牌可指定花色；无牌可出时持续摸牌；先出完手牌者获胜。"}
    return GamePlan(game_kind="crazy_eights", players=2, tools=tools, initial=initial,
                    entry="round_seed", nodes=nodes, step_limit=512)


def whist_plan(*, players: int = 4, cards_each: int = 5, teams=((0, 2), (1, 3)),
               ranks=("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"),
               suits=("S", "H", "D", "C")) -> GamePlan:
    """Partnership trick-taking: follow suit, trump decides, most tricks per team wins.

    Contract: ``cards_each`` tricks; the turn-up kitty card fixes the trump; a
    player must follow the led suit when able; a trick goes to the highest trump
    else the highest card of the led suit; the winning team is the one with the
    most tricks (a tie shares).
    """
    teams = [list(team) for team in teams]
    tools = [
        {"name": "state"},
        {"name": "logic"},
        {"name": "deck", "config": {"ranks": list(ranks), "suits": list(suits)}},
        {"name": "pattern"},
        {"name": "trick", "config": {"teams": teams}},
    ]

    def seat_nodes(seat: int) -> dict:
        return {
            f"turn{seat}": {"kind": "call", "next": f"wait{seat}", "action": {
                "tool": "trick", "operation": "legal",
                "args": {"state": "$state", "hand_index": seat},
                "result_key": "legal_card_indices"}},
            f"wait{seat}": {"kind": "wait", "inputs": {"play": f"play{seat}"}},
            f"play{seat}": {"kind": "call", "next": "check_end", "action": {
                "tool": "trick", "operation": "play",
                "args": {"state": "$state", "hand_index": seat,
                         "card_index": "$state.input.card_index", "trump": "$state.trump"}}},
        }

    nodes = {
        "round_seed": {"kind": "call", "next": "deal", "action": {
            "tool": "logic", "operation": "evaluate",
            "args": {"expression": {"join": ["$state.seed", ":whist"]}},
            "result_key": "round_seed"}},
        "deal": {"kind": "call", "next": "turn_up", "action": {
            "tool": "deck", "operation": "deal",
            "args": {"seed": "$state.round_seed", "hands": players,
                     "cards_each": cards_each, "kitty": 1},
            "result_key": "deal"}},
        "turn_up": {"kind": "call", "next": "init", "action": {
            "tool": "pattern", "operation": "describe",
            "args": {"cards": "$state.deal.kitty"}, "result_key": "top_desc"}},
        "init": {"kind": "call", "next": "turn", "action": {
            "tool": "state", "operation": "update", "args": {"state": "$state", "values": {
                "hands": "$state.deal.hands", "deal": None,
                "trump": "$state.top_desc.suit", "led_suit": "", "trick": [],
                "trick_seats": [], "table": [], "tricks_won": [0] * players,
                "tricks_total": cards_each, "trick_index": 0,
                "teams": teams, "current_player": 0, "phase": "play",
                "finished": False, "winners": [], "private_hands": True}}}},
        "turn": {"kind": "branch", "value": "$state.current_player",
                 "cases": [{"value": seat, "target": f"turn{seat}"} for seat in range(players)],
                 "next": "turn0"},
        **{key: value for seat in range(players) for key, value in seat_nodes(seat).items()},
        "check_end": {"kind": "branch", "value": "$state.finished",
                      "cases": [{"value": True, "target": "finish"}], "next": "turn"},
        "finish": {"kind": "call", "next": "end", "action": {
            "tool": "state", "operation": "update",
            "args": {"state": "$state", "values": {"phase": "finished"}}}},
        "end": {"kind": "end"},
    }
    initial = {"finished": False, "winners": [], "current_player": 0, "round": 1, "max_rounds": 1,
               "phase": "play", "private_hands": True, "teams": teams,
               "instructions": "跟牌时必须先跟同花色；将牌最大；每墩胜者领出；吃到最多墩的队伍获胜。"}
    return GamePlan(game_kind="whist", players=players, tools=tools, initial=initial,
                    entry="round_seed", nodes=nodes, step_limit=1024)


def five_card_poker_plan(*, stacks: int = 100, min_raise: int = 10, cards_each: int = 5,
                         ranks=("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"),
                         suits=("S", "H", "D", "C")) -> GamePlan:
    """Heads-up five-card showdown with one no-limit betting round.

    Contract: five private cards each; fold / check / call / raise / all-in;
    the round ends when every live stack has acted and matched; a fold wins the
    pot uncontested, otherwise the better five-card hand takes it (ties split).
    """
    tools = [
        {"name": "state"},
        {"name": "logic"},
        {"name": "deck", "config": {"ranks": list(ranks), "suits": list(suits)}},
        {"name": "hand_rank"},
        {"name": "betting", "config": {"min_raise": min_raise}},
        {"name": "ledger"},
    ]

    def act_node(action: str) -> dict:
        return {"kind": "call", "next": "check_round", "action": {
            "tool": "betting", "operation": "act",
            "args": {"state": "$state", "action": action, "amount": "$state.input.amount"}}}

    def award_node(seat: int, winners: list[int], name: str) -> dict:
        return {"kind": "call", "next": f"finish_{name}", "action": {
            "tool": "ledger", "operation": "settle",
            "args": {"state": "$state", "winners": {0: winners}}}}

    def finish_node(winners: list[int]) -> dict:
        return {"kind": "call", "next": "end", "action": {
            "tool": "state", "operation": "update", "args": {"state": "$state", "values": {
                "finished": True, "winners": winners, "phase": "finished", "pot": 0}}}}

    nodes = {
        "round_seed": {"kind": "call", "next": "deal", "action": {
            "tool": "logic", "operation": "evaluate",
            "args": {"expression": {"join": ["$state.seed", ":poker"]}},
            "result_key": "round_seed"}},
        "deal": {"kind": "call", "next": "init", "action": {
            "tool": "deck", "operation": "deal",
            "args": {"seed": "$state.round_seed", "hands": 2, "cards_each": cards_each, "kitty": 0},
            "result_key": "deal"}},
        "init": {"kind": "call", "next": "pot", "action": {
            "tool": "state", "operation": "update", "args": {"state": "$state", "values": {
                "hands": "$state.deal.hands", "deal": None,
                "stacks": [stacks, stacks], "committed": [0, 0], "hand_committed": [0, 0],
                "folded": [], "acted": [], "current_bet": 0, "min_raise": min_raise,
                "current_player": 0, "street_done": False, "private_hands": True,
                "phase": "betting", "finished": False, "winners": [],
                "instructions": "五张私有牌 + 一轮无上限下注：弃牌/过牌/跟注/加注/全下；弃牌即输，否则比五张牌型。"}}}},
        "pot": {"kind": "call", "next": "betting_turn", "action": {
            "tool": "ledger", "operation": "total", "args": {"state": "$state"},
            "result_key": "pot"}},
        "betting_turn": {"kind": "call", "next": "wait", "action": {
            "tool": "betting", "operation": "legal", "args": {"state": "$state"},
            "result_key": "available_actions"}},
        "wait": {"kind": "wait", "inputs": {"fold": "act_fold", "check": "act_check",
                                              "call": "act_call", "raise": "act_raise",
                                              "all_in": "act_all_in"}},
        "act_fold": act_node("fold"),
        "act_check": act_node("check"),
        "act_call": act_node("call"),
        "act_raise": act_node("raise"),
        "act_all_in": act_node("all_in"),
        "check_round": {"kind": "branch", "value": "$state.street_done",
                        "cases": [{"value": True, "target": "resolve"}], "next": "pot"},
        "resolve": {"kind": "call", "next": "folded_branch", "action": {
            "tool": "logic", "operation": "evaluate",
            "args": {"expression": {"count": ["$state.folded"]}},
            "result_key": "folded_count"}},
        "folded_branch": {"kind": "branch", "value": "$state.folded_count",
                          "cases": [{"value": 1, "target": "folded_winner"}],
                          "next": "showdown_compare"},
        "folded_winner": {"kind": "branch", "value": "$state.folded.0",
                          "cases": [{"value": 0, "target": "award_p1"},
                                    {"value": 1, "target": "award_p0"}], "next": "award_p0"},
        "showdown_compare": {"kind": "call", "next": "showdown_branch", "action": {
            "tool": "hand_rank", "operation": "compare",
            "args": {"left": "$state.hands.0", "right": "$state.hands.1"},
            "result_key": "showdown"}},
        "showdown_branch": {"kind": "branch", "value": "$state.showdown.outcome",
                            "cases": [{"value": "left", "target": "award_p0"},
                                      {"value": "right", "target": "award_p1"}],
                            "next": "award_tie"},
        "award_p0": award_node(0, [0], "p0"),
        "award_p1": award_node(1, [1], "p1"),
        "award_tie": award_node(0, [0, 1], "tie"),
        "finish_p0": finish_node([0]),
        "finish_p1": finish_node([1]),
        "finish_tie": finish_node([0, 1]),
        "end": {"kind": "end"},
    }
    initial = {"finished": False, "winners": [], "current_player": 0, "round": 1, "max_rounds": 1,
               "phase": "betting", "private_hands": True}
    return GamePlan(game_kind="five_card_poker", players=2, tools=tools, initial=initial,
                    entry="round_seed", nodes=nodes, step_limit=512)


def war_plan(*, max_rounds: int = 3,
             ranks=("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"),
             suits=("S", "H", "D", "C")) -> GamePlan:
    """Two-player rank duel: each round both draw one card; the higher card scores.

    Contract this plan implements (and playtest enforces): ``max_rounds``
    independent deals, one point to the higher card, ties score nothing, and
    the highest total wins (ties are shared).
    """
    tools = [
        {"name": "state"},
        {"name": "logic"},
        {"name": "deck", "config": {"ranks": list(ranks), "suits": list(suits)}},
        {"name": "rank_compare"},
        {"name": "score_settle"},
        {"name": "winner_resolve"},
    ]
    nodes = {
        "round_seed": {"kind": "call", "next": "deal", "action": {
            "tool": "logic", "operation": "evaluate",
            "args": {"expression": {"join": ["$state.seed", ":", "$state.round"]}},
            "result_key": "round_seed"}},
        "deal": {"kind": "call", "next": "init", "action": {
            "tool": "deck", "operation": "deal",
            "args": {"seed": "$state.round_seed", "hands": 2, "cards_each": 1},
            "result_key": "deal"}},
        "init": {"kind": "call", "next": "wait", "action": {
            "tool": "state", "operation": "update",
            "args": {"state": "$state", "values": {
                "hands": "$state.deal.hands", "phase": "play"}}}},
        "wait": {"kind": "wait", "inputs": {"play": "judge"}},
        "judge": {"kind": "call", "next": "verdict_branch", "action": {
            "tool": "rank_compare", "operation": "call",
            "args": {"left": "$state.hands.0", "right": "$state.hands.1"},
            "result_key": "verdict"}},
        "verdict_branch": {"kind": "branch", "value": "$state.verdict.outcome",
                           "cases": [{"value": "left", "target": "score_left"},
                                     {"value": "right", "target": "score_right"}],
                           "next": "round_check"},
        "score_left": {"kind": "call", "next": "round_check", "action": {
            "tool": "score_settle", "operation": "call",
            "args": {"scores": "$state.scores", "winners": [0], "points": 1},
            "result_key": "scores"}},
        "score_right": {"kind": "call", "next": "round_check", "action": {
            "tool": "score_settle", "operation": "call",
            "args": {"scores": "$state.scores", "winners": [1], "points": 1},
            "result_key": "scores"}},
        "round_check": {"kind": "call", "next": "round_branch", "action": {
            "tool": "logic", "operation": "evaluate",
            "args": {"expression": {"ge": ["$state.round", "$state.max_rounds"]}},
            "result_key": "at_end"}},
        "round_branch": {"kind": "branch", "value": "$state.at_end",
                         "cases": [{"value": True, "target": "resolve"}], "next": "bump"},
        "bump": {"kind": "call", "next": "apply_round", "action": {
            "tool": "logic", "operation": "evaluate",
            "args": {"expression": {"add": ["$state.round", 1]}}, "result_key": "next_round"}},
        "apply_round": {"kind": "call", "next": "round_seed", "action": {
            "tool": "state", "operation": "update",
            "args": {"state": "$state", "values": {"round": "$state.next_round", "phase": "play"}}}},
        "resolve": {"kind": "call", "next": "finish", "action": {
            "tool": "winner_resolve", "operation": "call",
            "args": {"values": "$state.scores"}, "result_key": "winners_indexes"}},
        "finish": {"kind": "call", "next": "end", "action": {
            "tool": "state", "operation": "update",
            "args": {"state": "$state", "values": {
                "finished": True, "winners": "$state.winners_indexes", "phase": "finished"}}}},
        "end": {"kind": "end"},
    }
    initial = {"finished": False, "winners": [], "scores": [0, 0], "current_player": 0,
               "round": 1, "max_rounds": max_rounds, "phase": "play",
               "instructions": "每轮各抽一张，点数大者得 1 分；最后总分高者获胜（并列平局）。"}
    return GamePlan(game_kind="war", players=2, tools=tools, initial=initial,
                    entry="round_seed", nodes=nodes, step_limit=256)
