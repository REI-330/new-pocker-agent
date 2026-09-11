# Pocker Agent v0.4 开发设计

> 状态：设计稿（2026-09-11）
> 基线：`main@0d2110d`（v0.2，家族引擎）
> 参考分支：`codex/doudizhu-holdem`（v0.3，ToolPlan/FlowRuntime，未合并）
> 本设计已开始实现：`src/pocker_agent/core/`（增量 1，见附录 C）

---

## 0. 文档目的

这份文档回答四个问题：

1. 为什么现有实现无法支撑"自然语言 → 可玩新玩法"；
2. v0.4 的架构应该长什么样；
3. 如何保证 tool 覆盖大部分游戏、可扩充、可测试、可量化；
4. "生成不了某个玩法"时，如何得到一个**明确的、可测量的**答案。

---

## 1. 背景：现有实现的真实问题

### 1.1 v0.2（`main`）

- 只有一个按阶段执行的 DSL（play/discard/draw/pass + 最高牌/手牌数计分）。
- 之后用 `family_engines.py` 硬加了 arithmetic / blackjack / shedding 三个**游戏专属引擎**。
- 结果：每加一个玩法就加一个引擎，违背"原子复用"。

### 1.2 v0.3（`codex/doudizhu-holdem`，47 commits）

引入了 `ToolPlan` + `FlowProgram` + `RuleExecutor` + `FlowRuntime`，方向对，但有三个结构性错误：

**错误一：两套执行模型长期并存。**

```
create_engine():
    if tool_plan has flow  → FlowRuntime      (声明式)
    else                   → FamilyEngine     (游戏专属引擎)
```

- 网页试玩走 `FlowRuntime`，部分模拟走 `FamilyEngine`；
- 同一规则两套实现，持续漂移。

**错误二：两个真相源。**

- 宿主 `plan_for_rules(rules)` 里有**手写的完整 flow**（成品模板）；
- `EngineAgent` 又把这份成品塞进 prompt，让模型"照抄骨架"再产一份 plan；
- 结果模型只是复读器，且宿主 plan 与模型 plan 不一致时行为分叉。

**错误三：没有验证门槛。**

- `RuleAgent.turn` 的启动检查用的是**宿主 plan**，不是模型 plan；
- `EngineAgent.compose` 只做 `GameLayer.from_plan`（实例化，不跑流程）；
- `confirm` 只跑一次 `setup()` + 校验工具名；
- 没有任何"完整对局"的检查。

**后果（实测 bug）：**

| 缺陷 | 现象 |
|---|---|
| `plans.py:81 give_up` 写 `winners:[0]` | 点"放弃"显示"你赢了" |
| `plans.py:79-80 no_solution` 调了 solver 但不检查结果 | 假"无解"被判胜 |
| flow `initial` 没有 `max_rounds`/`target` | 3 题的算术变成 1 题，目标显示空白 |
| `run_bots` 对 `tool_flow` 直接 return | 电脑不行动，人类要点所有座位 |
| wait inputs 是 `bid:*`/`play:*` 字面量 | 前端点了报 422，斗地主不可玩 |
| `DoudizhuTurnTool` 的 `pass` 不重置 `last_play` | 斗地主 trick 永不重置，会卡死 |
| `RuleExecutor` 与 `FamilyEngine.invoke_tool` 两套调度 | 白名单/回滚/引用解析只有一套生效 |
| `zone.zones`/`draw_discard.stock` 是实例状态 | `serialize` 只存 `context.state`，恢复丢状态 |

### 1.3 诊断

> **不是"文件丑"，是"接缝错了"：同一件事有两个权威实现，且哪个生效取决于参数。**

这种问题**不能靠增量清理解决**——每加功能都要实现两遍，两份继续漂移。必须重写执行核心。

---

## 2. 设计目标与非目标

### 2.1 目标

| 目标 | 含义 |
|---|---|
| **可组合** | 新玩法优先通过"已有原子 + 数据计划"表达，而不是新写引擎 |
| **可扩充** | 覆盖度沿**维度（axis）**乘法增长，不是逐个游戏加法增长 |
| **可生成** | 新玩法可在 kernel 之上**生成 macro**（声明式 tool），注册后复用 |
| **可测试** | 每个 tool 带契约；每个计划必须过完整对局门槛 |
| **可量化** | 能算出"缺哪条 axis、补了能覆盖多少玩法" |
| **可复现** | plan + seed + state 决定一切；运行时不依赖模型 |
| **诚实** | 做不到就 `unsupported`，绝不降级成同名简化版 |

### 2.2 非目标

- 不做通用编程语言（不追求图灵完备）。
- 不让模型默认输出可执行代码。
- 不做多人联网、实时同步、账号体系。
- 本轮不做动画/表现层（素材沿用现有，事件稳定即可）。
- 不做训练/自对弈。

---

## 3. 五条不可让步原则

1. **模型不执行。** 模型只产出数据（IR / 计划）；宿主解释执行。可执行代码是最后手段，不是默认路径。
2. **唯一执行路径。** 同一时刻只能有一个解释器能跑一个玩法。迁移期允许共存，但必须有删除日期。
3. **唯一权威表示。** `RulesIR` 是合同（给用户核对），`GamePlan` 是实现（给解释器执行）。验证是"Plan 对照 IR"。
4. **验证只能证伪。** 正确性来自执行 + 不变量 + 契约，不来自模型或用户的声明。
5. **边界必须显式。** `unsupported` 是一等公民；缺机制必须被命名。

---

## 4. 总体架构

