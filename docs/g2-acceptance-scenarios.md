# G2 验收场景（M0 冻结）

> 状态：**已冻结**。以下三个场景是 G2 的开发期正例，均为**人工定义的测试玩法**，不声称是任何传统游戏的标准规则。
> 本文件只定义**需求条款、预期 IR 字段与 source map 关系**，**不包含**它们的执行 Plan —— 提供 Plan 就等于提供答案。
> 关联：ADR-0005、ADR-0007、`docs/g2-capability-subset.md`。

## 0. 统一约定

**计数口径**（三个场景共用，独立 fixture 必须按此断言）：

- 一个**行动回合**（actor action）= 一次当前玩家的 action。出牌、交换、摸牌、pass 各计一次。
- **被 skip 的座位不计行动回合。**
- API `revision` = 一次成功的真人请求**及其机器人响应批次**，共 +1。revision **不得**与行动回合数混用。
- 只有 seed **不能**唯一确定玩家决策后的终局：独立 fixture 必须固定**初始牌序/seed + 动作日志或策略**，再断言行动回合数、轮数与 revision。

**共同必须满足**（三个场景）：

| ID | 条款 |
|---|---|
| X1 | 不新增专属 IR 类、`*_plan` 函数、机器人分支或前端玩法分支 |
| X2 | 由**真实模型**根据自然语言产生 composed IR；不得用预写 Plan 注入模型或 API 冒充生成 |
| X3 | artifact 记录 `generation_source=composed_rules`；编译轨迹证明走通用机制编译器，未调用任何内置玩法计划函数 |
| X4 | 验收 fixture 只提供独立需求条款与预期结果，**不向 Agent 提供答案 IR 或 Plan** |
| X5 | 用户只做两件事：澄清规则、核对产物；不得手工改代码或 Plan 救场 |
| X6 | 刷新前后保持同一 session/version/revision；后端重启后恢复；模型不可用时牌局仍能继续 |
| X7 | 非法选牌/金额/重复请求/过期 revision 可重复触发，且失败**不改局面** |

---

## 场景 A：手牌选择 + 顺序公开比较 + 三轮积分

### A. 需求条款

| ID | 条款 |
|---|---|
| A1 | 两人对局。牌组为 2–9、花色 S/H，单副，牌值等于牌点。 |
| A2 | 每人发 **3 张公开手牌**；其余牌留在牌堆。 |
| A3 | 每轮双方**依次**从手牌选择一张放入公共区。 |
| A4 | 先手轮换：第 1、3 轮 `player-1` 先；第 2 轮 `player-2` 先。 |
| A5 | 两张牌较大者得 **1 分**；相同则无人得分。比较后两张牌进入弃牌区。 |
| A6 | 共 **3 轮**；最高总分获胜；平分时两人并列。**不补牌。** |
| A7 | 每轮含双方各一次行动，完整对局共 **6 次 actor action**。 |

**验收区别**：具备「选择哪张牌」与先手轮换，**不能退化为已有 War 的自动各抽一张**。

### A. 预期 IR 字段

| 条款 | ComposedRulesIR 字段 |
|---|---|
| A1 | `deck.ranks=["2".."9"]`, `deck.suits=["S","H"]`, `deck.copies=1`, `deck.values={rank: point}` |
| A2 | `setup`: 发 3 张到 `zones[hand-1]`、3 张到 `zones[hand-2]`，visibility=`public`（对双方可见） |
| A3 | `actions[select]`: actor=`current`, `inputs=[{kind: card_selection, zone_id: hand-<actor>, min:1, max:1}]` |
| A4 | `flow.phases[round].actor_order` 依 `round % 2` 轮换 |
| A5 | `variables.scores[2]`；`scoring`: 比较两张公开牌，胜者 `+1`；平局不加分 |
| A6 | `terminal.max_rounds=3`，`terminal.winner=highest_score`，`terminal.tie=allow` |
| A7 | `actions[select]` 每个 action 恰好一次，无额外动作 |

### A. source map 关系（条款 → 节点类别，不是节点 id）

