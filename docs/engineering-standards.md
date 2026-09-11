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

**S1 准出状态：已完成（核心闭环）。**

- `core/ir.py`：`RulesIR`（arithmetic / war，判别联合）+ `host_compile` + `required_axes` + `check_ir`。
- `agent/meta_tools.py` + `agent/loop.py`：11 个元工具（ask_user / propose_ir / patch_ir / capability_check / compose_plan / validate_plan / simulate / playtest / finalize / unsupported）、observation 回灌、步数预算、`finalize` 宿主门槛。
- 新增机制工具 `deck` / `rank_compare` / `winner_resolve`；`rank_compare` 由 planned 升为 **stable**（11 个语料玩法需要它）；新增 planned 轴 `point_total`（软 A 求和），因此 blackjack 仍**不可表达**（诚实，不滥用 axis 标签）。
- 覆盖：`arithmetic24` + `war` 可表达（2/30）。
- 测试：`test_ir_and_war.py`、`test_agent_loop.py`（预算耗尽 / 非 JSON 输出修复 / plan 失败后修复 / finalize 前必须过 playtest / unsupported）；HTTP 链路 `test_session_api.py::test_agent_composes_a_game_and_it_becomes_playable_over_http`。
- 真实端到端：`scripts/e2e_smoke.py` 16 项（含 `agent_meta_tools_exposed`、未配置模型时安全失败）。
- 前端：新增「新建玩法」（goal → 元工具 → observation 日志 → 开始试玩）与「模型设置」。
- 有意未做：仍用“JSON 决策”文本协议而非 OpenAI function-calling（接口可替换）；`agent_compose` 仅在已知族回退到 `host_compile`，新机制仍走 `unsupported`。

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

**S2 准出状态：部分完成（两条关键 axis 已落地，覆盖 7/30）。**

- `pattern_lang` → **stable**：`pattern` 工具（match / choices / describe / classify / beats）+ `matching` 工具（play / draw）。一套参数化牌型与合法性引擎，不再按玩法分叉。
- `info_set` → **stable**：`state.private_hands` + `Interpreter.view(viewer)`；非授权玩家的手牌按视角隐藏，终止后揭示。
- 新增可玩族 `crazy_eights`（隐藏手牌 + 同花/同点 + 万能牌指定花色 + 摸到能出），已过 playtest。
- `agent_compose` 真实路径跑通：模型提供 **plan**（非 `host_compile`）→ validate → playtest → finalize → 注册 → 可玩。
- 新增 planned 轴 `hidden_draw`，因此 Go Fish / 抽乌龟仍**不可表达**（不滥用 axis 标签）。
- 契约检查又抓到一类真实 bug：`init` 把 `state.hands/stock/table` 别名到 `deal` 载荷上，导致后续出牌“越权”改了 `deal`——被 `out_of_contract_state_change` 拦下，已修。
- 覆盖：**2/30 → 7/30（23.3%）**；缺口直方图 `turn_adapter 9 · team 7 · betting 6 · layout 3 · ledger 3 · trigger 3 · hidden_draw 2 · point_total 1`。
- 验证：`90 passed`；真实 HTTP 端到端 `19/19`。
- **缺口重估（诚实）**：之前估“pattern_lang + info_set → 12–15/30”偏高，实际 7/30。要到 12–15 必须补 **`turn_adapter`（+`team`）**，它是当前最大杠杆。

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

**S3 准出状态：部分完成（`turn_adapter` + `team` 已落地；macro / 沙箱后置）。**

- `turn_adapter` → **stable**：`trick` 工具（`legal` 跟花色合法性 / `play` 墩牌结算），支持将牌、领出花色、墩胜者领出。
- `team` → **stable**：`trick` 的 `teams` 配置 + `team_winners`，队伍共享胜负，并列共享（输出有序）。
- 新增可玩族 `whist`（4 人 2 队、跟牌、将牌、墩牌计分），已过 playtest；`SessionStore` 的声明式 bot 策略驱动 3 个非人类座位。
- 语料修正（诚实性）：`hearts` 去掉 `team`（红心大战不是组队游戏）、`spades`/`bridge` 去掉 `betting`（竞叫属于 `turn_adapter`）；`whist` 标注 `builtin`。
- 覆盖：**7/30 → 16/30（53.3%）**；剩余缺口 `betting 4 · layout 3 · ledger 3 · trigger 3 · hidden_draw 2 · point_total 1`。
- 验证：`100 passed`；真实 HTTP 端到端 `21/21`。
- 预算提示：core 工具 **11 / 12**；下一次新增机制工具前需先做一次“提升为 axis 或提高预算”的 ADR 决定。
- 未做：macro 形式化与提升规则、沙箱隔离协议（移到 S4）。

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

