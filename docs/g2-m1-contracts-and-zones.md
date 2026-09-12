# G2 / M1 交付说明：契约类型化 · 通用牌区 · 拆开匹配

> 状态：**M1 的 1/2/3 项已实现并实测**（含 4 的「回收策略显式化」部分）。4 的其余项、5、6 按计划留给 M2 及后续包。
> 关联：ADR-0009、`docs/g2-capability-subset.md` §3 的 M1 更新、`docs/development-plan-phase2.md` §6 M1。

## 1. 本包落地位置

### 1.1 操作契约类型化（M1.1）

`core/contracts.py`：`OperationSpec` 新增

| 字段 | 含义 |
|---|---|
| `input_schema` / `output_schema` | 机器可读的输入/输出形状（小型 JSON-Schema 子集） |
| `reads` | 操作读取的状态键，供组合编译器检查依赖 |
| `writes` | `effects` 的**只读别名**（保留 `effects` 作为被解释器强制的写集，不破坏兼容） |
| `feature_constraints` | 机制标签，闭集在 `FEATURE_TAGS` |
| `config_schema` | 绑定配置的形状；由 `ToolSpec.config_schema` 复制到该工具的每个 operation |

`ToolSpec` 新增 `config_schema`：**无条件**下发到该工具的每个 operation；operation 若自带冲突的 `config_schema` 则报 `operation_config_conflict`（不允许工具级与 operation 级不同步）。`export()` 对嵌套 schema 做深拷贝，外部修改导出结果不会改变 `contract_hash()`。
本包**未改 `GamePlan` 的持久化字段**，`schema_version` 仍为 `0.4`：按 ADR-0007，只有改变已持久化字段的含义才必须换版本号；操作契约是工具注册表的元数据，不进入存档。
`core/registry.py` 为全部 18 个工具的每个 operation 补齐了上述字段；`params` / `returns` 保留为兼容视图，并由 `tests/test_g2_m1.py` 断言 `params` 与 `input_schema.properties` 一致，避免两处真相。

### 1.2 稳定牌区与通用选择/移动（M1.2）

- `core/zones.py`：纯机制。`state["zones"] = {zone_id: {"owner", "visibility", "cards": [CardRef]}}`，复用 `cards.CardRef`，不新建平行牌堆模型。
  - `select_cards`：数量在界内、无重复 id、卡牌确实属于该区，**零副作用**。
  - `apply_moves`：在副本上逐条应用 `{from,to,card_ids}`，任一非法则整表不变；结束时校验唯一归属。
  - `ownership_problems` / `assert_unique_ownership`：同一 `card.id` 不得同时存在于两个区；`copies>1` 靠唯一 id 区分并守恒。
- `core/zone_tools.py`：`zones` 工具（第 18 个，预算 18/18），operations `select` / `move` / `top` / `count` / `verify`；`select` 等为 `reject_only`，只有 `move` 写 `("zones",)`。
- `Interpreter.project_zones(viewer)`：按 `public` / `owner_only`（仅拥有者或终局可见）/ `hidden`（只给数量）投影到 `view()["zones"]`。这是**视图**规则：它阻止隐藏牌从投影泄露，但**不**等同于每视角动作权限（工具层无 viewer 上下文，见 §4）。
- `invariants.authoritative_cards`：有 `zones` 时只统计权威牌区，避免把 `picked_*` 等派生选择结果重复计数；无 `zones` 的旧计划回退到原来的递归扫描。

### 1.3 匹配机制拆职责（M1.3）

| 职责 | 之前 | 现在 |
|---|---|---|
| 合法性 | `matching.play` 内联 `pattern.match` | 仍是 `pattern.match`（已独立） |
| 移牌 | `matching.play` | `matching.play` 只移牌；返回 `hand_empty` |
| 计分 | 不在匹配工具 | `score_settle.call`（不变） |
| 终局 | **工具内置**「空手获胜」 | **计划显式声明**：`crazy_eights` / `uno` 新增 `empty{seat}`→`declare{seat}` |
| 回收 | `matching.draw(recycle=True)` 默认回收 | `recycle` 默认 `False`；两个消费者显式传 `True` |

### 1.4 回收策略显式化（M1.4 的一部分）

