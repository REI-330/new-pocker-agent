# 可执行 DSL v0.1 合约

- deck.ranks 从弱到强排列。标准牌 A 最大时为 2…10,J,Q,K,A。
- deck.suits 可以是自定义标签；S/H/D/C 和对应 Unicode 符号有内置开源牌面。无法匹配素材的自定义牌使用文字。
- phases 顺序执行。每阶段 max_turns 是所有玩家累计的动作次数上限。无人能执行时提前进入下一阶段，不能留下无按钮的活动状态。
- play/discard 从当前玩家手牌拿出 amount 张连续牌，card_index 指定起始位置。
- play 记录出牌者和桌面；discard 不参与已出牌最高牌比较。
- draw 从剩余牌堆摸 amount 张；pass 不变更牌。draw/pass 必须使用 system，play/discard 必须使用 hand。
- 最后阶段结束时结算本轮。highest_card 比较玩家本轮已出的最高牌（没有出牌时用手牌最高牌）；most_cards 比较轮末剩余手牌数量。并列者各得 points。
- scoring 为空时默认 highest_card、每轮 1 分。整局按累计总分定胜负；并列为平局。
- 轮末弃掉剩余手牌；下一轮从原牌堆继续发 starting_hand_size 张，不回收、不重洗。
- 达到 max_rounds 或牌堆不足以开始下一轮则结束，返回明确 finish_reason。
- 起始和回合转换后，状态必须满足 finished=true 或 legal_actions 非空。
- 模拟超过 max_steps 会报错，不伪造“完成”。
- 重复动作使用 revision 检查；过期提交返回 409，刷新牌局后再操作。
- 一轮允许多个阶段，单人试玩自动执行其他玩家，直到再次轮到玩家一或结束。

不支持任意自定义动作、下注、牌型、跟牌、特殊效果脚本。验证器 extra=forbid，不能靠额外 JSON 字段悄悄增加未实现的规则。

## v0.2 规则族

通过 schema_version=0.2 和 kind 区分执行协议，旧v0.1规则不变。所有新协议共享 game_id、title、description、deck、players、max_rounds。字段定义和约束以 game_rules.py 为准。

### arithmetic：四张牌算式练习

- players固定为1/1/0，单人，不支持多人抢答；max_rounds为题数，1到20。
- card_count=4；target为1到1000的整数。rank_values必须覆盖牌组中的所有rank。
- operations为+ - * /的非空子集；括号可用；每张牌的数值恰好使用一次。禁止拼接数字、额外常数、函数、幂和一元负号；减法可以产生负数。
- fractional_intermediates控制是否允许中间分数。判题使用Fraction，最终结果必须精确相等。
- dealing=fresh_deck_each_round：每题独立洗牌，跨题可以重复出现同一张牌。deal_mode=random允许无解，solvable通过求解器筛选。64次候选仍找不到解时明确报错，不伪造有解题。
- submit_expression传expression；错误输入或错误结果返回422，题目、分数和revision不变。正确答案得1分。
- no_solution通过穷举验证；有解时拒绝。give_up得0分并显示参考答案。
- 作答完成后显示结果，next_round显式进入下一题；最后一题结束显示总分。

### blackjack：无下注21点练习

- 标准52张无大小王，每轮独立洗牌；players固定2/2/2，人数包含庄家。
- target=21；A为1或11，J/Q/K为10；hit要牌，stand停牌。
- dealer_stand_on=17；dealer_hits_soft_17决定软17时是否继续要牌。
- natural_beats_21=true；两张牌21点优先。玩家爆牌立即输；同点平局，胜者得1分，平局不加分。
- 庄家一张暗牌，结算时公开。next_round进入下一轮。
- betting=false；不支持下注、分牌、加倍、保险及赌场赔率结算，不能将此协议称作完整赌场规则。

### shedding：同花/同点接牌

- 标准52张，2到4人；玩家数及起手张数必须明确；max_rounds=1。
- match=suit_or_rank；wild_rank可为牌组内的一个rank或null，疯狂八通常为8。起始顶牌不是万能牌。
- 有万能牌时，按最大玩家数发牌后至少留5张，保证任何种子都能找到非万能起始牌；无万能牌时至少留1张。
- play使用card_index选择一张合法牌；万能牌还必须提供declared_suit。
- draw_policy=until_playable：有合法牌必须出；没有时持续摸，摸到能出后出牌，当前玩家才结束回合。无牌可摸且不能出时pass。
- recycle_discard=true时牌堆空则重洗弃牌，保留顶牌；false时不回收。
- 首位出完牌者获胜。全员被阻塞时按blocked_result=draw或fewest_cards结算。
- 没有罚摸、跳人、反转或“最后一张牌”喊牌惩罚。这是需用户明确接受的规则变体。
