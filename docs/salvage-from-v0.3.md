# v0.3 可回收清单（受控移植）

> 旧仓库：`REI-330/pocker-agent`，参考分支 `origin/codex/doudizhu-holdem`
> 目的：**新仓库是干净的，旧仓库是采石场——不是垃圾场，也不是倾倒场。**
> 规则：本清单之外的任何 v0.3 代码**不得直接搬入**。每一项移植都必须走独立 PR，附测试，并在本表更新状态。

---

## 为什么需要这份清单

v0.3 的问题不是"代码没用"，而是**接线的架构是错的**（双执行路径、双真相源、按玩法分叉的工具、宿主成品模板当 prompt）。

所以移植原则是：

```
移植「算法 / 词汇 / 数据 / 素材」，不移植「接线 / 引擎 / 模板」
```

直接 `copy` 整个 `tools/` 或 `flow_plans/` 会把污染一起带进来——那正是新仓库要避免的。

---

## A. 低污染，较完整可用（每项一个 PR）

| 项 | 来源 | 用途 | 目标 | 前置条件 | 阶段 |
|---|---|---|---|---|---|
| QuickJS 隔离 + 帧校验 | `plugin_schema.py` `plugin_sandbox.py` `plugin_worker.py` | 沙箱逃生口 | `src/pocker_agent/sandbox/` | 适配 `core/contracts`；把"帧校验"与主执行面显式隔离（ADR 要求） | S3 |
| 自然语言用例语料 | `benchmarks/common_games.json` | `capability_check` / 生成基准的语料种子 | `benchmarks/` | 把 `expected`（v0.2/0.3 形状）映射到 `RulesIR` 字段 | S2 |
| 真实模型回归脚本 | `scripts/benchmark_games.py` | 端到端生成基准 | `scripts/` | 改为产出"IR + Plan + playtest 结果"；加"生成必过 playtest"断言 | S1/S4 |
| 工程问题清单 | `docs/development-issues.md` | 路线图输入 | `docs/`（作为历史输入引用） | 标注哪些已由 v0.4 解决 | 现在 |
| 前端壳与素材 | `frontend/src/components/ModelSettings.tsx`、`assets/cards/*`、`PlayingCard` | UI 复用 | `frontend/` | 与 v0.4 `Interpreter.view()` 字段对齐 | S2 |
| 历史验收记录 | `docs/mainstream-games-review.md`、`benchmarks/acceptance-results.json` | 追溯 | `docs/archive/` | 标注"验收于旧 commit，不代表当前状态" | 现在 |

## B. 需重构后可用（移植算法，不移植接线）

**共同前提**：v0.3 工具把状态放在**实例字段**（如 `zones.zones`、`draw_discard.stock`），与 v0.4 的"单一 state dict"冲突。移植时必须：

1. 状态全部经由 `state` dict 传参（工具无状态）；
2. 补 `OperationSpec` 契约（`params/requires/ensures/effects/failure`）；
3. 由架构不变量测试检查命名不含游戏名。

| 项 | 来源 | 目标 axis | 备注 | 阶段 |
|---|---|---|---|---|
| 可见性账本 | `tools/zones.py` | `info_set` | 保留"按 region + 可见性"的思路，重写为纯 state | S2（P0 axis） |
| 匹配 / 分组 | `tools/matching.py`（`matches`/`follow_suit`/`group_by`） | `pattern_lang` | 与下面两项合并成**一套**参数化牌型引擎 | S2（P0 axis） |
| 牌型 / 排序 | `tools/ranking.py`（`best_of`/`five_card_rank`）、`tools/patterns.py`（`resolve_trick`/`detect_meld`） | `pattern_lang` | **禁止**再出现 `doudizhu_hand_rank`/`holdem_hand_rank` 这类分叉 | S2（P0 axis） |
| 压牌判定 | `tools/climbing.py`、`tools/doudizhu.py`（`classify`/`beats`） | `pattern_lang` 的 follow 规则 | 算法可复用 | S2 |
| 下注账本 | `tools/betting.py`（`PotTool`）、`tools/holdem.py`（`BettingRoundTool`/`settle_pots`/`side_pots`） | `betting` | 整数守恒、边池退款逻辑值得保留 | S3 |
| 结算 / 胜负 | `tools/settlement.py`、`tools/gameflow.py` | 通用结算原语 | 参考实现，非直接搬运 | S2/S3 |
| 抽弃 / 回合 / 触发 | `tools/draw_discard.py`、`tools/turns.py`、`tools/triggers.py` | `turn_adapter` / `trigger` | `triggers.py` 在 v0.3 是空壳，只借接口形状 | S2/S3 |
| 参考 flow（作为规格） | `tools/plans.py` 的 arithmetic / shedding flow、`flow_plans/*.py` | `host_compile` 的**行为规格** | 照着**重写**，不搬运：它们正是"宿主成品模板"问题的来源 | S2 |

