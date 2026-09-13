# ADR-0018: 前端消费 M5 冻结契约（M5-4 迁移）

- 日期：2026-09-14
- 状态：accepted（**实现状态：M5-4 已实现**）
- 决策者：架构 owner
- 关联：ADR-0007（适配层与删除条件）、ADR-0016（M5 API 冻结）、ADR-0017（状态机与冲突语义）、`docs/development-plan-phase2.md` §M5、`docs/frontend-design-v0.4.md`

## 背景

M5-1..M5-3 已冻结并实现后端契约：通用 `action_id`/`input_values`/`revision` 动作、有界事件游标、`draft → … → registered` 设计状态机、`verify → confirm → publish` 顺序，以及 `game_id + version + session_id` 的版本绑定。前端仍停留在 v0.3 形态：

1. 工作台按动作名分支（`play`/`raise`/`submit_expression`），并用 `card_index` 位置而不是牌 id 选择；
2. 只渲染 `legal_card_indices` 与顶层状态键，看不到组合玩法的 `zones`；
3. 设计页调用旧 `/api/agent/loop`，没有正式验证、用户确认与注册；
4. 恢复逻辑在**任何**错误时删除 session key；
5. 事件直接用 `view().events`，没有断点续读。

前端迁移必须在**不新增后端路径、不新增前端玩法逻辑**的前提下完成，否则 ADR-0016 的「唯一契约来源」会再次漂移。

## 决策

### 1. 恢复键与清理时机

- 本地持久化一个 JSON：`{"game_id", "version", "session_id"}`，键名 `pocker-session`；旧的 `pocker-session-id` 在成功恢复后迁移并删除。
- 刷新时用 `GET /api/sessions/{id}` 恢复；**只有 404**（后端确认牌局不存在）才清除存储。网络错误、409、5xx 都保留 key。
- 事件游标按会话持久化到 `pocker-cursor:<session_id>`，重连时以 `after=<cursor>` 增量续读。

### 2. 单一动作入口

- 所有动作（内置玩法与组合玩法）都走 `POST /api/sessions/{id}/actions`，携带 `action_id` + `input_values` + `revision` + `request_id`。
- 前端不再调用旧 `/api/sessions/{id}/actions/{action}`；该路径仅作为后端适配层保留，删除条件见 ADR-0007。
- 内置 0.4 玩法没有描述符，前端把它们的字段（`card_index`/`expression`/`declared_suit`/`amount`）作为 `input_values` 发出；后端 `_action_payload` 对无描述符计划原样透传。

### 3. 描述符驱动渲染

- 工作台以 `ZoneView` / `ActionForm` / `EventTimeline` 泛化渲染：
  - `ZoneView` 只读 `view().zones`，隐藏区域只显示数量与牌背；
  - `ActionForm` 只渲染 `view().actions[].inputs` 声明的输入，按 `kind` 选择控件（`card_selection` 用 `options` 中的牌 id，其它 kind 用通用标量输入），组合 `input_values` 后提交；
  - 事件时间线先显示人能读懂的动作，原始 operation/JSON 收进折叠诊断区。
- **不得按动作名分支**。唯一的例外是：`view().actions` 为空的内置 0.4 玩法，回退到字段式控件（仍然只发送后端返回的 `legal_actions`）。
- 非法选择、越界、重复牌、不可见牌都由后端拒绝；前端只做 `min_count`/`max_count` 的即时提示。

### 4. 冲突与失败是一等状态

- 409（`stale_revision`/`request_id_conflict`/…）：重新 `GET` 会话状态，保留 session id，向用户提示「局面已更新」；动作的 `request_id` 在内容与 revision 不变时复用，保证重试幂等。
- 404：清除恢复 key，回到玩法库。
- 设计页把状态映射为阶段 `澄清 / 组合 / 验证 / 待核对 / 已注册`（`failed` 单列），并：
  - `verify` 显示绑定的失败项与契约检查失败；
  - `confirm` 只在 `context.verification.ok && verification.ir_hash === ir_hash` 时可用；
  - 规则改动后若 `context.confirmation.ir_hash !== ir_hash`，明确提示需重新验证并确认；
  - `/messages` 返回 `run_id`，运行中轮询 `GET /runs/{run_id}`，刷新后仍能恢复最新一次运行。

### 5. 前端不实现玩法逻辑

前端不计算胜负、牌型、计分或回合推进；不解析 IR 生成计划；不拼接产物。规则页只展示 `GET /api/games/{id}/versions/{v}` 返回的 `rules`/`source_map`/`verification`/`plan`。

## 备选方案与为何不选

| 方案 | 优点 | 为何不选 |
|---|---|---|
| 继续按动作名写表单 | 改动最小 | 每个新组合都要改前端，直接违反 G2 X1 与 M6「不新增专属代码」 |
| 保留旧 `/actions/{action}` 作为前端主路径 | 兼容旧代码 | 等于保留第二套实现；ADR-0016 明确迁移期后才可删除，M5 是迁移完成点 |
| 事件游标只放内存 | 简单 | 刷新/切页无法续读，M5 要求断点恢复 |
| 恢复失败即删 key | 简单 | 一次网络抖动就丢牌局，与「刷新不丢」承诺冲突 |
| 允许前端在 `confirm` 前 `publish` | 少一步 | 违反 ADR-0008/0017 的证据与用户确认绑定 |

## 后果

- 正面：一套工作台渲染内置与组合两大类玩法，新增玩法不改前端。
- 正面：`409`/`running`/验证失败/确认失效/游标续读都有明确 UI 状态。
- 正面：刷新与后端重启后恢复同一 `game_id + version + session_id`。
- 负面：内置 0.4 玩法临时保留字段式回退分支；随旧计划退役可删除。
- **强制方式**：`npm run build`（tsc + vite）为前端门禁；`artifacts/m5-4_frontend_probe.py` 对真实服务断言 48 项（三个组合玩法的 zones/descriptors/通用动作/游标/409、版本详情、设计状态与 422），`scripts/e2e_smoke.py` 的 32 项继续通过。

## 迁移与删除

- 旧 `/api/sessions/{id}/actions/{action}` 与 `/api/agent/loop` 在 M5 交付包内保留为适配层；消费者迁移已完成（前端不再调用）。
- 删除需先补齐「旧 → 新往返」测试，按 ADR-0007 单独 PR；本 ADR 不授权删除。
- 前端字段式回退分支在 0.4 内置计划退役时一并删除。
