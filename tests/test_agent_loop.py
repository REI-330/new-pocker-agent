"""The agent loop: bounded, host-gated, and repairable from observations."""
from __future__ import annotations

import json

from pocker_agent.agent import run_loop


class ScriptedModel:
    """Deterministic stand-in for the LLM; records the transcript it was given."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.messages: list[dict[str, str]] = []

    def complete(self, messages, **kwargs):
        self.messages = messages
        if self.calls >= len(self.responses):
            raise RuntimeError("scripted responses exhausted")
        response = self.responses[self.calls]
        self.calls += 1
        return response


def decision(tool: str, **args) -> str:
    return json.dumps({"tool": tool, "args": args})


def war(**overrides) -> dict:
    payload = {"kind": "war", "game_id": "agent-war", "title": "Agent 比大小", "max_rounds": 3, "players": 2}
    payload.update(overrides)
    return payload


def test_loop_composes_a_new_game_without_a_human():
    model = ScriptedModel(
        decision("propose_ir", ir=war()),
        decision("capability_check"),
        decision("compose_plan"),
        decision("playtest"),
        decision("finalize"),
    )
    result = run_loop("做一个两人各抽一张比大小的游戏", model)
    assert result.kind == "proposal" and result.finalized is True
    assert result.ir["kind"] == "war"
    assert result.playtest["ok"] is True
    assert result.attempts == 5
    assert all(observation["ok"] for observation in result.observations)


def test_loop_repairs_an_invalid_ir_from_the_observation():
    model = ScriptedModel(
        decision("propose_ir", ir=war(max_rounds=0)),      # rejected by the host
        decision("propose_ir", ir=war()),
        decision("compose_plan"),
        decision("playtest"),
        decision("finalize"),
    )
    result = run_loop("x", model)
    assert result.finalized is True
    assert any(not observation["ok"] for observation in result.observations)
    assert "invalid_ir" in json.dumps(result.observations, ensure_ascii=False)


def test_loop_cannot_finalize_before_playtest():
    model = ScriptedModel(
        decision("propose_ir", ir=war()),
        decision("compose_plan"),
        decision("finalize"),        # rejected: no playtest yet
        decision("playtest"),
        decision("finalize"),
    )
    result = run_loop("x", model)
    assert result.finalized is True
    finalize_outcomes = [observation["ok"] for observation in result.observations
                         if observation["tool"] == "finalize"]
    assert finalize_outcomes == [False, True]


def test_loop_recovers_from_a_plan_that_fails_playtest():
    broken = {"schema_version": "0.4", "game_kind": "war", "players": 2,
              "tools": [{"name": "deck"}], "initial": {}, "entry": "end",
              "nodes": {"end": {"kind": "end"}}}
    model = ScriptedModel(
        decision("propose_ir", ir=war(max_rounds=2)),
        decision("compose_plan", plan=broken),
        decision("playtest"),        # fails: end requires a finished state
        decision("compose_plan"),    # falls back to host_compile
        decision("playtest"),
        decision("finalize"),
    )
    result = run_loop("x", model)
    assert result.finalized is True
    playtests = [observation["ok"] for observation in result.observations
                 if observation["tool"] == "playtest"]
    assert playtests == [False, True]


def test_loop_survives_non_json_output():
    model = ScriptedModel(
        "抱歉，我先解释一下我的思路。",                       # not JSON
        decision("propose_ir", ir=war()),
        decision("compose_plan"),
        decision("playtest"),
        decision("finalize"),
    )
    result = run_loop("x", model)
    assert result.finalized is True
    assert any(observation.get("error") == "model_output_invalid_json"
               for observation in result.observations)


def test_loop_stops_at_the_step_budget():
    model = ScriptedModel(*[decision("capability_check") for _ in range(5)])
    result = run_loop("x", model, max_steps=3)
    assert result.kind == "error"
    assert "budget_exhausted" in result.message
    assert result.attempts == 3
    assert len(result.observations) == 3


def test_loop_reports_unsupported_with_missing_axes():
    model = ScriptedModel(decision("unsupported", message="缺少 info_set 与 trigger",
                                   missing=["info_set", "trigger"]))
    result = run_loop("做一个抽乌龟", model)
    assert result.kind == "unsupported"
    assert "info_set" in result.message
    assert result.finalized is False


def test_loop_asks_once_when_the_goal_is_incomplete():
    model = ScriptedModel(decision("ask_user", question="共有几轮？", missing=["max_rounds"]))
    result = run_loop("做个比大小的游戏", model)
    assert result.kind == "question" and "几轮" in result.message
    assert result.attempts == 1
    assert result.messages == [{"role": "user", "content": "做个比大小的游戏"},
                               {"role": "assistant", "content": "共有几轮？"}]


def test_loop_continues_a_multi_turn_conversation():
    first = ScriptedModel(decision("ask_user", question="共有几轮？", missing=["max_rounds"]))
    asked = run_loop("做个比大小的游戏", first)
    assert asked.kind == "question" and len(asked.messages) == 2

    second = ScriptedModel(
        decision("propose_ir", ir=war(max_rounds=5)),
        decision("compose_plan"),
        decision("playtest"),
        decision("finalize"),
    )
    done = run_loop("5 轮", second, history=asked.messages)
    assert done.finalized is True
    assert done.ir["max_rounds"] == 5
    # the history is carried forward, so the client can keep the thread
    assert done.messages[:2] == asked.messages
    assert done.messages[-2] == {"role": "user", "content": "5 轮"}
    assert done.messages[-1]["role"] == "assistant" and "已生成" in done.messages[-1]["content"]


def test_clean_history_drops_tool_transcript_noise():
    from pocker_agent.agent.loop import clean_history

    kept = clean_history([
        {"role": "user", "content": "做UNO"},
        {"role": "assistant", "content": "{\"tool\": \"propose_ir\"}"},   # tool json is still a turn
        {"role": "tool", "content": "observation: ..."},                    # dropped
        {"role": "user", "content": ""},                                   # dropped
        "not a dict",
    ])
    assert kept == [{"role": "user", "content": "做UNO"},
                    {"role": "assistant", "content": "{\"tool\": \"propose_ir\"}"}]


def test_loop_ships_a_model_authored_plan_and_it_becomes_playable():
    """agent_compose: the plan comes from the model, not from host_compile."""
    from pocker_agent.core.plans import war_plan
    from pocker_agent.core.session import SessionStore

    authored = war_plan(max_rounds=7).model_dump(mode="json")
    model = ScriptedModel(
        decision("propose_ir", ir={"kind": "war", "game_id": "agent-novel-war",
                                     "title": "七轮比大小", "max_rounds": 7, "players": 2}),
        decision("compose_plan", plan=authored),
        decision("playtest"),
        decision("finalize"),
    )
    result = run_loop("做一个七轮的比大小", model)
    assert result.finalized is True
    composed = next(observation for observation in result.observations
                    if observation["tool"] == "compose_plan")
    assert composed["source"] == "agent_compose"

    store = SessionStore()
    store.register_plan("agent-novel-war", result.plan, result.playtest)
    session = store.create("agent-novel-war", seed=3)
    for _ in range(20):
        if session.interpreter.state["finished"]:
            break
        session.interpreter.step("play")
    assert session.interpreter.state["finished"] is True
    assert session.interpreter.state["round"] == 7
