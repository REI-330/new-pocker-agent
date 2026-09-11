"""Deterministic executors for arithmetic puzzles, blackjack and shedding rules."""
import random
from dataclasses import asdict

from .arithmetic import calculate, solve
from .engine import Card, GameState, PlayerState, build_deck


def restore_state(data):
    data = dict(data)
    data["players"] = [PlayerState(p["id"], [Card(**c) for c in p["hand"]], p["score"], [Card(**c) for c in p["played"]]) for p in data["players"]]
    data["deck"] = [Card(**c) for c in data["deck"]]
    data["table"] = [Card(**c) for c in data["table"]]
    return GameState(**data)


class FamilyEngine:
    def __init__(self, rules, seed=0, player_count=None):
        count = rules.players.min_players if player_count is None else player_count
        if not rules.players.min_players <= count <= rules.players.max_players:
            raise ValueError("玩家数超出规则范围")
        self.rules, self.seed = rules, seed
        self.state = GameState(1, "准备", 0, [PlayerState(f"player-{i+1}") for i in range(count)])
        self.events, self.extra, self.discard = [], {}, []

    def emit(self, event, **payload):
        entry = {"event": event, "round": self.state.round_number, **payload}
        self.events.append(entry)
        return entry

    def fresh_deck(self, attempt=0):
        cards = build_deck(self.rules)
        random.Random(f"{self.seed}:{self.state.round_number}:{attempt}").shuffle(cards)
        return cards

    def setup(self):
        if self.events: raise RuntimeError("game_already_started")
        self.emit("game_started", kind=self.rules.kind)
        self.deal()
        return self.events

    def require(self, action):
        if action not in self.legal_actions():
            raise ValueError("当前不能执行该动作：" + str(action))

    def finish(self, winners, reason):
        self.state.finished, self.state.winners, self.state.finish_reason = True, winners, reason
        return self.emit("game_finished", winners=winners, winner=winners[0] if len(winners) == 1 else None, reason=reason)

    def close_round(self):
        if self.state.round_number >= self.rules.max_rounds:
            best = max(p.score for p in self.state.players)
            self.finish([p.id for p in self.state.players if p.score == best], "round_limit")

    def next_round(self):
        self.state.round_number += 1
        self.deal()
        return self.events[-1]

    def serialize(self):
        return {"rules": self.rules.model_dump(mode="json"), "state": asdict(self.state),
                "events": self.events, "seed": self.seed, "extra": self.extra,
                "discard": [c.as_dict() for c in self.discard]}

    def run(self, max_steps=1000):
        self.setup()
        for _ in range(max_steps):
            if self.state.finished: return self.events
            self.step()
        if not self.state.finished: raise RuntimeError("simulation_step_limit: 模拟超过步数限制")
        return self.events

    def view(self):
        return {"kind": self.rules.kind, "phase": self.state.phase,
                "players": [{"id": p.id, "hand": [c.as_dict() for c in p.hand], "score": p.score} for p in self.state.players],
                "feedback": self.extra.get("feedback", ""), "events": self.events[-100:]}


