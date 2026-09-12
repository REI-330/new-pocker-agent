# ADR-0011: 场景 C 的成对移除机制（成组选择）

- 日期：2026-09-14
- 状态：proposed（等待架构 owner 决策）
- 决策者：架构 owner

## 背景

场景 C 要求「从本人手牌移除**全部同点对子**到收集区，每对加 **2** 分；移除后从牌堆补到 3 张」。当前机制无法表达：

- `HiddenDrawTool.discard_pairs`（`core/hidden_tools.py:62`）是**隐牌抽牌玩法专属**：写死状态 key `pairs`、每对固定 **+1**、目标区固定，规则无法声明收集区与分值（`docs/g2-capability-subset.md` §3 已登记为 M1/M2 欠账）。
- `pattern.classify`（`core/tools.py:301`）只能**校验一个已给出的集合**（如 `same_rank`），不能从区域内**发现**同点分组。
- `zones.select` 只接受调用方给出的有限 `card_ids`，没有「找出所有重复组」的选择操作。

因此 C 卡在机制粒度，不是模型能力问题。核心工具预算为 18 已满（`test_core_tool_budget_is_a_ratchet`），不能为此新增工具；按 ADR-0009 的拆分原则，**计分与目标区必须由规则声明**，不能由工具内置疑死。

## 决策（建议）

**在既有 `zones` 工具上新增一个通用、纯选择、无副作用的操作，并让编译器把「成对移除」降层为它 + 既有 `zones.move` + 规则声明的计分。**

```text
zones.select_duplicates(state, zone, key="rank", min_count=2)
  -> {zone, groups: [[card_id, ...], ...], ids: [card_id, ...], count}
  failure = reject_only（无 effects）
```

- `key` 首个消费者只用 `rank`；实现按声明的 key 分组，保留 `>= min_count` 的组。
- 不写状态、不计分、不决定目标区：计分与收集区由 `RemovePairsEffect` 规则声明。
- 编译器降层（机制组合，不按游戏名分派）：

```text
select_duplicates(source, min_count=2)         -> sel_{r}
logic.evaluate(mul(count(sel_{r}.ids), points_per_pair))  -> t_pair_points
zones.move(source -> collection, card_ids=sel_{r}.ids)    -> 原子移动
score_settle.call(scores, winners=[actor], points=$state.t_pair_points)
```

- `RemovePairsEffect` 字段：`kind="remove_pairs"`, `from_zone`, `to_zone`, `min_count=2`, `points_per_pair>=1`。
- `refill` 用既有 `zones.count` + `zones.top` + `zones.move` 的**有界展开**表达（上限声明，不引入无界循环）。

## 备选方案与为何不选

| 方案 | 优点 | 为何不选 |
|---|---|---|
| 直接复用 `hidden_draw.discard_pairs`，把 +1 改成参数 | 改动最小 | 仍是隐牌玩法专属工具，目标区/计分耦合在工具里；违反 ADR-0009「规则声明结果」，且无法给收集区 |
| 给 `pattern.classify` 加「发现分组」模式 | 复用现有工具 | `classify` 的契约是「校验给定集合」，改成能扫描区域会让它同时承担选择职责，职责混淆 |
| 让规则用多次 `zones.select` 手动列出对子 | 零新操作 | 需要模型预先知道牌面 id；选牌是不可预测运行态，表达不了 |
| 新增第 19 个工具 | 语义清晰 | 违反 18 工具棘轮；`zones` 已是「区域选择/移动」的归属工具，成组选择属于其职责 |
| 用 `assign` 变量在计划里循环扫描 | 零新操作 | 需要无界循环，动态结构分析明确拒绝 |

## 后果

- 正面：C 的成对移除与计分由规则声明；`zones` 仍是唯一区域操作来源；工具数保持 18。
- 正面：第二个真实消费者是 Go Fish 的「凑齐四张成一本书」收集，可证明该操作是通用机制而非 C 专属。
- 负面 / 代价：`zones` 增加一个操作 → `registry.contract_hash()` 变化 → **所有既有 verification 凭据失效，必须重新验证**（ADR-0008 预期行为）。需在迁移说明中登记。
- **强制方式**：变异测试要求「改 `points_per_pair` 或改成对后仍留一张」被 `contract_check` 的 scoring/conservation 监测器拒绝；`zones.select_duplicates` 必须声明 `effects=()` 且 `failure=reject_only`。

## 迁移与删除

- `HiddenDrawTool.discard_pairs` 在 Go Fish 消费者迁移到新机制后删除（另一个 PR），删除前保留现有行为与测试。
- 契约哈希变化后，`core_verifications` 中的旧记录不得再授权注册（`verification_stale`），需重新验证。
- 本 ADR 获批前不实现 C 的编译器降层；先补该操作的契约测试与失败矩阵。