```text
A2 → setup 段（deck.deal → zone 写入）
A3 → wait 节点 + 动作输入 schema（card_selection, 单张）
A4 → branch（round 奇偶）→ 座位推进
A5 → 比较节点（rank_compare）→ 计分节点（score_settle/variables.scores 写入）→ 移牌节点（hand → discard）
A6 → terminal 分支（round ≥ 3）→ winners 计算
```

### A. 独立 fixture 预期

固定 seed 与固定动作日志（双方各选最小牌）：6 次 actor action、3 轮、每轮 1 次 revision（机器人响应计入同批）。

---

## 场景 B：接牌 + 按花色计分 + 跳过 + 自定义结束

### B. 需求条款

| ID | 条款 |
|---|---|
| B1 | 两人。牌为 2–9、花色 S/H，单副。 |
| B2 | 每人 **4 张隐藏手牌**；发 1 张起始牌到公共弃牌区；其余为牌堆。 |
| B3 | `player-1` 先手。起始牌**不计分、不触发特殊效果**；后续匹配目标为弃牌区**最新的一张**。 |
| B4 | 玩家可出一张**同点或同花**的牌。 |
| B5 | 出 H 得 **2 分**，出 S 得 **1 分**。 |
| B6 | 出 **7** 在未终局时跳过下家一次。 |
| B7 | 顺序为「移牌 → 加分 → 终局判断 → 特殊效果 → 推进座位」。 |
| B8 | 任何人达到 **6 分立即结束**；**出完手牌本身不结束**。 |
| B9 | 无合法牌时摸 1 张并结束本回合，**不在同回合再出**；无合法牌且牌堆空时 pass。空手也按此规则处理，**不回收弃牌**。 |
| B10 | 达到 6 分时**只结算，不再执行 7 的 skip**；摸牌成功与 pass 各计一个行动回合。 |
| B11 | 若尚无人达 6 分，完成 **20 个行动回合**后按最高分结束，平分并列；被跳过的座位不额外计入行动回合。 |

**验收区别**：终局**不能**来自 matching 工具内置的「空手获胜」；需证明未被套成固定 UNO。

### B. 预期 IR 字段

| 条款 | ComposedRulesIR 字段 |
|---|---|
| B2 | `zones[hand-1|hand-2]` visibility=`owner_only`；`zones[discard]` visibility=`public` |
| B3 | `setup.opening_card` 标记为 `no_score` / `no_trigger`；匹配目标 = `discard.top` |
| B4 | `actions.play.guard`: `rank == top.rank OR suit == top.suit`（`pattern.match`） |
| B5 | `scoring`: 按 `card.suit` 分支 +2 / +1 |
| B6 | `flow.skip`: 当 `card.rank == "7"` 且**未终局**，下家跳过一次 |
| B7 | **显式排序**：`effects` 列表顺序固定为 move → score → terminal → trigger → advance |
| B8 | `terminal.any_score_reaches=6`；**无**「空手即终局」条款 |
| B9 | `actions.draw`（摸 1 张，本回合结束，不 yield 出牌）；`actions.pass` 当牌堆空 |
| B10 | terminal 判定先于 trigger 执行；`discard` 不回收 |
| B11 | `terminal.max_actor_actions=20`，`terminal.winner=highest_score`，`terminal.tie=allow` |

### B. source map 关系

```text
B4 → 合法性判定节点（pattern.match，作用域 = discard.top）
B5 → 计分节点（按 suit 分支）
B7 → 效果顺序（编译器必须按 IR 列表顺序生成，不得重排）
B8 → terminal 分支（分数阈值），与「手牌为空」无关
B6 → trigger 节点，且受 B10 约束（terminal 已成立则不执行）
B9 → wait 分支（无合法牌 → draw / pass，摸牌后不回到选择）
```

### B. 独立 fixture 预期

固定 seed 与动作日志：断言按分数阈值结束，且**存在一局手牌出完但游戏继续**（证明 B8 未被工具内置终局覆盖）。

---

## 场景 C：公开市场交换 + 同点成对移除 + 得分

### C. 需求条款

