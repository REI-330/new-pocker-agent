"""v0.4 core: one interpreter, declared tool contracts, transactional rollback."""
import copy

import pytest

from pocker_agent.core import (
    GamePlan,
    Interpreter,
    ToolError,
    ToolSpec,
    boundary_first,
    core_registry,
    first_legal,
    playtest,
)
from pocker_agent.core.contracts import OperationSpec, ToolRegistry

RANK_VALUES = {"A": 1, **{str(n): n for n in range(2, 11)}, "J": 11, "Q": 12, "K": 13}


def test_a_tool_reading_missing_state_is_a_contract_violation_not_a_crash():
    """Tools index shared state directly; a missing key must not escape as KeyError.

    A structurally valid plan can still ask a tool for state that was never dealt
    (a poker plan without `committed`, for instance). That is a plan bug, so it has
    to arrive as a rejectable ToolError with a rollback -- otherwise it propagates
    through playtest and out of the agent loop as an HTTP 500.
    """
    plan = GamePlan.model_validate({
        "schema_version": "0.4", "game_kind": "poker", "players": 2,
        "tools": [{"name": "betting", "config": {"min_raise": 10}}],
        "initial": {"stacks": [100, 100]},          # no `committed`
        "entry": "start",
        "nodes": {"start": {"kind": "wait", "inputs": {"go": "legal"}},
                  "legal": {"kind": "call", "next": "hold", "action": {
                      "tool": "betting", "operation": "legal",
                      "args": {"state": "$state"},
                      "result_key": "legal_bets"}},
                  "hold": {"kind": "wait", "inputs": {"poke": "hold"}}},
        "step_limit": 64})
    interpreter = Interpreter(plan, core_registry(), seed=0)
    interpreter.setup()
    before = copy.deepcopy(interpreter.state)
    where = interpreter.pc
    with pytest.raises(ToolError, match="tool_state_missing:betting\\.legal"):
        interpreter.step("go")
    assert interpreter.state == before, "a rejected call must roll back fully"
    assert interpreter.pc == where, "the flow pointer must roll back too"


def test_a_crashing_plan_fails_playtest_instead_of_raising():
    """playtest is the gate, so it must report a bad plan, never explode on it."""
    plan = GamePlan.model_validate({
        "schema_version": "0.4", "game_kind": "poker", "players": 2,
        "tools": [{"name": "betting", "config": {"min_raise": 10}}],
        "initial": {"stacks": [100, 100]}, "entry": "start",
        "nodes": {"start": {"kind": "wait", "inputs": {"go": "legal"}},
                  "legal": {"kind": "call", "next": "hold", "action": {
                      "tool": "betting", "operation": "legal",
                      "args": {"state": "$state"},
                      "result_key": "legal_bets"}},
                  "hold": {"kind": "wait", "inputs": {"poke": "hold"}}},
        "step_limit": 64})
    report = playtest(plan, core_registry(), first_legal, seeds=(0,))
    assert report.ok is False
    assert any("tool_state_missing" in failure for failure in report.failures), report.failures


def arithmetic(**overrides):
    from pocker_agent.core import arithmetic_plan

    base = {"target": 24, "max_rounds": 3, "card_count": 4,
            "operations": ("+", "-", "*", "/"), "fractional": True,
            "rank_values": RANK_VALUES, "deal_mode": "solvable"}
    base.update(overrides)
    return arithmetic_plan(**base)


def solve_strategy(interpreter):
    actions = interpreter.legal_actions()
    if "submit_expression" in actions:
        answer = interpreter.tools["exact_expression"].solve(interpreter.state["numbers"])
        if answer is None:
            return ("no_solution", {})
        return ("submit_expression", {"expression": answer})
    return (actions[0], {}) if actions else None


def give_up_strategy(interpreter):
    return ("give_up", {})


def false_no_solution_strategy(interpreter):
    return ("no_solution", {})


def table_invariant(interpreter):
    table = interpreter.state.get("table")
    numbers = interpreter.state.get("numbers")
    if table is not None and numbers is not None:
        assert len(table) == len(numbers) == 4, "table/numbers size mismatch"
        assert interpreter.state["round"] <= interpreter.state["max_rounds"], "round overflow"


def run(plan, strategy, seed=7, registry=None):
    interpreter = Interpreter(plan, registry or core_registry(), seed=seed)
    interpreter.setup()
    for _ in range(1000):
        if interpreter.state["finished"]:
            return interpreter
        action, payload = strategy(interpreter)
        interpreter.step(action, **payload)
    raise AssertionError("game did not finish")


# --------------------------------------------------------------- plan schema

def test_plan_rejects_undeclared_tool():
    data = arithmetic().model_dump(mode="json")
    data["nodes"]["deal"]["action"]["tool"] = "not_registered"
    with pytest.raises(ValueError, match="plan_action_tool_not_declared"):
        GamePlan.model_validate(data)