```
                     ┌─────────────────────────────┐
用户自然语言 ───────▶ │  agent/  元工具 + loop       │
                     └──────────────┬──────────────┘
                                    │ 产出 IR / 组装计划
                                    ▼
                     ┌─────────────────────────────┐
                     │  ir/     RulesIR（合同）     │
                     └──────────────┬──────────────┘
                          host_compile │ agent_compose
                                    ▼
                     ┌─────────────────────────────┐
                     │  plan/   GamePlan（可执行）  │
                     └──────────────┬──────────────┘
                                    ▼
   ┌──────────────┐    ┌─────────────────────────────┐
   │ capability/  │───▶│  runtime/  Interpreter       │
   │ axes+contracts│    │  + EventLog + Session        │
   │ + Registry   │    └──────────────┬──────────────┘
   └──────┬───────┘                   ▼
          │              ┌─────────────────────────────┐
          └─────────────▶│  verify/  静态/动态/独立验证 │
                         │  + playtest 门槛             │
                         └─────────────────────────────┘
```

依赖必须单向：

- `agent` → `ir` → `capability`
- `plan` → `capability`
- `runtime` → `plan`, `capability`
- `verify` → `plan`, `ir`, `capability`
- **`capability` 是被所有层共享的宪法**，不依赖任何上层。

禁止：`core` 依赖旧的 `engine.py` / `family_engines.py`。

---

## 5. Tool 三层模型

覆盖度与可扩充性的矛盾，靠"三层 + 提升规则"解决。

```
┌─ Kernel（内核原语）──────────────────────────────────┐
│ 固定、完备、~15-20 个；永不生成、永不游戏专属         │
│ state · zone · move · deal · select · condition      │
│ compare · turn · effect · score · ledger · emit      │
└──────────────────────────────────────────────────────┘
                    ▲ 只能用来组装
┌─ Axis（参数化机制轴）────────────────────────────────┐
│ 人工设计、带契约、按维度参数化；覆盖度乘数            │
│ pattern_lang · turn_adapter · info_set · trigger     │
│ betting · meld · trick · team · layout               │
└──────────────────────────────────────────────────────┘
                    ▲ 只引用 kernel + axis
┌─ Macro（生成的 tool）────────────────────────────────┐
│ agent 用 kernel+axis 组装；声明式；可展开；可验证     │
│ host 审核 + 契约测试 + playtest → 注册进项目宏库      │
└──────────────────────────────────────────────────────┘
```

### 5.1 三条硬规则 + 一条执行面约束

1. **解释器只执行 kernel + axis 的 operation。**
2. **macro 不是代码，是声明。** 声明"用哪些原语、什么顺序、满足什么前后置条件" → 才可能测试和量化。
3. **macro 在组装时展开为"引用 kernel/axis 的计划片段"，运行时仍由同一个 Interpreter 执行。**

**执行面约束（钉死，防止沙箱重新长出第二套执行）：**

```text
Kernel / Axis / Macro  →  都进入同一个 Interpreter（主执行面）
Sandbox code           →  不进入主 Interpreter，属于独立隔离执行协议
```

- 三层能力带（§11.3）描述的是**能力获取方式**，不是执行路径。
- Macro 与 Sandbox 不是同一条降级链上的两级：Macro 是"主执行面的组合"，Sandbox 是"另一个进程里的执行协议"，二者**只能有一个**在某个玩法上生效。

### 5.2 Kernel 原语（建议清单）

| 原语 | 职责 | 关键约束 |
|---|---|---|
| `state.get/set/update` | 共享状态读写 | 拒绝保留字段（`input`/`seed`/`_`） |
| `zone.create/move/take/top/size` | 牌区与移动 | 牌身份守恒，禁止凭空造牌 |
| `deal` | 发牌（round_robin/block） | 从 seed 确定性 |
| `select` | 选牌/选人 | 只产出"候选"，不改状态 |
| `condition.evaluate` | 全函数布尔表达式 | 无副作用、有限深度 |
| `compare` | 数值/序比较 | 不内置任何玩法的牌型 |
| `turn.advance/set/skip/reverse` | 回合指针 | 不内置顺序语义 |
| `effect.emit` | 触发事件 | 事件是纯数据 |
| `score.add/transfer` | 计分/筹码 | 整数、零和可校验 |
| `ledger.commit/pots` | 账本 | 非负整数、守恒 |
| `log.emit` | 事件流 | 表现层唯一接口 |

> Kernel 里**没有** `doudizhu_turn`、`holdem_hand_rank`、`climb_beats`。这些是 axis 或 macro。

### 5.3 Axis（机制轴）

每条 axis 是一个**参数化的机制族**，不是某个玩法：

| Axis | 参数化内容 | 覆盖 |
|---|---|---|
| `pattern_lang` | 牌型语法（set/run/same-rank/all/suit约束/长度约束/rank_key/trump） | 组合/出牌/墩牌类 |
| `turn_adapter` | `sequential` / `priority` / `trick` / `bidding` / `simultaneous` | 回合结构差异 |
| `info_set` | 可见性、按玩家视角、揭示时序、隐藏位置抽取 | 不完全信息类 |
| `trigger` | `on(event) if(condition) then(effects)` | 特殊牌、反应、连锁 |
| `betting` | 下注轮、跟注、加注、全下、边池 | 下注类 |
| `meld` | 组合识别与计分（Rummy 类） | 组合类 |
| `team` | 队伍、共享胜负、合作计分 | 组队类 |
| `layout` | 目标区、自动移动、耐心牌区 | 布局类 |

### 5.4 Macro（"在基础上生成 tool"）

Macro 是 agent 生成出来的具名 tool，形态如下：

```yaml
macro:
  name: draw_hidden_from_player
  params: {target_player: int, position: int}
  requires:                                  # 前置条件
    - "current_player is active"
    - "hands[target_player].size > position"
  ensures:                                   # 后置条件（跑完必须成立）
    - "current_player.hand.size == old + 1"
    - "hands[target_player].size == old - 1"
    - "total_cards conserved"
  effects: [hands]
  body:                                      # 只允许 kernel/axis 调用
    - op: zone.take_hidden
      args: {from: "hands.$target_player", index: "$position", to: "hands.$current"}
    - op: log.emit
      args: {event: "hidden_card_drawn", target: "$target_player"}
```

宿主审核 macro 的四步（全部机械化）：

