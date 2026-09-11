# Pocker Agent 工程规范（阶段门 + 测试 + Review + 防膨胀）

> 状态：v1（2026-09-11）
> 适用范围：`v0.4-core` 及之后所有分支
> 配套强制物：`tests/test_architecture_invariants.py`（用测试锁死本规范的硬规则）

---

## 0. 为什么需要这份规范

`codex/doudizhu-holdem` 的教训不是"代码写得多"，而是：

- **47 commits / +7325 行 / 189 测试全绿**，却同时长出：
  - 两套执行路径（`FamilyEngine` 与 `FlowRuntime`）；
  - 两个真相源（宿主成品 plan 与模型 plan）；
  - 没有验证门槛（只跑一次 `setup()`）；
  - 文档与实现漂移（"166 passed"、"条款说 3 题实际 1 题"）。
- **"测试全绿"和"大家约定"都拦不住这些**，因为它们不是测试失败，是**架构决策漂移**。

所以本规范的核心不是"多写测试"，而是把规则变成三种东西：

```
不可谈判规则  →  可执行不变量测试（tests/）
阶段准出      →  门禁（不通过不得进入下一阶段）
结构变更      →  单向门 + ADR + 同 PR 删除
```

---

## 1. 十条不可谈判规则（Invariants）

| # | 规则 | 强制方式 | 位置 |
|---|---|---|---|
| 1 | **单一执行路径**：同一玩法只能有一个执行器 | 架构测试：`core/` 不得 import 旧引擎；core 只能有一个解释器类 | `test_core_is_a_single_execution_path` / `test_core_has_exactly_one_interpreter` |
| 2 | **模型只产数据**：不在运行时执行模型逻辑 | Review 红线 + §5 清单 | PR checklist |
| 3 | **单一权威表示**：`RulesIR` 是合同、`GamePlan` 是实现 | `contract_check`（S2 起） | `verify/` |
| 4 | **契约先行**：没有 `requires/effects/failure` 的 operation 不得注册 | 架构测试 + 解释器构造期校验 | `test_every_core_operation_declares_its_contract` |
| 5 | **未过 playtest 不得称可玩** | `finalize` 门槛 + `playtest` 测试 | `core/playtest.py` |
| 6 | **缺能力必须命名**：`unsupported + missing_axes` | `capability_check`（S2） | `verify/capability.py` |
| 7 | **工具无状态 / 随机只来自 seed** | 状态往返测试 + 契约 `effects` 差异检查 | `test_serialize_restore_is_byte_stable_across_play` |
| 8 | **不新增游戏专属引擎/工具** | 架构测试：注册表不得含游戏名 | `test_no_game_specific_tool_in_the_core_registry` |
| 9 | **结构性改动必须带删除** | Review 红线 + 删除积压必须为 0 | §6.3 |
| 10 | **文档与代码同 PR**；每条规则引用强制它的测试 | Review 红线 | §8 |

> 规则 1/4/7/8 已经由 `tests/test_architecture_invariants.py` 自动强制。**测试失败时，正确的动作是改架构或改这条规则（走 ADR），而不是让测试变绿。**

---

## 2. 阶段模型与门禁

阶段命名统一（括号内是历史叫法）：

```
S0 = P0 = M1   执行内核
S1 = P1 = M2   Agent loop
S2 = P2 = M3   IR + 编译 + 关键 axis
S3 = P3 = M4   工具库扩展 + 沙箱
S4             覆盖度量与交付（横切，贯穿始终）
```

**门禁铁律：未通过当前阶段的"准出证据"，不得开始下一阶段的功能开发。** 允许提前做"探索性 spike"，但必须在独立分支、不进主干。

---

## 3. 每阶段开发规范

### S0 · 执行内核（当前）

**目标**：唯一执行路径 + 契约 + playtest 门槛 + 能力判定。

