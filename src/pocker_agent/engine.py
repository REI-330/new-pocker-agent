from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import GameRuleDSL, ScoreRule


@dataclass(frozen=True)
class Card:
    suit: str
    rank: str
    value: int

    def as_dict(self):
        return asdict(self)


@dataclass
class PlayerState:
    id: str
    hand: list[Card] = field(default_factory=list)
    score: int = 0
    played: list[Card] = field(default_factory=list)


@dataclass
class GameState:
    round_number: int
    phase: str
    current_player: int
    players: list[PlayerState]
    table: list[Card] = field(default_factory=list)
    deck: list[Card] = field(default_factory=list)
    finished: bool = False
    phase_index: int = 0
    phase_turns: int = 0
    round_turns: int = 0
    winners: list[str] = field(default_factory=list)
    finish_reason: str = ""


def build_deck(rules: GameRuleDSL) -> list[Card]:
    # DSL rank order is ascending strength, independent of the displayed label.
    return [Card(suit, rank, index + 1) for _ in range(rules.deck.copies)
            for index, rank in enumerate(rules.deck.ranks) for suit in rules.deck.suits]


class RuleEngine:
    """Canonical DSL executor shared by simulations, runtime, and export compilation."""

    def __init__(self, rules: GameRuleDSL, seed: int = 0, player_count: int | None = None):
        count = rules.players.min_players if player_count is None else player_count
        if not rules.players.min_players <= count <= rules.players.max_players:
            raise ValueError("player_count is outside the DSL player range")
        self.rules = rules
        deck = build_deck(rules)
        random.Random(seed).shuffle(deck)
        self.state = GameState(1, rules.phases[0].name, 0,
                               [PlayerState(f"player-{index + 1}") for index in range(count)], deck=deck)
        self.events: list[dict[str, Any]] = []

    def _emit(self, event: str, **payload):
        record = {"event": event, "round": self.state.round_number, **payload}
        self.events.append(record)
        return record

    def setup(self):
        if self.events:
            raise RuntimeError("game_already_started")
        self._emit("game_started", phase=self.state.phase, players=[p.id for p in self.state.players])
        self._deal()
        self._advance()
        return self.events

    def _deal(self):
        self._emit("round_started", phase=self.state.phase)
        for player in self.state.players:
            for _ in range(self.rules.players.starting_hand_size):
                self._draw_to(player)

    def _draw_to(self, player):
        if not self.state.deck:
            raise RuntimeError("deck_exhausted")
        card = self.state.deck.pop()
        player.hand.append(card)
        self._emit("card_drawn", player=player.id, card=card.as_dict())
        return card

    def legal_actions(self, player_index=None):
        if self.state.finished:
            return []
        player = self.state.players[self.state.current_player if player_index is None else player_index]
        phase = self.rules.phases[self.state.phase_index]
        return [action.name for action in self.rules.actions
                if action.name in phase.actions
                and (action.name not in {"play", "discard"} or len(player.hand) >= action.amount)
                and (action.name != "draw" or len(self.state.deck) >= action.amount)]

    def step(self, action_name: str | None = None, card_index: int = 0):
        if self.state.finished:
            raise RuntimeError("game_finished")
        legal = self.legal_actions()
        action_name = action_name or (legal[0] if legal else "")
        if action_name not in legal:
            raise ValueError(f"illegal_action:{action_name}")
        action = next(a for a in self.rules.actions if a.name == action_name)
        player = self.state.players[self.state.current_player]
        cards = []
        if action_name in {"play", "discard"}:
            if card_index < 0 or card_index + action.amount > len(player.hand):
                raise ValueError("card_index is outside the playable hand")
            cards = player.hand[card_index:card_index + action.amount]
            del player.hand[card_index:card_index + action.amount]
            if action_name == "play":
                player.played.extend(cards)
                self.state.table.extend(cards)
        elif action_name == "draw":
            cards = [self._draw_to(player) for _ in range(action.amount)]
        event = self._emit("action_executed", player=player.id, action=action_name, cards=[c.as_dict() for c in cards])
        self.state.phase_turns += 1
        self.state.round_turns += 1
        self.state.current_player = (self.state.current_player + 1) % len(self.state.players)
        self._advance()
        return event

    def _advance(self):
        # Return only a playable state or a terminal state; clients must never need
        # to invent a "next" action to escape an empty legal_actions list.
        while not self.state.finished:
            phase = self.rules.phases[self.state.phase_index]
            if self.state.phase_turns < phase.max_turns:
                for _ in self.state.players:
                    if self.legal_actions():
                        return
                    self.state.current_player = (self.state.current_player + 1) % len(self.state.players)
            self._emit("phase_finished", phase=phase.name)
            if self.state.phase_index + 1 < len(self.rules.phases):
                self.state.phase_index += 1
                self.state.phase = self.rules.phases[self.state.phase_index].name
                self.state.phase_turns = 0
                self.state.current_player = 0
                continue
            if self.state.round_turns == 0:
                self.finish("no_legal_actions")
                return
            self._score_round()
            if self.state.round_number >= self.rules.max_rounds:
                self.finish("round_limit")
                return
            needed = self.rules.players.starting_hand_size * len(self.state.players)
            if len(self.state.deck) < needed:
                self.finish("deck_exhausted")
                return
            self.state.round_number += 1
            self.state.phase_index = self.state.phase_turns = self.state.round_turns = 0
            self.state.phase = self.rules.phases[0].name
            self.state.current_player = 0
            self.state.table.clear()
            for player in self.state.players:
                player.hand.clear()
                player.played.clear()
            self._deal()

    def _score_round(self):
        awards = []
        for scoring in self.rules.scoring or [ScoreRule(condition="highest_card")]:
            values = {p.id: max((c.value for c in (p.played or p.hand)), default=0)
                      if scoring.condition == "highest_card" else len(p.hand) for p in self.state.players}
            best = max(values.values())
            winners = [p.id for p in self.state.players if values[p.id] == best]
            for player in self.state.players:
                if player.id in winners:
                    player.score += scoring.points
            awards.append({"condition": scoring.condition, "winners": winners, "values": values, "points": scoring.points})
        self._emit("round_finished", awards=awards, scores={p.id: p.score for p in self.state.players})

    def finish(self, reason="completed"):
        if self.state.finished:
            return self.events[-1]
        self.state.finished = True
        self.state.finish_reason = reason
        best = max(p.score for p in self.state.players)
        self.state.winners = [p.id for p in self.state.players if p.score == best]
        return self._emit("game_finished", winner=self.state.winners[0] if len(self.state.winners) == 1 else None,
                          winners=self.state.winners, reason=reason, scores={p.id: p.score for p in self.state.players})

    def run(self, max_steps=1000):
        self.setup()
        for _ in range(max_steps):
            if self.state.finished:
                return self.events
            self.step()
        if not self.state.finished:
            raise RuntimeError("simulation_step_limit: 游戏超过模拟步数限制，请减少阶段回合数")
        return self.events

    def serialize(self):
        return {"rules": self.rules.model_dump(mode="json"), "state": asdict(self.state), "events": self.events}

    @classmethod
    def restore(cls, data):
        engine = cls(GameRuleDSL.model_validate(data["rules"]))
        state = dict(data["state"])
        state["players"] = [PlayerState(p["id"], [Card(**c) for c in p["hand"]], p["score"], [Card(**c) for c in p["played"]]) for p in state["players"]]
        state["deck"] = [Card(**c) for c in state["deck"]]
        state["table"] = [Card(**c) for c in state["table"]]
        engine.state = GameState(**state)
        engine.events = data["events"]
        return engine
