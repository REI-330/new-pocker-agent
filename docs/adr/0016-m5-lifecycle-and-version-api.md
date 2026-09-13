# ADR-0016: M5 设计生命周期与版本读取 API（Schema 冻结）

- 日期：2026-09-14
- 状态：accepted（**实现状态：M5-1、M5-2A..M5-2D 已实现**）
- 决策者：架构 owner
- 关联：ADR-0008（证据与版本绑定）、ADR-0014（设计会话存储）、ADR-0015（设计服务工具）、`docs/development-plan-phase2.md` §M5

## 背景

M4 已经给出「设计会话 + 宿主工具 + 有界回合 + 候选产物」，但还缺少把候选送到**用户核对**并**注册不可变版本**的 HTTP 契约，也缺少让一次长回合可被轮询的持久记录。前端要在 M5 重做，如果路径和字段再漂移一次，前端、e2e 与文档会各自实现一套。因此本 ADR 冻结 M5 的路径与语义，作为后续 M5-2..M5-6 的唯一契约来源。

三条既有约束必须在新接口上保持：

1. 只有 ``publish_composed`` 能注册版本，且必须由宿主记录的 ``VerificationResult`` 授权（ADR-0008）；
2. 模型只能调用设计工具产生**候选**，不能确认、不能注册（ADR-0015）；
3. 设计状态的唯一写入者是 ``DesignStore.commit`` 的乐观锁（ADR-0014）。

## 决策

### 1. 路径冻结

| 方法/路径 | 用途 | 持久层 |
|---|---|---|
| `POST /api/designs` | 创建设计会话 | `DesignStore.create` |
| `GET /api/designs` | 列出设计会话摘要 | `DesignStore.list_sessions` |
| `GET /api/designs/{id}` | 恢复草案、IR、证据与阶段 | `DesignStore.get` |
| `POST /api/designs/{id}/update` | M4-1 显式字段写（保留） | `DesignStore.commit` |
| `POST /api/designs/{id}/messages` | 一轮设计对话；响应含 `run_id` | 设计循环 + `DesignRunStore` |
| `GET /api/designs/{id}/runs` | 该会话最近的运行摘要（默认 20，最多 100） | `DesignRunStore.list_runs` |
| `GET /api/designs/{id}/runs/{run_id}` | 一次运行的阶段、Observation、预算用量 | `DesignRunStore.get` |
| `POST /api/designs/{id}/verify` | 宿主直接跑正式验证门槛 | `DesignService.dispatch("verify_game")` |
| `POST /api/designs/{id}/confirm` | **用户**核对指定 `ir_hash` | `DesignStore.commit`（`context.confirmation`） |
| `POST /api/designs/{id}/publish` | 证据与确认齐备后注册不可变版本 | `SessionStore.verify_and_register` |
| `GET /api/games/{id}/versions` | 该 game_id 的版本摘要列表 | `SessionStore.list_versions` |
| `GET /api/games/{id}/versions/{version}` | 规则、产物摘要、验证证据、source-map 条款路径 | `SessionStore.get_artifact` / `get_verification` |

M5-2 同样冻结：`POST /api/sessions` 接受可选 `version`；`POST /api/sessions/{id}/actions` 接受 `action_id`/`input_values`/`revision`/`request_id`；`GET /api/sessions/{id}/events?after=&viewer=&limit=` 做有界增量读取。旧 `POST /api/sessions/{id}/actions/{action}` 与 `POST /api/agent/loop` 在迁移期仅是同一服务的适配层，删除需先有旧→新往返测试（ADR-0007）。

### 2. M5-2 通用对局运行时