| 项 | 内容 |
|---|---|
| 允许做 | 契约层、计划 schema、解释器、`playtest`、核心工具、`capability_check`、Session、API 接线、删除旧路径 |
| 禁止做 | 新 axis、agent loop、前端重构、新玩法引擎 |
| 必需测试 | 契约测试、解释器单元、事务回滚、`playtest`（多 seed×策略 + wait 覆盖 + 字节重放）、架构不变量 |
| Review 重点 | 回滚完整性、契约差异检查、确定性、旧路径是否真的删了 |
| 准出证据 | ① `pytest` 全绿含架构不变量；② 旧引擎已移出 dispatch（有删除 commit）；③ 一条真实 rules 的条款与 trace 逐条一致 |
| 预算 | core 工具 ≤ 12（架构测试棘轮）；execution paths = 1 |

**S0 准出状态：已完成。**

- 已完成：契约强制、playtest 策略与覆盖门槛、serialize/restore 不变量、架构不变量测试、`capability_check` + 能力状态机、语料库覆盖率报表、`core/session.py` + v0.4 API（`app.py`）、真实 HTTP 端到端（`scripts/e2e_smoke.py`，14 项）、前端按原型改造并接通真实接口、**旧引擎生产路径已删除（删除积压 = 0）**。
- 旧引擎删除前先抽取了黄金 trace（`benchmarks/oracle/*-seed7.json`，`confirmation: pending_human`），由 `tests/test_oracle_fixtures.py` 校验；执行代码已删除，行为以数据形式保留（engineering-standards §14.3）。

### S1 · Agent loop

**目标**：闭合"起草 → 组装 → 失败 → 修复 → 冻结"，模型可观察、可反复调用。

| 项 | 内容 |
|---|---|
| 允许做 | 元工具（`ask_user/propose_ir/patch_ir/compose_plan/validate_plan/simulate/playtest/repair/finalize`）、observation 回灌、预算控制 |
| 禁止做 | 让模型改 IR 之外偷补语义；让 `finalize` 绕过门槛；在 loop 里直接调游戏工具 |
| 必需测试 | 元工具单元测试；loop 集成测试（构造会失败的 plan → 断言能自我修复）；预算耗尽测试；observation 不含全量 trace 的测试 |
| Review 重点 | 模型不能执行/不能自证；失败必须是 observation；预算硬封顶；`agent_compose` 不得改 IR |
| 准出证据 | ① 2 个族（arithmetic + shedding）端到端无人工通过；② 故意注入错误 plan，loop 能修好；③ 超出预算时有明确失败 |
| 预算 | loop 步数 / token / 时间 各设上限并在测试中断言 |

### S2 · RulesIR + 编译 + 关键 axis

**目标**：从"能生成"到"覆盖大部分常见玩法的组合"。

| 项 | 内容 |
|---|---|
| 允许做 | `RulesIR` schema、`host_compile`、`capability_check`、`pattern_lang`、`info_set`、语料库与覆盖率报表 |
| 禁止做 | 用游戏专属工具替代 axis；用 `unsupported` 之外的降级；跳过覆盖率报表 |
| 必需测试 | IR schema 测试；`host_compile` 对每族的一致性测试（编译产物过 playtest）；每条 axis 的属性测试（守恒/可见性/可达/边界）；`capability_check` 的 missing_axes 正确性测试 |
| Review 重点 | axis 是否参数化（不是分叉）；IR 是否"IR 没写的 Plan 不许发明"；覆盖率报表是否更新 |
| 准出证据 | ① 语料库覆盖率报表 + 失败直方图；② 每个 `stable` 族有 golden trace；③ `planned` 状态不得被当作 covered |
| 预算 | 每条 axis 的 operation 数 ≤ 8；语料库 ≥ 30 个玩法并标注所需 axis |

### S3 · 工具库扩展 + 沙箱

**目标**：处理长尾/陌生机制，且不破坏主执行面。

| 项 | 内容 |
|---|---|
| 允许做 | macro 形式化（契约 + 属性测试 + 注册 + 提升规则）、T2 新原语（**人工实现 + Review**）、沙箱作为独立协议 |
| 禁止做 | 让沙箱代码进入主 Interpreter；同一玩法混用两个执行面；让模型自行把 T2 塞进内核 |
| 必需测试 | macro 展开合法性测试；macro 契约测试；沙箱帧校验测试；"同一玩法只有一个执行面"的测试 |
| Review 重点 | macro body 是否只含已声明 op；沙箱是否隔离；两个执行面是否互斥 |
| 准出证据 | ① 至少 1 个 macro 被 ≥2 玩法复用（触发提升评估）；② 沙箱失败时有明确错误与回退；③ 执行面互斥有测试 |
| 预算 | macro 数量、沙箱超时/内存/输出上限必须显式配置并有测试 |