1. **展开**：body 里的 op 必须全部是已声明的 kernel/axis，否则拒绝；
2. **契约检查**：`requires`/`ensures` 能静态检查的静态检查；
3. **属性测试**：牌张守恒、边界（空手、越界、最后一张）、确定性重放；
4. **并入玩法 playtest**：通过后注册进项目宏库。

### 5.5 提升规则（避免"一游戏一 tool"）

```
同一 macro（或同形 macro）被 ≥ K 个玩法使用
        → 提升为参数化 axis tool（人工设计契约 + 属性测试）
否则
        → 留在该玩法的计划里当局部 macro
```

> 工具库按"**被证明的复用**"增长，而不是按"又做了一个游戏"增长。

### 5.6 沙箱代码生成：隔离协议，不在主解释器内

沙箱是**最后出口**，与 kernel/axis/macro 不是同一层：

| 维度 | 主执行面（Kernel/Axis/Macro） | 沙箱执行面 |
|---|---|---|
| 执行器 | 同一个 `Interpreter` | 独立子进程 + JS VM |
| 状态 | 单一 `state` dict，可序列化/恢复 | 沙箱内部状态，宿主只做帧校验 |
| 契约 | `OperationSpec` requires/ensures | 帧级不变量（守恒/合法性/确定性） |
| 验证强度 | 强（完整对局 + 契约对照） | 弱（属性 + 用户确认） |
| 是否主路径 | 是 | 否，仅当主执行面表达不了时启用 |

硬规则：**任何玩法不得同时依赖两个执行面**；启用沙箱即声明该玩法的主执行面为沙箱，不再混合。

---

## 6. 数据模型

### 6.1 RulesIR（合同，给用户核对）

```yaml
meta:    {game_id, title, version}
players: {count: {min, max}, teams: [[0,1],[2]], seats: fixed|rotating}
deck:    {ranks, suits, copies, excluded, jokers}
zones:   [{id, owner, initial, visibility: public|owner|players[..]|hidden}]
deal:    {pattern: round_robin|block, from, to, count}
turn:    {structure: sequential|priority|simultaneous|bidding,
          order, first, advance, pass:{consecutive_resets_trick}}
actions: [{id, when: {phase, actor, zone, constraints},
           select: {zone, count, pattern_ref},
           effects: [ref], reveals: [zone], next}]
patterns:[{id, grammar, rank_key, trump}]           # 参数化牌型
effects: [{on: event, if: condition, then: [mutation]}]   # 触发器
win:     {condition, winners: actor|team|fewest|last, when}
score:   {mode: zero_sum|points|chips, multipliers}
end:     [conditions]
```

**IR 是给用户确认的产物**，不是内部草稿。它必须完整到"能编译成计划"。

### 6.2 GamePlan（实现，给解释器）

```yaml
schema_version: "0.4"
game_kind: str
players: int
tools: [{name, config}]                 # 工具绑定（一个 binding = 一个实例）
initial: {state...}                     # 初始共享状态
entry: node_id
nodes:
  <id>: {kind: call|branch|wait|end,
         action: {tool, operation, args, result_key},   # call
         value, cases: [{value, target}], next,          # branch
         inputs: {action_name: target_node}}             # wait
step_limit: int
```

**已实现**（`src/pocker_agent/core/plan.py`）。

### 6.3 IR 与 Plan 的关系

- IR 里没有的语义，Plan 不许发明；
- Plan 做不到的，IR 就不该声称；
- `verify.contract_check(ir, trace)` 强制两者一致。

---

## 7. 工具契约规范

**这是 v0.4 的"实现合同"。P1 开始前必须完备**（否则 §9.1 的静态验证是空的）。

```yaml
OperationSpec:
  name: str                        # 操作名（plan 里引用）
  method: str                      # 实例方法名，默认 = name
  params: {name: type}             # → JSON Schema，导出给模型
  requires: [predicate]            # 前置条件（LogicTool 语法，对调用前 state 求值）
  ensures:  [predicate]            # 后置条件（对调用后 state 求值）
  effects:  [state key] | ["*"]    # 允许改动的 state key；"*" = 不受限（仅 state.update）
  returns: type                    # 返回 schema
  failure: rollback | reject_only  # 失败语义
  deterministic: bool
```

**解释器的强制语义（机器可执行，不是文档约定）：**

1. 调用前求值 `requires`，任一为假 → `precondition_failed:<tool>.<op>`。
2. 调用前后对 `state` 做差异比较；改动过的 key 必须 ∈ `effects`（`"*"` 与 `result_key` 除外），否则 → `out_of_contract_state_change:<key>`。
3. 调用后求值 `ensures`，任一为假 → `postcondition_failed:<tool>.<op>`。
4. 上述任一失败都触发**全量回滚**（state / events / pc），与 §8.2 一致。

> `requires`/`ensures` 为空表示"未声明约束"，但 `effects` 的差异检查**始终生效**——这是防止"operation 改了契约外的状态"的最低保障。

**规则**：

1. 工具尽量**无状态**；有状态必须能由 `plan config + state` 重建（否则 `restore` 必丢数据）。
2. 随机只来自 seed，工具内禁止时钟/全局随机。
3. 参数化，不分叉。两个工具 80% 逻辑相同、只差一个玩法规则 → 应该是一条 axis。
4. 契约必须导出成机器可读形式（`registry.export()`）；agent 的工具表与解释器的 dispatch **同源**。
5. 机制类工具（solver / deal / settle / logic）`effects` 必须为空——它们只能返回值，不能写状态。

**实现状态**：`requires/ensures/effects/returns/failure` 已在 `contracts.py` 声明，解释器已强制执行（`interpreter.py` 的 `_check_predicates` + 差异检查），并有测试覆盖（越权改 state 被拒、前置条件被拒）。

---

## 8. 运行时：Compiler + Interpreter + Session

### 8.1 Compiler

