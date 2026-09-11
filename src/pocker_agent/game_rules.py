"""Versioned, executable rule families. Fields describe behavior, not game titles."""
from typing import Literal

from pydantic import Field, TypeAdapter, model_validator

from .models import DeckSpec, GameRuleDSL, PlayerSpec, StrictSpec


class ExplicitPlayers(PlayerSpec):
    min_players: int = Field(ge=1, le=12)
    max_players: int = Field(ge=1, le=12)
    starting_hand_size: int = Field(ge=0, le=52)


class SoloPlayers(StrictSpec):
    min_players: Literal[1] = 1
    max_players: Literal[1] = 1
    starting_hand_size: Literal[0] = 0


class BlackjackPlayers(StrictSpec):
    min_players: Literal[2] = 2
    max_players: Literal[2] = 2
    starting_hand_size: Literal[2] = 2


class RuleHeader(StrictSpec):
    schema_version: Literal["0.2"] = "0.2"
    game_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=100)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    deck: DeckSpec
    players: ExplicitPlayers
    max_rounds: int = Field(ge=1, le=20)


class ArithmeticRule(RuleHeader):
    players: SoloPlayers
    kind: Literal["arithmetic"]
    target: int = Field(ge=1, le=1000)
    card_count: Literal[4]
    rank_values: dict[str, int]
    operations: list[Literal["+", "-", "*", "/"]] = Field(min_length=1, max_length=4)
    fractional_intermediates: bool
    deal_mode: Literal["random", "solvable"]
    # Each puzzle is an independent draw; this is solo practice, not a race mode.
    dealing: Literal["fresh_deck_each_round"]

    @model_validator(mode="after")
    def executable(self):
        if self.players.min_players != 1 or self.players.max_players != 1 or self.players.starting_hand_size != 0:
            raise ValueError("算式玩法当前为单人练习，起手0张，四张题面牌放在桌面")
        if set(self.rank_values) != set(self.deck.ranks) or any(type(v) is not int or not 1 <= v <= 100 for v in self.rank_values.values()):
            raise ValueError("rank_values 必须为每种牌点指定1到100的整数值")
        if len(set(self.operations)) != len(self.operations):
            raise ValueError("operations 不得重复")
        if len(self.deck.ranks) * len(self.deck.suits) * self.deck.copies < 4:
            raise ValueError("题面牌组至少需要4张牌")
        return self


class BlackjackRule(RuleHeader):
    players: BlackjackPlayers
    kind: Literal["blackjack"]
    target: Literal[21]
    dealer_stand_on: Literal[17]
    dealer_hits_soft_17: bool
    natural_beats_21: Literal[True]
    dealing: Literal["fresh_deck_each_round"]
    betting: Literal[False]

    @model_validator(mode="after")
    def executable(self):
        standard_deck(self.deck)
        if (self.players.min_players, self.players.max_players, self.players.starting_hand_size) != (2, 2, 2):
            raise ValueError("21点练习为一位玩家和一位庄家，各起手2张；不支持下注、分牌或加倍")
        return self


class SheddingRule(RuleHeader):
    kind: Literal["shedding"]
    match: Literal["suit_or_rank"]
    wild_rank: str | None
    draw_policy: Literal["until_playable"]
    recycle_discard: bool
    blocked_result: Literal["draw", "fewest_cards"]

    @model_validator(mode="after")
    def executable(self):
        standard_deck(self.deck)
        if self.wild_rank is not None and self.wild_rank not in self.deck.ranks:
            raise ValueError("wild_rank 必须在牌组中")
        if not 2 <= self.players.min_players <= self.players.max_players <= 4 or self.players.starting_hand_size < 1:
            raise ValueError("接牌玩法支持2到4人，起手至少1张")
        if self.players.max_players * self.players.starting_hand_size >= 52:
            raise ValueError("发牌后必须留出桌面起始牌")
        # Four wild cards may be left together; reserve at least one non-wild
        # for every seed, not just the seed used by the proposal startup check.
        if self.wild_rank is not None and self.players.max_players * self.players.starting_hand_size > 47:
            raise ValueError("有万能牌时，发牌后至少留5张牌，以保证能翻出非万能起始牌")
        if self.max_rounds != 1:
            raise ValueError("接牌玩法当前是一局出完手牌结束，max_rounds 必须为1")
        return self