### S4 · 覆盖度量与交付（横切）

**目标**：让"适配大部分游戏"变成可验收的数字。

| 项 | 内容 |
|---|---|
| 允许做 | 语料库维护、覆盖率报表、生成基准、失败直方图、能力矩阵对外 |
| 禁止做 | 用单条成功用例宣布能力；用"看起来能跑"替代报表 |
| 必需测试 | 报表生成脚本的回归；`capability_check` 对已知玩法分类正确 |
| 准出证据 | ① 生成成功率 / playtest 通过率 / 平均修复轮数 / unsupported 率；② **生成的游戏 100% 过 playtest** |
| 输出 | 每次发布的覆盖率报表；差距清单（按缺失 axis 分桶） |

---

## 4. 测试规范

### 4.1 测试层次（每层证明什么）

| 层 | 证明什么 | 例子 |
|---|---|---|
| 契约测试 | operation 的 `requires/ensures/effects` 被强制 | 越权改 state 被拒且回滚 |
| 单元测试 | 纯机制正确 | 精确算式、计分、账本 |
| 属性测试 | 状态空间性质 | 牌张守恒、终止、可达、边界 |
| 计划测试 | 计划结构合法 | 目标存在、工具已声明、operation 存在 |
| 对局测试 | 端到端正确 | `playtest`：多 seed×策略 + 不变量 + wait 覆盖 + 字节重放 |
| 架构不变量测试 | 架构没漂移 | `test_architecture_invariants.py` |
| 端到端测试 | API/前端链路 | `/api/runtime/*` 一致性护栏 |

### 4.2 改动类型 → 必需测试（矩阵）

| 改动 | 必需新增/更新测试 |
|---|---|
| 新增/修改 tool operation | 契约测试（effects/requires/ensures）+ 单元测试 |
| 新增 axis | 属性测试（守恒/边界/确定性）+ 至少 2 个消费它的玩法编译测试 |
| 修改 plan schema | 计划测试 + 全部引用它的族重跑 |
| 修改 IR | `host_compile` 一致性测试 + 覆盖率报表重跑 |
| 修改 API | 一致性护栏测试（同 seed 同动作 trace 与 playtest 一致） |
| 修改前端 | 端到端冒烟（至少：开局→动作→结束→重开） |
| 修改核心解释器 | 上述全部 + 架构不变量 |

### 4.3 禁止的测试

1. 只断言 happy path；
2. **只跑 `setup()` 就算验证**（旧分支的根因之一）；
3. 无 seed 的随机测试（不可重放）；
4. 断言实现细节（事件条数、内部字段）而非不变量；
5. 用模型输出当 oracle（模型不能自证）。

### 4.4 失败信息规范

失败必须指出**违反了哪条不变量或契约**，例如：

```
out_of_contract_state_change:sneaky
precondition_failed:exact_expression.validate
wait_nodes_uncovered:wait_b
nondeterministic_replay
```

不允许 `assert result`（无信息）。

---

## 5. Code Review 规范

### 5.1 PR 粒度

- **有效 diff ≤ 400 行**（不含自动生成、锁文件、纯移动）；
- 结构性改动（新目录/新执行入口/schema 变更）**单独 PR**；
- 一个 PR 只做一件事；**禁止"顺手重构"混入功能 PR**。

### 5.2 红线检查（reviewer 必须逐条回答）

1. 是否新增了第二条执行路径或第二个真相源？
2. 是否新增了"游戏专属"引擎/工具？（规则 8）
3. 结构性改动是否**同 PR 删除了旧路径**？（规则 9）
4. 新 operation 是否有完整契约并被解释器强制？
5. 是否让"未过 playtest"的东西变成可玩？
6. 模型是否被赋予了"执行"或"自证"的能力？
7. 是否引入无 seed 的随机、时钟或全局状态？
8. 文档是否同 PR 更新，且每条规则引用了强制它的测试？