- **输入契约全部 `extra="forbid"`**：未知字段不再被静默丢弃（典型是旧客户端传 `version`）。缺失版本返回 404 `游戏版本不存在`，不存在的创作玩法仍是 422。
- **通用动作**：`SessionStore.act_generic` 先从计划的 `ActionDescriptor` 取输入形状，按 `input_values` 校验（未知输入、缺必填、牌数越界、重复牌、他人/隐藏牌不可见均为 422），再把值交给解释器；解释器仍是最终权威，回退后 revision 不变。旧 `/actions/{action}` 与 `/api/agent/loop` 走同一服务。
- **有界事件**：`GET /api/sessions/{id}/events` 以解释器追加式事件索引为游标，`after`/`limit`（上限 500）返回 `cursor`/`total`/`has_more`；`seed`/`args`/`state` 等敏感键永不出边界。
- **回合幂等**：`/messages` 的 `request_id` 由 `DesignRunStore.find_by_request` 记录；重试在 revision 检查**之前**重放已存响应，相同 key 不同内容为 `request_id_conflict`（409），处理中的重复请求也返回 409。
- **发布幂等**：`publish` 先看 `context.published`，再看 `SessionStore.find_artifact_by_ir`；即使 artifact 已写入而会话提交失败，重试也复用同一版本号，不重复注册。
- **运行回收**：`DesignRunStore` 记录 `started_at`，应用启动时 `recover_stale` 把崩溃遗留的 `running` 运行标为 failed（`host_restarted`）。这是 agent 层允许使用时钟的唯一位置。

### 3. 设计运行（design run）

新增 `agent/design_runs.py::DesignRunStore`，表 `core_design_runs(run_id, session_id, seq, payload)`。一次 `messages` 回合：

- **在询问模型之前**写入 `status="running"` 的运行（因此长请求期间可被轮询）；
- 回合结束后写入终止状态、`kind`、`observations`（上限 64）、`used`、`budget`、候选 `artifact` 与 `verification`；
- 会话级上限 50 条，超出后淘汰最旧；
- 并发/重试冲突（`stale_revision`、`request_id_conflict`）把运行标记为 `failed` 并原样抛出（HTTP 409）。

运行只是**轨迹**：不持有 IR、不写 `SessionStore`、不注册。`run_status_for` 把 `finalized`/`question`/`unsupported` 视为 `completed`，其余（`error`/`budget_exhausted`）视为 `failed`。

### 4. 确认与注册语义

`confirm` 是独立的用户动作，只接受**等于当前 `session.ir_hash`** 的哈希；不一致返回 `approval_mismatch`（422）。确认写入 `context.confirmation = {ir_hash, revision, verified, verification_id}`。

`publish` 的前置顺序固定，任一步失败都不改动会话：

1. 已存在 `context.published` 且 `ir_hash` 相同 → 幂等返回（不重复注册、不新增版本）；
2. `context.confirmation.ir_hash == session.ir_hash`，否则 `confirmation_required`（422）；
3. `context.verification.ok` 且其 `ir_hash` 绑定当前规则，否则 `verification_required`（422）；
4. 仅组合 IR 可注册（`publish_requires_composed_ir`，422）；
5. 若 `SessionStore.find_artifact_by_ir` 已有同 `ir_hash` 的版本则复用它（含 artifact 已写、会话提交失败的恢复），否则版本号取调用方显式值或 `SessionStore.next_version`；
6. `SessionStore.verify_and_register(..., approval_ir_hash=session.ir_hash)` 重新跑宿主门槛、记录凭据、注册产物（`build_artifact` 在哈希不符时抛 `approval_mismatch`）；
7. 成功后才把摘要写入 `context.published` 并置 `status="finalized"`。

前端不得自行拼接规则或产生产物；它只调用这些接口并渲染返回的 `view()`。

### 5. 版本读取

- 列表项是**廉价摘要**：`schema_version/game_id/version/title/generation_source/plan_hash/ir_hash/compiler_version/registry_contract_hash/verification_id/approval_ir_hash`，不含 `plan`/`ir`。
- 详情额外返回 `rules`（确认过的 IR）、`source_map`（条款→节点与节点→IR 路径）、`verification`（宿主记录的凭据，含独立契约检查）与 `plan`，供规则页展示完整条款来源与真实证据，而不是只看 `kind/max_rounds`。
- 未注册的版本 `GET .../versions/{version}` 返回 404；未注册的 game_id 列表返回空数组。

## 备选方案与为何不选

