"""v0.4 execution core.

One deterministic path: a validated ``GamePlan`` is interpreted by
``Interpreter``, which dispatches only declared tool operations from a
``ToolRegistry``. ``playtest`` is the gate that a plan must pass before it can
be shown as playable.
"""
from .capability import AXES, Capability, CapabilityReport, capability_check, capability_matrix
from .cards import CardRef, decode, encode
from .compositions import (
                     composition_samples,
                     exchange_compare_composition,
                     exchange_strategy,
                     exchange_suit_score_composition,
)
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
                     UnoIR,
                     WarIR,
                     WhistIR,
                     check_ir,
                     host_compile,
                     is_host_compiled,
                     parse_ir,
                     required_axes,
)
from .macro_library import MATCH_TURN, PLAN_MACROS, default_macros
from .macros import MacroRegistry, MacroSpec, expand_macro, promotion_report, validate_macro, wire
from .plan import ActionDescriptor, ActionInputDescriptor, FlowCase, FlowNode, GamePlan, ToolBinding, ToolCall
from .plans import (
                     arithmetic_plan,
                     blackjack_plan,
                     crazy_eights_plan,
                     five_card_poker_plan,
                     go_fish_plan,
                     uno_plan,
                     war_plan,
                     whist_plan,
)
from .playtest import PlaytestReport, boundary_first, card_first, first_legal, playtest, random_legal, resilient_first
from .policy import bet_first, bot_action, composed_action, run_bots
from .reference import REFERENCE_GAMES, build_plan, ensure_playtested, list_reference_games
from .registry import core_registry
from .rules import (
                     CompiledRules,
                     CompileError,
                     ComposedRulesIR,
                     CompositionReport,
                     compile_composed,
                     derive_requirements,
                     ir_hash,
                     resolve_composition,
)
from .session import Session, SessionStore
from .zone_tools import ZonesTool
from .zones import (
                     ZONES_KEY,
                     all_cards,
                     apply_moves,
                     assert_unique_ownership,
                     find_zone_of,
                     ownership_problems,
                     select_cards,
                     top_card,
                     zone_cards,
                     zone_table,
)

__all__ = [
                     "AXES",
                     "MATCH_TURN",
                     "PLAN_MACROS",
                     "REFERENCE_GAMES",
                     "ZONES_KEY",
                     "ActionDescriptor",
                     "ActionInputDescriptor",
                     "ArithmeticIR",
                     "BlackjackIR",
                     "Capability",
                     "CapabilityReport",
                     "CardRef",
                     "CompileError",
                     "CompiledRules",
                     "ComposedRulesIR",
                     "CompositionReport",
                     "FlowCase",
                     "FlowNode",
                     "GamePlan",
                     "GoFishIR",
                     "Interpreter",
                     "MacroRegistry",
                     "MacroSpec",
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
                     "UnoIR",
                     "WarIR",
                     "WhistIR",
                     "ZonesTool",
                     "all_cards",
                     "apply_moves",
                     "arithmetic_plan",
                     "assert_unique_ownership",
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
                     "compile_composed",
                     "composed_action",
                     "composition_samples",
                     "core_registry",
                     "coverage_report",
                     "crazy_eights_plan",
                     "decode",
                     "default_macros",
                     "derive_requirements",
                     "encode",
                     "ensure_playtested",
                     "exchange_compare_composition",
                     "exchange_strategy",
                     "exchange_suit_score_composition",
                     "expand_macro",
                     "find_zone_of",
                     "first_legal",
                     "five_card_poker_plan",
                     "go_fish_plan",
                     "host_compile",
                     "ir_hash",
                     "is_host_compiled",
                     "list_reference_games",
                     "load_corpus",
                     "never_finishes_without_winners_or_scores",
                     "ownership_problems",
                     "parse_ir",
                     "playtest",
                     "promotion_report",
                     "random_legal",
                     "required_axes",
                     "resilient_first",
                     "resolve_composition",
                     "run_bots",
                     "select_cards",
                     "top_card",
                     "uno_plan",
                     "validate_macro",
                     "war_plan",
                     "whist_plan",
                     "wire",
                     "zone_cards",
                     "zone_table",
]