```
capability_check(ir) -> Report
    ├─ covered        → host_compile(ir)          # 确定性编译；agent 不参与
    ├─ macro_capable  → agent_compose(ir)         # agent 组装候选 Plan
    └─ unsupported    → return {missing_axes}     # 明确失败，不降级
```

**责任边界（钉死）：**

| 角色 | 能做 | 不能做 |
|---|---|---|
| `capability_check`（宿主） | 判定 covered / macro_capable / unsupported | 不能由模型自称覆盖 |
| `host_compile`（宿主） | 把 IR 确定性编译成 Plan | 不能发明 IR 之外的语义 |
| `agent_compose`（模型+元工具） | 产**候选 Plan**；失败时返回错误 | **不能修改 IR**；不能偷偷补语义 |
| `finalize`（宿主） | 门槛通过后才冻结 | 不接受模型自证 |

**失败协议**：`agent_compose` 若发现 IR 本身不可表达（缺 axis/机制），必须返回 `unsupported + missing_axes`，**不得**用近似语义蒙混。允许的唯一回退是：回到 `ask_user` 让用户确认是否接受变体（变体要写成**新 IR** 再重新 `capability_check`）。

- 已知玩法族（算术、21点、接牌、斗地主、德州）：`host_compile`，模型不写计划。
- 新机制：`agent_compose`，模型用元工具组装。

### 8.2 Interpreter（唯一执行器）

已实现 `src/pocker_agent/core/interpreter.py`：

- 构造期 fail-fast：校验每个 action 的 operation 在 registry 声明过；
- 单一 `state` dict = `initial` + `seed` + `input`；
- `resolve` 只解析 `$state.a.b`，**不 eval**；
- `call`：operation 校验 → `getattr` → resolve args → `method(**args)` → `result_key` 写回 → emit；
- `advance`：call/branch 直走，`wait` 返回，`end` 校验 finished，超 `step_limit` 报错；
- **`step` 事务语义**：备份 `state/events/pc`，失败全量恢复；
- `setup / legal_actions / run / serialize / restore / view`。

**状态模型硬约束（每条都要有自动测试）：**

```text
1. 所有可影响结果的状态必须位于 Interpreter.state；
2. Tool 实例不得保存不可重建的状态；
3. serialize(restore(serialize(x))) 必须逐字节一致；
4. 事件日志是派生产物，不能代替权威状态；
5. view() 不得暴露 seed / input / plan 内部字段。
```

- 第 3 条对应测试：随机走若干步，逐步 serialize→restore，两边 serialize 必须全等。
- 第 2 条目前由"核心工具均为无状态"保证；未来状态型工具必须能由 `plan config + state` 重建。
- 第 5 条已实现：`view()` 不含 `seed` / `input` / `state`。

### 8.3 Session

```
Session = Interpreter + revision + plan + seed
```

- 动作：`step` → 成功则 `revision += 1` 并持久化；
- 并发：`revision` 不匹配返回 409（stale_revision）；
- 运行时不依赖模型，模型挂了也能继续玩。

---

## 9. 验证体系

**验证分四类，缺一不可。** 单纯的"静态/动态/独立"三分法不够：它盖不住隐藏信息、合法动作完备性、可达性和触发路径。

| 类别 | 回答的问题 | 手段 | 阶段 |
|---|---|---|---|
| **结构验证** | 计划本身合法吗？ | schema / 建图 / 引用 / 工具存在性 / 契约 requires-ensures / capability | 静态 |
| **轨迹验证** | 跑出来符合条款吗？ | 事件序列、计分、终局、`contract_check` | 动态 |
| **状态验证** | 状态空间的性质成立吗？ | 守恒、可见性、合法动作完备、可达性 | 动态/属性 |
| **差分验证** | 和独立参照一致吗？ | oracle / 人工确认 trace / 多实现对照 | 独立 |

### 9.1 结构验证（静态）

- Schema / 建图完整（目标存在、工具已声明）；
- 工具存在性、operation 存在性；
- 工具契约 `requires/ensures` 中可静态判定的部分；
- `capability_check` 覆盖判定（只有 `stable` 才算 covered，见 §11.1）。

### 9.2 轨迹验证（动态）

- 事件序列符合动作顺序、轮次、终局；
- 计分/胜负/发牌次数与 IR 声明一致（`contract_check`）。

**`contract_check` 的能力边界（必须写清它证明不了什么）：**

| 能证明 | 证明不了 |
|---|---|
| 轮数、发牌次数、分数、胜负、终局原因 | 隐藏信息是否真的不可见；合法动作集合是否**完整**；某状态是否可达；不同玩家视角是否一致；触发器是否在**所有路径**上执行；随机分布是否符合约束 |

> 例：IR 说"3 题、目标 24、放弃 0 分"，trace 必须体现 3 次发牌、每轮目标 24、放弃不加分且不产生 winner。

### 9.3 状态验证（动态/属性）

- 牌张守恒（每一步）；
- **可见性**：私有牌区对非授权玩家不可见（按玩家视角断言）；
- **合法动作完备性**：有可选动作但 `legal_actions` 为空 = 失败；
- **可达性**：目标终局状态可达；
- **触发器覆盖**：声明的触发事件至少被触发一次。

### 9.4 差分验证（独立）

- 已知族：**人工确认的关键 trace**（不是旧引擎的当前输出，见 §14.3）；
- 旧引擎差分：只作参考，不作 oracle；
- 新玩法：属性测试（守恒、终止、可达、边界）。

### 9.5 Playtest 门槛与策略协议

```
playtest(plan, registry, strategies, seeds, invariants, require_wait_coverage=True)
```

**策略必须显式给出，且必须是解释器的纯函数**（禁止闭包带可变状态，否则重放不确定）。内置三种：

| 策略 | 目的 |
|---|---|
| `random_legal` | 合法随机（由 interpreter seed 派生，确定性） |
| `boundary_first` | 优先边界动作（pass / give_up / no_solution / fold / check） |
| `branch_coverage` | 优先未覆盖分支（依据已访问节点计数） |

