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
from .playtest import (PlaytestReport, boundary_first, first_legal, playtest,
                      random_legal)
from .plans import arithmetic_plan
from .registry import core_registry

__all__ = [
    "CardRef", "decode", "encode",
    "Observation", "OperationSpec", "ToolError", "ToolRegistry", "ToolSpec",
    "Interpreter",
    "FlowCase", "FlowNode", "GamePlan", "ToolBinding", "ToolCall",
    "PlaytestReport", "playtest", "first_legal", "random_legal", "boundary_first",
    "arithmetic_plan", "core_registry",
]
