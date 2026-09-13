# ADR-0017: M5-3 设计状态机、验证幂等、模型地址安全与边界

- 日期：2026-09-14
- 状态：accepted（**实现状态：M5-3 已实现**）
- 决策者：架构 owner
- 关联：ADR-0008（证据与版本绑定）、ADR-0014（设计会话存储）、ADR-0015（设计服务工具）、ADR-0016（M5 API 冻结）、`docs/development-plan-phase2.md` §5/§M5

## 背景

M5-2 之后，后端已经能创建指定版本的对局、提交通用动作并增量读事件，但前端迁移前还有四类会直接影响 UI 状态与错误处理的契约问题：

1. 设计状态词表仍是 `draft/diagnosed/compiled/verified/finalized/failed`，与开发计划 §5 的状态机（`... → verified → awaiting_confirmation → ready → registered`）不一致，且 `publish` 后写成 `finalized` 无法与「已注册」区分；
2. `/verify` 虽接 `request_id` 却会重新计算并可能产生新 revision，`/verify` 的并发/重复冲突还会以 HTTP 200 的失败 Observation 返回；
3. 模型 `base_url` 只校验 URL 形状，可被指向 loopback、私网或云 metadata（SSRF）；
4. 一些小的契约缺口：内存模式 `SessionStore.get()` 返回内部对象、`confirm` 在未验证时仍返回 `confirmed: true`、`context`/Observation 无深度与大小上限、`PROJECT.md` 与 README 对离线导出的表述矛盾、产品版本 `0.4.0` 与包元数据 `0.1.0` 不一致。

本 ADR 冻结 M5-3 的决策，前端（M5-4 起）只消费这里定义的状态与错误。

## 决策

### 1. 设计状态机（替换 ADR-0014 的词表）

```text
draft
  --capability_check--> diagnosed
  --compose_plan(ok)--> compiled
  --verify_game(ok)--> verified
  --finalize(ok) 或 confirm--> awaiting_confirmation
  --publish(ok)--> registered

verify_game(fail) / unsupported --> failed
propose_ir / patch_ir --> draft            （从任意状态回退；作废编译/验证/确认证据）
编译失败（compiler_error）              --> 状态不变，只记录 diagnosis/context.failure（可恢复）
```

`DESIGN_STATUSES = ("draft", "diagnosed", "compiled", "verified", "awaiting_confirmation", "registered", "failed")`，删除 `finalized`。

语义固定：

- `verified`：宿主正式门槛通过，且 `context.verification` 绑定当前 `ir_hash`；
- `awaiting_confirmation`：候选已冻结（`finalize`）或用户已确认；此时不允许再改 IR 而不作废确认；
- `registered`：`publish` 成功，`SessionStore` 中存在不可变版本；
- `failed`：验证失败或明确表达不了；`propose_ir`/`patch_ir` 可修复回 `draft`。

`confirm` **要求**当前 `context.verification.ok` 且其 `ir_hash` 等于当前草案，否则 `verification_required`（422）。因此 `confirmed: true` 永远不会与「未验证」同时出现。

### 2. `/verify` 真幂等

`verify_game` 在会话 `context.verify_requests` 里保存一个有界的重放表（上限 8 项）：

```json
{"<request_id>": {"ir_hash": "...", "observation": {...}}}
```

- 相同 `request_id` + 相同 `ir_hash`：返回**逐字节相同**的 `observation`（含原 `revision`），不增加 revision、不重跑门槛；
- 相同 `request_id` + 已变化的 `ir_hash`：`request_id_conflict`（HTTP 409）；
- 并发重复：后到者的乐观锁提交得到 `stale_revision`（HTTP 409）；
- 验证失败也保存并可重放失败 Observation（不把它当作「这个玩法不可能表达」的结论）；
- `/api/designs/{id}/verify` 把冲突类 Observation 提升为 HTTP 409，而不是 200 包一个失败结果。

### 3. 模型地址安全（SSRF）

- 新增开关 `POCKER_AGENT_ALLOW_PRIVATE_MODEL_URLS`，**默认关闭**；仅在开发机连接本机/内网模型（如 Ollama）时显式设为 `1`。
- 关闭时，`normalize_url`/`validate_model_host` 拒绝：`localhost`/`*.localhost`、`metadata.*`、`instance-data`，以及解析到的每个 IPv4/IPv6 地址中属于 loopback、private、link-local、reserved、multicast、unspecified 的地址（含 `127.0.0.1`、`10/8`、`172.16/12`、`192.168/16`、`169.254.169.254`、`::1`、`fe80::/10`）。
- DNS 解析后**逐地址**检查，不只检查 hostname；字面 IPv6 直接解析，不依赖 `getaddrinfo` 的 scope 处理。
- OpenAI SDK 使用带 `httpx` 事件钩子的 client，**每一次请求（含每个重定向 hop）**都复检目标主机；`follow_redirects` 仍开启，但重定向目标会被拒绝。
- 继续拒绝 URL 中的用户名、密码、query 与 fragment。

