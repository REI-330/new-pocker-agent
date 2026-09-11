"""v0.4 execution core.

One deterministic path: a validated ``GamePlan`` is interpreted by
``Interpreter``, which dispatches only declared tool operations from a
``ToolRegistry``. ``playtest`` is the gate that a plan must pass before it can
be shown as playable.

The old ``engine.py`` / ``family_engines.py`` chain is intentionally not
imported here; this package is the strangler target.
"""
from .capability import AXES, Capability, CapabilityReport, capability_check, capability_matrix
from .cards import CardRef, decode, encode
from .contracts import Observation, OperationSpec, ToolError, ToolRegistry, ToolSpec
from .corpus import coverage_report, load_corpus
from .interpreter import Interpreter
from .plan import FlowCase, FlowNode, GamePlan, ToolBinding, ToolCall
from .plans import arithmetic_plan
from .playtest import PlaytestReport, boundary_first, first_legal, playtest, random_legal
from .reference import REFERENCE_GAMES, build_plan, ensure_playtested, list_reference_games
from .registry import core_registry
from .session import Session, SessionStore

__all__ = [
    "AXES",
    "REFERENCE_GAMES",
    "Capability",
    "CapabilityReport",
    "CardRef",
    "FlowCase",
    "FlowNode",
    "GamePlan",
    "Interpreter",
    "Observation",
    "OperationSpec",
    "PlaytestReport",
    "Session",
    "SessionStore",
    "ToolBinding",
    "ToolCall",
    "ToolError",
    "ToolRegistry",
    "ToolSpec",
    "arithmetic_plan",
    "boundary_first",
    "build_plan",
    "capability_check",
    "capability_matrix",
    "core_registry",
    "coverage_report",
    "decode",
    "encode",
    "ensure_playtested",
    "first_legal",
    "list_reference_games",
    "load_corpus",
    "playtest",
    "random_legal",
]