def test_plan_rejects_missing_target():
    data = arithmetic().model_dump(mode="json")
    data["nodes"]["init"]["next"] = "does_not_exist"
    with pytest.raises(ValueError, match="plan_target_missing"):
        GamePlan.model_validate(data)


def test_registry_exports_machine_readable_contracts():
    exported = {tool["name"]: tool for tool in core_registry().export()}
    assert {"state", "logic", "exact_expression", "solvable_deal", "score_settle"} <= set(exported)
    operations = {op["name"] for op in exported["exact_expression"]["operations"]}
    assert operations == {"solve", "validate", "assert_unsolvable"}
    assert all(op["deterministic"] for op in exported["logic"]["operations"])


def test_registry_rejects_unknown_operation():
    with pytest.raises(ToolError, match="unknown_tool_operation"):
        core_registry().spec("logic").operation("eval")


def test_registry_is_a_whitelist_for_creation():
    with pytest.raises(ToolError, match="unknown_tool"):
        core_registry().create("os_shell")


def test_duplicate_registration_is_rejected():
    registry = core_registry()
    with pytest.raises(ToolError, match="tool_name_duplicate"):
        registry.register(ToolSpec("logic", lambda **_: None,
                                   (OperationSpec("evaluate"),)))


# ---------------------------------------------------------------- behaviour

def test_solve_strategy_scores_every_round_without_inventing_a_winner():
    interpreter = run(arithmetic(max_rounds=3), solve_strategy)
    assert interpreter.state["finished"]
    assert interpreter.state["scores"] == [3]
    assert interpreter.state["round"] == 3
    assert interpreter.state["winners"] == []          # a drill has no winner
    deals = [e for e in interpreter.events if e.get("operation") == "deal"]
    assert len(deals) == 3                              # one deal per round


def test_give_up_scores_zero_and_never_wins():
    interpreter = run(arithmetic(max_rounds=2), give_up_strategy)
    assert interpreter.state["scores"] == [0]
    assert interpreter.state["winners"] == []
    assert interpreter.state["finished"]


def test_false_no_solution_claim_is_rejected_transactionally():
    interpreter = Interpreter(arithmetic(), core_registry(), seed=7)
    interpreter.setup()
    before = copy.deepcopy(interpreter.serialize())
    with pytest.raises(ToolError, match="puzzle_is_solvable"):
        interpreter.step("no_solution")
    assert interpreter.serialize() == before           # state, events and pc unchanged
    assert interpreter.legal_actions() == ["submit_expression", "no_solution", "give_up"]


def test_wrong_answer_is_rejected_and_can_be_retried():
    interpreter = Interpreter(arithmetic(), core_registry(), seed=7)
    interpreter.setup()
    before = copy.deepcopy(interpreter.serialize())
    with pytest.raises(ToolError):
        interpreter.step("submit_expression", expression="24")
    assert interpreter.serialize() == before
    answer = interpreter.tools["exact_expression"].solve(interpreter.state["numbers"])
    interpreter.step("submit_expression", expression=answer)
    assert interpreter.state["scores"] == [1]


def test_unknown_operation_is_rejected_when_the_interpreter_is_built():
    data = arithmetic().model_dump(mode="json")
    data["nodes"]["submit"]["action"]["operation"] = "eval_code"
    plan = GamePlan.model_validate(data)  # shape is legal
    with pytest.raises(ToolError, match="unknown_tool_operation"):
        Interpreter(plan, core_registry(), seed=7)


def test_illegal_action_is_rejected():
    interpreter = Interpreter(arithmetic(), core_registry(), seed=7)
    interpreter.setup()
    with pytest.raises(ToolError, match="illegal_action"):
        interpreter.step("teleport")


def test_restore_is_exact_and_continues_the_same_game():
    first = Interpreter(arithmetic(), core_registry(), seed=7)
    first.setup()
    answer = first.tools["exact_expression"].solve(first.state["numbers"])
    first.step("submit_expression", expression=answer)

    restored = Interpreter.restore(first.serialize(), core_registry())
    assert restored.serialize() == first.serialize()

    for interpreter in (first, restored):
        while not interpreter.state["finished"]:
            action, payload = solve_strategy(interpreter)
            interpreter.step(action, **payload)
    assert first.serialize() == restored.serialize()


def test_setup_cannot_run_twice():
    interpreter = Interpreter(arithmetic(), core_registry(), seed=7)
    interpreter.setup()
    with pytest.raises(ToolError, match="game_already_started"):
        interpreter.setup()


# ----------------------------------------------------------------- playtest

def test_playtest_passes_for_the_reference_plan():
    report = playtest(arithmetic(), core_registry(), solve_strategy,
                      seeds=(0, 7, 23), invariants=(table_invariant,))
    assert report.ok, report.failures


def test_playtest_flags_a_policy_that_violates_the_contract():
    report = playtest(arithmetic(max_rounds=1), core_registry(), false_no_solution_strategy,
                      seeds=(0,))
    assert not report.ok
    assert any("puzzle_is_solvable" in failure for failure in report.failures)


