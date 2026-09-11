"""Machine-enforced architecture invariants.

The v0.3 branch regrew a second execution path, a pile of game-specific engines
and a template-copying "agent" while every unit test stayed green. Conventions
alone did not stop it, so the rules that matter are executable here.

If one of these tests fails, the fix is an architecture decision (and an update
to this file), never a quick patch to make the test pass.
"""
from __future__ import annotations

import ast
from pathlib import Path

from pocker_agent.core import core_registry

CORE_DIR = Path(__file__).resolve().parents[1] / "src" / "pocker_agent" / "core"
APP_PATH = Path(__file__).resolve().parents[1] / "src" / "pocker_agent" / "app.py"

# Deliberate ratchets. Raising a budget is a reviewable architecture decision,
# not a side effect of adding a feature.
CORE_TOOL_BUDGET = 16

# Names that signal "this is a whole game's grammar", not an orthogonal atom.
# Explicit game titles only: generic mechanisms (score_settle, exact_expression)
# are allowed; a per-game ranking tool is not.
GAME_TITLE_MARKERS = ("doudizhu", "holdem", "shedding", "blackjack", "arithmetic",
                      "uno", "mahjong", "bridge", "rummy", "poker", "_hand_rank")

# Modules that belong to the legacy (v0.2/v0.3) execution paths.
LEGACY_ENGINE_MODULES = {"engine", "family_engines", "doudizhu_engine",
                         "holdem_engine", "plugin_engine", "flow_runtime",
                         "rule_executor", "executors"}


def _core_modules() -> list[Path]:
    return sorted(CORE_DIR.glob("*.py"))


def _imported_roots(module: Path) -> set[str]:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.lstrip(".").split(".")[-1])
        elif isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[-1] for alias in node.names)
    return roots


def test_core_is_a_single_execution_path():
    """core/ must never import a legacy engine or a second interpreter."""
    for module in _core_modules():
        leaked = _imported_roots(module) & LEGACY_ENGINE_MODULES
        assert not leaked, f"{module.name} imports legacy execution module(s): {sorted(leaked)}"


def test_app_serves_only_the_core_execution_path():
    """The v0.4 app must not reach the legacy family/plugin engines."""
    leaked = _imported_roots(APP_PATH) & LEGACY_ENGINE_MODULES
    assert not leaked, f"app.py imports legacy execution module(s): {sorted(leaked)}"


def test_core_has_exactly_one_interpreter():
    """Exactly one class in core may define the step/advance state machine."""
    classes: list[str] = []
    for module in _core_modules():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
                if {"step", "advance"} <= methods:
                    classes.append(f"{module.name}:{node.name}")
    assert classes == ["interpreter.py:Interpreter"], classes


def test_no_game_specific_tool_in_the_core_registry():
    """Game grammars belong in plans/axes, never as a registry entry."""
    for name in core_registry().names():
        offending = [marker for marker in GAME_TITLE_MARKERS if marker in name]
        assert not offending, f"core tool '{name}' looks game-specific ({offending})"


def test_core_tool_budget_is_a_ratchet():
    assert len(core_registry().names()) <= CORE_TOOL_BUDGET, (
        "core tool budget exceeded: promote to an axis or justify a raise in review")


def test_every_core_operation_declares_its_contract():
    for tool in core_registry().export():
        assert tool["operations"], tool["name"]
        for operation in tool["operations"]:
            assert isinstance(operation["effects"], list)
            assert operation["failure"] in {"rollback", "reject_only"}
            assert operation["deterministic"] is True
            assert isinstance(operation["params"], list)
            assert isinstance(operation["requires"], list)
            assert isinstance(operation["ensures"], list)


def test_only_state_tool_may_write_arbitrary_keys():
    wildcard = [tool["name"] for tool in core_registry().export()
                if any("*" in operation["effects"] for operation in tool["operations"])]
    assert wildcard == ["state"], wildcard


def test_every_registered_tool_is_used_by_a_reference_plan():
    """No dead tools: an unused registration is bloat and must be removed."""
    from pocker_agent.core.reference import REFERENCE_GAMES

    used: set[str] = set()
    for game in REFERENCE_GAMES.values():
        used |= {binding.name for binding in game.build().tools}
    assert set(core_registry().names()) == used, (
        f"unused core tools: {sorted(set(core_registry().names()) - used)}")
