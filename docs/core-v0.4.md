# v0.4 core: 单一执行路径

日期：2026-09-12。分支 `v0.4-core`。基线 `main@0d2110d`（v0.2 家族引擎）保持不动。

## 为什么重写

`codex/doudizhu-holdem` 引入了 `ToolPlan`/`FlowRuntime`，但迁移没有终点：
家族引擎与 flow 两套执行同时在线，宿主成品 plan 与模型 plan 是两个真相源，
`run_bots` 对 flow 直接禁用，条款文本与实际执行脱节。**接缝错了，不是文件丑。**

`core/` 是绞杀（strangler）目标：新建的干净执行核心，旧路径按玩法逐个迁移后删除，
不允许再出现两条长期并存的执行路径。

## 分层

```
ToolRegistry (contracts.py)   白名单 + 机器可读契约；模型能看到的就是这里导出的
      │
GamePlan (plan.py)            tools + flow 图；唯一可执行表示，extra=forbid
      │
Interpreter (interpreter.py)  解析 $state.*、只调声明过的 operation、失败全量回滚
      │
playtest (playtest.py)        多 seed 完整对局 + 不变量 + 确定性重放；发货门槛
```

- `cards.py`：`CardRef` 值对象 + 状态编解码（`CardRef` 只在这里定义一次）。
- `zones.py` / `zone_tools.py`：通用牌区（`select` / `move` / `top` / `count` / `verify`）与唯一归属校验；牌区只存 `CardRef`，不新建平行牌堆模型（ADR-0009）。
- `tools.py`：机制（精确算式、状态写入、计分、接牌移牌），不含玩法策略。匹配工具的终局/回收已交还计划（ADR-0009）。
- `registry.py`：核心工具白名单；每个 operation 另带 `input_schema` / `output_schema` / `reads` / `writes` / `feature_constraints` / `config_schema`，`export()` 即未来 agent loop 的 function-calling 工具表。
- `plans.py`：宿主编写的参考计划（当前 `arithmetic_plan`），同时作为黄金 trace。

## 硬规则

1. **只有一个解释器。** 新玩法只能通过声明工具 + 计划表达；不允许回到"每个玩法一个引擎"。
2. **模型不执行代码、不调用工具。** 计划是数据；宿主解释它。任何可执行扩展走沙箱 escape hatch，且是最后手段。
3. **计划未通过 `playtest` 不得标记为可玩。** 门槛包括：能终止、同 seed 重放出逐字节相同的事件、调用方提供的不变量成立。
4. **失败必须事务回滚。** `Interpreter.step` 抛错时 state/events/pc 全部还原（已有测试覆盖）。

## 已完成（增量 1）

- 契约层、计划层、解释器、playtest 门槛、核心工具白名单。
- `arithmetic_plan`：多题、每题独立发牌、答对/正确无解各 1 分、放弃 0 分且**不产生 winner**。
- 17 个测试，覆盖：假"无解"被拒且状态不变、错答可重试、多题发牌、确定性重放、未知 operation/工具被拒、序列化恢复。

对照修复的既有缺陷（分支上的 bug）：
| 分支缺陷 | core 中的表现 |
|---|---|
| `give_up → winners:[0]`（放弃即获胜） | 放弃只置 `finished`，`winners` 保持空 |
| 假 `no_solution` 被接受 | `assert_unsolvable` 由宿主复核后拒绝 |
| `max_rounds`/`target` 在 flow 中丢失 | 计划显式持有并在 view 暴露 |
| flow 与家族引擎/条款不一致 | 只有一套执行，playtest 强制条款 |

## 未完成（按顺序）

- **P0 收尾**：把 API `/api/runtime/*` 与 `/api/simulations` 接到 core；删除 `main` 的 `ArithmeticEngine` 生产路径，旧引擎降级为测试 oracle。shedding/blackjack 依次迁移，迁完即删旧路径。
- **P1 agent loop**：`meta_tools.py`（`propose_ir`/`compose_plan`/`validate_plan`/`simulate`/`playtest`/`repair`/`finalize`），用 `registry.export()` 驱动 function calling，把 `ToolError`/playtest 报告回灌给模型。
- **表现层**：暂缓。素材沿用现有 SVG 与 `view()` 字段；动画只订阅事件流，不进规则层。

## 迁移删除清单（每个玩法迁完立即执行）

- `family_engines.py`、`doudizhu_engine.py`、`holdem_engine.py`：降为 golden trace / oracle，移出生产 dispatch。
- `tools/plans.py` 的宿主成品 flow 模板：删除，改由 core 的 IR→plan 或 agent 组装。
- `EngineAgent` 的"复制骨架"prompt：替换为真正的 tool-calling loop。
- `runtime.py` 里 `tool_plan or plan_for_rules(...)` 的双来源分支：单一来源。