class ArithmeticEngine(FamilyEngine):
    def numbers(self):
        return tuple(self.rules.rank_values[c.rank] for c in self.state.table)

    def solution(self):
        return solve(self.numbers(), self.rules.target, tuple(self.rules.operations), self.rules.fractional_intermediates)

    def deal(self):
        for attempt in range(64):
            self.state.deck = self.fresh_deck(attempt)
            self.state.table = [self.state.deck.pop() for _ in range(4)]
            if self.rules.deal_mode == "random" or self.solution() is not None: break
        else:
            raise ValueError("按当前牌值、运算符和目标未找到有解题，请调整规则或使用随机出题")
        self.state.phase = "作答"
        self.extra = {"feedback": ""}
        self.emit("round_started", numbers=self.numbers(), target=self.rules.target)

    def legal_actions(self):
        if self.state.finished: return []
        return ["next_round"] if self.state.phase == "查看答案" else ["submit_expression", "no_solution", "give_up"]

    def step(self, action_name=None, card_index=0, *, expression="", declared_suit=""):
        if action_name is None:
            if self.state.phase == "查看答案": action_name = "next_round"
            else:
                expression = self.solution()
                action_name = "submit_expression" if expression is not None else "no_solution"
        self.require(action_name)
        if action_name == "next_round": return self.next_round()
        if action_name == "submit_expression":
            value = calculate(expression, self.numbers(), self.rules.operations, self.rules.fractional_intermediates)
            if value != self.rules.target:
                raise ValueError(f"算式结果为{value}，目标是{self.rules.target}；请重新尝试")
            feedback = f"正确：{expression} = {self.rules.target}，得1分"
        elif action_name == "no_solution":
            if self.solution() is not None: raise ValueError("这组牌有解，请继续尝试或选择放弃查看答案")
            feedback = "判断正确：按本局允许的运算确实无解，得1分"
        else:
            answer = self.solution()
            feedback = f"本题放弃，得0分。参考答案：{answer} = {self.rules.target}" if answer else "本题放弃，得0分。这组牌无解。"
        points = 0 if action_name == "give_up" else 1
        self.state.players[0].score += points
        self.extra["feedback"] = feedback
        self.state.phase = "查看答案"
        event = self.emit("round_finished", action=action_name, expression=expression, points=points, feedback=feedback)
        self.close_round()
        return event

    def view(self):
        return {**super().view(), "numbers": self.numbers(), "target": self.rules.target,
                "instructions": "每张牌恰好使用一次，允许括号及 " + " ".join(self.rules.operations),
                "result_title": f"完成练习 · {self.state.players[0].score} / {self.rules.max_rounds} 分"}


def hand_value(cards):
    total = sum(1 if c.rank == "A" else 10 if c.rank in {"J", "Q", "K"} else int(c.rank) for c in cards)
    soft = any(c.rank == "A" for c in cards) and total + 10 <= 21
    return total + (10 if soft else 0), soft


class BlackjackEngine(FamilyEngine):
    def deal(self):
        self.state.deck = self.fresh_deck()
        self.state.table = []
        self.extra = {"feedback": ""}
        for p in self.state.players: p.hand.clear()
        for _ in range(2):
            for p in self.state.players: p.hand.append(self.state.deck.pop())
        self.state.phase = "要牌或停牌"
        self.emit("round_started")
        if any(hand_value(p.hand)[0] == 21 for p in self.state.players): self.settle()

    def legal_actions(self):
        if self.state.finished: return []
        return ["next_round"] if self.state.phase == "本轮结算" else ["hit", "stand"]

    def step(self, action_name=None, card_index=0, *, expression="", declared_suit=""):
        if action_name is None:
            action_name = "next_round" if self.state.phase == "本轮结算" else "hit" if hand_value(self.state.players[0].hand)[0] < 17 else "stand"
        self.require(action_name)
        if action_name == "next_round": return self.next_round()
        if action_name == "hit":
            self.state.players[0].hand.append(self.state.deck.pop())
            event = self.emit("action_executed", player="player-1", action="hit")
            if hand_value(self.state.players[0].hand)[0] >= 21: self.settle()
            return event
        return self.settle()

    def settle(self):
        human, dealer = self.state.players
        h, _ = hand_value(human.hand)
        d, soft = hand_value(dealer.hand)
        hn, dn = h == 21 and len(human.hand) == 2, d == 21 and len(dealer.hand) == 2
        if h <= 21 and not hn and not dn:
            while d < 17 or (d == 17 and soft and self.rules.dealer_hits_soft_17):
                dealer.hand.append(self.state.deck.pop())
                d, soft = hand_value(dealer.hand)
        winner = None
        if h > 21: winner, reason = dealer, "你爆牌，庄家获胜"
        elif hn and dn: reason = "双方均为两张牌21点，平局"
        elif hn: winner, reason = human, "你以两张牌21点获胜"
        elif dn: winner, reason = dealer, "庄家以两张牌21点获胜"
        elif d > 21: winner, reason = human, "庄家爆牌，你获胜"
        elif h > d: winner, reason = human, "你的点数更高，获胜"
        elif d > h: winner, reason = dealer, "庄家的点数更高，获胜"
        else: reason = "点数相同，本轮平局"
        if winner: winner.score += 1
        self.extra["feedback"] = f"你 {h} 点 · 庄家 {d} 点。{reason}。"
        self.state.phase = "本轮结算"
        event = self.emit("round_finished", human_total=h, dealer_total=d, winner=winner.id if winner else None, reason=reason)
        self.close_round()
        return event

    def view(self):
        view = super().view()
        for i, player in enumerate(view["players"]):
            hidden = i == 1 and self.state.phase != "本轮结算"
            player["label"] = "庄家" if i == 1 else "你"
            player["hand"] = player["hand"][:1] if hidden else player["hand"]
            player["hidden_count"] = 1 if hidden else 0
            player["total"] = None if hidden else hand_value(self.state.players[i].hand)[0]
        return {**view, "instructions": "选择要牌或停牌。A可按1或11计，超过21点爆牌。"}


