# ADR-0003: 再提 core 工具预算 16 → 17（hidden_draw）

- 日期：2026-09-11
- 状态：accepted
- 决策者：架构 owner

## 背景

`tests/test_architecture_invariants.py` 的棘轮在 ADR-0002 后为 16，S5 结束时是 **15/16**。

S6 要覆盖“从对手隐藏手牌获取牌”的一类玩法（语料库 `go_fish`、`old_maid`），需要一个新的正交机制：

- `hidden_draw`：按位置/按点数从**他人隐藏手牌**取牌、成对消除、按手牌生成可询问选项、判定牌堆耗尽。

它与现有工具都不重叠：`pattern`/`matching` 只操作自己的牌与公共顶牌，`ledger`/`betting` 是筹码，`point_total`/`hand_rank` 是牌值/牌型。`matching` 的 `draw` 只从公共牌堆摸牌，不触碰他人手牌。

## 决策

**预算 16 → 17，新增 `hidden_draw` 工具。**

保留约束（不变）：棘轮只在测试里修改；每个注册工具必须被某个参考计划使用；契约（effects/requires/returns）由解释器强制。

同时引入一个**引擎级小改动**：wait 节点的通配输入（`ask:*`）现在会按 `state.available_actions` 展开为**具体动作**（`ask:7`）。这是“参数化动作”第一次成为一等公民——引擎只展开与过滤，仍然不发明动作。

## 备选方案与为何不选

| 方案 | 优点 | 为何不选 |
|---|---|---|
| 用 `matching.draw` + `state.update` 手写偷牌 | 不新增工具 | 偷牌要按位置/点数选源手牌并保持隐藏语义，手写会绕过 `effects` 检查 |
| 把 `hidden_draw` 并入 `matching` | 少一个工具 | `matching` 的契约范围会膨胀成“什么都干”，重演旧分支的杂糅工具 |
| 一次提到 20 | 少写 ADR | 预算压力是防膨胀的主要机制；每次只提必要的量 |

## 后果

- 正面：Go Fish/抽乌龟类可表达；参数化动作让“询问点数”“叫牌”等玩法有了通用表达。
- 负面 / 代价：`legal_actions` 的语义多了一层展开，必须同时测“展开”和“拒绝字面通配符”。
- **强制方式**：`test_core_tool_budget_is_a_ratchet`（17）、`test_every_registered_tool_is_used_by_a_reference_plan`、以及 `tests/test_s6_axes.py` 中“字面 `ask:*` 被拒、只有具体选项可提交”的用例。

## 迁移与删除

不涉及旧路径。
