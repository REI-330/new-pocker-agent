# ADR-0014: 设计会话的持久化与乐观锁（M4-1）

- 日期：2026-09-14
- 状态：accepted（**实现状态：已实现**，M4-1）
- 决策者：架构 owner
- 关联：ADR-0008（证据与版本绑定）、ADR-0013、`docs/development-plan-phase2.md` §M4

## 背景

M4 让 Agent 通过多轮对话组合规则，需要一份跨请求、跨重启都存在的 **设计状态**：原始描述、当前规范化 IR、最近一次诊断、以及可追踪的历史。这份状态与「可运行版本」是两回事——后者只能来自 ADR-0008 的 `publish_composed` 产生的不可变 `GameArtifact`。把两者混在一起，会重新打开「模型自己宣布可玩」的旧口子。

同时，多个请求可能并发修改同一份设计；没有并发控制就会出现静默覆盖，恰好是 G2 反例表里「失败却改了状态」的形态。

## 决策

**新增 `DesignSession` 与 `DesignStore`（`agent/design_store.py`），用 SQLite 持久化，所有写操作带 `expected_revision`。**

`DesignSession` 字段：

| 字段 | 含义 |
|---|---|
| `session_id` | 持久身份（宿主分配） |
| `game_id` | 目标玩法 id（仅设计命名，不注册） |
| `description` | 原始自然语言描述 |
| `ir` / `ir_hash` | 当前**规范化** IR 及其内容身份 |
| `diagnosis` | 最近一次诊断结果（编译/能力/验证失败的结构化摘要） |
| `revision` | 单调递增，乐观锁版本 |
| `status` | `draft / diagnosed / compiled / verified / finalized / failed` |
| `history` | 可追踪事件（`seq / event / revision`，有界） |
| `processed` | 幂等 `request_id → {fingerprint, session}`（有界） |

固定语义：

1. **写操作必须带 `expected_revision`**。不匹配返回 `stale_revision`，**不覆盖**已存储的新状态。
2. **原子提交**：全部变更在副本上完成后才写；IR 非法、状态未知、诊断非对象都会在写之前抛错，因此失败时 `revision`、`ir`、`diagnosis` 保持不变。SQLite 写在单个事务里。
3. **读取返回深拷贝**，调用方无法通过返回值修改已存储对象。
4. **重启恢复**：状态存于与对局相同的数据库（表 `core_design_sessions`），进程重启后按 `session_id` 完整恢复。
5. **幂等**：相同 `request_id` 的重复提交返回该次请求记录的结果，不二次应用；同 id 不同内容返回 `request_id_conflict`。
6. **设计 ≠ 产物**：`DesignStore` 没有任何注册入口；`finalized` 仅表示设计流程完成。可运行版本仍只能由 `publish_composed` 生成并写入 `SessionStore`。设计里的 `game_id` 在注册前不可开局。

API（M4-1 目标接口，M5 再扩展）：

| 方法/路径 | 用途 |
|---|---|
| `POST /api/designs` | 创建设计会话（`game_id`, `description`） |
| `GET /api/designs` | 列出会话摘要 |
| `GET /api/designs/{id}` | 恢复会话（IR、诊断、revision、状态、历史） |
| `POST /api/designs/{id}/update` | 带 `expected_revision` 的局部更新（`description/ir/diagnosis/status`）；`request_id` 幂等 |

错误映射沿用现有约定：`stale_revision` / `request_id_conflict` → 409；`invalid_ir` / `design_status_unknown` / schema 校验 → 422；未知会话 → 404。

## 备选方案与为何不选

| 方案 | 优点 | 不选原因 |
|---|---|---|
| 复用 `Session`（对局）存设计草稿 | 零新表 | `Session` 是「正在运行的一局」，绑定已注册产物与解释器；设计没有产物，语义冲突 |
| 只放内存 | 实现最简 | 不满足「重启后恢复」，也不跨请求 |
| 最后写入获胜（无 revision） | 调用方简单 | 静默覆盖并发修改，正是要消除的失败形态 |
| 设计状态里直接注册可玩版本 | 少一步 | 会让模型绕过 `publish_composed` 与验证凭据 |

## 后果

- 正面：设计过程可恢复、可并发、可追踪；产物门槛不被绕过。
- 正面：`revision` 与幂等键让「重复请求/旧 revision」有确定行为，M4-2 的元工具可以直接复用。
- 负面：设计会话与对局共享一个 SQLite 文件，需要各自独立的表（已用 `core_design_sessions` 与 `core_sessions` 分开）。
- 负面：`processed` 与 `history` 必须有界（本实现 `16` / `256`），否则长会话会让 payload 无界增长。

## 失败矩阵

| 输入 / 故障 | 必须观察到的结果 |
|---|---|
| 用旧 `expected_revision` 写入 | `stale_revision`，已存储状态与 revision 不变 |
| IR 非法 / 状态未知 | 写前报错，`revision`、`ir`、`diagnosis` 不变 |
| 相同 `request_id` 重复提交 | 返回首次结果，revision 只增一次 |
| 相同 `request_id` 不同内容 | `request_id_conflict` |
| 读取后修改返回值 | 已存储会话不受影响 |
| 进程重启 | 会话、revision、IR、诊断可恢复 |
| 用设计的 `game_id` 开局 | 未注册则拒绝；只有 `publish_composed` 产物可开局 |

## 迁移

- 新增表 `core_design_sessions`，不影响既有 `core_sessions` / `core_artifacts` / `core_verifications`。
- `agent/design_store.py` 是 M4-2 元工具与 M5 `/api/designs/*` 的唯一状态来源；后续 `propose_ir` / `patch_ir` / `verify_game` 只通过 `commit` 落盘。
