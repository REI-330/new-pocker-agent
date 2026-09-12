# ADR-0010: 组合玩法的终局语义

- 日期：2026-09-14
- 状态：accepted（**实现状态：已实现**，M3 终局模式包；B/C 效果类型后续包沿用本语义）
- 决策者：架构 owner

## 背景

`ComposedRulesIR` 目前的终局只有一种：`TerminalSpec.max_rounds`（必填）在每轮 resolve 之后按轮数结束。场景 B（「任何人达到 6 分立即结束」，且「完成 20 个行动回合后结束」）与场景 C（「共 12 个行动回合」，「不因提前空手自动结束」）都需要**按行动数或分数阈值**结束。如果直接把这些条件塞进编译器而不先固定语义，B/C 会各自定义「何时判定、谁算赢、边界值是否多执行一次」，从而产生同一机制两种解释。

## 决策

**`TerminalSpec` 支持三种上界/阈值，均为宿主声明、编译器确定性降层、独立监测器可核对。**

```text
TerminalSpec
  max_rounds: int | None          # 按轮结束
  max_actor_actions: int | None   # 按行动回合数结束
  score_reaches: int | None       # 任一座位达到阈值即提前结束
  winner: "highest_score"         # 本轮固定
  tie: "allow"                    # 本轮固定
```

固定语义（B/C 必须按此实现，不得各自解释）：

1. **计数口径**。`max_actor_actions` 统计**所有座位完成的行动**（人类与机器人、出牌/交换/摸牌/pass 各计一次），即 `state.action_count`；被 skip 的座位**不计**。`max_rounds` 统计**完成的轮**（编译器在每轮 resolve 之后自增 `state.round`）。
2. **阈值为任意座位**。`score_reaches=N` 的语义是「**任一**座位的分数 `>= N`」。编译器把它降层为对声明座位数的有限 `any` 展开（`players <= 4`），不引入动态集合遍历。
3. **多条件取 OR**。三种条件同时声明时，任意一个成立即结束。至少要有一个**硬上界**（`max_rounds` 或 `max_actor_actions`）；只有 `score_reaches` 不构成有界性保证，IR 校验直接拒绝。
4. **判定时机**。终局判定发生在**当前行动的移动/计分等效果完成之后**，在**推进到下一座位之前**；对按轮计分的玩法（`flow.resolve` 里的 `compare`），还必须在 `resolve` 完成之后、进入下一轮之前再判定一次。因此编译器在两处发出「终局闸门」：行动后与解析后。
5. **边界值立即结束，不多执行一次行动**。`action_count == max_actor_actions` 成立时当次行动已计入并完成其效果，随后立即结束；不会先推进座位再结束。`score >= N` 同理。
6. **胜者与快照确定性**。结束时以**当时的 `scores` 列表**计算赢家：`winner = argmax(scores)`，`tie=allow` 时所有并列最高分的座位都是赢家（可多个）。分数快照保留在 `state.scores`，不被终局写入重置。`winners` 由 `winner_resolve.call(mode="max")` 产生，模型不得写死。
7. **无等待环必须有界**。行动后闸门保证 `max_actor_actions`/`score_reaches` 可在轮内提前结束；轮上界保证 `max_rounds`。结构分析仍要求每个环含 `set_round`/`set_turn_index`，且至少一个硬上界存在。
8. **按轮计分时行动预算只在本轮 resolve 之后判定**。若 `flow.resolve` 含 `compare`，轮内行动不改变分数，行动后闸门只判 `score_reaches`，`max_actor_actions` 只在解析后闸门判定；同时要求 `max_actor_actions` 是 `players` 的整数倍，否则 IR 校验失败（`terminal_action_budget_must_align_with_rounds`）。这保证「不多执行一次行动」（第 5 条）与「该轮计分不被跳过」同时成立。无 `compare` 的玩法（行动内计分，如场景 B/C）不受此限制。

## 备选方案与为何不选

| 方案 | 优点 | 为何不选 |
|---|---|---|
| 把阈值判定做成各玩法自定义的 `assign` + 分支 | 编译器改动小 | 每个玩法自己决定「何时判定/谁算赢」，正是本 ADR 要消除的分歧；且独立监测器无法核对 |
| 只加 `max_actor_actions`，不加 `score_reaches` | 够场景 C | 场景 B 的「达到 6 分立即结束」无法表达，被迫用近似玩法 |
| `score_reaches` 指某个指定座位（`seat` 字段） | 更灵活 | 当前两个场景都是「任意座位」；在没有消费者前加座位维度是投机复杂度，暂不引入 |
| 阈值为 `>` 而不是 `>=` | 更简单 | 「达到 6 分结束」按常规读作 `>= 6`；用 `>` 会多打一轮，语义含糊 |
| 在推进座位后判定 | 实现最省事 | 会让边界值多执行一次行动（第 N+1 个行动的座位被推进），违反第 5 条 |

## 后果

- 正面：B/C 共用一套终局判定；阈值、计数、胜者都可由 `contract_check` 独立核对。
- 正面：`max_rounds` 变为可选后，`compare` 容量下界改用「行动预算换算出的最大轮数」，容量检查仍然健全。
- 负面 / 代价：编译器多两个闸门节点；`TerminalSpec.max_rounds` 由必填改为可选，需要更新既有测试与文档。
- **强制方式**：失败案例矩阵（下表）逐条成为可执行用例；`contract_check` 的 `terminal` 监测器必须独立于编译器重算上述第 1/2/3/6 条，并由变异测试证明「写死赢家」「改阈值/轮数/行动数」会被拒绝。

## 失败案例矩阵

| 输入 / 故障 | 必须观察到的结果 |
|---|---|
| 只声明 `score_reaches`，无硬上界 | IR 校验失败：`terminal_requires_a_hard_bound` |
| 阈值达成后仍多执行一次行动 | `contract_check.terminal`：`action_count` 超过声明阈值或轮次超界 |
| 写死 `winners` 与 `argmax(scores)` 不一致 | `contract_check.terminal` 拒绝 |
| `max_actor_actions` 把被 skip 的座位计入 | 行动计数与独立逐个行动统计不符（B 的 skip 用例） |
| `max_rounds` 与 `score_reaches` 同时声明，阈值先达成 | 允许提前结束；独立监测器要求「要么阈值达成、要么某个硬上界恰好用尽」 |
| 阈值判定在下一轮效果之后才发生 | 终局状态与「立即结束」不符（分数/轮次快照被后续效果改写） |
| 平局时只保留一个赢家 | `contract_check.terminal` 要求 `winners == argmax(scores)` 的全部并列 |
| 含 `compare` 的玩法声明非整除的 `max_actor_actions` | IR 校验失败：`terminal_action_budget_must_align_with_rounds` |
| 含 `compare` 的玩法在轮中因行动预算提前结束、跳过该轮计分 | 不允许：行动预算只在解析后闸门判定，该轮照常 resolve |

## 迁移与删除

- 现有只声明 `max_rounds` 的 IR（含场景 A 与三个开发期样例）行为不变，仅字段由必填改为可选。
- `max_rounds` 的旧存档/存档指纹不变；`TerminalSpec` 是 IR 结构而非 Plan 结构，Plan Schema 无需再升版。
- 本 ADR 的失败矩阵在 `tests/test_g2_m3_terminal.py` 落地；实现完成后状态为已实现。