**S4 准出状态：机制部分完成（betting / ledger / hand_rank 已落地；macro 与沙箱未做）。**

- `betting` → **stable**：无上限下注轮（fold/check/call/raise/all_in、最小加注、轮次完成判定）。
- `ledger` → **stable**：筹码账本（提交、主池/边池/退款、奇数筹码、结算守恒）。
- `hand_rank` → **stable**：五张牌型评分与比较（含 A2345 轮子顺、七选五）。
- 新增可玩族 `five_card_poker`（五张私有牌 + 一轮下注 + 摊牌；弃牌即输、平局平分）。
- `legal_actions` 新增 `available_actions` 过滤：计划可按状态**收窄** wait 的静态动作集，**引擎只过滤、不发明**动作。
- 覆盖率：**16/31 → 21/31（67.7%）**；缺口 `layout 3 · trigger 3 · hidden_draw 2 · point_total 2`。
- 预算：**ADR-0002** 把 core 工具上限 12 → 16；当前 **14/16**，并保留“每个注册工具必须被参考计划使用”的不变量。
- 验证：`114 passed`；真实 HTTP 端到端 `23/23`。
- 未做（后置）：macro 形式化与提升规则、沙箱隔离协议。

**S5 准出状态：完成（`point_total` + 生成基准）。**

- `point_total` → **stable**：手牌点数与软 A 求和、庄家固定补牌策略、目标结算（爆牌 / 自然 21 / 平局）。
- 新增可玩族 `blackjack`（无下注 21 点：庄家暗牌、要牌/停牌、每轮独立洗牌、赢一轮 1 分）。
- `deck` 增加 `draw`；`bot_action` 现在覆盖三种回合形态（下注 / 牌局 / 兜底探测）。
- **基准跑出一个真实缺口**：通用策略在算术族上无法出牌（只会 `submit_expression`），导致 playtest 失败；已修为“三态分派 + 兜底探测”。
- 覆盖率：**21/31 → 23/31（74.2%）**；剩余缺口 `layout 3 · trigger 3 · hidden_draw 2`。
- **生成基准** `scripts/benchmark_generation.py` + `benchmarks/generation_cases.json`（8 例：6 可玩 + 2 边界）：
  - scripted 模式：`finalized 6/6`、`playtest pass 6/6`、`correctly unsupported 2/2`、`mean attempts 3.25`、缺口直方图 `{hidden_draw: 1, layout: 1}`；
  - 真实模型模式：用已保存配置运行，报告写入 `artifacts/`（已 gitignore）。
- 验证：`129 passed`；真实 HTTP 端到端 `24/24`。
- 工具：**15/16**。
- 未做（后置）：macro 形式化与提升规则、沙箱隔离协议。

**S6 准出状态：完成（`hidden_draw` + Go Fish + ADR-0003）。**

- `hidden_draw` → **stable**：向对手隐藏手牌按点数取牌、成对消除并计分、按手牌生成可询问选项、空手补牌、牌堆耗尽判定。
- 引擎增强：wait 的**通配输入**（`ask:*`）现在按 `state.available_actions` 展开为**具体动作**（`ask:7`）。参数化动作第一次成为一等公民；引擎只展开与过滤，**字面 `ask:*` 会被拒绝**。
- 新增可玩族 `go_fish`（两人钓鱼：向对手要牌、成功继续、失败摸牌换手、成对得分、牌堆耗尽结束）。
- 覆盖率：**23/31 → 25/31（80.7%）**；剩余缺口 `layout 3 · trigger 3`。
- 预算：**ADR-0003** 把上限 16 → 17；当前 **16/17**。
- 生成基准：9 例（7 可玩 + 2 边界）；scripted 模式 `finalized 7/7`、`playtest pass 7/7`、`correctly unsupported 2/2`、`mean attempts 3.33`、缺口 `{layout:1, trigger:1}`。
- 验证：`141 passed`；真实 HTTP 端到端 `26/26`。
- 未做：macro 形式化 + 提升规则、沙箱隔离协议。

