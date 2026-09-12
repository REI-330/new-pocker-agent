# ADR-0009: 用 `zones` 通用牌区操作拆开匹配机制，并把终局/回收交还给规则

- 日期：2026-09-12
- 状态：accepted（**实现状态：M1 已实现**；M2 起由组合编译器消费，旧匹配族按 M5 迁移删除）
- 决策者：架构 owner
- 关联：ADR-0005、ADR-0007、`docs/g2-capability-subset.md` §3、`docs/g2-acceptance-scenarios.md`

## 背景

M0 审计定位到三处**机制替规则做决定**的耦合（见 `docs/g2-capability-subset.md` §3）：

1. `tools.py` `MatchingTool.play` 在出完手牌时自动 `finished=True, winners=[...]`。场景 B 明确要求「出完手牌本身不结束」，终局是「达到 6 分」；这个内置终局让 B 无法表达。
2. `MatchingTool.draw(..., recycle=True)` 把「牌堆耗尽后回收弃牌」作为**默认**。规范要求回收策略由规则显式决定。
3. 牌的位置是各family 私有的一堆顶层 key（`hands` / `stock` / `table` / `discard`），没有稳定的牌区引用，也没有「一张牌只属于一个区」的统一校验；跨区选择只能按 `card_index` 位置，无法表达场景 C 的「一手牌 + 一个市场牌」双区域选择。

同时 §6.4 的「工具提升规则」要求注册表只能按**被证明的复用**增长，而 §7 把 core 工具上限冻结在 18。

## 决策

### 1. 新增第 18 个 core 工具 `zones`（预算用满 18/18）

`zones` 只暴露通用牌区机制，不内置任何玩法后果：

| operation | 语义 | effects |
|---|---|---|
| `select(state, zone, card_ids, min_count, max_count)` | 校验选择：数量在界内、无重复、卡牌确实属于该区 | 无（`reject_only`） |
| `move(state, moves)` | 原子移动：每个 `{from,to,card_ids}` 顺序应用，任一非法则整表不变 | `("zones",)` |
| `top(state, zone)` | 读取区顶牌 | 无 |
| `count(state, zone)` | 读取各区张数 | 无 |
| `verify(state)` | 唯一归属审计 | 无 |

牌区模型是 `state["zones"] = {zone_id: {"owner", "visibility", "cards": [CardRef]}}`，**复用 `core/cards.py` 的 `CardRef`**，不新建平行牌堆/牌值模型。唯一归属（同一 `card.id` 不得同时存在于两个区）由 `zones.assert_unique_ownership` 强制；`copies>1` 的重复副本靠**唯一 id** 区分并守恒。

### 2. 拆开匹配机制：合法性 / 移牌 / 计分 / 终局分离

- **合法性**：继续由 `pattern.match` / `pattern.choices` 承担（本来已独立）。
- **移牌**：`MatchingTool.play` 只做「校验 + 移牌 + 更新 active_suit」，**不再**写 `finished` / `winners` / `phase`。返回 `hand_empty` 供规则参考。
- **计分**：一直由 `score_settle.call` 承担，匹配工具不参与。
- **终局**：由**计划**显式声明。`crazy_eights` 与 `uno` 两个参考计划新增 `empty{seat}` → `declare{seat}`（`state.update(finished, winners)`），把原先的内置终局搬到规则里。
- **回收**：`MatchingTool.draw` 的 `recycle` 默认改为 `False`；`crazy_eights` / `uno` 在 draw 节点显式传 `recycle=True`，即规则自己声明回收策略。

### 3. 兼容策略（不留长期双路径）

- `matching.play` / `matching.draw` 保留为**薄适配**：它们调用与 `zones` 相同的 `CardRef` 与 `PatternTool` 机制，不复制第二份行为实现；旧参考族继续可用。
- 迁移消费者清单与删除位置：`crazy_eights`、`uno` 是 `matching.*` 的仅有两个消费者，ADR-0005/0006 的 M5 完成组合 IR 前端迁移后，在同一交付包内删除 `matching` 工具或给出明确删除 PR。当前**没有**第二个行为实现，只有同一个机制的两个入口。
- 诚实说明：M1 **未**迁移 `trick` / `hidden_draw` 的内部策略（墩数上限终局、`discard_pairs` 每对 +1 与写死 key `pairs`、`ask` 失败自动摸牌）。计划 §6 M1.3 明确「先迁移 matching，再按第二个消费者的需要拆 trick/hidden draw」；这些留给 M2 的组合编译器按需拆，M1 不一次性大重写。

### 4. 两个真实消费者

§6.4 要求新工具至少有 2 个真实消费者。M1 交付两个**开发期组合样例**（`core/compositions.py`，明确不是参考玩法、不可从 app 到达）：

- `zones_exchange_compare`：双区域选择 + 交换 → `rank_compare` 比较 → 计分；
- `zones_exchange_suit_score`：**同一组** `zones.select` / `zones.move` / `score_settle.call` / `wait` 回合 → 按花色计分。

两者都由 `test_composition_consumers_are_real_plans_that_share_generic_ops` 真实跑完 playtest，并断言共享同一批通用 operation。M2 用真正的组合产物替换它们。

## 备选方案与为何不选

| 方案 | 优点 | 为何不选 |
|---|---|---|
| 把牌区操作塞进 `matching` / 新增 `shedding` 工具 | 不改预算 | 违反 §7「不把不同职责塞进一个全能工具」，也违反工具命名黑名单 |
| 只做纯函数 `core/zones.py`，不注册工具 | 不加工具 | 组合 IR 无法从计划调用牌区操作，M2 无法降层 |
| 把 `hands/stock/table` 全部就地改成 `zones` | 只有一个模型 | 一次性重写 8 个参考族，违反 M1「避免一次性大重写」，且破坏既有存档 |
| 保留 `matching.play` 内置终局，用标志位关闭 | 改动最小 | 默认真值就是场景 B 的阻塞点；「默认可关闭」仍需要规则写额外开关，且容易被漏 |
| 提高 core 工具上限到 19 留余量 | 方便 | §7 冻结 18；ADR 只为真实复用增长，不为方便放宽 |

## 后果

- 正面：终局与回收策略从工具移到计划，场景 B 可表达；跨区选择可表达场景 C。
- 正面：`zones` 用满 18/18，但换来一张可审计的牌区不变量与跨族复用；M2 无需再新增工具即可降层。
- 正面：`registry.contract_hash()` 让「工具契约变化 ⇒ 旧验证失效」在 M3 有可执行输入。
- 负面 / 代价：`matching.*` 与新 `zones.*` 在迁移期并存（有明确删除判据，非长期双路径）。
- 负面：`trick` / `hidden_draw` 的内置策略仍在，M2 才拆。
- **强制方式**：`tests/test_architecture_invariants.py`（工具预算 18、无死工具、组合消费者是真实计划）＋`tests/test_g2_m1.py`（选牌数量/所有权错误不改状态、空手不自动终局、重复副本守恒、参考族回归）。改动预算或删除判据必须改这些测试。

## 迁移与删除

- 顺序：M1 拆 `matching` 的终局/回收 → M2 组合编译器消费 `zones` → M5 前端与消费者迁移后删除 `matching.*`。
- 删除判据：参考族 `crazy_eights` / `uno` 不再引用 `matching.*`，且 `core_registry()` 不再注册 `matching`。
- 兼容层的往返测试：`tests/test_g2_m1.py::test_crazy_eights_still_finishes_with_a_plan_declared_terminal` 与 `::test_uno_still_finishes_with_a_plan_declared_terminal`，以及既有 playtest 门槛。