def standard_deck(deck):
    if set(deck.ranks) != set(["A", *map(str, range(2, 11)), "J", "Q", "K"]) or set(deck.suits) != {"S", "H", "D", "C"} or deck.copies != 1:
        raise ValueError("此玩法当前使用标准52张牌，花色S/H/D/C，一副，无大小王")


PlayableRule = GameRuleDSL | ArithmeticRule | BlackjackRule | SheddingRule
RULE_ADAPTER = TypeAdapter(PlayableRule)
RULE_MODELS = {"arithmetic": ArithmeticRule, "blackjack": BlackjackRule, "shedding": SheddingRule}


def parse_rule(payload):
    # Dispatch explicitly: a misspelled kind must never fall back to high-card.
    if isinstance(payload, dict) and "kind" in payload:
        kind = payload["kind"]
        model = RULE_MODELS.get(kind) if isinstance(kind, str) else None
        if model is None:
            raise ValueError("未知玩法引擎 kind")
        return model.model_validate(payload)
    return GameRuleDSL.model_validate(payload)


def rule_facts(rules):
    """Player-facing terms derived from the actual executor configuration."""
    if isinstance(rules, ArithmeticRule):
        return [f"单人算式练习：每题{rules.card_count}张牌，目标{rules.target}，共{rules.max_rounds}题",
                "每张牌恰好使用一次；允许括号和 " + "、".join(rules.operations),
                "牌点：" + "，".join(f"{k}={v}" for k, v in rules.rank_values.items()),
                ("允许" if rules.fractional_intermediates else "不允许") + "中间结果为分数；最终结果必须精确等于目标",
                "每题独立洗牌抽牌；" + ("只出有解题" if rules.deal_mode == "solvable" else "随机出题，可提交无解判断"),
                "答对或正确判断无解得1分；答错可重试，放弃得0分并展示答案"]
    if isinstance(rules, BlackjackRule):
        return [f"无下注21点练习：你对庄家，共{rules.max_rounds}轮，每轮独立洗牌，各发2张",
                "A按1或11计，J/Q/K按10计；你可要牌或停牌，超过21点爆牌",
                "庄家不足17点要牌；软17" + ("要牌" if rules.dealer_hits_soft_17 else "停牌"),
                "两张牌21点优先于普通21点；同点平局，双方爆牌时玩家先爆已判负",
                "胜者得1分，平局不加分；支持庄家暗牌，不包含分牌、加倍、保险"]
    if isinstance(rules, SheddingRule):
        return [f"{rules.players.min_players}人接牌，起手{rules.players.starting_hand_size}张，先出完手牌者获胜",
                "发牌后翻一张牌作为桌面起始牌，起始牌不是万能牌",
                "出牌必须与顶牌同花色或同点数；其他玩家手牌隐藏",
                (f"{rules.wild_rank}是万能牌，打出后指定花色" if rules.wild_rank else "没有万能牌"),
                "无合法牌时持续摸牌，摸到能出的牌必须出牌；无法摸牌且不能出牌时跳过",
                ("牌堆空时回收弃牌，保留顶牌" if rules.recycle_discard else "不回收弃牌"),
                "全员无法行动时" + ("判平局" if rules.blocked_result == "draw" else "剩余手牌最少者获胜")]
    return [f"{rules.players.min_players}人，每轮起手{rules.players.starting_hand_size}张，共{rules.max_rounds}轮",
            "牌点由小到大：" + "、".join(rules.deck.ranks),
            *[f"阶段「{p.name}」允许{','.join(p.actions)}，累计最多{p.max_turns}次动作" for p in rules.phases],
            "每轮剩余手牌弃置，不回收；最高总分获胜，并列平局"]