**S7 准出状态：完成（`trigger` + UNO 类 + ADR-0004）。**

- `trigger` → **stable**：声明式特殊牌效果（摸 N 张给某座位、跳过、反向、指定花色）；效果表是纯数据，可跨玩法复用。
- `logic` 增加 `mul` / `mod`：多座位的方向与跳过用 `(current + direction*(1+skip)) mod players` 在计划里算，而不是写进 Python。
- 新增可玩族 `uno`（同花/同点接牌 + 万能牌指定花色 + 2 摸两张并跳过 + K 跳过）。
- **修掉一个真实缺陷**：`matching` 之前把桌面当作“只有顶牌”，弃牌无法回收 → 牌堆耗尽；现在维护独立弃牌堆，桌面只暴露顶牌。
- **诚实性修正**：`trigger` 落地后发现 `spoons` 真正缺的是**同时行动**，不是触发器；新增 planned 轴 `simultaneous`，覆盖率因此从 90.3% 回落到 **87.1%**。
- 覆盖率：**25/31 → 27/31（87.1%）**；剩余缺口 `layout 3 · simultaneous 1`。
- 预算：**ADR-0004** 把上限 17 → 18；当前 **17/18**。
- 生成基准：10 例（8 可玩 + 2 边界）；scripted 模式 `finalized 8/8`、`playtest pass 8/8`、`correctly unsupported 2/2`、`mean attempts 3.4`、缺口 `{layout:1, simultaneous:1}`。
- 验证：`152 passed`；真实 HTTP 端到端 `27/27`。
- 未做：macro 形式化 + 提升规则、沙箱隔离协议。

**S8 准出状态：完成（macro 形式化 + 提升规则）。**

- `core/macros.py`：`MacroSpec` / `expand_macro` / `wire` / `MacroRegistry` / `validate_macro` / `promotion_report`。
  - 宏是**声明式数据**，在**构建期内联**进计划（解释器不变），因此宏不可能引入新的运行时能力。
  - 展开时前缀节点 id 并重写内部引用；`@exit:` 占位符由调用方 `wire`，**未接线即报错**。
  - `validate_macro` 拒绝 `end` 节点、未声明的 exit、不存在的 operation（对接 registry）。
- `core/macro_library.py`：`match_turn` 宏（“算出该座位的合法牌 → 等待出牌或摸牌”），由 **crazy_eights 与 uno 两个计划真实复用**（不是声明性的）。
- **提升规则**：`promotion_report` 只标记 `kind="mechanism"` 且被 ≥2 个计划复用的宏；**flow 宏不参与**（axis 是机制，不是控制流形状）。
- 覆盖与工具数不变（27/31，17/18）；**两个被重构的族仍过 playtest 与完整对局测试**。
- 验证：`161 passed`；Web 逐族启动验证 **8/8**（每族 `legal_actions` 正确）。
- 新增 `scripts/run_web.ps1`：一条命令构建前端并启动应用。
- 未做：`layout`、`simultaneous`、沙箱隔离协议。

**S8 补丁：常驻进程可观测性（2026-09-11）。**

- 事故：设计页「发送」无效。真因**不是前端** —— uvicorn 在启动时只 import 一次 app，
  一个早于 `4304601` 的进程仍在 8000 上服务：页面加载新 bundle，API 还是旧 schema，
  `POST /api/agent/loop` 返回 `body.goal: Field required`。
- `/health` 现在返回 `pid` / `code` / `assets` / `assets_present`。`code` 是**import 时**算出的包源码指纹，
  刻意不在请求时重算：常驻进程必须一直报「它实际加载的代码」，否则这个信号就没了。
- `scripts/doctor.py --url http://127.0.0.1:8000`：把服务报的 `code` 与**磁盘当前**指纹对比，
  并探测 chat schema 合约（空 `message` 必须回 `请输入玩法描述`；陈旧进程会回 `goal: Field required`）。