任一条为"是/否（视问题而定）"不符合时，**必须拒绝**，不能"下次再说"。

### 5.3 分级

| 变更 | 需要谁审 |
|---|---|
| tool / 单元 | ≥1 名 reviewer |
| axis / IR / 解释器 / 契约 | ≥2 名（其中 1 名架构 owner） |
| 执行路径 / schema 版本 / 新依赖 | 架构 owner + ADR |

### 5.4 Reviewer 的正确姿态

- 审**不变量与边界**，不审代码风格（风格交给 formatter/lint）；
- 审**删除了什么**，不只审新增了什么；
- 审**测试是否真的能失败**（故意破坏一次，看测试是否报错）。

---

## 6. 防膨胀机制

### 6.1 单向门（One-way doors）

以下决策**不可逆或代价极高**，必须走 ADR + 双人 review：

- 新增执行入口 / 解释器；
- schema 版本变更（`GamePlan`/`RulesIR`）；
- 新增运行时依赖（尤其带原生扩展的）；
- 新增顶层包/目录；
- 允许沙箱代码进入可用路径。

### 6.2 棘轮（Ratchets，只能收紧）

预算写进**可执行测试**，改动它必须改测试 → 天然触发 review：

| 预算 | 当前值 | 强制位置 |
|---|---|---|
| 执行路径数 | 1 | `test_core_has_exactly_one_interpreter` |
| 游戏专属工具数 | 0 | `test_no_game_specific_tool_in_the_core_registry` |
| core 工具数 | ≤ 12 | `test_core_tool_budget_is_a_ratchet` |
| 未使用工具数 | 0 | `test_every_registered_tool_is_used_by_a_reference_plan` |
| 只允许 `state` 用 `"*"` effects | — | `test_only_state_tool_may_write_arbitrary_keys` |
| core LOC / 文件数 | 记录并只许持平 | 报表（S2 起自动化） |
| 删除积压 | 0 | §6.3 · `tests/test_oracle_fixtures.py::test_no_legacy_engine_module_remains_in_the_package`（旧引擎已删，行为以 `benchmarks/oracle/` 数据保留） |

### 6.3 删除纪律（防止"只加不减"）

**这是旧分支膨胀的直接原因：只做加法。**

1. 迁移一个玩法 → **同一个 PR 删除旧路径**（引擎降级为 oracle 也算"移动"，必须解释）。
2. 维护一张"删除积压"清单（旧引擎、成品模板、双调度、双来源分支），**必须保持为 0**。
3. **结构性 PR 不允许是 add-only**：reviewer 直接问"删了什么？"
4. 任何"暂时保留"必须有 deadline 和负责人，写进 issue，且计入积压。

### 6.4 工具提升规则（防止"一游戏一工具"）

```
同一 macro（或同形 macro）被 ≥ K 个玩法使用  →  提升为参数化 axis
否则                                        →  留在该玩法的计划里当局部 macro
```

K 默认 2，可在 ADR 中调整。**注册表只能按"被证明的复用"增长。**

### 6.5 文档规范

**活文档**（必须与代码同 PR 更新）：

- `docs/development-design-v0.4.md`（架构）
- `docs/core-v0.4.md`（core 规则）
- `docs/engineering-standards.md`（本文件）
- 能力矩阵（`/api/capabilities` 的数据源）

**每条设计规则必须标注"强制它的测试或门禁"**；找不到强制手段的规则，要么补测试，要么降级为"建议"。

### 6.6 ADR（架构决策记录）

必须写 ADR 的情形：§6.1 的单向门、预算调整、能力状态升级（`planned → experimental → stable`）、执行面变更。

存放：`docs/adr/NNNN-title.md`，模板见 §11.3。

---

## 7. CI 门槛（阻断合并）

