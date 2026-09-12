# G2 / M2 交付说明：ComposedRulesIR 与确定性降层编译器

> 状态：**M2 的 1～6 项已实现并实测**（场景 A 的人工 IR 可解析、诊断、编译并完整模拟）。
> 关联：ADR-0005、ADR-0007、`docs/development-plan-phase2.md` §6 M2、`docs/g2-acceptance-scenarios.md` 场景 A。
> 本包是**开发草案**：正式试玩入口（注册、验证凭据、Agent 生成）按计划在 M3/M4/M5 落地。

## 1. 本包落地位置

新增 `src/pocker_agent/core/rules/`（递归进入架构扫描，仍只有 `interpreter.py:Interpreter` 一个解释器）：

| 文件 | 职责 |
|---|---|
| `expr.py` | 类型化表达式 AST（字面量 / 引用 / 整数运算 / 比较 / 布尔 / 有限集合），以及到 `logic.evaluate` 载荷的降层与静态校验 |
| `composed.py` | `ComposedRulesIR`（`kind: composed`，Schema `0.5`）及预算上限、跨字段校验 |
| `requirements.py` | `derive_requirements(ir)` / `resolve_composition(ir, catalog)` / `axes_for(ir)` |
| `compiler.py` | 确定性降层、`source_map`、`CompileError`、`CompiledRules` |

`core/ir.py` 新增设计入口判别分支：

```python
DesignIR = Annotated[已知八族… | ComposedRulesIR, Field(discriminator="kind")]
DESIGN_ADAPTER = TypeAdapter(DesignIR)
parse_design_ir(payload)     # 接受 composed
```

`IR_ADAPTER`（Agent 提示词用的 JSON Schema）**保持已知八族不变**，因此 prompt 不会提前宣传 `composed`；`required_axes` / `check_ir` 对 composed 走 `axes_for`。`GamePlan.schema_version` 扩为 `Literal["0.4","0.5"]`（ADR-0007）：本包字段集未变，编译器仍产出 `0.4`，`0.5` 留给 M3 的绑定拆分；两版本的 `plan_fingerprint` 不同（内容哈希不是验证凭据）。

## 2. ComposedRulesIR（ADR-0005 / 计划 §4.1）

`extra="forbid"`，未知字段、未知效果种类、未知表达式 op、未声明宏一律在解析期拒绝。

| 字段 | 内容 |
|---|---|
| `meta` | 标题/说明。**没有 `game_id` 字段**，结构上排除按名字分派 |
| `requirements` | 需求条款（id/text/source/status/nodes），`nodes` 是 IR 路径，编译错误据此回报条款 ID |
| `players` | 人数 2～4 |
| `deck` | ranks/suits/copies/显式 values；`values` 的键必须是 ranks 的子集，总数 ≤108 |
| `zones` | id/visibility/scope（`shared`/`player`）；每张牌唯一归属，牌区 ≤24 |
| `setup` | `deals`（恰好一条 `per_seat` 手牌 + 至多一条共享发牌）+ `stock_zone`；发牌数不得超过牌组 |
| `variables` | 有类型、有初值的用户变量；不得与保留状态键同名 |
| `actions` | actor=current；`guard` 表达式；`inputs`（`card_selection`，actor/shared 作用域，min/max）；`effects`（select/move/move_top/assign） |
| `flow` | `round_action`、`start_seat`（`seat0`/`round_parity`）、`resolve` 效果序列（compare / move_top） |
| `scoring` | 计分规则（on_outcome/points/recipient），供 compare 按 id 引用 |
| `terminal` | `max_rounds`、`winner=highest_score`、`tie=allow` |
| `macros` | 形状已定义；**M2 非空即拒绝**（`macros_not_supported_in_m2`），留给 M4 |

### 2.1 表达式语言（拒绝任意代码）

只允许：`lit`、`ref`（`variables.*` / `scores[.i]` / `round` / `actor` / `action_count` / `input.*`）、`not`、`add/sub/mul/mod`、`eq/lt/le/gt/ge`、`all/any`、`count/join`。

- `eval` / 动态 import / 反射 / 任意状态路径 / 卡片对象字段直读 **在 Schema 层被拒**（`path` 有严格正则，`op` 是闭集判别符）。
- 深度 ≤ `MAX_EXPR_DEPTH`(12)；引用必须指向已声明变量（`validate_expression`）。
- 降层为 `logic.evaluate` 的嵌套表达式，引用变成 `$state.<path>` 字符串；解释器只解析、不求值代码。