class SheddingEngine(FamilyEngine):
    def deal(self):
        self.state.deck = self.fresh_deck()
        for _ in range(self.rules.players.starting_hand_size):
            for p in self.state.players: p.hand.append(self.state.deck.pop())
        top = next(i for i, c in enumerate(self.state.deck) if c.rank != self.rules.wild_rank)
        self.discard = [self.state.deck.pop(top)]
        self.state.table = self.discard[-1:]
        self.state.phase = "同花色或同点数接牌"
        self.extra = {"active_suit": self.discard[-1].suit, "recycles": 0, "feedback": ""}
        self.emit("round_started", top=self.discard[-1].as_dict())

    def choices(self, player=None):
        p = self.state.players[self.state.current_player if player is None else player]
        top = self.discard[-1]
        return [i for i, c in enumerate(p.hand) if c.rank == self.rules.wild_rank or c.rank == top.rank or c.suit == self.extra["active_suit"]]

    def can_draw(self):
        return bool(self.state.deck or (self.rules.recycle_discard and len(self.discard) > 1))

    def legal_actions(self):
        if self.state.finished: return []
        if self.choices(): return ["play"]
        return ["draw"] if self.can_draw() else ["pass"]

    def step(self, action_name=None, card_index=0, *, expression="", declared_suit=""):
        p = self.state.players[self.state.current_player]
        if action_name is None:
            action_name = self.legal_actions()[0]
            if action_name == "play":
                card_index = self.choices()[0]
                declared_suit = max(self.rules.deck.suits, key=lambda suit: sum(c.suit == suit for i, c in enumerate(p.hand) if i != card_index))
        self.require(action_name)
        if action_name == "play":
            if card_index not in self.choices(): raise ValueError("这张牌不能接在当前顶牌上，请选择同花色、同点数或万能牌")
            card = p.hand[card_index]
            if card.rank == self.rules.wild_rank and declared_suit not in self.rules.deck.suits:
                raise ValueError("打出万能牌时必须指定下一位玩家要跟的花色")
            p.hand.pop(card_index)
            self.discard.append(card)
            self.state.table = self.discard[-1:]
            self.extra["active_suit"] = declared_suit if card.rank == self.rules.wild_rank else card.suit
            event = self.emit("action_executed", player=p.id, action="play", card=card.as_dict(), active_suit=self.extra["active_suit"])
            if not p.hand:
                p.score += 1
                self.finish([p.id], "hand_empty")
                return event
        elif action_name == "draw":
            if not self.state.deck:
                self.state.deck, self.discard = self.discard[:-1], self.discard[-1:]
                self.extra["recycles"] += 1
                random.Random(f"{self.seed}:recycle:{self.extra['recycles']}").shuffle(self.state.deck)
                self.emit("discard_recycled", count=len(self.state.deck))
            p.hand.append(self.state.deck.pop())
            # The new card is private; public events must not reveal it.
            return self.emit("action_executed", player=p.id, action="draw", count=1)
        else:
            event = self.emit("action_executed", player=p.id, action="pass")
        self.state.current_player = (self.state.current_player + 1) % len(self.state.players)
        if not self.can_draw() and not any(self.choices(i) for i in range(len(self.state.players))):
            best = min(len(p.hand) for p in self.state.players)
            winners = [p.id for p in self.state.players if self.rules.blocked_result == "draw" or len(p.hand) == best]
            self.finish(winners, "all_blocked")
        return event

    def view(self):
        view = super().view()
        for i, p in enumerate(view["players"]):
            p["hidden_count"] = len(p["hand"]) if i and not self.state.finished else 0
            if p["hidden_count"]: p["hand"] = []
        return {**view, "legal_card_indices": self.choices() if not self.state.finished else [],
                "active_suit": self.extra["active_suit"], "wild_rank": self.rules.wild_rank,
                "instructions": "接同花色或同点数的牌；无合法牌时持续摸牌。",
                "suit_options": self.rules.deck.suits}