- `scripts/run_web.py`：端口被占用且上面是本应用时，直接说明那个进程是「当前代码」还是「陈旧代码」，
  并给出 `Stop-Process -Id <pid>`；不再静默换端口继续跑（那会让人一直看旧页面）。
- `scripts/e2e_smoke.py`：模型已配置时不再发 `goal` 探测（那会变成真实 LLM 调用并超时），
  改为始终校验空 `message` 合约，跳过的项打印 `SKIP` 说明。
- 验证：`171 passed`；真实 HTTP 端到端 `27/27`（1 项 SKIP）；`doctor.py --serve` 与 `doctor.py --url` 全绿。
- 同时修掉一处同类陈量：`SYSTEM_PROMPT` 的能力清单到 S8 仍写 `kind = arithmetic | war`（实际 8 个族）。
  已改为从 `core/ir.REQUIRED_AXES` 推导（`IR_KINDS`），并加测试断言每个 kind 都出现在提示里；
  否则设计对话会被带偏（修前 4 个对话生成的玩法全是 `war`）。同一处陈量也在 `propose_ir` 的工具描述里（会显示到前端），已一并推导。

**S8 补丁 2：轴状态与实现对齐（2026-09-11）。**

- `Capability` 新增 `note`：**未覆盖的轴必须说明到底缺什么**（否则缺口会变成静默失败），由架构测试强制。
  四条 `planned` 轴现在各自写清楚了缺口：`layout`（无区域原语）、`simultaneous`（wait 只能挂一个座位）、
  `sandbox`（需独立隔离协议）、`macro`。
- 新不变量：**`stable` 轴不得建立在占位机制上**（`axis.*` / `protocol.*`）。
  `stable` 的含义是「宿主能编译且能验证需要它的玩法」，占位符只是承诺，不是实现。
- `macro` 轴澄清（它看似矛盾：实现已完成，轴却是 `planned`）：
  `core/macros.py` 的**构建期内联**已实现且 `match_turn` 被 2 个计划真实复用（宿主侧完成）；
  但**模型没有生成/注册宏的元工具**，而轴的名字正是「声明式宏生成与注册」——对 agent 而言仍是缺口。
  故保持 `planned`，并用测试钉住：一旦有人加了 macro 元工具，就必须同时提升该轴。
- 提升规则变成活规则：`promotion_report(PLAN_MACROS, default_macros())` 必须为空，且其结果现在从
  `/api/capabilities.macro_promotion` 暴露，玩法库页「宏提升规则」一栏直接显示（当前：无待提升项）。

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
| 修改 axis 状态 | `stable` 必须指向真实机制且该机制的玩法过 playtest；`planned` 必须带 note 说明缺口 |
| 修改 plan schema | 计划测试 + 全部引用它的族重跑 |
| 修改 IR | `host_compile` 一致性测试 + 覆盖率报表重跑 |
| 修改 API | 一致性护栏测试（同 seed 同动作 trace 与 playtest 一致） |
| 修改前端 | 端到端冒烟（至少：开局→动作→结束→重开） |
| 修改启动/诊断脚本 | `doctor.py --url` 冒烟：服务报的 `code` 指纹 == 磁盘指纹，且 chat schema 合约成立 |
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
| core 工具数 | ≤ 18（ADR-0004）· 当前 17 | `test_core_tool_budget_is_a_ratchet` |
| 未使用工具数 | 0 | `test_every_registered_tool_is_used_by_a_reference_plan` |
| 只允许 `state` 用 `"*"` effects | — | `test_only_state_tool_may_write_arbitrary_keys` |
| `stable` 轴必须指向真实机制 | 0 个占位 | `test_a_stable_axis_must_name_a_real_mechanism` |
| 未覆盖轴必须说明缺口 | 4/4 有 note | `test_every_uncovered_axis_says_what_is_missing` |
| 宏提升欠账 | 0 | `test_the_shipped_macro_library_owes_no_promotion` |
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
| 长驻进程存活于一次改动之后 | 前端已更新、API 还是旧 schema；"发不出去"被误判为前端 bug |
| 提示词里的能力清单手工维护 | `SYSTEM_PROMPT` 到 S8 仍写 `kind = arithmetic \| war`，设计对话被带偏到 war 族 |

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
