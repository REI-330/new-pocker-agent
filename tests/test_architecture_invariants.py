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

import pytest

from pocker_agent.core import ToolError, core_registry

CORE_DIR = Path(__file__).resolve().parents[1] / "src" / "pocker_agent" / "core"
APP_PATH = Path(__file__).resolve().parents[1] / "src" / "pocker_agent" / "app.py"

# Deliberate ratchets. Raising a budget is a reviewable architecture decision,
# not a side effect of adding a feature.
CORE_TOOL_BUDGET = 18

# Names that signal "this is a whole game's grammar", not an orthogonal atom.
# Explicit game titles only: generic mechanisms (score_settle, exact_expression)
# are allowed; a per-game ranking tool is not.
GAME_TITLE_MARKERS = ("doudizhu", "holdem", "shedding", "blackjack", "arithmetic",
                      "uno", "mahjong", "bridge", "rummy", "poker", "_hand_rank")

# Modules that belong to the legacy (v0.2/v0.3) execution paths.
LEGACY_ENGINE_MODULES = {"engine", "family_engines", "doudizhu_engine",
                         "holdem_engine", "plugin_engine", "flow_runtime",
                         "rule_executor", "executors"}


def core_modules(root: Path = CORE_DIR) -> list[Path]:
    """Every module under `core/`, at any depth.

    Recursive on purpose: these checks are what keep a second execution path or a
    legacy engine from reappearing, and G2 adds subpackages here.
    """
    return sorted(root.rglob("*.py"))


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
    for module in core_modules():
        leaked = _imported_roots(module) & LEGACY_ENGINE_MODULES
        assert not leaked, f"{module.name} imports legacy execution module(s): {sorted(leaked)}"


def test_app_serves_only_the_core_execution_path():
    """The v0.4 app must not reach the legacy family/plugin engines."""
    leaked = _imported_roots(APP_PATH) & LEGACY_ENGINE_MODULES
    assert not leaked, f"app.py imports legacy execution module(s): {sorted(leaked)}"


def test_core_has_exactly_one_interpreter():
    """Exactly one class in core may define the step/advance state machine."""
    classes: list[str] = []
    for module in core_modules():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
                if {"step", "advance"} <= methods:
                    classes.append(f"{module.relative_to(CORE_DIR).as_posix()}:{node.name}")
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


def test_failure_mode_is_derived_from_effects_and_cannot_contradict_them():
    """`reject_only` means "writes nothing", so it must not declare effects."""
    for tool in core_registry().export():
        for operation in tool["operations"]:
            writes = bool(operation["effects"])
            assert (operation["failure"] == "rollback") is writes, (
                f"{tool['name']}.{operation['name']} declares "
                f"failure={operation['failure']} with effects={operation['effects']}")

    from pocker_agent.core.contracts import OperationSpec

    with pytest.raises(ToolError, match="reject_only_must_not_write_state"):
        OperationSpec("bad", effects=("hands",), failure="reject_only")
    assert OperationSpec("reader").failure == "reject_only"          # derived
    assert OperationSpec("writer", effects=("hands",)).failure == "rollback"


def test_only_state_tool_may_write_arbitrary_keys():
    wildcard = [tool["name"] for tool in core_registry().export()
                if any("*" in operation["effects"] for operation in tool["operations"])]
    assert wildcard == ["state"], wildcard


def test_the_architecture_scan_covers_new_subpackages(tmp_path):
    """New directories must not dodge the single-interpreter / legacy-engine scan.

    G2 splits `core/` into subpackages (rules/, compiler/, verify/); a
    `core/*.py`-only scan would silently stop covering them.
    """
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "engine.py").write_text("import executors\n", encoding="utf-8")
    scanned = core_modules(tmp_path)
    assert [path.name for path in scanned] == ["engine.py"]
    assert _imported_roots(scanned[0]) & LEGACY_ENGINE_MODULES == {"executors"}
    # the real package is walked recursively, not just its top level
    assert core_modules() == sorted(CORE_DIR.rglob("*.py"))


