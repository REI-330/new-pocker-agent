from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .llm import ModelClient
from .models import GameRuleDSL
from .game_rules import PlayableRule, RULE_ADAPTER, RULE_MODELS, rule_facts
from .executors import create_engine
from .validation import validate_dsl

CONTRACT = """
你是 Pocker Agent 的规则设计助手，只返回有效 json 对象。
这是有限的纸牌 DSL demo；不能假装支持 DSL 未表达的规则。
支持四种执行协议，必须按机制选对 kind，禁止把新玩法伪装成比大小：
1. 旧版 schema_version=0.1（无kind）：按阶段play/discard/draw/pass，最高牌或最多手牌逐轮计分。
2. kind=arithmetic，schema_version=0.2：单人四张牌算式练习，如24点。target可调整，rank_values明确每种牌的数值，operations只允许+ - * /，括号可用，每张牌恰好使用一次。
   fractional_intermediates控制中间分数，deal_mode=random或solvable。dealing=fresh_deck_each_round，每题独立洗牌。答对或正确判断无解1分，答错可重试，放弃0分。players必须1/1/0，max_rounds为题数。
   不支持多人抢答、计时竞赛、幂/阶乘/开根号。仅说24点时集中澄清牌值范围、题数、是否只出有解题等，不能默认改成比大小。
3. kind=blackjack，schema_version=0.2：标准52张牌，无下注21点练习。你对庄家，各2张；target=21,dealer_stand_on=17,dealer_hits_soft_17需明确,natural_beats_21=true,betting=false,dealing=fresh_deck_each_round。
   players必须是min_players=2,max_players=2,starting_hand_size=2，人数包含电脑庄家。A为1/11，JQK为10；要牌/停牌，庄家不足17要牌，软17按配置；两张21优先，平局不加分，胜者1分；玩家爆牌立即负。庄家暗牌隐藏。不能支持分牌、加倍、保险、下注。
4. kind=shedding，schema_version=0.2：标准52张同花色或同点数接牌，match=suit_or_rank，可wild_rank='8'（疯狂八）或null（无万能牌）。万能牌打出要指定花色。
   支持2到4人，先出完者赢，max_rounds=1。引擎固定在发手牌后翻出一张非万能牌作为桌面起始牌，因此初始顶牌不是8的要求完全支持，不需要另加字段。
   draw_policy=until_playable：有合法牌必须出，没有时一直摸到能出为止；不能摸时跳过。
   recycle_discard决定牌堆耗尽是否回收弃牌（保留顶牌）；全员无法行动时blocked_result=draw或fewest_cards。其他人的手牌隐藏。
不支持：斗地主/跑得快的组合牌型压制、德州扑克下注与牌型、桥牌/升级跟花色争墩、抽乌龟抽取对手手牌、复杂特殊牌效果。返回unsupported，列出缺少机制，不得生成同名简化版。
用户要求不支持的规则时，返回 {"type":"unsupported","message":"说明缺少的机制","missing":["..."]}，不可擅自删减。只有用户明确同意简化后才生成简化规则。
缺少胜负、玩家数、初始手牌、轮数、牌强度等重要信息时，用一次简短 question 集中澄清。
question 格式：{"type":"question","question":"...","missing":["..."]}。
信息完整时：{"type":"proposal","summary":"中文准确复述所有规则","rules":<DSL>}。
以下执行语义仅适用于旧版无kind的阶段玩法：
deck.ranks 按从小到大排列，例如 2,3,4,5,6,7,8,9,10,J,Q,K,A。suits 推荐 S,H,D,C。
每个阶段 max_turns 是所有玩家合计动作次数；阶段结束进入下一阶段，最后阶段结束结算本轮。
每轮开始从剩余牌堆给每人发 starting_hand_size 张牌；轮末手牌弃置，不洗回牌堆。
牌堆不足以再次发牌时提前结束，必须向用户说明，或选择充足牌组。
play/discard 使用 hand，draw/pass 使用 system；amount 是动作处理张数，pass 设 amount=0。
play 记录桌面和出牌者；discard 弃牌不参与本轮最高牌比较。
scoring.highest_card 比较本轮各玩家已出的最高牌（没出过则用手牌最高牌），并列者各得 points。
scoring.most_cards 比较轮末手牌数量，并列者各得 points。scoring 为空等同最高牌每轮1分。
max_rounds 后总分最高者获胜，并列为平局。电脑玩家自动选择第一个合法动作。
你必须用这些语义准确表达用户规则。摘要说明阶段、牌点顺序、平局和轮末处理。
不要生成代码。修正规则时保留用户已经确定的规则。
"""


@dataclass
class AgentTurn:
    kind: str
    message: str
    rules: PlayableRule | None = None
    missing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class AgentSession:
    messages: list[dict[str, str]] = field(default_factory=list)
    proposal: dict[str, Any] | None = None
    confirmed: bool = False