def test_playtest_detects_nondeterminism(monkeypatch):
    import pocker_agent.core.interpreter as interpreter_module

    original = interpreter_module.Interpreter.step
    calls = {"n": 0}

    def jittery_step(self, *args, **kwargs):
        calls["n"] += 1
        self.emit("jitter", n=calls["n"])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(interpreter_module.Interpreter, "step", jittery_step)
    report = playtest(arithmetic(max_rounds=1), core_registry(), solve_strategy, seeds=(0,))
    assert not report.ok
    assert any("nondeterministic_replay" in failure for failure in report.failures)


# ------------------------------------------------- tool contract enforcement

class _Sneaky:
    def call(self, state):
        state["sneaky"] = 1
        return True


class _Guarded:
    def call(self):
        return True


def _mini_registry(name, tool, operation):
    registry = ToolRegistry()
    registry.register(ToolSpec(name, lambda **_: tool, (operation,)))
    return registry


def _mini_plan(name="sneaky", operation="call", args=None, initial=None):
    return GamePlan(game_kind="mini", players=1, tools=[{"name": name}],
                    initial=initial or {}, entry="start", nodes={
                        "start": {"kind": "call", "next": "end", "action": {
                            "tool": name, "operation": operation, "args": args or {}}},
                        "end": {"kind": "end"}})


def test_out_of_contract_state_change_is_rejected_and_rolled_back():
    registry = _mini_registry("sneaky", _Sneaky(),
                              OperationSpec("call", params=("state",), effects=()))
    interpreter = Interpreter(_mini_plan(args={"state": "$state"}), registry, seed=7)
    with pytest.raises(ToolError, match="out_of_contract_state_change:sneaky"):
        interpreter.setup()
    assert "sneaky" not in interpreter.state          # setup rolled back


def test_operation_precondition_is_enforced():
    registry = _mini_registry("guarded", _Guarded(),
                              OperationSpec("call", requires=(({"gt": ["$state.n", 0]}),), effects=()))
    interpreter = Interpreter(_mini_plan(name="guarded", initial={"n": 0}), registry, seed=7)
    with pytest.raises(ToolError, match=r"precondition_failed:guarded\.call"):
        interpreter.setup()


def test_mechanism_tools_declare_no_state_effects():
    exported = {tool["name"]: tool for tool in core_registry().export()}
    for name in ("logic", "exact_expression", "solvable_deal", "score_settle"):
        for operation in exported[name]["operations"]:
            assert operation["effects"] == [], (name, operation["name"])
    assert exported["state"]["operations"][0]["effects"] == ["*"]
    assert exported["exact_expression"]["operations"][0]["requires"]


# ---------------------------------------------------- playtest coverage gate

def test_playtest_requires_all_wait_nodes_to_be_covered():
    plan = GamePlan(game_kind="mini", players=1, tools=[{"name": "state"}],
                    initial={"finished": False, "winners": []}, entry="wait_a", nodes={
                        "wait_a": {"kind": "wait", "inputs": {"go": "end_ok"}},
                        "end_ok": {"kind": "call", "next": "end", "action": {
                            "tool": "state", "operation": "update",
                            "args": {"state": "$state", "values": {"finished": True}}}},
                        "wait_b": {"kind": "wait", "inputs": {"never": "end_ok"}},
                        "end": {"kind": "end"}})
    report = playtest(plan, core_registry(), first_legal, seeds=(0,))
    assert not report.ok
    assert any("wait_nodes_uncovered:wait_b" in failure for failure in report.failures)
    assert report.covered_wait_nodes == ["wait_a"]


def test_playtest_accepts_multiple_strategies():
    report = playtest(arithmetic(max_rounds=1), core_registry(),
                      [solve_strategy, boundary_first], seeds=(0, 7))
    assert report.ok, report.failures


def test_playtest_requires_at_least_one_strategy():
    with pytest.raises(ToolError, match="playtest_requires_a_strategy"):
        playtest(arithmetic(), core_registry(), [])


# ----------------------------------------------- persistence and visibility

def test_serialize_restore_is_byte_stable_across_play():
    for seed in range(6):
        live = Interpreter(arithmetic(max_rounds=2), core_registry(), seed=seed)
        live.setup()
        while not live.state["finished"]:
            restored = Interpreter.restore(live.serialize(), core_registry())
            assert restored.serialize() == live.serialize()
            action, payload = solve_strategy(live)
            live.step(action, **payload)
            restored.step(action, **payload)
            assert restored.serialize() == live.serialize()
        assert live.state["finished"]


def test_view_does_not_leak_seed_or_input():
    interpreter = Interpreter(arithmetic(), core_registry(), seed=7)
    interpreter.setup()
    view = interpreter.view()
    assert "seed" not in view and "input" not in view and "state" not in view