**默认门槛：**

1. 每个 seed 跑**完整对局**（不能只跑 setup）；
2. 检查调用方不变量；
3. 同 seed 重放逐字节一致；
4. **所有 `wait` 节点至少被覆盖一次**（跨 seeds × strategies 取并集）；
5. 出现"动作可用但 `legal_actions` 为空" = 失败。

> 未通过 `playtest` + `contract_check` 的计划不得标记为可玩；`finalize` 由宿主强制拒绝。
> 已实现：三种策略中的 `random_legal` / `boundary_first` / `first_legal`，以及 wait 全覆盖门槛（`core/playtest.py`）。

---

## 10. Agent 层

### 10.1 元工具清单

agent 调用的是**元工具**（操作 IR / 计划 / 验证），**不是游戏工具**。

**需求 / IR 层**
```
ask_user(question, missing[])            → Observation(message)
propose_ir(ir)                           → Observation(ok, errors[], ir)
patch_ir(path, value)                    → Observation(ok, errors[], ir)
```

**能力发现层**
```
list_tools()                             → Observation(tools=[...])       # = registry.export()
describe_tool(name)                      → Observation(operations, params, effects, deterministic)
```

**计划组装层**
```
compose_plan(ir)                         → Observation(ok, plan | errors[])     # 粗粒度
new_plan(game_kind, players, tools[])    → Observation(plan_id, revision)       # 细粒度
bind_tool(name, config)                  → Observation(ok, errors[])
add_node(id, kind)                       → Observation(ok, errors[])
set_call(node, tool, operation, args, result_key, next)  → Observation
set_branch(node, value, cases[], next)   → Observation
set_wait(node, inputs{action: target})   → Observation
set_next(node, next) / connect(from,to)  → Observation
set_end(node)                            → Observation
set_entry(node) / set_initial(values)    → Observation
delete_node(id)                          → Observation
get_plan()                               → Observation(plan, revision)
```

**验证 / 调试层**
```
validate_plan()                          → Observation(ok, errors[])
dry_run(seed, actions[])                 → Observation(events, first_error)
simulate(seeds[], policy)                → Observation(摘要: 事件数/是否到达end/首个错误)
playtest(seeds[], invariants[])          → Observation(ok, failures[])
check_contract(ir, trace)                → Observation(violations[])
inspect_state()                          → Observation(状态摘要)
```

**终局**
```
finalize()                               → Observation(ok)   # 未过门槛一律拒绝
```

### 10.2 Loop 与 Observation

```
loop:
  d = model.decide(messages, tools=meta_tools_schema)   # function calling
  if d.kind == ask:      return question(d)
  if d.kind == finalize: return plan                    # 宿主强制门槛
  try:
      obs = dispatch(d)                                 # 宿主执行，模型碰不到实现
  except ToolError as e:
      obs = Observation(ok=False, error=str(e))
  messages.append(obs)                                  # 回灌 → 闭环
```

- **observation 是结构化摘要**（ok/error + 状态 diff + trace 摘要），不是全量事件，防止上下文爆炸；
- **失败也是 observation**：`ToolError` 回灌让模型修；
- 预算硬性封顶（步数 / token / 时间），超限返回明确错误，不静默降级。

### 10.3 Agent 角色分档（由宿主判定，fail-closed）

| 情况 | 例子 | agent 做什么 | agent 不做什么 |
|---|---|---|---|
| 已知玩法族 | 标准斗地主 | 澄清 + 填 IR + 修补 IR | 不写计划、不执行 |
| 已知族变体 | "炸弹翻 3 倍" | `patch_ir` 改参数 | 不写计划 |
| 新机制 | 抽乌龟 | 元工具**组装计划**，按 playtest 报告修复 | 不跳过门槛、不自证 |
| 超出协议 | 联网实时 | 返回 `unsupported` + 缺哪些机制 | 不降级成同名比大小 |

### 10.4 组装时的两种粒度

| 粒度 | 调用 | 优点 | 缺点 | 适用 |
|---|---|---|---|---|
| 粗 | `compose_plan(ir)` | 轮数少、省 token | 一次吐大 JSON，错了难定位 | 中等复杂度 |
| 细 | `add_node/connect/set_*` | 每步立即校验、错误局部化 | 轮数多、上下文长 | 复杂玩法 |

实务：**先粗后细**——`compose_plan` 出骨架，`playtest` 失败后用细粒度工具定点修复。

---

## 11. 能力边界与 Capability Check

### 11.1 能力矩阵

宿主拥有、不依赖模型描述（现有 `/api/capabilities` 是雏形）：

```yaml
capabilities:
  - id: sequential_turn      status: stable
  - id: pattern_lang         status: stable
  - id: information          status: planned
  - id: trigger              status: planned
  ...
```

**状态机（钉死，`planned` 不得参与可表达判断）：**

```text
planned  <  experimental  <  stable  <  deprecated
```

| status | 算 covered | 可否 finalize | 说明 |
|---|---|---|---|
| `planned` | ❌ | ❌ | 只出现在路线图；`capability_check` 必须报 missing |
| `experimental` | ⚠️ 仅生成草案 | ❌ | 可 `agent_compose` 出草稿，不得冻结 |
| `stable` | ✅ | ✅ | 唯一可作为 covered 的状态 |
| `deprecated` | ❌ | ❌ | 保留兼容，新计划不得引用 |

### 11.2 capability_check（诊断输出）

给定候选 IR，返回结构化缺口——**这是"缺哪方面"的答案**：

```json
{
  "game_id": "old_maid",
  "expressible": false,
  "required_axes": ["information", "turn", "pattern", "win"],
  "covered_axes":  ["turn", "pattern", "win"],
  "missing": [
    {"axis": "information",
     "need": "draw_hidden_from_player",
     "reason": "zone.visibility 只支持整体隐藏，缺按位置抽取对手隐藏牌"}
  ],
  "decomposable": true,
  "suggestion": "生成 macro: draw_hidden_from_player"
}
```