`MatchingTool.draw` 的 `recycle` 默认 `False`，回收策略由计划决定（见 ADR-0009）。回合方向与 skip 的消费时机原本就写在 `uno_plan` 的 `advance` / `reset_skip` 节点里，本包未改；`trigger.apply` 内部的回收与 `trick`/`hidden_draw` 的内置策略**未改**（见 §4）。

### 1.5 兼容层与删除判据（M1.6 的记录）

`matching.play` / `matching.draw` 保留为薄适配：它们与 `zones` 共用 `CardRef` 与 `PatternTool`，没有第二份行为实现。消费者只有 `crazy_eights` 与 `uno`；ADR-0009 记录删除判据（两族不再引用 `matching`，`core_registry()` 不再注册 `matching`），M5 前端迁移后执行。

## 2. M1 准出测试的实际结果

`tests/test_g2_m1.py`（13 项）+ `tests/test_architecture_invariants.py`：

| M1 准出条件 | 强制它的测试 | 结果 |
|---|---|---|
| 选牌数量错误不能改状态 | `test_a_wrong_selection_count_cannot_change_state` | PASS（`serialize()` 前后逐字节相同） |
| 选牌所有权错误不能改状态 | `test_selecting_a_card_the_zone_does_not_own_cannot_change_state` | PASS |
| 出完手牌但终局是「达到分数」不得自动结束 | `test_matching_play_does_not_declare_a_winner_for_an_empty_hand`、`test_a_score_terminal_keeps_running_after_the_hand_empties` | PASS（`finished=False`、`legal_actions==["play"]`） |
| 重复副本守恒 | `test_duplicate_copies_are_conserved_through_a_move`、`test_the_same_card_id_in_two_zones_is_an_ownership_violation`、`test_move_is_atomic_when_one_of_several_moves_is_invalid` | PASS |
| 同一 move/score/turn 操作用于两种不同组合 | `test_composition_consumers_are_real_plans_that_share_generic_ops`（两个样例真实跑完 playtest 并断言共享 operation） | PASS |
| 旧玩法受影响部分真实运行无退化 | `test_crazy_eights_still_finishes_with_a_plan_declared_terminal`、`test_uno_still_finishes_with_a_plan_declared_terminal`、既有 `test_s2_axes` / `test_s7_axes` / `test_session_api` | PASS |
| 契约类型化无缺口 | `test_every_operation_declares_a_typed_contract`、`test_tool_config_schema_is_copied_onto_its_operations`、`test_feature_constraints_use_the_declared_vocabulary` | PASS |
| 牌区选择结果可持久化 | `test_a_zone_selection_result_survives_serialize_and_restore` | PASS |
| 每个导出的 operation 都真实可调用 | `test_every_exported_operation_is_callable_on_its_tool` | PASS（含 `deck.cards` → `catalog`） |
| 必填/分支输出契约与实际一致 | `test_required_inputs_are_declared_in_the_input_schema`、`test_branching_outputs_are_a_union_not_a_false_flat_object`、`test_matching_play_declares_the_discard_it_reads` | PASS |
| 非法（重复 id）牌区不得丢牌 | `test_apply_moves_refuses_a_table_whose_zone_already_shares_a_card_id` | PASS（拒绝而非静默丢牌） |
| `card_conservation` 不重复计数派生选择 | `test_card_conservation_counts_authoritative_zones_not_selections` | PASS |
| 契约导出不是活对象的别名 | `test_contract_export_does_not_alias_the_live_schemas` | PASS |
| 视图投影尊重可见性 | `test_zone_view_projection_respects_visibility_and_owner` | PASS |

两个组合样例（`core/compositions.py`，开发期样例，不是参考玩法）：

- `zones_exchange_compare`：双区域选择 + 交换 → `rank_compare` → 计分；
- `zones_exchange_suit_score`：**同一组** `zones.select` / `zones.move` / `score_settle.call` / `wait` 回合 → 按花色计分。

## 3. 真实运行证据

在专用数据目录 `artifacts/g2-runtime` 启动当前 checkout：

```powershell
$env:POCKER_AGENT_DATA_DIR = 'C:\Users\hr206\new-pocker-agent\artifacts\g2-runtime'
uv run python scripts/run_web.py --skip-build --port 8012
```