### 4. 边界与一致性

- 设计 `context`：列表键继续从尾部保留；新增最大键数 32、最大深度 8、字符串上限 8000；`verification/compiled/artifact/published/confirmation/verify_requests` 等宿主证据保持完整结构。
- 运行 Observation：单条上限深度 6、字符串 2000、宽度 64；返回体与落库的重放体使用同一函数，保证重试一致。
- 内存模式 `SessionStore.get()` 返回副本（用 `Interpreter.restore` 重建解释器，避免复制 registry），兑现「读取即副本」注释。
- 产品版本统一为 `0.4.0`：`pyproject.toml`、`src/pocker_agent/__init__.py`、`app.py`、`frontend/package.json`/`package-lock.json`（`uv.lock` 同步）。
- `PROJECT.md` 与 README 统一：v0.4 **没有**离线导出。

## 备选方案与为何不选

| 方案 | 优点 | 不选原因 |
|---|---|---|
| 保留 `finalized` 并新增 `registered` | 改动最小 | 前端无法区分「候选已冻结」与「已注册」；与开发计划 §5 冲突 |
| `confirm` 允许未验证并返回 `publishable:false` | 顺序自由 | 仍会出现 `confirmed:true` 被误读；发布需要二者指向同一 `ir_hash` |
| `/verify` 幂等只在 store 层提交去重 | 少一层 | 仍会重跑昂贵门槛；失败观测无法逐字节重放 |
| SSRF 只查 hostname 字符串 | 简单 | 域名可解析到私网、跳转可指向 metadata；必须逐地址 + 每跳复检 |
| 默认允许私网、加告警 | 不破坏本机模型 | 默认即 SSRF 面；改为显式 opt-in，默认安全 |

## 后果

- 正面：前端有唯一状态机与唯一冲突语义；`409`/`awaiting_confirmation`/`registered` 可直接映射 UI。
- 正面：重复验证不再重跑门槛，也不产生伪 revision；失败可重放且不误导。
- 正面：默认关闭的 SSRF 防护覆盖解析后地址与重定向。
- 负面：本机模型用户必须设置 `POCKER_AGENT_ALLOW_PRIVATE_MODEL_URLS=1`（文档与错误信息已说明）。
- 负面：状态词表变更使 M4 时期断言 `finalized` 的测试与文档需要同步；已更新测试并保留旧 ADR 作历史。

## 失败矩阵

| 输入 / 故障 | 必须观察到的结果 |
|---|---|
| `confirm` 在未绑定验证时 | `verification_required`，422，无 confirmation |
| `confirm` 哈希与当前草案不同 | `approval_mismatch`，422 |
| `verify` 相同 `request_id`+相同 IR | 逐字节相同响应，revision 不变 |
| `verify` 相同 `request_id`+变化 IR | HTTP 409 `request_id_conflict` |
| `verify` 并发重复 | HTTP 409 `stale_revision`（不被包装成 200） |
| 修改 IR 后 publish | `confirmation_required`，无版本；状态回 `draft` |
| `publish` 成功 | 状态 `registered`，版本复用 `(game_id, ir_hash)` |
| `normalize_url` 指向 loopback/私网/metadata | `BlockedModelHost`，不发起请求 |
| 重定向跳转到私网/metadata | `BlockedModelHost`，该跳被拒绝 |
| 深层/超长 context 或 Observation | 被裁剪，宿主证据结构保持 |
| 内存模式读取后修改 | 存储状态不变 |

## 迁移

- 前端「待核对」= `awaiting_confirmation`，「已注册」= `registered`，「失败」= `failed`；刷新用 `game_id + version + session_id` 恢复。
- `POST /api/designs/{id}/confirm` 现在要求先有绑定验证；调用顺序为 `verify → confirm → publish`。
- 本机模型：设置 `POCKER_AGENT_ALLOW_PRIVATE_MODEL_URLS=1` 后行为与 M5-2 相同。
- 旧 ADR-0014 的 `finalized` 词表由本 ADR 取代；ADR-0014 保留为历史记录。
