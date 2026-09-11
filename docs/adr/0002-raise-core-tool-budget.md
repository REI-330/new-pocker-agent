# ADR-0002: 提高 core 工具预算 12 → 16

- 日期：2026-09-11
- 状态：accepted
- 决策者：架构 owner

## 背景

`tests/test_architecture_invariants.py` 用棘轮把 core 工具数限制在 12。S3 结束时是 **11/12**。

S4 需要三个新的正交机制才能覆盖扑克类玩法（语料库 5 个玩法需要）：

- `betting`：无上限下注轮状态机（fold/check/call/raise/all_in、最小加注、轮次完成判定）。
- `ledger`：筹码账本（提交、主池/边池、退款、结算）。
- `poker_hand`：五张牌型评分与比较（`hand_rank` 轴）。

三者互不正交于任何现有工具：现有的 `pattern` 是接牌/组合类牌型语言，`rank_compare` 只比单张牌力，都不表达下注轮或边池。

## 决策

**把 core 工具预算从 12 提高到 16，并新增 `betting` / `ledger` / `poker_hand` 三个工具。**

保留的约束：

- 棘轮改为 16（`CORE_TOOL_BUDGET`），仍然只允许在测试里修改 —— 提高预算必须改测试，天然触发 review。
- 每个新工具必须被某个参考计划使用（`test_every_registered_tool_is_used_by_a_reference_plan`），否则 CI 失败：**预算不能被"死工具"占位**。
- 三个工具都要在 `registry.export()` 里声明契约（effects / requires / returns），由解释器强制。

## 备选方案与为何不选

| 方案 | 优点 | 为何不选 |
|---|---|---|
| 把下注/账本塞进现有 `state` 工具 | 不新增工具 | `state.update` 是 `"*"` effects 的通用写入器，塞进领域逻辑会让契约检查失效，退化成"什么都能改" |
| 只做 `betting`，账本用 `state.update` 手写 | 少一个工具 | 边池/退款/奇数筹码分配是易错逻辑，必须集中一处并可测试 |
| 提高预算到 24 | 一次到位 | 预算越大越容易"顺手加工具"；16 覆盖剩余缺口（betting/ledger/hand_rank + 一个余量），保持压力 |

## 后果

- 正面：扑克类玩法（Texas / Omaha / Five-card draw / Canasta）可表达；下注与边池逻辑集中、可测。
- 负面 / 代价：工具数增加，"提升为 axis"的压力变小；靠使用性不变量与 ADR 约束。
- **强制方式**：`tests/test_architecture_invariants.py::test_core_tool_budget_is_a_ratchet`（值 16）+ `test_every_registered_tool_is_used_by_a_reference_plan`。

## 迁移与删除

不涉及旧路径。下一次触及预算前，应先评估是否把某个已复用 ≥2 次的机制提升为参数化 axis（`docs/engineering-standards.md` §6.4）。
