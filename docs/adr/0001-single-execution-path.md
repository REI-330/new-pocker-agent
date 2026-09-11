# ADR-0001: 单一执行路径（Single Execution Path）

- 日期：2026-09-11
- 状态：accepted
- 决策者：架构 owner

## 背景

`codex/doudizhu-holdem`（47 commits）引入了 `ToolPlan`/`FlowRuntime`，但迁移没有终点，导致：

- `FamilyEngine`（游戏专属引擎）与 `FlowRuntime`（声明式 flow）**两套执行同时在线**；
- 宿主 `plan_for_rules` 的成品 flow 与 `EngineAgent` 产出的模型 plan **两个真相源**；
- 同一 rules 经不同入口走不同执行器，行为漂移；
- 旧引擎里存在已确认的错误语义（如斗地主 `last_play` 不重置）。

"测试全绿"未能阻止这些问题，因为它们是**架构决策漂移**，不是功能缺陷。

## 决策

**系统在任何时刻只允许一个执行器能运行某个玩法。**

- 新执行核心为 `src/pocker_agent/core/`：一个 `Interpreter` 解释 `GamePlan`（数据）。
- 内置玩法一律通过 `host_compile(RulesIR) → GamePlan` 或 `agent_compose` 产生计划，**不再新增游戏专属引擎**。
- 旧引擎（`family_engines` / `doudizhu_engine` / `holdem_engine` / `plugin_engine`）**降级为测试 oracle**，移出生产 dispatch，并在迁移 PR 中删除。
- 沙箱代码（若启用）是**独立的隔离执行协议**，与主 `Interpreter` 互斥使用；同一玩法不得同时依赖两个执行面。

## 备选方案与为何不选

| 方案 | 优点 | 为何不选 |
|---|---|---|
| 继续在旧分支上增量清理 | 复用现有代码 | 双路径是结构问题，增量清理永远收不了口，每加功能要写两遍 |
| 完全从零重写 | 干净 | 丢失精确判题、沙箱、LLM 客户端、配置、前端、测试等已验证资产 |
| 保留双路径、用开关切换 | 迁移期平滑 | 开关会让两套继续漂移，且"哪个生效取决于参数"正是病根 |

## 后果

- 正面：行为可复现、可审计；迁移有终点；模型不在运行时。
- 负面 / 代价：迁移期间需要短暂并存，必须靠删除纪律约束；旧引擎的既有 bug 需要在迁移时用人工确认的 trace 取代其输出作为参照。
- **强制方式**：
  - `tests/test_architecture_invariants.py::test_core_is_a_single_execution_path`
  - `tests/test_architecture_invariants.py::test_core_has_exactly_one_interpreter`
  - PR 红线：结构性改动必须同 PR 删除旧路径；删除积压必须为 0（`docs/engineering-standards.md` §6.3）

## 迁移与删除

按 `docs/development-design-v0.4.md` §14：**arithmetic → shedding → blackjack → doudizhu → holdem**，每个玩法迁完即删旧路径，旧引擎同时保留为 oracle 但移出 dispatch。
每个玩法必须并存四样参照：规则样例、人工确认的黄金 trace、旧引擎差分结果、已知旧缺陷列表。
