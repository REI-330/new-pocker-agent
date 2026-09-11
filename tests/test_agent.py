import json

from pocker_agent.agent import AgentSession, RuleAgent


class FakeModel:
    def __init__(self, *responses: str):
        self.responses = list(responses)

    def complete(self, messages, *, response_format=None):
        return self.responses.pop(0)


class FailingRepairModel:
    def __init__(self, first_response: str):
        self.first_response = first_response
        self.calls = 0

    def complete(self, messages, *, response_format=None):
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("upstream_timeout")
        return self.first_response


def valid_rules():
    return {
        "game_id": "agent-demo",
        "title": "Agent Demo",
        "deck": {"suits": ["S", "H"], "ranks": ["A", "K"]},
        "players": {"min_players": 2, "max_players": 2, "starting_hand_size": 1},
        "actions": [{"name": "play"}],
        "phases": [{"name": "main", "actions": ["play"]}],
    }


def test_agent_asks_for_missing_rule_then_proposes():
    model = FakeModel(
        json.dumps({"type": "question", "question": "需要几名玩家？", "missing": ["player_count"]}),
        json.dumps({"type": "proposal", "summary": "规则已生成。", "rules": valid_rules()}),
    )
    agent = RuleAgent(model)
    session = AgentSession()
    first = agent.turn(session, "我想做一个比大小的游戏")
    second = agent.turn(session, "两名玩家，每人一张牌")
    assert first.kind == "question"
    assert second.kind == "proposal"
    assert second.rules is not None


def test_agent_repairs_invalid_dsl():
    broken = {**valid_rules(), "phases": [{"name": "main", "actions": ["missing"]}]}
    model = FakeModel(json.dumps({"type": "proposal", "rules": broken}), json.dumps(valid_rules()))
    result = RuleAgent(model).turn(AgentSession(), "生成规则")
    assert result.kind == "proposal"
    assert result.rules is not None


def test_confirm_requires_proposal():
    result = RuleAgent(FakeModel()).confirm(AgentSession())
    assert result.kind == "error"


def test_agent_prompt_explicitly_requests_json_for_json_mode_gateways():
    from pocker_agent.agent import RuleAgent

    assert "json" in RuleAgent._system_prompt()


def test_repair_failure_is_visible_to_caller():
    broken = {**valid_rules(), "phases": [{"name": "main", "actions": ["missing"]}]}
    model = FailingRepairModel(json.dumps({"type": "proposal", "rules": broken}))
    result = RuleAgent(model).turn(AgentSession(), "生成规则")
    assert result.kind == "error"
    assert any(error.startswith("repair_failed:") for error in result.errors)


def test_24_cannot_be_disguised_as_high_card_even_after_repair():
    model = FakeModel(json.dumps({"type":"proposal","rules":valid_rules()}), json.dumps(valid_rules()))
    result = RuleAgent(model).turn(AgentSession(), "做一个24点游戏，输入算式求24")
    assert result.kind == "error"
    assert any("arithmetic" in error for error in result.errors)


def test_unsupported_game_is_not_returned_as_a_playable_proposal():
    result = RuleAgent(FakeModel(json.dumps({"type":"unsupported","message":"目前缺少斗地主的叫地主和组合牌型"}))).turn(AgentSession(),"斗地主")
    assert result.kind == "unsupported" and result.rules is None


def test_displayed_terms_are_derived_from_repaired_rule_and_repair_keeps_requirements():
    class RecordingModel(FakeModel):
        def complete(self,messages,**kwargs):
            self.latest=messages
            return super().complete(messages,**kwargs)
    broken = {**valid_rules(), "max_rounds":0}
    model=RecordingModel(json.dumps({"type":"proposal","summary":"一共999轮","rules":broken}),json.dumps({**valid_rules(),"max_rounds":2}))
    result=RuleAgent(model).turn(AgentSession(),"只玩2轮")
    assert result.kind == "proposal" and "999" not in result.message and "2轮" in result.message
    assert "只玩2轮" in model.latest[-1]["content"]


def test_explicit_negative_game_name_does_not_force_wrong_family():
    assert RuleAgent._expected_family("不做24点，改成21点练习") == "blackjack"
    assert len(RuleAgent._system_prompt("arithmetic")) < len(RuleAgent._system_prompt()) * .6
