# ADR-0019: 明确的对局摘要 DTO 与可重复的浏览器验收

- 日期：2026-09-14
- 状态：accepted（**实现状态：已实现**）
- 决策者：架构 owner
- 关联：ADR-0016（M5 API 冻结）、ADR-0017（状态机与冲突语义）、ADR-0018（前端消费契约）、`docs/development-plan-phase2.md` §M5/§M6

## 背景

M5-4 的接口探测（48 项）无法证明页面可操作、响应式布局与刷新恢复。补上真实浏览器验收后暴露了两个问题：

1. **`/api/games` 的 playtest 有两种形状**：内置玩法返回完整 `PlaytestReport`（`seeds/checks/failures/covered_wait_nodes/event_counts`），已注册产物只返回 `{ok: true}`。前端为兼容把字段设为可选，`LibraryPage`/`RulesPage` 仍对缺失数组调用 `.join/.length`，一旦列出组合玩法就整页崩溃。可选字段掩盖了契约漂移。
2. **浏览器验收不可重复**：脚本放在被 `.gitignore` 的 `artifacts/` 下，只在一台机器的 Chromium 上手工跑过一次，既没有跨浏览器，也没有事件日志截断、旧游标、发布中断、多标签页竞争等异常恢复场景。

## 决策

### 1. 单一、严格的对局摘要 DTO

- `GET /api/games` 的每一项都由 `GameSummaryDTO` 校验后返回，其中 `PlaytestDTO` 的七个键**永远存在**：`ok/seeds/checks/failures/covered_wait_nodes/event_counts/evidence`。
- `evidence` 是封闭词表 `reference | verification | agent_playtest`，说明摘要来自哪份宿主记录：
  - 内置玩法 → `reference`（完整 playtest 报告）；
  - 已注册产物 → `verification`（从 `core_verifications` 里该版本的宿主凭据重建 `seeds/checks/failures/covered_wait_nodes`）；
  - Agent 注册计划 → `agent_playtest`（注册时记录的宿主报告）。
- 两个 DTO 都 `extra="forbid"`：多一个字段就是校验错误，而不是被静默丢弃。可选产物字段（`version/verification_id/source`）对内置玩法显式为 `null`。
- 前端类型随之恢复为**必填**，不再用可选字段兜底；崩溃点消失，契约漂移会在后端 500、前端编译或测试中直接暴露。

### 2. 可重复的浏览器验收

- `scripts/m5_acceptance.py` 是唯一入口，负责全流程：把冻结的 G2 场景发布为组合玩法（`scripts/m5_acceptance_fixtures.py`，IR 从后端测试导入，不复制）→ 启动真实 uvicorn 服务 → 用宿主门槛把设计会话置于 `verified`（不需要模型）→ 运行 Playwright 脚本的 `full` 阶段 → **重启后端** → 运行 `recover` 阶段 → 汇总每一项检查。
- `frontend/acceptance/m5-browser.mjs` 由 UI/HTTP 驱动构建产物，不导入任何应用源码；`playwright` 是 `frontend` 的 devDependency，脚本在 `frontend/` 下运行以便解析模块。
- 每个浏览器使用**独立的数据目录**：设计流程会注册玩法，共享数据库会让下一个浏览器的首次发布变成幂等，掩盖问题。
- 验收作为 CI 的独立 `browser` job（`needs: [python, frontend]`），在 Chromium、Firefox、WebKit 上运行；失败即红。

### 3. 异常恢复是一等场景

`full` 阶段除正常闭环外必须覆盖：

- **事件日志截断 / 旧游标**：写入一个远超服务端事件总数的游标，刷新后前端必须从 0 重建（游标与展示一致，不保留幻影事件）；
- **发布中断**：产物已写入但会话提交丢失（清掉 `context.published`）后重试，必须复用同一版本（`idempotent=true`）且只存在一个版本；
- **多标签页竞争**：两个标签页同时 `confirm` 只有一个成功、另一个得到 409；同时 `publish` 只产生一个版本、会话只注册一次；
- **断网与重试、重复 `request_id`、非法输入、事件分页、隐藏牌、三档视口**（M5-4 已有）。

## 备选方案与为何不选

| 方案 | 优点 | 为何不选 |
|---|---|---|
| 保持 `{ok: true}` 并让前端字段可选 | 改动最小 | 正是掩盖漂移、导致整页崩溃的做法；可选类型使契约不可检查 |
| 产物摘要只回 `{ok, verification_id}`，其余留空 | 实现简单 | 规则页需要真实 `seeds/checks/covered_wait_nodes`；留空等于丢失证据 |
| 浏览器验收脚本留在 `artifacts/` | 不污染仓库 | 不可重复、不进 CI，无法作为验收门禁 |
| 只在 Chromium 跑 | 快、稳 | WebKit 的导航/离线/时序差异正是真实用户会遇到的；跨浏览器是明确要求 |
| 所有浏览器共享一个数据库 | 少一次发布 | 第二个浏览器的首次发布变幂等，检查失真 |
| 用固定 `sleep` 等异步发布 | 简单 | 发布要重跑宿主门槛，时长不定；轮询状态才是确定性的 |

## 后果

- 正面：`/api/games` 只有一种形状，前端类型与契约一致；组合玩法的规则页能看到真实验证证据。
- 正面：浏览器验收可一键复现并进入 CI，覆盖三浏览器与异常恢复。
- 负面：CI 增加一个下载浏览器、启动真实服务的 job，时长与资源开销上升。
- 负面：产物摘要多一次 `core_verifications` 读取（廉价，且列表页本就有上限）。
- **强制方式**：`GameSummaryDTO` 的 `extra="forbid"` 校验；`tests/test_m5_game_summary_dto.py` 断言七键齐全、`evidence` 合法、产物摘要来自凭据、漂移字段报错；CI 的 `browser` job 以 `scripts/m5_acceptance.py` 的退出码为准（任一检查失败即失败）。

## 迁移

- 前端 `Playtest`/`GameInfo` 恢复必填字段；`LibraryPage`/`RulesPage` 去掉可选兜底并展示 `evidence`/`source`。
- 浏览器验收入口从 `artifacts/` 迁到 `scripts/` 与 `frontend/acceptance/`；旧的临时脚本保留为本地历史，不进入版本库。
- 删除兼容形状需先确认没有其它消费者按 `{ok: true}` 解析 `/api/games`（当前仅前端与 e2e，均已更新）。
