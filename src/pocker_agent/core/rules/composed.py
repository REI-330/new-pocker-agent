"""``ComposedRulesIR``: the declarative input to the generic compiler.

G2 replaces "one IR family per new game" with a single ``kind: composed`` branch
(ADR-0005). A rule is a *mechanism declaration* -- players, deck, zones, setup,
typed variables, actions made of selection/move/scoring effects, a finite phase,
and a terminal rule -- not a hand-written control-flow graph and not a template.

Everything is strict (``extra="forbid"``). Unknown fields, unknown effect kinds,
unknown expression operations and macros (deferred to M4) are rejected here,
before the compiler ever sees them. Field limits mirror the budget table in
``docs/development-plan-phase2.md`` section 7.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .expr import (
    ANY_TYPE,
    BOOLEAN,
    RESERVED_STATE_NAMES,
    Expr,
    check_assignable,
    infer_type,
    validate_expression,
)

# Budgets (development-plan-phase2 section 7). Raising one is an architecture
# decision recorded in review, not a diff side effect.
MAX_PLAYERS = 4
MAX_CARDS = 108
MAX_ZONES = 24
MAX_ACTIONS = 16
MAX_SELECTION = 5
MAX_MACROS = 16
MAX_MACRO_DEPTH = 3
MAX_ROUNDS = 50
MAX_PLAN_NODES = 512
MAX_STEP_LIMIT = 1024


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetaSpec(_Strict):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class RequirementClause(_Strict):
    """A user-facing requirement clause and the IR paths that realise it.

    The clause id is what a compile error points at, so a failure names the
    requirement it violated rather than an opaque node.
    """

    id: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    text: str = Field(min_length=1, max_length=2000)
    source: str = Field(default="", max_length=2000)
    status: Literal["expressed", "open"] = "expressed"
    nodes: list[str] = Field(default_factory=list, max_length=64)


class PlayerSpec(_Strict):
    count: int = Field(ge=2, le=MAX_PLAYERS)


class DeckSpec(_Strict):
    ranks: list[str] = Field(min_length=2, max_length=26)
    suits: list[str] = Field(min_length=1, max_length=4)
    copies: int = Field(default=1, ge=1, le=8)
    values: dict[str, int] = Field(default_factory=dict, max_length=26)


class ZoneSpec(_Strict):
    id: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_-]*$")
    visibility: Literal["public", "owner_only", "hidden"] = "public"
    scope: Literal["shared", "player"] = "shared"


class DealSpec(_Strict):
    zone: str = Field(min_length=1, max_length=32)
    count: int = Field(ge=1, le=13)
    per_seat: bool = True


class SetupSpec(_Strict):
    deals: list[DealSpec] = Field(default_factory=list, max_length=8)
    stock_zone: str | None = Field(default=None, max_length=32)


class VariableSpec(_Strict):
    name: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")
    type: Literal["integer", "boolean", "string", "integer_list"]
    initial: Any = None


class ActionInputSpec(_Strict):
    id: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")
    kind: Literal["card_selection"] = "card_selection"
    zone: str = Field(min_length=1, max_length=32)
    scope: Literal["actor", "shared"] = "actor"
    min_count: int = Field(default=1, ge=0, le=MAX_SELECTION)
    max_count: int = Field(default=1, ge=0, le=MAX_SELECTION)


# --------------------------------------------------------------------- effects
class SelectEffect(_Strict):
    kind: Literal["select"] = "select"
    input: str = Field(min_length=1, max_length=32)
    result: str = Field(default="picked", min_length=1, max_length=32,
                        pattern=r"^[a-z][a-z0-9_]*$")


class MoveSelectionEffect(_Strict):
    kind: Literal["move"] = "move"
    from_zone: str = Field(min_length=1, max_length=32)
    to_zone: str = Field(min_length=1, max_length=32)
    selection: str = Field(min_length=1, max_length=32)


class MoveTopEffect(_Strict):
    """Move ``count`` cards from the top of one zone to another.

    ``count`` is explicit and bounded, so the compiler can unroll the move into
    a finite sequence of ``zones.top`` + ``zones.move`` nodes. There is no
    unbounded "empty this pile" loop.
    """

    kind: Literal["move_top"] = "move_top"
    from_zone: str = Field(min_length=1, max_length=32)
    to_zone: str = Field(min_length=1, max_length=32)
    count: int = Field(ge=1, le=MAX_SELECTION)


class CompareEffect(_Strict):
    kind: Literal["compare"] = "compare"
    zone: str = Field(min_length=1, max_length=32)
    left: int = Field(default=0, ge=0, le=MAX_SELECTION)
    right: int = Field(default=1, ge=0, le=MAX_SELECTION)
    rules: list[str] = Field(default_factory=list, max_length=8)


class AssignEffect(_Strict):
    kind: Literal["assign"] = "assign"
    variable: str = Field(min_length=1, max_length=32)
    value: Expr


Effect = Annotated[
    SelectEffect | MoveSelectionEffect | MoveTopEffect | CompareEffect | AssignEffect,
    Field(discriminator="kind"),
]


class ActionSpec(_Strict):
    id: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")
    guard: Expr | None = None
    inputs: list[ActionInputSpec] = Field(default_factory=list, max_length=4)
    effects: list[Effect] = Field(default_factory=list, max_length=64)


class ScoreRule(_Strict):
    id: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")
    on_outcome: Literal["left", "right", "tie"]
    points: int = Field(default=1, ge=0, le=100)
    recipient: Literal["left", "right"] = "left"


class FlowSpec(_Strict):
    round_action: str = Field(min_length=1, max_length=32)
    start_seat: Literal["seat0", "round_parity"] = "seat0"
    resolve: list[Effect] = Field(default_factory=list, max_length=64)


class TerminalSpec(_Strict):
    """When the game ends and how the winner is decided (ADR-0010).

    Any of ``max_rounds`` / ``max_actor_actions`` / ``score_reaches`` may be set;
    the conditions are OR-ed. At least one *hard bound* (rounds or actions) is
    required: a threshold alone is not a termination guarantee. ``score_reaches``
    means "any seat's score >= N", and the winner is always ``argmax(scores)``
    (all tied seats win under ``tie=allow``).
    """

    max_rounds: int | None = Field(default=None, ge=1, le=MAX_ROUNDS)
    max_actor_actions: int | None = Field(default=None, ge=1, le=MAX_STEP_LIMIT)
    score_reaches: int | None = Field(default=None, ge=1, le=1000)
    winner: Literal["highest_score"] = "highest_score"
    tie: Literal["allow"] = "allow"

    @model_validator(mode="after")
    def bounded(self) -> TerminalSpec:
        if self.max_rounds is None and self.max_actor_actions is None:
            raise ValueError("terminal_requires_a_hard_bound")
        return self


class MacroSpec(_Strict):
    """Local declarative macro. M2 parses the shape but compiles none of it."""

    id: str = Field(min_length=1, max_length=32)
    body: list[Effect] = Field(default_factory=list, max_length=16)


class ComposedRulesIR(_Strict):
    schema_version: Literal["0.5"] = "0.5"
    kind: Literal["composed"] = "composed"
    meta: MetaSpec
    requirements: list[RequirementClause] = Field(default_factory=list, max_length=64)
    players: PlayerSpec
    deck: DeckSpec
    zones: list[ZoneSpec] = Field(min_length=1, max_length=MAX_ZONES)
    setup: SetupSpec = Field(default_factory=SetupSpec)
    variables: list[VariableSpec] = Field(default_factory=list, max_length=16)
    actions: list[ActionSpec] = Field(min_length=1, max_length=MAX_ACTIONS)
    flow: FlowSpec
    scoring: list[ScoreRule] = Field(default_factory=list, max_length=16)
    terminal: TerminalSpec
    macros: list[MacroSpec] = Field(default_factory=list, max_length=MAX_MACROS)

    # -------------------------------------------------------------- helpers
    def zone(self, zone_id: str) -> ZoneSpec | None:
        return next((zone for zone in self.zones if zone.id == zone_id), None)

    def action(self, action_id: str) -> ActionSpec | None:
        return next((action for action in self.actions if action.id == action_id), None)

    def variable(self, name: str) -> VariableSpec | None:
        return next((item for item in self.variables if item.name == name), None)

    @property
    def variable_names(self) -> frozenset[str]:
        return frozenset(item.name for item in self.variables)

    def variable_types(self) -> dict[str, str]:
        return {item.name: item.type for item in self.variables}

    # ------------------------------------------------------------ validation
    @model_validator(mode="after")
    def executable(self) -> ComposedRulesIR:
        self._check_deck()
        self._check_zones()
        self._check_setup()
        self._check_variables()
        self._check_actions()
        self._check_scoring_and_flow()
        self._check_macros()
        return self

    def _check_deck(self) -> None:
        if len(set(self.deck.ranks)) != len(self.deck.ranks):
            raise ValueError("deck_ranks_must_be_unique")
        if len(set(self.deck.suits)) != len(self.deck.suits):
            raise ValueError("deck_suits_must_be_unique")
        unknown = sorted(set(self.deck.values) - set(self.deck.ranks))
        if unknown:
            raise ValueError("deck_values_unknown_ranks:" + ",".join(unknown))
        for rank, value in self.deck.values.items():
            if type(value) is not int or not 1 <= value <= 100:
                raise ValueError(f"deck_value_not_integer:{rank}")
        total = len(self.deck.ranks) * len(self.deck.suits) * self.deck.copies
        if total > MAX_CARDS:
            raise ValueError(f"deck_too_large:{total}>{MAX_CARDS}")

    def _check_zones(self) -> None:
        ids = [zone.id for zone in self.zones]
        if len(set(ids)) != len(ids):
            raise ValueError("zone_ids_must_be_unique")
        if not any(zone.scope == "shared" for zone in self.zones):
            raise ValueError("at_least_one_shared_zone_required")

    def _check_setup(self) -> None:
        seats = [deal for deal in self.setup.deals if deal.per_seat]
        if len(seats) != 1:
            raise ValueError("setup_requires_exactly_one_per_seat_deal")
        hand = seats[0]
        zone = self.zone(hand.zone)
        if zone is None:
            raise ValueError(f"setup_unknown_zone:{hand.zone}")
        if zone.scope != "player":
            raise ValueError(f"setup_hand_zone_must_be_player_scoped:{hand.zone}")
        for deal in self.setup.deals:
            if self.zone(deal.zone) is None:
                raise ValueError(f"setup_unknown_zone:{deal.zone}")
        if self.setup.stock_zone is not None:
            stock = self.zone(self.setup.stock_zone)
            if stock is None or stock.scope != "shared":
                raise ValueError(f"setup_stock_zone_must_be_shared:{self.setup.stock_zone}")
        # The deck must cover every dealt card.
        dealt = hand.count * self.players.count + sum(
            deal.count for deal in self.setup.deals if not deal.per_seat)
        total = len(self.deck.ranks) * len(self.deck.suits) * self.deck.copies
        if dealt > total:
            raise ValueError(f"setup_deals_exceed_deck:{dealt}>{total}")

    def _check_variables(self) -> None:
        names = [item.name for item in self.variables]
        if len(set(names)) != len(names):
            raise ValueError("variable_names_must_be_unique")
        for item in self.variables:
            if item.name in RESERVED_STATE_NAMES:
                raise ValueError(f"variable_name_reserved:{item.name}")
            if item.type == "integer" and not isinstance(item.initial, int):
                raise ValueError(f"variable_initial_type_mismatch:{item.name}")
            if item.type == "boolean" and not isinstance(item.initial, bool):
                raise ValueError(f"variable_initial_type_mismatch:{item.name}")
            if item.type == "string" and not isinstance(item.initial, str):
                raise ValueError(f"variable_initial_type_mismatch:{item.name}")
            if item.type == "integer_list" and not (
                    isinstance(item.initial, list)
                    and all(isinstance(value, int) for value in item.initial)):
                raise ValueError(f"variable_initial_type_mismatch:{item.name}")

    def _check_actions(self) -> None:
        ids = [action.id for action in self.actions]
        if len(set(ids)) != len(ids):
            raise ValueError("action_ids_must_be_unique")
        for action in self.actions:
            if action.guard is not None:
                validate_expression(action.guard, self.variable_names)
                guard_type = infer_type(action.guard, self.variable_types())
                if guard_type not in {BOOLEAN, ANY_TYPE}:
                    raise ValueError(
                        f"guard_type_mismatch:{action.id}:{guard_type}")
            self._check_action_effects(action)

    def _check_action_effects(self, action: ActionSpec) -> None:
        inputs = {item.id: item for item in action.inputs}
        if len(inputs) != len(action.inputs):
            raise ValueError(f"action_input_ids_must_be_unique:{action.id}")
        seen_selections: set[str] = set()
        for effect in action.effects:
            if isinstance(effect, SelectEffect):
                if effect.input not in inputs:
                    raise ValueError(f"select_unknown_input:{action.id}:{effect.input}")
                if effect.result in seen_selections:
                    raise ValueError(f"select_duplicate_result:{action.id}:{effect.result}")
                seen_selections.add(effect.result)
            elif isinstance(effect, MoveSelectionEffect):
                for zone_id in (effect.from_zone, effect.to_zone):
                    if self.zone(zone_id) is None:
                        raise ValueError(f"move_unknown_zone:{action.id}:{zone_id}")
                if effect.selection not in seen_selections:
                    raise ValueError(f"move_unknown_selection:{action.id}:{effect.selection}")
            elif isinstance(effect, MoveTopEffect):
                for zone_id in (effect.from_zone, effect.to_zone):
                    zone = self.zone(zone_id)
                    if zone is None:
                        raise ValueError(f"move_top_unknown_zone:{action.id}:{zone_id}")
                    if zone.scope != "shared":
                        raise ValueError(f"move_top_requires_shared_zone:{action.id}:{zone_id}")
            elif isinstance(effect, AssignEffect):
                variable = self.variable(effect.variable)
                if variable is None:
                    raise ValueError(f"assign_unknown_variable:{action.id}:{effect.variable}")
                validate_expression(effect.value, self.variable_names)
                actual = infer_type(effect.value, self.variable_types())
                if not check_assignable(actual, variable.type):
                    raise ValueError(
                        f"assign_type_mismatch:{action.id}:{effect.variable}:{actual}!={variable.type}")
            elif isinstance(effect, CompareEffect):
                raise ValueError(f"compare_is_a_flow_resolve_effect:{action.id}")
        for item in action.inputs:
            zone = self.zone(item.zone)
            if zone is None:
                raise ValueError(f"input_unknown_zone:{action.id}:{item.zone}")
            if item.scope == "actor" and zone.scope != "player":
                raise ValueError(f"actor_input_requires_player_zone:{action.id}:{item.zone}")
            if item.scope == "shared" and zone.scope != "shared":
                raise ValueError(f"shared_input_requires_shared_zone:{action.id}:{item.zone}")
            if item.max_count < item.min_count:
                raise ValueError(f"input_bounds_invalid:{action.id}:{item.id}")

    def _check_scoring_and_flow(self) -> None:
        rules = {rule.id for rule in self.scoring}
        if len(rules) != len(self.scoring):
            raise ValueError("scoring_rule_ids_must_be_unique")
        if self.action(self.flow.round_action) is None:
            raise ValueError(f"flow_round_action_not_declared:{self.flow.round_action}")
        for effect in self.flow.resolve:
            if isinstance(effect, CompareEffect):
                if self.zone(effect.zone) is None:
                    raise ValueError(f"compare_unknown_zone:{effect.zone}")
                if effect.left == effect.right:
                    raise ValueError(f"compare_needs_two_positions:{effect.zone}")
                for rule in effect.rules:
                    if rule not in rules:
                        raise ValueError(f"compare_unknown_scoring_rule:{rule}")
                self._check_compare_capacity(effect)
            elif isinstance(effect, MoveTopEffect):
                for zone_id in (effect.from_zone, effect.to_zone):
                    zone = self.zone(zone_id)
                    if zone is None:
                        raise ValueError(f"move_top_unknown_zone:{zone_id}")
                    if zone.scope != "shared":
                        raise ValueError(f"move_top_requires_shared_zone:{zone_id}")
            elif isinstance(effect, SelectEffect | MoveSelectionEffect | AssignEffect):
                raise ValueError(f"resolve_effect_not_allowed:{effect.kind}")
        if not self.flow.resolve:
            raise ValueError("flow_resolve_required")
        # A round-scoring rule must not end mid-round on the action budget: the
        # budget is checked only after resolve (ADR-0010 rule 8), so it must land
        # on a round boundary.
        if any(isinstance(effect, CompareEffect) for effect in self.flow.resolve):
            budget = self.terminal.max_actor_actions
            if budget is not None and budget % self.players.count != 0:
                raise ValueError("terminal_action_budget_must_align_with_rounds")

    def _check_compare_capacity(self, effect: CompareEffect) -> None:
        """Guarantee the compared positions are filled at *every* resolve.

        Models the compare zone's card count across rounds with sound bounds:
        additions use the selection ``min_count`` (a lower bound), removals use
        ``max_count`` (an upper bound), plus the exact ``move_top`` amounts. The
        setup's shared deal into the zone counts as the round-0 starting stock.
        The minimum over all rounds must reach ``max(left, right) + 1``.
        """
        action = self.action(self.flow.round_action)
        select_by_result = {item.result: item for item in action.effects
                            if isinstance(item, SelectEffect)}
        bounds = {item.id: (item.min_count, item.max_count) for item in action.inputs}
        per_action_in = per_action_out = 0
        for item in action.effects:
            if not isinstance(item, MoveSelectionEffect):
                continue
            select = select_by_result.get(item.selection)
            if select is None:
                continue
            low, high = bounds.get(select.input, (0, 0))
            if item.to_zone == effect.zone:
                per_action_in += low
            if item.from_zone == effect.zone:
                per_action_out += high
        turn_in = per_action_in * self.players.count
        turn_out = per_action_out * self.players.count

        initial = sum(deal.count for deal in self.setup.deals
                      if not deal.per_seat and deal.zone == effect.zone)
        resolve_in = sum(item.count for item in self.flow.resolve
                         if isinstance(item, MoveTopEffect) and item.to_zone == effect.zone)
        resolve_out = sum(item.count for item in self.flow.resolve
                          if isinstance(item, MoveTopEffect) and item.from_zone == effect.zone)
        pre_in = pre_out = 0
        for item in self.flow.resolve:
            if item is effect:
                break
            if isinstance(item, MoveTopEffect):
                if item.to_zone == effect.zone:
                    pre_in += item.count
                if item.from_zone == effect.zone:
                    pre_out += item.count

        first_round = initial + turn_in - turn_out + pre_in - pre_out
        round_net = (turn_in - turn_out) + (resolve_in - resolve_out)
        if round_net >= 0:
            minimum = first_round
        else:
            # The worst case is every allowed round. A round bound is exact; an
            # action bound converts to floor(actions / players) full rounds.
            budget = self.terminal.max_rounds
            if budget is None:
                budget = max(1, (self.terminal.max_actor_actions or 1) // self.players.count)
            minimum = first_round + (budget - 1) * round_net
        needed = max(effect.left, effect.right) + 1
        if minimum < needed:
            raise ValueError(
                f"compare_zone_too_small:{effect.zone}:{minimum}<{needed}")

    def _check_macros(self) -> None:
        if self.macros:
            raise ValueError("macros_not_supported_in_m2")
