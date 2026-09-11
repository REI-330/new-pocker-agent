from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeckSpec(StrictSpec):
    suits: list[str] = Field(min_length=1, max_length=8)
    ranks: list[str] = Field(min_length=1, max_length=20)
    copies: int = Field(default=1, ge=1, le=8)

    @model_validator(mode="after")
    def unique_cards(self):
        if len(set(self.suits)) != len(self.suits) or len(set(self.ranks)) != len(self.ranks):
            raise ValueError("suits and ranks must be unique")
        return self


class PlayerSpec(StrictSpec):
    min_players: int = Field(default=2, ge=1, le=12)
    max_players: int = Field(default=4, ge=1, le=12)
    starting_hand_size: int = Field(default=5, ge=0, le=52)

    @model_validator(mode="after")
    def validate_range(self):
        if self.max_players < self.min_players:
            raise ValueError("max_players must be greater than or equal to min_players")
        return self


class ActionSpec(StrictSpec):
    name: Literal["play", "discard", "draw", "pass"]
    source: Literal["hand", "system"] = "hand"
    amount: int = Field(default=1, ge=0, le=52)

    @model_validator(mode="after")
    def executable(self):
        required = "hand" if self.name in {"play", "discard"} else "system"
        if "source" not in self.model_fields_set:
            self.source = required
        if self.source != required:
            raise ValueError(f"{self.name} requires source={required}")
        if self.name != "pass" and self.amount == 0:
            raise ValueError("card actions must have amount >= 1")
        return self


class PhaseSpec(StrictSpec):
    name: str = Field(min_length=1, max_length=64)
    actions: list[str] = Field(min_length=1)
    max_turns: int = Field(default=100, ge=1, le=10000)


class ScoreRule(StrictSpec):
    condition: Literal["highest_card", "most_cards"]
    points: int = Field(default=1, ge=0, le=1000)


class GameRuleDSL(StrictSpec):
    schema_version: Literal["0.1"] = "0.1"
    game_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    deck: DeckSpec
    players: PlayerSpec
    phases: list[PhaseSpec] = Field(min_length=1, max_length=20)
    actions: list[ActionSpec] = Field(min_length=1)
    scoring: list[ScoreRule] = Field(default_factory=list)
    max_rounds: int = Field(default=1, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_references(self):
        action_names = {action.name for action in self.actions}
        if len(action_names) != len(self.actions):
            raise ValueError("action names must be unique")
        if len({phase.name for phase in self.phases}) != len(self.phases):
            raise ValueError("phase names must be unique")
        for phase in self.phases:
            unknown = set(phase.actions) - action_names
            if unknown:
                raise ValueError(f"phase '{phase.name}' references unknown actions: {sorted(unknown)}")
        deck_size = len(self.deck.suits) * len(self.deck.ranks) * self.deck.copies
        if self.players.starting_hand_size * self.players.max_players > deck_size:
            raise ValueError("starting hands require more cards than the configured deck contains")
        return self