class RuleAgent:
    def __init__(self, model: ModelClient):
        self.model = model

    def turn(self, session, user_text):
        session.messages.append({"role": "user", "content": user_text})
        expected = self._expected_family(user_text)
        schema_family = expected
        if not schema_family and "比大小" in user_text:
            schema_family = "legacy"
        if not schema_family and session.proposal and re.search(r"修改|改成|改为|保留|不变", user_text):
            schema_family = session.proposal.get("kind")
        messages = [{"role": "system", "content": self._system_prompt(schema_family)}]
        if session.proposal:
            messages.append({"role": "user", "content": "当前规则草案：" + json.dumps(session.proposal, ensure_ascii=False)})
        raw = self.model.complete(messages + session.messages, response_format={"type": "json_object"})
        result = self._parse_json(raw)
        if result.get("type") == "unsupported":
            message = result.get("message")
            if not isinstance(message, str) or not message.strip():
                raise RuntimeError("model_unsupported_message_empty")
            session.messages.append({"role": "assistant", "content": message})
            return AgentTurn("unsupported", message)
        if result.get("type") == "question":
            question = result.get("question")
            if not isinstance(question, str) or not question.strip():
                raise RuntimeError("model_question_empty")
            session.messages.append({"role": "assistant", "content": question})
            missing = result.get("missing", [])
            return AgentTurn("question", question, missing=missing if isinstance(missing, list) else [])
        proposal = result.get("rules", result)
        if not isinstance(proposal, dict):
            return AgentTurn("error", "模型没有返回可解析的规则对象。", errors=["model_output_not_object"])
        rules, errors = validate_dsl(proposal)
        if expected and proposal.get("kind") != expected:
            errors.append(f"玩法机制不匹配：用户描述要求kind={expected}，不能用比大小或其他玩法替代")
        if errors:
            repair, repair_error = self._repair(proposal, errors, session.messages, expected)
            if repair is not None:
                rules, errors = validate_dsl(repair)
                if expected and repair.get("kind") != expected:
                    errors.append(f"修复后仍未实现用户描述的{expected}机制")
            elif repair_error:
                errors.append(repair_error)
        if errors:
            return AgentTurn("error", "规则未通过执行校验，请修改。", errors=errors)
        try:
            create_engine(rules, seed=7).setup()
        except (ValueError, RuntimeError) as error:
            return AgentTurn("error", "规则未通过启动检查。", errors=[str(error)])
        session.proposal = rules.model_dump(mode="json")
        # Human-readable terms come from the executable rule, including after repair.
        summary = "已生成可执行规则，请核对：\n" + "\n".join(rule_facts(rules))
        session.messages.append({"role": "assistant", "content": json.dumps({"summary": summary, "rules": session.proposal}, ensure_ascii=False)})
        return AgentTurn("proposal", summary, rules)

    def confirm(self, session):
        if not session.proposal:
            return AgentTurn("error", "还没有可以确认的规则。", errors=["proposal_missing"])
        rules, errors = validate_dsl(session.proposal)
        if errors:
            return AgentTurn("error", "规则仍未通过校验。", errors=errors)
        session.confirmed = True
        return AgentTurn("confirmed", "规则已确认，可以开始模拟。", rules)

    def _repair(self, proposal, errors, conversation=None, family=None):
        try:
            raw = self.model.complete([
                {"role": "system", "content": self._system_prompt(family) + "修复下列 DSL 的结构错误，只返回 rules json 对象，保持用户规则含义。"},
                {"role": "user", "content": json.dumps({"rules": proposal, "errors": errors, "user_requirements": conversation or []}, ensure_ascii=False)},
            ], response_format={"type": "json_object"})
            repaired = self._parse_json(raw)
            candidate = repaired.get("rules", repaired)
            return (candidate, None) if isinstance(candidate, dict) else (None, "repair_output_not_object")
        except RuntimeError as error:
            return None, f"repair_failed:{error}"

    @staticmethod
    def _parse_json(raw):
        if not isinstance(raw, str):
            raise RuntimeError("model_output_not_text")
        text = raw.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1)
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise RuntimeError("model_output_invalid_json") from error
        if not isinstance(value, dict):
            raise RuntimeError("model_output_not_object")
        return value

    @staticmethod
    def _system_prompt(family=None):
        # A clear mechanism needs only its own contract; avoid growing every request
        # with every engine family. Ambiguous requests still see all capabilities.
        schema = (GameRuleDSL.model_json_schema() if family == "legacy" else
                  RULE_MODELS[family].model_json_schema() if family in RULE_MODELS else RULE_ADAPTER.json_schema())
        return CONTRACT + "\nDSL JSON Schema:\n" + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _expected_family(text):
        # Narrow fail-closed checks for unmistakable mechanisms, not a template generator.
        text = re.sub(r"(?:不做|不要|不玩|不是|别做|别玩)(?:改成)?\s*(?:24\s*点|二十四点|21\s*点|二十一点|疯狂八|blackjack|crazy\s*eights)", "", text, flags=re.I)
        if re.search(r"24\s*点|二十四点|四则运算|算式", text): return "arithmetic"
        if re.search(r"21\s*点|二十一点|blackjack", text, re.I): return "blackjack"
        if re.search(r"疯狂八|crazy\s*eights", text, re.I): return "shedding"
        return None
