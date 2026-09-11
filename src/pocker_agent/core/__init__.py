"""v0.4 execution core.

One deterministic path: a validated ``GamePlan`` is interpreted by
``Interpreter``, which dispatches only declared tool operations from a
``ToolRegistry``. ``playtest`` is the gate that a plan must pass before it can
be shown as playable.
"""
from .capability import AXES, Capability, CapabilityReport, capability_check, capability_matrix
from .cards import CardRef, decode, encode
from .contracts import Observation, OperationSpec, ToolError, ToolRegistry, ToolSpec
from .corpus import coverage_report, load_corpus
from .interpreter import Interpreter
from .invariants import card_conservation, never_finishes_without_winners_or_scores
from .ir import (
                 ArithmeticIR,
                 BlackjackIR,
                 GoFishIR,
                 PokerIR,
                 RulesIR,
                 SheddingIR,
                 WarIR,
                 WhistIR,
                 check_ir,
                 host_compile,
                 is_host_compiled,
                 parse_ir,
                 required_axes,
)
from .plan import FlowCase, FlowNode, GamePlan, ToolBinding, ToolCall
from .plans import (
                 arithmetic_plan,
                 blackjack_plan,
                 crazy_eights_plan,
                 five_card_poker_plan,
                 go_fish_plan,
                 war_plan,
                 whist_plan,
)
from .playtest import PlaytestReport, boundary_first, card_first, first_legal, playtest, random_legal, resilient_first
from .policy import bet_first, bot_action, run_bots
from .reference import REFERENCE_GAMES, build_plan, ensure_playtested, list_reference_games
from .registry import core_registry
from .session import Session, SessionStore

__all__ = [
                 "AXES",
                 "REFERENCE_GAMES",
                 "ArithmeticIR",
                 "BlackjackIR",
                 "Capability",
                 "CapabilityReport",
                 "CardRef",
                 "FlowCase",
                 "FlowNode",
                 "GamePlan",
                 "GoFishIR",
                 "Interpreter",
                 "Observation",
                 "OperationSpec",
                 "PlaytestReport",
                 "PokerIR",
                 "RulesIR",
                 "Session",
                 "SessionStore",
                 "SheddingIR",
                 "ToolBinding",
                 "ToolCall",
                 "ToolError",
                 "ToolRegistry",
                 "ToolSpec",
                 "WarIR",
                 "WhistIR",
                 "arithmetic_plan",
                 "bet_first",
                 "blackjack_plan",
                 "bot_action",
                 "boundary_first",
                 "build_plan",
                 "capability_check",
                 "capability_matrix",
                 "card_conservation",
                 "card_first",
                 "check_ir",
                 "core_registry",
                 "coverage_report",
                 "crazy_eights_plan",
                 "decode",
                 "encode",
                 "ensure_playtested",
                 "first_legal",
                 "five_card_poker_plan",
                 "go_fish_plan",
                 "host_compile",
                 "is_host_compiled",
                 "list_reference_games",
                 "load_corpus",
                 "never_finishes_without_winners_or_scores",
                 "parse_ir",
                 "playtest",
                 "random_legal",
                 "required_axes",
                 "resilient_first",
                 "run_bots",
                 "war_plan",
                 "whist_plan",
]