```
1. format / lint / typecheck
2. unit + contract tests
3. architecture invariants        ← 任何失败即阻断
4. playtest（参考族的完整对局）
5. build（前端 / 包）
6. （S2 起）语料库覆盖率报表：覆盖率不得下降
7. （S2 起）生成基准：成功率/通过率不得下降
```

- **覆盖率与成功率只许升或持平**（ratchet）；
- 报表与上次发布对比，下降必须解释或修复；
- CI 失败**不允许**"先合并、后修"。

---

## 8. Definition of Done（可交付）

一个变更要算"完成"，必须同时满足：

```text
[ ] 契约/单元/属性/对局测试按 §4.2 矩阵齐全
[ ] 架构不变量测试通过（未新增执行路径 / 游戏专属工具 / 死工具）
[ ] 相关族跑过 playtest（多 seed × 策略 + 不变量 + wait 覆盖 + 字节重放）
[ ] 若为结构性改动：旧路径已在本 PR 删除，删除积压仍为 0
[ ] 若为单向门：ADR 已写并被批准
[ ] 活文档同 PR 更新，新规则标注了强制它的测试
[ ] 有可复现命令（一行）证明上述内容
```

---

## 9. 反模式与红线（带真实教训）

| 反模式 | 旧分支的真实后果 |
|---|---|
| 只加不减的结构性 PR | 长出两套执行、双调度、双状态模型 |
| 用"测试全绿"代替验证 | `give_up→winners`、假无解、条款说 3 题实际 1 题 |
| 让模型复述宿主成品模板 | 模型成复读器，宿主/模型 plan 漂移 |
| 只跑 `setup()` 当验证 | 错误 plan 一路发到 UI |
| "以后再补测试" | 迁移没有终点，永远补不完 |
| 文档写在实现之前且不同步 | "166 passed"、"9 种游戏"等与实际不符 |
| 让"暂时保留旧路径"没有 deadline | 双路径永久化 |

**红线（一票否决）**：新增第二执行路径、新增游戏专属引擎、add-only 结构性 PR、未过 playtest 即可玩、让模型自证正确。

---

## 10. 度量仪表（S2 起每次发布更新）

| 指标 | 目标 | 来源 |
|---|---|---|
| 语料库覆盖率（stable） | 单调上升 | `capability_check` 批量跑 |
| `unsupported` 率 | 单调下降，且都带 `missing_axes` | 同上 |
| 生成成功率 / playtest 通过率 | 通过率必须 = 100% | 生成基准 |
| 平均修复轮数 | 记录，用于评估 IR/契约质量 | 生成基准 |
| 失败直方图（按缺失 axis） | 决定下一步补哪条 axis | 同上 |
| 预算使用（工具数/执行路径/删除积压） | 不超预算，积压 = 0 | 架构测试 + 报表 |

---

## 11. 模板

### 11.1 PR 模板

见 `.github/PULL_REQUEST_TEMPLATE.md`（包含 §5.2 的八条红线）。

### 11.2 阶段准出检查表

```text
阶段：S_
[ ] 本阶段必需测试齐全（§4.2）
[ ] 架构不变量通过且未放宽
[ ] 准出证据 ①②③ 已附命令与输出
[ ] 删除积压 = 0
[ ] 活文档已更新
[ ] 预算未超
[ ] 已归档 golden trace / 覆盖率报表（S2+）
```

### 11.3 ADR 模板

```markdown
# ADR-NNNN: <标题>
- 日期：
- 状态：proposed | accepted | superseded by ADR-XXXX

## 背景
（当前架构与遇到的问题）

## 决策
（选了什么，明确到可执行）

## 备选方案与为何不选

## 后果
- 正面：
- 负面/代价：
- 强制方式（哪个测试/门禁保证它不被绕过）：

## 迁移与删除
（旧路径何时删、由谁负责）
```

---

## 12. 一句话总结

**这套规范的目标不是"让团队更规范"，而是让旧分支那种失败在 CI 里就撞墙**：
结构漂移有架构不变量挡住，膨胀有棘轮和删除纪律挡住，"看起来能跑"有 playtest 门槛挡住，能力夸大有覆盖率报表挡住。**规则要么可执行，要么不写。**