- `uv run --frozen pytest -q` → **226 passed**（M0 基线 202；`1e624d8` 为 217，本轮审阅修复 +9）。
- 架构不变量 → **17 passed**。
- `uv run python scripts/doctor.py --url http://127.0.0.1:8012` → 全项 PASS，`server code matches the working tree`（本轮指纹 `4edafa448bd1`），`GET /api/games :: 8 games`。
- `uv run python scripts/e2e_smoke.py http://127.0.0.1:8012` → **28 checks passed**（含 8 个参考玩法的可玩性 / 隐私 / 终局检查）。
- `uvx --offline ruff check <CI 列表>` → All checks passed。

## 4. 独立审阅修复（审阅基线 `1e624d8`）

独立审阅（固定 `1e624d8`，工作树未修改）确认了 8 个问题，本轮全部修复：

| # | 问题 | 修复 |
|---|---|---|
| P1 | `deck.cards` 导出但方法是 `catalog`，无法调用 | `OperationSpec("cards", method="catalog")`；新增逐 operation 可调用性测试 |
| P1 | `pattern.match` 未声明必填 `card`/`top` | `_match_args` 的 `required=("card","top")` |
| P1 | `trick.play` 输出契约与实际分支不符 | 新增 `one_of`，输出契约声明两个真实分支 |
| P1 | 重复 card id 会被静默丢牌 | `apply_moves` 开始时先 `assert_unique_ownership`，并按对象身份而非 id 删除 |
| P1 | `card_conservation` 重复计数选择结果 | 新增 `authoritative_cards`：有 `zones` 只统计牌区，旧计划回退递归扫描 |
| P2 | 工具级 `config_schema` 非无条件下发 | 冲突即报 `operation_config_conflict`，否则一律下发 |
| P2 | 导出存在可变对象别名 | `export()` 深拷贝嵌套 schema，并加 hash 稳定性测试 |
| P2 | `matching.play` 的 `reads` 漏 `discard` | `reads` 补 `discard` |

同时按审阅建议补了「zones 视图投影」：`view()["zones"]` 现在按可见性/拥有者过滤。

## 5. 未做 / 降级（诚实说明）

1. **M1.4 未全做**：`trigger.apply` 内部仍有「牌堆空则用桌面回收」的实现；`trick`/`hidden_draw` 的内置策略（墩数上限终局、`discard_pairs` 每对 +1 且 key 写死 `pairs`、`ask` 失败自动摸牌）**未拆**。计划 M1.3 明确「按第二个消费者的需要拆」，属 M2 欠账。
2. **每视角动作权限未实现**：`project_zones` 只做视图过滤；工具层没有 viewer 上下文，因此从 hidden/他人 `owner_only` 牌区调用 `zones.select` **仍不会被拒绝**。真正的权限点在动作输入验证器与 API（M1.5 / M5）。当前单用户 API 固定 `viewer=player-1`。
3. **M1.5 未做**：没有新增通用动作 Schema `{action_id, input_values}` 与输入验证器；`input_schema/output_schema` 目前是声明，解释器尚未执行完整 JSON Schema 校验。组合样例通过 `Interpreter.step(action, **payload)` 传入 `hand_cards` / `market_cards`；HTTP `ActionInput` 仍只有 `card_index/expression/declared_suit/amount`，因此 `zones` 动作**还不能从浏览器驱动**。
4. **M1.6 部分**：兼容层已薄化并记录删除判据，但 `matching` 工具仍在注册表；删除要等 M5 消费者迁移。
5. `core/compositions.py` 是**开发期样例**，用于满足 §6.4 的「两个真实消费者」并证明操作复用；它们不是 G2 验收玩法，也未注册到 `/api/games`，不得计入「陌生玩法生成成功率」。M2 用真正的组合产物替换。
6. `zones` 工具用满 18/18 预算。后续若 M2 需要再增工具，必须先有 ADR。

## 6. 下一步（M2 依赖）

- 组合编译器按 `zones` / `pattern` / `rank_compare` / `score_settle` 的**类型化契约**降层，按需拆 `hidden_draw`（场景 C 的每对 +2 需要）。
- `generation_source`、artifact 与验证凭据按 ADR-0007 / ADR-0008 落地（M3）。