### 11.3 三层能力带与不降级

```
[声明式原子组合]   强验证，覆盖大部分传统牌类
       ↓ 表达不了
[受限新机制声明]   模型声明参数化机制（macro），宿主审核 + playtest
       ↓ 还表达不了
[沙箱代码生成]     最后手段，弱验证 + 用户确认
```

- 这三层是**能力获取方式**，不是执行路径：前两层（原子组合、macro 声明）都在**同一个 Interpreter** 内；第三层（沙箱）是**独立的隔离执行协议**（见 §5.6）。
- **同一玩法只能有一个执行面**：要么主执行面，要么沙箱，不得混用。
- **不降级原则**：任何一层做不到 → 明确 `unsupported`，绝不用同名简化版顶替。

---

## 12. 覆盖度量（可测试量化）

### 12.1 玩法语料库（corpus）

- 来源：Pagat 分类（比较/接牌/爬牌/墩牌/组合/隐藏信息/下注/布局）+ 自建；
- 每个玩法标注"需要哪些 axis"。

### 12.2 覆盖率报告

对语料库逐个跑 `capability_check`：

```
玩法总数:        120
直接可表达:       58   (48%)
可由 macro 表达:  34   (28%)
缺 axis:          24   (20%)   ← 按缺失 axis 出直方图
无解/超协议:       4    (3%)
```

### 12.3 失败直方图（投资优先级）

```
information      ████████████  11
pattern_lang     ███████        7
trigger          █████          5
turn_adapter     ██             2
team             █              1
```

→ 结论：**补 information / pattern_lang / trigger 三条 axis 能覆盖接下来最多的玩法。**

### 12.4 生成基准（benchmark）

喂 N 条自然语言，测量：

- 生成成功率、`unsupported` 率；
- **失败按缺失 axis 分桶**；
- playtest 通过率；
- 平均修复轮数、token、时延。

---

## 13. 缺失 axis 清单与优先级

| 优先级 | Axis | 现状 | 缺什么 | 卡住谁 |
|---|---|---|---|---|
| **P0** | 信息集 / 可见性 | 只有弱 `zones.visibility` | 按玩家视角、揭示时序、隐藏位置抽取 | Go Fish、Old Maid、Kemps、几乎所有不完全信息牌游 |
| **P0** | 参数化牌型语言 | 按玩法分叉（`doudizhu_hand_rank` 等） | 统一 pattern 引擎 | Big Two、President、Rummy、新组合玩法 |
| **P0** | 触发器 / 效果系统 | `triggers` 是空壳 | `on/if/then` 通用规则 | 特殊牌、连锁、反应——**陌生新玩法的真正来源** |
| P1 | 回合适配器 | 只有顺序 `turn_order` | priority/trick/bidding/simultaneous | Bridge、Hearts、Spades、拍卖、同时行动 |
| P1 | 队伍 / 合作 | 个体 + 零和 | 队伍、共享胜负 | Bridge、Spades 等组队 |
| P2 | 通用账本 / 经济 | 只有 `pot/betting` | 转移、生产-消耗、盲拍 | 交易、构筑、经济类 |
| P2 | 布局 / 耐心 | 无 | 目标区、自动移动 | Klondike、FreeCell、Pyramid |
| 横切 | 验证脚手架 | 基本没有 | 不变量库 + oracle + 契约测试 | 不是"生成不了"，是"生成了也不知道对不对" |

**若只补三个：P0 的三条（信息集、参数化牌型、触发器）。** 它们是乘法式覆盖。

---

## 14. 迁移策略

### 14.1 Strangler（绞杀）

- `main@0d2110d` 冻结为基线，**不合并** `codex/doudizhu-holdem`；
- 新 `core/` 是绞杀目标；
- 每个玩法迁到 `core` 后，**立即删除旧路径**；
- 旧引擎降级为测试 oracle / 黄金 trace，移出生产 dispatch。

### 14.2 删除清单

| 目标 | 处理 |
|---|---|
| `family_engines.py`、`doudizhu_engine.py`、`holdem_engine.py` | 降为 oracle，移出 dispatch |
| `tools/plans.py` 的宿主成品 flow 模板 | 删除，改由 IR→plan 编译或 agent 组装 |
| `EngineAgent` 的"复制骨架"prompt | 替换为元工具 loop |
| `runtime.py` 的 `tool_plan or plan_for_rules(...)` 双来源分支 | 收敛为单一来源 |
| `RuleExecutor` 与 `FamilyEngine.invoke_tool` 双调度 | 只保留一个 dispatcher |

### 14.3 迁移顺序与 oracle 使用限制

按"有 oracle、有测试"排：**arithmetic → shedding → blackjack → doudizhu → holdem**。

**旧引擎不能当唯一 oracle**（它本身可能带错误语义）。每个迁移玩法必须同时保存四样：

```text
1. 规则样例（IR + 自然语言条款）
2. 人工确认的关键 trace（黄金 trace，人看过并签字）
3. 旧引擎差分结果（参考，不是判据）
4. 已知旧缺陷列表（明确"这些行为不得被新实现继承"）
```

否则迁移可能只是把旧 bug 固化成新测试。

---

## 15. 落地路线（按评审调整）

### P0.1 统一能力带与沙箱边界

- 明确 Kernel/Axis/Macro 共享同一 Interpreter；沙箱为独立协议（§5.1、§5.6）。
- 能力状态机（§11.1）落地到 `/api/capabilities`。

### P0.2 补齐 OperationSpec 契约（最高优先）✅

- 字段：`requires` / `ensures` / `effects` / `returns` / `failure` / `deterministic`。
- 解释器强制：前置、后置、契约外状态变更检测 + 全量回滚。
- 机制类工具 `effects` 为空。

**验收**：构造一个"越权改 state"的工具，playtest 必须拒绝并回滚。✅

### P0.3 定义 playtest 策略与覆盖标准 ✅

