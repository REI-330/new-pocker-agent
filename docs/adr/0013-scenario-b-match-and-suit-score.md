# ADR-0013: 场景 B 的接牌合法性、花色计分与有界候选输入

- 日期：2026-09-14
- 状态：accepted（**实现状态：已实现**，M3g 场景 B 包）
- 决策者：架构 owner
- 关联：ADR-0011（成对移除与计分由规则声明）、ADR-0012（多动作回合、guard 顶牌引用、skip、draw/pass、效果顺序）

## 背景

ADR-0012 冻结了场景 B 的控制流：有序候选动作（`play`/`draw`/`pass`）、顶层 guard、`skip` trigger、固定阶段顺序。B 仍缺两块**机制**，都不是模型能力问题：

- B4「玩家可出一张同点或同花的牌」要求**校验被选中的那张牌是否匹配弃牌区顶牌**。`zones.select` 只校验数量与归属，不校验匹配；`pattern.match` 也不读取输入。把「手里有没有合法牌」交给 guard（`has_match`）只保证存在合法牌，不能保证玩家提交的那张合法。
- B5「出 H 得 2 分、出 S 得 1 分」要求**按刚打出的牌的属性计分**。既有计分是 `compare`（两牌比较）与 `remove_pairs`（成对移除），没有「读一个区顶牌的属性并查表计分」的动作效果。

另外，通用机器人/验证策略必须能生成**匹配**的输入，否则 `has_match` 为真的回合里机器人会提交一张不匹配的牌，被工具拒绝。

## 决策

**在既有 `zones` 工具上新增一个纯校验的选择操作，并新增一个「按顶牌属性计分」的动作效果；机器人按输入区有界轮换候选 payload。**

```text
zones.select_matching(state, zone, card_ids, top_zone, min_count, max_count)
  -> {zone, ids, cards, count}
  failure = reject_only（无 effects）
```

- 语义：先按 `zones.select` 校验数量与归属，再要求每张选中牌的 `suit` 或 `rank` 与 `top_zone` 顶牌相同（与 `pattern.match` 在无 wild、无 active_suit 时同一规则）。`top_zone` 为空或未声明即拒绝。
- 不写状态、不计分：移动与计分仍由规则声明的 `move`/`score_top` 完成。

```text
ScoreTopEffect: {kind: "score_top", zone, by: "suit"|"rank", points: {值: 分}, recipient: "actor"}
```

- 编译器降层为 `zones.top(zone)` + 按 `by` 字段分支：每个声明的值 → 一次 `score_settle.call(scores, winners=[actor], points)`；未声明的值**不计分**。效果顺序由 IR 列表固定（`move` 之后 `score_top`，读到的就是刚打出的牌）。

```text
SelectEffect.match_top: <shared zone id>
```

- 设置时编译器把该 `select` 降层为 `zones.select_matching`，否则仍是 `zones.select`。

**机器人/策略候选重试**：`playtest.descriptor_candidates` 为每个动作生成一组候选 payload，在输入区**有界**轮换（`INPUT_CANDIDATE_LIMIT = 16`），`composed_action`/`random_legal`/`boundary_first`/`goal_first` 依次在丢弃副本上探测，取第一个被宿主接受者。这不是玩法策略：它只按已声明的 `ActionInputDescriptor` 生成输入，仍由宿主契约决定合法性，且给定状态确定（回放字节一致）。

## 备选方案与为何不选

| 方案 | 优点 | 不选原因 |
|---|---|---|
| 只靠 `has_match` guard，不校验具体牌 | 零新操作 | 玩家/机器人可提交不匹配的牌改变局面，违反 B4 与 X7；guard 只保证「存在」 |
| 新增 `matching.play` 类玩法工具 | 语义贴近「接牌」 | 违反 ADR-0009「规则声明结果」：会把结束判定/计分重新塞回工具；且 `matching` 是已知族专用 |
| `score_top` 写成通用表达式 + 条件 | 表达力强 | 表达式语言无「按卡属性查表」的原语，强行加入会引入动态路径 |
| 机器人按 `game_kind`/动作名硬编码选牌 | 改动小 | 违反「机器人不按玩法分支」的准出条件；新玩法必须不需要新策略 |
| 让机器人只试第一张牌，失败即报错 | 零改动 | `has_match` 为真但第一张不匹配时机器人无动作，B 不可玩 |

## 后果

- 正面：B4 的合法性、B5 的花色计分由规则声明并可被独立监测；机器人保持通用。
- 正面：`select_matching` 是「带匹配约束的选择」，第二个消费者可以是任意「必须跟牌」的玩法，不是 B 专属。
- 负面 / 代价：`zones` 增加 `select_matching` → `registry.contract_hash()` 变化 → 既有 composed 验证凭据失效，必须重新验证（ADR-0008 预期）。
- 负面：候选重试使机器人每步最多探测 `min(区大小, 16)` 个 payload；这是有界的，但验证运行时间增加，需在预算内。

## 失败矩阵

| 输入 / 故障 | 必须观察到的结果 |
|---|---|
| 提交一张不匹配的牌 | `selection_does_not_match`，局面不变（`reject_only`） |
| `match_top` 指向未声明或非共享区 | IR 校验失败：`select_unknown_match_zone` / `select_match_zone_must_be_shared` |
| `score_top` 指向非共享区或空 points | IR 校验失败：`score_top_requires_shared_zone` / `score_top_requires_points` |
| 改动 `score_top` 的分值 | `contract_check` 的 `suit_scoring` 监测器拒绝（从结算前状态重算期望分） |
| 机器人第一张牌不匹配 | 在输入区内继续轮换，仍无合法输入才报缺口，不提交非法牌 |

## 迁移与删除

- `zones` 增加 `select_matching` 使 `contract_hash()` 变化，旧 `core_verifications` 不再授权注册（`verification_stale`）。
- 本 ADR 与 ADR-0012 一起完成 `docs/g2-acceptance-scenarios.md` 场景 B 的机制子集；场景 C 的 12 回合原文仍待后续包（ADR-0011 已登记）。