| 方案 | 优点 | 不选原因 |
|---|---|---|
| `messages` 直接返回完整结果，不建 run 表 | 少一张表 | 长请求无反馈、断线即丢证据；与 §M5「返回运行 ID、可查询 Observation/预算」冲突 |
| 用内存字典记录运行 | 实现最快 | 进程重启即丢，与 ADR-0014 的持久化基线不一致 |
| `publish` 复用设计服务里已存的 `context.verification`，不重跑 | 快 | 证据可能早于最后一处改动；重跑宿主门槛才符合 ADR-0008 的「宿主证据」 |
| `publish` 只按 `context.published` 判幂等 | 实现最简单 | artifact 已写、会话提交失败时重试会注册 version 2，违反「同一 IR 一个版本」；需以 `(game_id, ir_hash)` 兜底 |
| 输入模型默认忽略未知字段 | 兼容旧客户端 | `version` 被静默丢弃正是 bug 来源；M5 契约要求显式 422 |
| 允许模型工具调用 `confirm`/`publish` | 少一步用户动作 | 等于模型自确认、自注册，直接违反 ADR-0008/0015 |
| 详情接口只返回 `kind/max_rounds` | 页面简单 | 无法核对条款来源与证据，M5 明确要求完整展示 |

## 后果

- 正面：设计→确认→注册→试玩形成一条可恢复、可轮询、可审计的闭环。
- 正面：注册严格由用户确认 + 新鲜宿主证据双重绑定；模型无路径绕过。
- 正面：路径与字段冻结，前端、e2e、doctor 共用一份契约。
- 负面：`publish` 会重跑一次正式门槛，注册比「直接引用旧证据」慢；这是有意的保守选择。
- 负面：`DesignRunStore` 与 `DesignStore` 共享同一 SQLite 文件，需要各自短事务；跨进程并发仍以单进程座位为前提。

## 失败矩阵

| 输入 / 故障 | 必须观察到的结果 |
|---|---|
| `messages` 并发写入/重复 request_id 冲突 | 运行 `failed`，HTTP 409，会话不变 |
| 查询不存在或不属于该会话的 run | HTTP 404 `设计运行不存在` |
| `verify` 未提交 IR | 观测 `propose_ir_first`，revision 不变 |
| `confirm` 的哈希与当前草案不同 | `approval_mismatch`，422，无 confirmation |
| 未确认就 `publish` | `confirmation_required`，422，无版本 |
| 确认后未验证就 `publish` | `verification_required`，422，无版本 |
| 修改 IR 后 `publish` | `confirmation_required`（哈希已变），无版本 |
| 非组合 IR `publish` | `publish_requires_composed_ir`，422，无版本 |
| 同一 IR 重复 `publish` | 幂等返回同一 artifact，不新增版本 |
| artifact 已注册但会话提交失败后重试 `publish` | 复用同版本，`idempotent=true` |
| `POST /api/sessions` 带未知字段 | 422，不静默忽略；指定不存在 `version` 为 HTTP 404 |
| 通用动作输入未知/缺失/越界/不可见或动作非法 | 422，revision 与状态不变 |
| 通用动作旧 revision / 同 key 不同输入 | HTTP 409 `stale_revision` / `request_id_conflict` |
| 重复 `request_id` 的设计回合（即使 revision 已变） | 重放原响应，不再调用模型 |
| 事件 `after` 越界 | 空窗口且 `has_more=false`；`after<0` 为 422 |
| 崩溃遗留的 `running` 运行 | 应用启动时回收为 failed（`host_restarted`） |
| `GET /api/games/{id}/versions/{v}` 不存在 | HTTP 404 `游戏版本不存在` |

## 迁移

- M4 的 `/api/designs/{id}/messages` 只新增响应字段 `run_id`，请求不变；现有测试与前端无需改。
- `/api/designs/{id}/update` 继续作为 M4-1 的显式字段接口保留，供测试与脚手架使用。
- M5-2 起在 `POST /api/sessions` 增加 `version`，并新增通用 `actions` 与有界 `events`；旧 `actions/{action}` 暂作适配，删除条件见 ADR-0007。
- M5-2 的所有公共输入模型改为 `extra="forbid"`；未知字段从「静默忽略」变为 422，需前端同步发送准确的字段集。
- 本 ADR 冻结路径后，M5-3..M5-6 的前端实现不得新增别名路径，只能消费上表。
