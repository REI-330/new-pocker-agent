"""v0.4 execution core.

One deterministic path: a validated ``GamePlan`` is interpreted by
``Interpreter``, which dispatches only declared tool operations from a
``ToolRegistry``. ``playtest`` is the gate that a plan must pass before it can
be shown as playable.

The old ``engine.py`` / ``family_engines.py`` chain is intentionally not
imported here; this package is the strangler target.
"""
from .cards import CardRef, decode, encode
from .contracts import Observation, OperationSpec, ToolError, ToolRegistry, ToolSpec
from .interpreter import Interpreter
from .plan import FlowCase, FlowNode, GamePlan, ToolBinding, ToolCall
from .plans import arithmetic_plan
from .playtest import (
                      PlaytestReport,
                      boundary_first,
                      first_legal,
                      playtest,
                      random_legal,
)
from .registry import core_registry

__all__ = [
                      "CardRef",
                      "FlowCase",
                      "FlowNode",
                      "GamePlan",
                      "Interpreter",
                      "Observation",
                      "OperationSpec",
                      "PlaytestReport",
                      "ToolBinding",
                      "ToolCall",
                      "ToolError",
                      "ToolRegistry",
                      "ToolSpec",
                      "arithmetic_plan",
                      "boundary_first",
                      "core_registry",
                      "decode",
                      "encode",
                      "first_legal",
                      "playtest",
                      "random_legal",
]