- 三种内置策略；`wait` 节点全覆盖为默认门槛。

**验收**：故意留一个不可达 wait 节点，playtest 必须报 `wait_nodes_uncovered`。✅

### P0.4 固化 serialize/restore 与可见性不变量 ✅

- `serialize(restore(serialize(x)))` 字节一致（自动测试）；
- 可见性按玩家视角断言；`view()` 不暴露 seed/input。

### P1 Agent meta-tools loop

- 元工具（`propose_ir` / `compose_plan` / `validate_plan` / `simulate` / `playtest` / `repair` / `finalize`）；
- `registry.export()` 驱动 function calling；observation 回灌；
- 先覆盖 arithmetic + shedding。

**验收**：模型在预算内自主完成"起草 → 组装 → 失败 → 修复 → 通过 → 冻结"。

### P2 补齐 axis + 迁移玩法

- P0 三条 axis（信息集、参数化牌型、触发器）；
- 迁移 shedding → blackjack → doudizhu → holdem，迁完即删旧路径；
- known 族 `host_compile`，novel 族 `agent_compose`。

### P3 表现层 + 能力矩阵对外 + 沙箱兜底

- 事件 → 动画/素材映射；
- `/api/capabilities` 对外；
- 沙箱作为最后出口（独立协议）。

---

## 16. 反模式与红线

**禁止：**

1. 每个玩法写一个 Engine（`doudizhu_engine`、`holdem_engine`）。
2. 让"模型输出可执行代码"成为默认路径。
3. "能跑就算对"（没有验证的发货）。
4. 半迁移的长期双路径。
5. 为了让 demo 好看而绕过门槛或悄悄简化规则。
6. 把"通用牌型排序"这类伪通用概念当真（牌型体系不可通约，只能参数化）。
7. 把宿主成品计划当模板注入 prompt（会让模型变成复读器）。

**必须：**

1. 模型只产数据，不执行。
2. 单一执行路径。
3. 未过 playtest 不得称可玩。
4. 缺机制必须命名（`unsupported` + missing axes）。
5. 工具无状态 / 可重建。
6. 随机只来自 seed。

---

## 17. 附录

### A. 现有工具盘点（分支 38 个）

| 类别 | 工具 |
|---|---|
| 真·原子 | `deck` `zones` `turn_order` `state` `logic` `condition` `card_match` `group_cards` `draw_discard` `win_condition` `winner_resolve` `score_settle` `settlement` `triggers`(空壳) `phase_progress` |
| 半原子 | `follow_suit` `trick_resolve` `meld_detect` `climb_beats` `draw_until_playable` `point_contest` `dealer_policy` `community_deal` `pot` `betting_round` `all_in` `pot_winners` `settle_pots` `side_pots` `showdown` `hand_rank` |
| 游戏成品块（smell） | `doudizhu_turn` `doudizhu_settle` `doudizhu_hand_rank` `holdem_hand_rank` `exact_expression` `solvable_deal` `shedding_turn` |

**观察**：38 个名字里真原子 ~15 个；成品块只能加法增长，是覆盖度瓶颈的直接来源。

### B. 术语表

| 术语 | 含义 |
|---|---|
| IR | 严谨规则中间表示，给用户核对的合同 |
| Plan | 可执行计划（工具绑定 + flow 图） |
| Kernel | 固定内核原语集 |
| Axis | 参数化机制维度 |
| Macro | 用 kernel/axis 生成出来的具名 tool |
| playtest | 多 seed 完整对局 + 不变量 + 确定性重放的门槛 |
| capability_check | 给定 IR 返回"缺哪条 axis"的诊断 |
| Observation | 工具/元工具执行结果的结构化包装，回灌给 agent |

### C. 已实现（增量 1 + P0.2–P0.4）

分支 `v0.4-core`，提交 `3fd036c`（增量 1）、`375a0b7`（设计文档）、本次（P0.2–P0.4）。

`src/pocker_agent/core/`：

| 文件 | 内容 |
|---|---|
| `cards.py` | `CardRef` + 状态编解码 |
| `contracts.py` | `ToolError` / `Observation` / `OperationSpec`（含 requires/ensures/effects/returns/failure）/ `ToolSpec` / `ToolRegistry` |
| `plan.py` | `ToolCall` / `ToolBinding` / `FlowNode` / `GamePlan` |
| `interpreter.py` | `Interpreter`（resolve / dispatch / **契约强制** / rollback / serialize / view） |
| `tools.py` | `StateTool` `LogicTool` `ExactExpressionTool` `SolvableDealTool` `ScoreSettleTool` + 共享 `evaluate_expression` |
| `registry.py` | `core_registry()` + 契约导出（机制工具 `effects=()`） |
| `plans.py` | `arithmetic_plan`（多题、验证无解、放弃不产生 winner） |
| `playtest.py` | `playtest` + `PlaytestReport` + `first_legal` / `random_legal` / `boundary_first` + wait 覆盖门槛 |

**P0.2–P0.4 实测能力：**

- 契约强制：越权改 state → `out_of_contract_state_change` 且全量回滚；前置条件不满足 → `precondition_failed`。
- 覆盖门槛：不可达 `wait` 节点 → `wait_nodes_uncovered`。
- 持久化：逐步 `serialize/restore` 往返字节一致；`view()` 不含 `seed/input/state`。

测试：`tests/test_core_interpreter.py`（25 项），全量 `111 passed`。

对照修复的既有缺陷：

| 旧缺陷 | core 表现 |
|---|---|
| `give_up → winners:[0]` | 放弃只置 `finished`，`winners` 保持空 |
| 假 `no_solution` 被接受 | `assert_unsolvable` 由宿主复核后拒绝 |
| `max_rounds`/`target` 丢失 | 计划显式持有并在 view 暴露 |
| 双执行路径 | 只有一套执行，playtest 强制条款 |
| 机制工具越权改 state | 契约 `effects` 差异检查拒绝 + 回滚 |
| 只跑 setup 就算验证 | 完整对局 + 多策略 + wait 覆盖门槛 |