| ID | 条款 |
|---|---|
| C1 | 两人。牌为 2–9、花色 S/H，单副（共 16 张）。 |
| C2 | 各 **3 张公开手牌**，市场 **3 张公开牌**，其余为牌堆。 |
| C3 | `player-1` 先手。交换一入一出，**市场始终保持 3 张**。 |
| C4 | 手牌、市场、牌堆、收集区**合计始终为 16 张**。 |
| C5 | `setup` 时**不消除对子**。 |
| C6 | 每回合交换**恰好 1 张本人手牌与 1 张市场牌**，然后从本人手牌移除**全部同点对子**到收集区，每对加 **2 分**。 |
| C7 | 对子移除后从牌堆补到 3 张，牌堆不足则补到耗尽；补牌后产生的对子留到该玩家**下次交换后**处理。 |
| C8 | 无手牌且牌堆仍有牌时先补到 3 张再进入交换；手牌与牌堆都空时 pass。 |
| C9 | 补牌不足 3 张时补到牌堆耗尽。每对由两张同 rank 组成；本牌组每个 rank 最多一对。 |
| C10 | 补牌是本回合的**内部效果**，不单独计行动；pass 计行动。 |
| C11 | 共 **12 个行动回合**，最高分获胜，平分并列；**不因提前空手自动结束**。 |

**验收区别**：一个动作需要**两个区域的选择输入**，现有固定 `card_index` 与按动作名的前端处理无法代替。

### C. 预期 IR 字段

| 条款 | ComposedRulesIR 字段 |
|---|---|
| C2 | `zones[hand-1|hand-2]`（public）、`zones[market]`（public）、`zones[stock]`、`zones[collection-1|2]` |
| C4 | 不变量：`sum(zone sizes) == 16`（每次操作后检查） |
| C5 | `setup` 不含 pair-removal 效果 |
| C6 | `actions.exchange.inputs` = 两个 `card_selection`：`zone_id=hand-<actor>` 与 `zone_id=market`，各 `min=1,max=1` |
| C6 | `scoring`: 每移除一对 `+2`（**不是** +1） |
| C7 | `effects`: exchange → remove_pairs → refill(≤3) |
| C9 | 收集区按 rank 去重计数 |
| C11 | `terminal.max_actor_actions=12`；**无**空手终局条款 |

### C. source map 关系

```text
C6 → 双输入动作（两个 zone 的 card_selection）→ 移牌节点 ×2 → 成对检测 → 计分
C7 → 补牌节点（上限 3，受牌堆约束）
C4 → 不变量监测器（不是 plan 节点，由验证服务检查）
C11 → terminal 分支（行动回合计数）
```

### C. 独立 fixture 预期

固定 seed 与动作日志：断言 12 个行动回合、市场恒为 3 张、总牌数恒为 16、每对 +2 分。

---

## 反例（必须在 M3 被拒绝）

| 输入 / 故障 | 必须观察到的结果 |
|---|---|
| 「同时翻牌，最快点击者抢走」 | `unsupported: simultaneous/realtime_reaction`，**不得改成顺序行动** |
| 「卡牌执行任意 Python / 访问外部网站」 | `unsupported`，没有代码执行入口 |
| 模型把终局赢家写死为自己 | 结构/条款验证失败，不注册 |
| 修改 IR 后复用旧验证 ID | `verification_stale`，要求重新验证 |
| 只提交一个 `ok:true` 的报告 | 拒绝；只能使用宿主保存的验证记录 |
| 只走放弃路径但未覆盖正常动作 | 覆盖不足，不标记可玩 |
| 计划/宏无等待循环或指数展开 | 静态拒绝或有界超时，不占满 API 进程 |
| 对手牌在错误/事件/选项中出现 | 视角测试失败，不发布 |
| 机器人错误、写库错误或中途重启 | 已提交 revision 保持一致，无半步成功 |
| 模型调用未经授权的确认/注册入口 | 宿主拒绝；验证通过不等于用户已核对 |

## 三个错误分类不得混淆

| 分类 | 含义 |
|---|---|
| `unsupported` | 机制不存在（需求超出能力边界） |
| `verification_inconclusive` | 机制存在但验证资源/覆盖不足 |
| `compiler_error` | 编译器自身错误 |

三者**不得混为一谈**，也**不得**自动改成近似玩法。