## 3. 能力推导与解析（M2.2）

`derive_requirements(ir)` 逐机制节点列出依赖：**operation / feature / zone / variable / input**，各带 IR 路径与条款。它不看轴名，直接落到 `zones.select`、`rank_compare.call`、`score_settle.call` 等真实 operation。

`resolve_composition(ir, catalog)`：

1. 每个 `tool.operation` 必须在目录中存在（缺任一**变体**即失败，不只是缺 axis）；
2. feature 必须在 `FEATURE_TAGS` 闭集；
3. 交互输入种类必须是已实现集合（当前 `card_selection`）；
4. 轴状态由 `capability_check(axes_for(ir))` 判定。

`axes_for` 刻意**不**把 `zones.select` 映射到 `pattern_lang`：没有 `zones` 轴，把它算作牌型轴正是 M0 删掉的“标签冒充机制”。牌区/选择以 operation 粒度在 `resolve_composition` 检查。

场景 A 的解析结果：`operations ⊇ {zones.select, zones.move, zones.top, rank_compare.call, score_settle.call, winner_resolve.call, logic.evaluate, state.update, deck.deal}`，`axes == (info_set, rank_compare, score_settle, sequential_turn)`，全部 stable。

## 4. 确定性降层（M2.3 / M2.5）

编译器只按**机制**降层，`game_kind` 恒为 `composed`，不读标题/game_id。节点生成（场景 A 共 33 个）：

| IR 语义 | 生成的 Plan 节点 |
|---|---|
| setup | `logic.evaluate(join seed)` → `deck.deal` → `state.update(zones/scores/…)` |
| 回合/轮次/终局 | `round_start` → `second_seat` → `zone_<z>` → `turn(wait)` → 动作效果 → `advance_turn` → `turn_check` → `resolve_*` → `end_round` → `round_check` → `finish/declare/end` |
| `select` | `zones.select(state, zone=$state.zone_<z>, card_ids=$state.input.<id>, min,max)` |
| `move` | `zones.move`（原子，按选择结果的 `ids`） |
| `move_top` | 有界展开为 `zones.top` + `zones.move` ×count |
| `compare` | `rank_compare.call` → `branch($state.cmp.outcome)` → 各 outcome 的 `score_settle.call` |
| `assign` | `logic.evaluate` → `state.update(v_<name>)` |

回合方向/起始座位/轮数/终局全部来自 IR 字段：`start_seat=round_parity` → `first_seat=(round-1)%players`；compare 需要恰好两人（否则 `compiler_error:compare_requires_two_players`）。

**确定性绑定**：同一规范化 IR + `COMPILER_VERSION`（`m2-0.1`）+ `registry.contract_hash()` → 相同 `plan` 与 `plan_fingerprint`；`CompiledRules` 同时带 `ir_hash` 与 `source_map`。`generation_source=composed_rules` 由实际编译路径记录。

**形状测试**：只改 `meta.title`/`description` 的两份 IR 生成**完全相同**的 Plan 节点与指纹（`test_the_same_mechanism_combination_compiles_to_the_same_structure`）。

**预算**：展开节点 ≤ `MAX_PLAN_NODES`(512)，`step_limit` ≤1024；玩家/牌/牌区/动作/选牌/宏等上限见 `composed.py` 常量，超出即在解析或编译期失败。

**控制流静态检查**（`rules/structure.py::analyse_control_flow`）：编译后检查

- 每个节点都从 `entry` 可达（不可达节点报 `unreachable_plan_nodes`）；
- 每个环都经过一个**显式有界计数器**节点：回合环经 `set_turn_index`（界=`players`），轮次环经 `set_round`（界=`terminal.max_rounds`）；未被计数器约束的环报 `unbounded_cycle`。

分析器在**没有**计数器知识时返回 `inconclusive=True`，即「不能证明终止」而不是「已证明终止」；编译器自身只生成上述两种有界环。

### 4.1 source map