## C. 仅作 oracle / 参照（不进生产 dispatch）

| 项 | 用途 | 必须同时保存 |
|---|---|---|
| `family_engines.py`、`doudizhu_engine.py`、`holdem_engine.py` | 黄金 trace 差分参照 | 规则样例 + 人工确认的 trace + **已知旧缺陷列表**（否则会把旧 bug 固化成新测试） |
| `docs/review-and-acceptance.md`、`docs/mainstream-games-review.md` | 历史验收 | 标注对应 commit 与日期 |

## D. 禁止移植（污染源）

| 项 | 原因 |
|---|---|
| `engine_agent.py`（"复制骨架"prompt） | 让模型变成复读器；模型 plan 与宿主 plan 双真相 |
| `runtime.py` 的 `tool_plan or plan_for_rules(...)` | 双来源切换；行为取决于参数 |
| `rule_executor.py` + `flow_runtime.py` 直接复制为第二执行器 | 会重新长出第二条执行路径（可借鉴 `$state` 解析/回滚思想，但必须在 `core/` 内实现） |
| `tools/*_turn.py`、`*_hand_rank.py`、`*_settle.py` 等按玩法分叉的工具 | "一游戏一工具"，只加法增长 |
| `tools/plans.py` 作为架构（宿主成品 flow 当模板） | 迁移无终点、两个真相源 |
| `game_rules.py` 把游戏专属 rule model 当执行权威 | 只保留为**校验输入**，不作执行 |

---

## 移植流程（每个 A/B 项）

```text
1. 建 issue：来源、目标、需要哪些测试
2. 独立分支 + 独立 PR（≤400 行有效 diff，遵循 engineering-standards §5）
3. 必须包含：契约测试 + 单元/属性测试 + 受影响族的 playtest
4. 若引入新工具：通过架构不变量（命名、预算、死工具）
5. 更新本表状态（✅ 已移植 / 🚧 进行中 / ⛔ 放弃 + 原因）
```

## 状态表

| 阶段 | 项 | 状态 |
|---|---|---|
| 现在 | 工程问题清单归档 | ⛔ 暂不搬（旧仓库可查） |
| S0 | `benchmarks/common_games.json` → 语料库 | ✅ 已内置 `core/data/corpus.json`（30 个玩法 + 所需 axis，覆盖率报表在线） |
| S0 | 前端壳与素材对齐 | ✅ 已按原型设计系统重建（`library/rules/workspace/replay`），使用同一批牌面素材 |
| S1 | 生成基准脚本改造 | 🚧 待办 |
| S2 | `pattern_lang`（matching/ranking/patterns/climbing/doudizhu） | 🚧 待办（当前最大缺口：20/30 玩法需要它） |
| S2 | `info_set`（zones） | 🚧 待办（当前最大缺口：20/30） |
| S3 | 沙箱三件套 | 🚧 待办 |
| S3 | `betting` / `trigger` / `turn_adapter` | 🚧 待办 |
| — | C/D 类 | 仅参照，不排期 |
