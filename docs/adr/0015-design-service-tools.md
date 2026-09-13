# ADR-0015: 设计服务元工具与有界设计回合（M4-2..M4-7）

- 日期：2026-09-14
- 状态：accepted（**实现状态：已实现**，M4-2..M4-7）
- 决策者：架构 owner
- 关联：ADR-0005（composed IR 与编译）、ADR-0008（证据与版本绑定）、ADR-0014（设计会话存储）、`docs/development-plan-phase2.md` §M4

## 背景

M4-1 已经给出跨重启、带乐观锁的设计状态，但还没有任何**写入它的工具**。计划要求 Agent 通过多轮对话完成「查询能力 → 提交/修订 IR → 编译 → 诊断 → 正式验证 → 冻结」，并在表达不了时诚实退出。这里有三类风险：

1. 模型可以直接提交一个 raw `GamePlan` 绕过 IR、能力解析与编译器；
2. 模型可以宣布「可玩」并自行注册版本；
3. 模型为了通过验证而删除用户需求，或无上限地反复请求模型修复宿主错误。

## 决策

**新增 `agent/design_service.py`：设计会话的唯一写入者；每个工具都是一次宿主执行、经 `DesignStore.commit` 乐观落盘的调用。**

工具表 `DESIGN_TOOL_SCHEMAS` 与旧 `meta_tools.TOOL_SCHEMAS` **分开**：旧 loop 保留已知族契约，设计服务只暴露组合规则工具。语义固定如下：

| 工具 | 语义 |
|---|---|
| `list_capabilities` / `describe_mechanism` | 只读；分别取自 `capability_matrix()` 与 `core_registry().export()`，与 Interpreter 同源，无按游戏名分派 |
| `propose_ir` | 解析任意设计入口（composed / 已知族），写规范化 IR、`ir_hash`、需求条款 id，回到 `draft`，清空编译/验证证据 |
| `patch_ir` | 顶层或点分路径（`actions.0.guard`）合并后重解析；**默认拒绝删除已声明条款**（`requirement_removed:<id>`），显式允许才可；任何修改都作废旧证据 |
| `capability_check` / `compose_plan` | 能力解析与 `compile_composed`；`compose_plan` 拒绝 `args.plan`（`raw_plan_not_accepted`），编译失败以 `stage/path/clause` 结构化保存 |
| `validate_plan` / `simulate` | 只读诊断：可达性/有界循环校验；一条有界 bot 路径。都不改 revision |
| `verify_game` | 宿主正式门槛（多种子 × 多策略 × 独立契约检查），保存 `VerificationResult` 与 `verification_id`，状态置 `verified`/`failed` |
| `inspect_failure` | 只读：返回最近失败的路径/条款、`source_map` 条款→节点、精简状态 |
| `finalize` | 仅当保存的证据仍绑定当前 `ir_hash`/`plan_hash` 时，用 `build_artifact` 生成**候选**产物；只存摘要，**不写 SessionStore、不注册、不代表用户确认** |
| `ask_user` / `unsupported` | 诚实退出：前者持久化问题并返回 `kind=question`，后者置 `failed` 并返回 `kind=unsupported` |

**`run_design_loop`（`agent/design_loop.py`）**：decide → 宿主工具 → observation → decide，预算来自 §7（决策数、修复次数、估算 token、墙钟、单步输出）。可恢复失败（IR 非法、编译/验证失败、输出不可解析）回灌为 observation；宿主失败（模型传输、`tool_crashed`、`stale_revision`、`request_id_conflict`）停止该轮。聊天、预算与用量写入会话 `context`。

**宏本轮不做**：不提供 `define_local_macro` / `validate_macro`，`macro` 轴保持 `planned`。旧的 `match_turn` 宿主宏仍存在，但模型没有生成宏的入口，因此不能宣称该轴已覆盖。

API：`POST /api/designs/{id}/messages`；`POST /api/agent/loop` 在给定 `design_id`/`session_id` 时改走设计 loop，否则保持旧行为；设计工具表在 `GET /api/agent/design-tools`。

## 备选方案与为何不选

| 方案 | 优点 | 不选原因 |
|---|---|---|
| 复用 `meta_tools.dispatch` | 少一个模块 | 旧 loop 是已知族专用；混入组合工具会让「宏轴为 planned」的架构断言与语义同时失真 |
| `compose_plan` 接受模型 raw plan | 表达力强 | 绕过 IR、能力解析与编译器，正是 ADR-0005/0006 要消除的第二条路径 |
| `finalize` 直接调 `publish_composed` 注册 | 少一步 | 让模型自确认并写可运行版本，绕过 ADR-0008 的宿主证据与用户确认 |
| 无预算地让模型反复修宿主错误 | 实现简单 | 无上限请求、错误归因错误；§7 明确要求有限决策/修复/时间 |

## 后果

- 正面：组合、编译、验证、冻结全部经同一注册表与同一编译器；候选产物与可运行版本分离。
- 正面：失败可定位到 IR 路径与需求条款；重复/旧 revision 由 ADR-0014 的锁保证。
- 正面：预算与诚实退出，避免「模型为了过测删条款」。
- 负面：设计会话的 `context` 需要限制大小（列表裁剪，`plan` 不落库，按需重编译）。
- 负面：宏能力仍缺，必须继续在能力矩阵与文档里标 `planned`。

## 失败矩阵

| 输入 / 故障 | 必须观察到的结果 |
|---|---|
| `compose_plan` 带 `plan` | `raw_plan_not_accepted`，不写 plan |
| `patch_ir` 删除已声明条款 | `requirement_removed:<id>`，IR 不变 |
| 非法 IR / 行为语义变化 | `invalid_ir` / 证据清空，revision 不变（非法时） |
| 编译失败 | `compiler_error:<code>` + `path`/`clause`，状态不前进 |
| 验证失败 | `verification_failed`，状态 `failed`，`inspect_failure` 可定位 |
| 证据与当前规则不符 | `finalize` 返回 `verification_stale` |
| `finalize` 成功 | 仅返回候选摘要，`SessionStore` 无新版本 |
| 模型传输/工具崩溃/并发旧 revision | 该轮停止并返回 `error`，不请求模型修复宿主 |
| 宏相关请求 | 无对应工具；`macro` 轴保持 `planned` |

## 迁移

- 设计工具只经 `DesignStore.commit` 写状态；`/api/designs/{id}/update` 仍是 M4-1 的显式字段接口，`context` 也接受。
- M5 的 `/api/designs/{id}/runs/*`、`confirm`、`publish` 在此之上扩展：`finalize` 产出的候选摘要即「待核对」对象，注册仍走 `publish_composed`。