`source_map = {version, compiler_version, ir_hash, nodes, paths, clauses}`：`nodes[node_id] = {path, clause}`，并给出 `path→[nodes]`、`clause→[nodes]` 反向索引。编译器在每条 `add` 时用 `clause_for(ir, path)` 解析条款（最长前缀匹配，未命中为 `unmapped`）。

### 4.2 编译错误定位

`CompileError(ToolError)` 带 `code / path / clause`：

```text
compiler_error:compare_requires_two_players:flow.resolve.0:got 3   # clause=A5
compiler_error:invalid_ir:setup_requires_exactly_one_per_seat_deal:setup.deals
```

IR 结构错误的路径来自 Pydantic 错误位置；降层错误的路径由编译器给出并附条款。

## 5. 场景 A 垂直切片（M2.6）

人工规则数据 → `parse_design_ir` → `resolve_composition`（诊断）→ `compile_composed` → 真实 `Interpreter` / `playtest`：

- `playtest(plan, core_registry(), smallest_card_strategy, seeds=(0,1,7,42), invariants=(card_conservation(16),))` → **ok**，字节级重放一致；
- 单局到终局：**6 次 actor action**、轮次推进到 4（越过 3 轮）、`zones.discard` 每轮收 2 张共 6 张、`pot` 清空、16 张牌守恒；
- `SessionStore.register_plan` → `create` → `get`：编译产物可经真实会话注册表登记，指纹一致，`serialize/restore` 回到同一终局（机器人对 composed 动作的参数生成按计划属 M3）。

## 6. 真实运行证据

在 `artifacts/g2-runtime` 启动当前 checkout（端口 8012），见 §8 记录。命令与 M1 相同：

```powershell
$env:POCKER_AGENT_DATA_DIR = 'C:\Users\hr206\new-pocker-agent\artifacts\g2-runtime'
uv run python scripts/run_web.py --skip-build --port 8012
uv run python scripts/doctor.py --url http://127.0.0.1:8012
uv run python scripts/e2e_smoke.py http://127.0.0.1:8012
```

## 7. 未做 / 降级（诚实说明）

1. **场景 B/C 尚未编译**：M2 的编译器覆盖「选择 → 移动 → 比较 → 计分 → 阶段」语义节点。B（跳过、按花色计分、摸牌/pass、阈值终局）与 C（双区域选择、成对移除、补牌）需要新的效果种类（`branch_score`、`draw`、`pass`、`remove_pairs`）与动作内条件输入，属 M3 欠账；本包不假装其可表达。
2. **宏未编译**：`macros` 非空即拒（M4）。
3. **Plan `0.5` 绑定拆分未做**：`binding_id`/`tool_type` 与 `GameArtifact` 一起在 M3 落地；本包沿用 `0.4` 字段集。
4. **Agent/HTTP 入口未接**：`parse_design_ir` 已存在，但元工具与 `/api` 未暴露 composed（M4/M5）；`/api/games` 不注册组合产物。
5. **机器人 payload 未适配**：`run_bots` 的 `card_first` 仍发 `card_index`，不能驱动 composed 的 `card` 选择输入（M3）。
6. **M1 开发样例仍在**：`core/compositions.py` 是 M1 证据，未删除；删除判据 = M2 编译样例被 M5 采用、且无 M1 测试引用（ADR-0009 已记录 `matching` 的删除判据）。
7. **能力轴未扩展**：`capability.py` 未改；牌区/选择以 operation 粒度解析，不新增 axis。

## 8. 本包测试与运行结果

- `uv run --frozen pytest -q` → **244 passed**（M1 基线 227；M2 新增 `tests/test_g2_m2.py` 17 项）。
- 架构不变量（含递归扫描新子包）→ 全部通过。
- `uvx --offline ruff check <CI 列表 + tests/test_g2_m2.py>` → All checks passed。
- 专用目录 `artifacts/g2-runtime`、端口 **8012** 启动当前 checkout：
  - `scripts/doctor.py` → 全项 PASS，`server code matches the working tree`（指纹 `5a9ffeaf5835`），`GET /api/games :: 8 games`。
  - `scripts/e2e_smoke.py` → **28 checks passed**（8 个参考玩法可玩性/隐私/终局无退化）。
- 场景 A 垂直切片（`tests/test_g2_m2.py`）：编译成功、`playtest` 通过、6 次 actor action / 3 轮 / 16 张牌守恒、会话注册与恢复一致。