def test_every_axis_claim_points_at_a_real_operation():
    """A coverage label is a claim about the host, so it must be checkable.

    The matrix used to name `tool.shedding` (no such tool) and
    `tool.trick.team_winners` (no such operation) on `stable` axes -- which is
    how a corpus label can pass for a runnable capability.
    """
    from pocker_agent.core.capability import AXES, Capability, mechanism_problems

    assert mechanism_problems(core_registry()) == []

    # prove the audit is not vacuous: it must catch the two historical claims
    stale = (Capability("pattern_lang", "t", "stable", ("tool.shedding",)),
             Capability("team", "t", "stable", ("tool.trick.team_winners",)))
    found = mechanism_problems(core_registry(), stale)
    assert {(item["mechanism"], item["problem"]) for item in found} == {
        ("tool.shedding", "unknown_tool"),
        ("tool.trick.team_winners", "unknown_operation")}

    # non-registry references are deliberately not audited here
    non_tool = (Capability("sequential_turn", "t", "stable",
                           ("plan.wait", "state.current_player", "view(viewer)")),)
    assert mechanism_problems(core_registry(), non_tool) == []
    assert len(AXES) == 18


def test_stable_axes_that_over_claim_name_their_limit():
    """`turn_adapter` used to read "... / bidding / priority" with neither."""
    from pocker_agent.core.capability import AXIS_BY_ID

    turn = AXIS_BY_ID["turn_adapter"]
    assert "bidding" not in turn.title, turn.title
    assert turn.note, "an axis whose title dropped a claim must say why"
    assert AXIS_BY_ID["hidden_draw"].note, "hidden_draw covers rank asks only"


def test_a_stable_axis_must_name_a_real_mechanism():
    """`stable` claims the host can compile AND verify a game needing this axis.

    A placeholder mechanism (`axis.x` / `protocol.x`) is a promise, not an
    implementation, so it can never back a covered axis. Without this check the
    axis table drifts into claiming capability it does not have.
    """
    from pocker_agent.core.capability import AXES

    placeholders = ("axis.", "protocol.")
    for axis in AXES:
        if not axis.covered:
            continue
        assert axis.mechanisms, f"stable axis '{axis.id}' names no mechanism"
        fake = [m for m in axis.mechanisms if m.startswith(placeholders)]
        assert not fake, f"axis '{axis.id}' is stable on a placeholder: {fake}"


def test_every_uncovered_axis_says_what_is_missing():
    """A gap without an explanation becomes a silent failure."""
    from pocker_agent.core.capability import AXES

    for axis in AXES:
        if axis.covered:
            continue
        assert axis.note.strip(), f"axis '{axis.id}' is {axis.status} but has no note"


def test_the_macro_axis_stays_a_gap_until_the_agent_can_author_macros():
    """The macro machinery exists, yet the agent-facing capability does not.

    ``core/macros.py`` inlines macros at build time and ``match_turn`` is reused
    by two plans, so the host side is done. What is missing is a meta-tool that
    lets the model *generate* a macro, which is exactly what the axis names.
    """
    from pocker_agent.agent.meta_tools import TOOL_SCHEMAS
    from pocker_agent.core.capability import AXIS_BY_ID

    assert AXIS_BY_ID["macro"].status == "planned"
    assert not any("macro" in tool["name"] for tool in TOOL_SCHEMAS), (
        "the macro axis is only planned while the model has no macro meta-tool; "
        "if one was added, promote the axis (and say so)")


def test_the_shipped_macro_library_owes_no_promotion():
    """Section 6.4 is a live rule, not a one-off review note."""
    from pocker_agent.core import PLAN_MACROS, default_macros, promotion_report

    assert promotion_report(PLAN_MACROS, default_macros()) == []


# Composed games that passed the design service are legitimate consumers too.
# G2 records them here instead of forcing a Python reference game per mechanism
# (section 6.4 of the standards). Still empty: M2 produces the first verified
# composition. Entries must be real tool names -- checked below.
COMPOSITION_CONSUMERS: tuple[tuple[str, tuple[str, ...]], ...] = ()


def test_every_registered_tool_is_used_by_a_reference_plan_or_a_composition():
    """No dead tools: an unused registration is bloat and must be removed."""
    from pocker_agent.core.reference import REFERENCE_GAMES

    used: set[str] = set()
    for game in REFERENCE_GAMES.values():
        used |= {binding.name for binding in game.build().tools}
    for sample, tools in COMPOSITION_CONSUMERS:
        assert sample, "a composition consumer must name the sample that uses it"
        assert set(tools) <= set(core_registry().names()), (sample, tools)
        used |= set(tools)
    assert set(core_registry().names()) == used, (
        f"unused core tools: {sorted(set(core_registry().names()) - used)}")
