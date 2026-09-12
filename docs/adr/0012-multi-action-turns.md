# ADR-0012: 多动作回合与条件效果（场景 B 前置）

- 日期：2026-09-14
- 状态：accepted（**实现状态：实施中**，M3 场景 B 包）
- 决策者：架构 owner

## 背景

M2/M3 的回合只有一个 `flow.round_action`：一个 `wait` 节点，一层可选 guard。场景 B 需要「有合法牌则出牌，否则摸一张，牌堆空则 pass」和「出 7 跳过下家」，即**同一回合内多个候选动作**，并且效果有**固定阶段顺序**（B7：移牌 → 加分 → 终局判断 → 特殊效果 → 推进座位）。当前模型既表达不了「多动作」，也没有「终局先于 trigger」的保证；若每个玩法各自用 `assign` 拼阶段顺序，B 的「达到 6 分时不再执行 skip」就无法被独立核对。

## 决策

**`FlowSpec` 增加有序动作序列 `turn_actions`；编译器按顺序做 guard 选择，并把效果按固定阶段降层。**

```text
FlowSpec
  round_action: str                  # 保留：单动作规则的默认/主动作
  turn_actions: list[str] = []       # 有序候选动作；为空时等价 [round_action]
```

固定语义：

1. **一个回合 = 有序候选动作列表**。序列内 id 唯一、必须已声明；生效序列是 `turn_actions or [round_action]`。单动作规则行为与 M2 完全一致。
2. **按顺序选择第一个 guard 通过的动作**。编译器为序列生成 guard 链：动作 `i` 的 guard 为真 → 进入该动作的 `wait`；为假 → 进入动作 `i+1` 的 guard。
3. **guard 失败的行为**：继续尝试下一个候选动作；若**所有** guard 都失败（含最后一个动作有 guard 且为假），本回合**不执行动作、不计 `action_count`**，直接推进座位。这与 M2 单动作「guard 为假 → 跳过该回合」一致。已验证规则应让最后一个候选动作无条件，避免空转回合（覆盖检查会要求每个 `wait` 可达）。
4. **`legal_actions` / bot / HTTP / 前端**：因为 guard 在 `wait` 之前求值，任一时刻只有一个候选动作的 `wait` 被到达，故 `legal_actions` 返回**该动作 id**（与单动作规则同形）。bot 用该动作的 `ActionDescriptor` 生成 payload；HTTP/前端提交 `action_id + input_values`，沿用 M3a 的协议。需要「玩家在多个合法动作间选择」的玩法不在本 ADR 范围（那需要按动作分别路由的 `wait`，另立 ADR）。
5. **执行顺序固定**（不得由规则重排）：

   ```text
   action(输入) → effects → score(计入 effects) → terminal → trigger → advance
   ```

   其中 `effects` 是动作声明的普通效果（含 `move`/`refill`/`remove_pairs`/`score_settle`）；`terminal` 是 ADR-0010 的终局闸门；`trigger` 是声明为 trigger 阶段的效果（如 `skip`）。编译器把 trigger 阶段的效果**一律排在终局闸门之后**，与它们在 `effects` 列表中的位置无关。
6. **终局后不再执行 trigger、skip 或换回合**：终局闸门为真 → 直接 `finish`，跳过 trigger 与座位推进。这就是 B10「达到 6 分只结算，不再执行 7 的 skip」。
7. **`skip` 语义**：`skip` trigger 声明「满足条件时跳过下家一次」。被跳过的座位**不计 `action_count`**（B11）。实现上 `skip` 设置 `state.skip_next`，推进座位时前进 `1 + skip_next` 个座位并清零；回合完成以 `action_count` 为准（而非被跳过的座位数）。
8. **guard 只允许闭合语法**：在 M2 表达式之上增加**顶牌引用**，不得有动态路径、任意函数或 `eval`：

   ```text
   top(<zone>).rank        # 区域顶牌的 rank；空区域 → false（不报错），编译期区域必须已声明
   top(<zone>).suit
   top(<zone>).value
   match(top(<zone>), <card-expr>)   # 复用 pattern.match 语义：同花或同点
   ```

   空区域上的 `top(...)` 在布尔语境返回 `false`；在数值语境（如 `.value` 比较）为**编译错误**（`guard_top_requires_cards`），避免运行期裸异常。`<zone>` 必须是声明区域；`<card-expr>` 只能是动作输入或顶牌引用。
9. **多动作与区域实例**：actor 区域实例（`zone_<id>`）在回合开始时统一计算一次，序列内所有动作共用；guard 在区域实例之后求值。
10. **终局、trace、回放、契约监测复用**：多动作不新增执行器；`record_trace` 记录实际 `input`（含动作 id），`contract_check` 按 ADR-0010 的终局监测与新增 trigger 监测核对。

## 备选方案与为何不选