### D. 待办清单（按 §15 优先级）

**P0（进入 P1 前必须完成）**
- [x] P0.2 工具契约 `requires/ensures/effects/returns/failure` + 解释器强制
- [x] P0.3 playtest 策略（random_legal / boundary_first）+ wait 全覆盖门槛
- [x] P0.4 serialize/restore 往返字节一致 + view 不泄漏测试
- [x] P0.1 `capability_check` + 能力状态机 + 语料库覆盖率报表
- [x] `core/session.py`：Session + revision + 持久化
- [x] v0.4 API（`app.py`）+ 真实 HTTP 端到端（`scripts/e2e_smoke.py`）
- [x] 前端按原型改造并接通真实接口（玩法库 / 规则与能力 / 试玩工作台 / 回放）
- [x] 摘除旧引擎生产路径（`api` / `family_engines` / `engine` / `executors` / `runtime` / `simulation` / `game_rules` / `validation` / `exporting` / `agent` / `models`）；删除积压归零；删除前已抽取 `benchmarks/oracle/` 黄金 trace

**P1**
- [x] `core/ir.py`：`RulesIR` + `host_compile`
- [x] `agent/meta_tools.py` + `agent/loop.py`（含 budget / finalize 门槛 / observation 回灌）
- [x] 新增机制工具 `deck` / `rank_compare` / `winner_resolve`；`rank_compare` → stable
- [x] 前端「新建玩法」+「模型设置」
- [x] 真实端到端覆盖整条链路（16 项；HTTP 层 also 覆盖 agent→compose→play）

**P2**
- [x] `capability_check` + 语料库 + 覆盖率报表
- [x] `pattern_lang`（参数化牌型/合法性）+ `info_set`（按视角可见性）→ stable，新增可玩族 `crazy_eights`
- [x] `agent_compose` 真实路径（模型提供 plan → playtest → finalize → 可玩）
- [x] `turn_adapter`（墩牌/跟牌/竞叫）+ `team`（队伍）→ stable，新增可玩族 `whist`
- [x] 语料修正：`hearts` 去 `team`、`spades`/`bridge` 去 `betting`（竞叫归 `turn_adapter`）
- [x] `betting` + `ledger` + `hand_rank` → stable（ADR-0002 预算 12→16），新增可玩族 `five_card_poker`
- [x] `point_total` → stable，新增可玩族 `blackjack`
- [x] 生成基准（`scripts/benchmark_generation.py` + 语料用例 + 缺口直方图）
- [x] `hidden_draw` → stable（ADR-0003 预算 16→17），新增可玩族 `go_fish`；wait 通配输入展开为具体动作
- [x] `trigger` → stable（ADR-0004 预算 17→18），新增可玩族 `uno`；新增 planned 轴 `simultaneous`
- [ ] `layout`（接龙 3 个玩法）/ `simultaneous`（同时行动）
- [x] macro 形式化 + 提升规则（`core/macros.py`，`match_turn` 被两个计划真实复用）
- [x] 长驻进程陈旧检测：`/health` 暴露 `code` 指纹 + `doctor.py --url` + 启动器端口归属提示
- [x] 轴状态与实现对齐：`Capability.note`（缺口必须说明）+ `stable` 不得建立在占位机制上 + 宏提升欠账为 0
- [ ] 沙箱隔离协议

---

## 18. 评审修订记录（2026-09-11）

本轮评审提出的 9 点及处理：

| # | 评审意见 | 处理 | 位置 |
|---|---|---|---|
| 1 | 三层能力带与"不互相混用"冲突，沙箱容易长出第二条执行 | 新增"执行面约束"：Kernel/Axis/Macro 共享同一 Interpreter；沙箱为独立隔离协议；同一玩法只能有一个执行面 | §5.1 / §5.6 / §11.3 |
| 2 | `RulesIR → GamePlan` 责任边界不清（host_compile / agent_compose / compose_plan 并存） | 写成严格决策协议 + 责任表 + 失败协议：`agent_compose` **不得修改 IR** | §8.1 |
| 3 | `contract_check` 容易被高估 | 验证拆成四类（结构/轨迹/状态/差分）；新增 `contract_check` 能力边界表（它能证明什么、证明不了什么） | §9 |
| 4 | 缺统一对局策略协议 | 定义策略协议（必须是解释器纯函数）+ 三种内置策略 + **wait 节点全覆盖**为默认门槛 | §9.5 |
| 5 | `planned` 不得参与可表达判断 | 新增能力状态机 `planned < experimental < stable < deprecated`，只有 `stable` 算 covered | §11.1 |
| 6 | 工具契约与设计目标不匹配（缺 requires/ensures） | `OperationSpec` 补齐 `requires/ensures/effects/returns/failure/deterministic`；解释器**强制**前置/后置/契约外状态变更 + 全量回滚；**已实现** | §7 |
| 7 | 状态模型需更严格持久化规范 | 新增五条硬约束（单一 state / 工具无不可重建状态 / serialize 往返字节一致 / 事件日志非权威 / view 不泄漏），并配自动测试；**已实现第 3、5 条** | §8.2 |
| 8 | 旧引擎作 oracle 要限制用法 | 规定每个迁移玩法同时保存：规则样例 / 人工确认 trace / 旧引擎差分 / 已知缺陷列表 | §14.3 |
| 9 | 日期问题 | 设计稿日期改为 2026-09-11 | 文档头 |

本轮同步完成的实现（P0.2/P0.3/P0.4，测试 `111 passed`）：

- `OperationSpec` 契约字段 + 解释器强制（越权改 state / 前置条件失败 → 拒绝 + 全量回滚）；
- `playtest` 多策略（`random_legal` / `boundary_first` / `first_legal`）+ wait 覆盖门槛（`wait_nodes_uncovered`）；
- `serialize/restore` 逐步字节一致测试 + `view()` 不泄漏 `seed/input/state`。