| 方案 | 优点 | 不选原因 |
|---|---|---|
| 每个玩法用 `assign`/`branch` 手写阶段顺序 | 编译器改动小 | 阶段顺序变成玩法私有约定，「终局先于 trigger」无法被独立监测器核对，正是本 ADR 要消除的分歧 |
| 用多个 `wait` 节点让玩家自由选择动作 | 表达力强 | 需要按动作分别路由的输入协议与前端选择器，超出 B 所需；B 的候选动作由合法性唯一确定 |
| guard 允许任意区域扫描（如「手里是否有匹配牌」） | 能直接表达「有合法牌」 | 引入非闭合的集合量化与动态路径，违反 M2 的表达式边界；B 用「先尝试出牌、失败则摸牌」的选择链表达同一效果 |
| `skip` 用无界循环实现 | 灵活 | 结构分析拒绝无界环；跳过次数必须显式有界（B 为 1） |
| 终局与 trigger 由工具内部排序 | 工具简单 | 违反 ADR-0009「结果由规则声明」；B10 明确要求终局先于 skip |

## 后果

- 正面：B 的 draw/pass/skip 与固定阶段顺序可以由规则声明并被独立核对；单动作规则不变。
- 正面：guard 的顶牌引用是闭合语法，可静态类型检查并给出明确编译错误。
- 负面 / 代价：编译器多 guard 链与 trigger 阶段降层；`skip` 需要以 `action_count` 驱动回合完成。
- 负面：多动作规则的 plan fingerprint 与单动作不同（预期），旧 composed verification 需重新验证。
- **强制方式**：失败矩阵逐条成为用例；`contract_check` 增加「终局后无 trigger 效果」「skip 不计行动」监测；变异测试要求「把 trigger 排在 terminal 之前」被拒绝。

## 失败矩阵

| 输入 / 故障 | 必须观察到的结果 |
|---|---|
| `turn_actions` 引用未声明或重复动作 | IR 校验失败：`turn_action_unknown` / `turn_action_duplicate` |
| 所有 guard 均为假 | 本回合无动作、`action_count` 不增、座位照常推进 |
| guard 使用动态路径 / `eval` / 未声明区域 | 编译或校验失败，绝不执行 |
| 空区域上 `top(...).value` 用于数值比较 | 编译错误 `guard_top_requires_cards` |
| 达到分数阈值后仍执行 `skip` | `contract_check` 监测器拒绝（终局后无 trigger） |
| 被 skip 的座位计入 `action_count` | 「行动数 == 独立逐动作统计」监测器拒绝 |
| 把 trigger 效果排到终局闸门之前 | 阶段顺序监测器拒绝 |

## 实现补充（M3g-guard，`docs/adr/0012` 实施记录）

第 8 条的闭合语法在实现时做了一处收窄与一处扩展，均保持「无动态路径、无任意函数」：

- **收窄**：`match(top(zone), <card-expr>)` 的 `<card-expr>` 若为**本动作输入**，则它在 guard 求值时尚未产生（guard 先于 `wait`），无法作为回合内候选动作的判据。场景 B 的「有合法牌」因此用**有界** `has_match(zone, top(other))` 表达：它只扫描一个已声明牌区（≤ `MAX_SELECTION` 的单次查询），语义等同 `pattern.choices` 后取 `count > 0`，复用既有 `pattern.choices`，不引入集合量化或动态路径。
- **扩展**：`zone_count(<zone>)`（读一个已声明牌区的张数）用于「牌堆是否为空」这类有界条件。
- **空区域**：`top(...)` 在布尔语境返回 `false`（编译成 `zones.count_zone` + 分支，空区写入 null 卡），数值语境 `top(...).value` 是编译错误。
- **新增宿主操作**：`zones.cards(state, zone)`（纯读，返回该区牌列表），供 `has_match` 使用；它使 `registry.contract_hash()` 变化，旧 composed 验证凭据按 ADR-0008 失效并需重新验证。
- **指纹**：不含顶牌引用的单动作 guard 仍编译为 `pre_turn`/`guard_branch`，M2 指纹不变；含顶牌引用的 guard 会先发宿主调用再求值，指纹变化属预期。

第 7 条的 trigger 实现：`ActionSpec.trigger` 是一个有序效果列表（本包只允许 `skip`），编译器把它降层到终局闸门**之后**的共享阶段，用 `state.input.action` 分发到实际运行的动作；`skip` 写 `state.skip_next`，`bump_turn` 前进 `1 + skip_next` 并在 `set_turn_index` 里清零。规则无 trigger 时保留 M2 的 `bump_turn`/初始化，指纹不变。新增 `contract_check` 监测器 `action_counting`（行动数 == 记录到的动作数）与 `trigger_after_terminal`（结束局面的那个动作不得写 `skip_next`），对应失败矩阵的最后两行。

## 迁移与删除

- `round_action` 保持必填；`turn_actions` 为空即单动作，行为与 plan 指纹不变（回归测试锁定）。
- 多动作规则的旧 composed artifact 需重新验证（fingerprint 变化）。
- 前端在本包只扩展「按 `ActionDescriptor` 展示当前动作的输入」；真正的多动作选择 UI 在 M5 随 API 契约一并处理。
