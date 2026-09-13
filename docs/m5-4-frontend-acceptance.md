# M5-4 前端迁移与验收记录

> 关联：ADR-0016（M5 API 冻结）、ADR-0017（状态机与冲突语义）、ADR-0018（前端消费契约）、ADR-0019（playtest DTO 与可重复浏览器验收）、`docs/frontend-design-v0.4.md`。

## 迁移内容

- `shared/api/types.ts` / `client.ts` 覆盖 M5 冻结接口：版本列表与详情、通用动作、有界事件游标、设计会话/运行/验证/确认/发布。
- 工作台以 `ZoneView` / `ActionForm` / `EventTimeline` 泛化渲染 `view().zones` 与 `view().actions`；所有玩法都走通用 `POST /actions`。
- 设计页实现 `澄清 / 组合 / 验证 / 待核对 / 已注册` 状态机、运行轮询与 `verify → confirm → publish`。
- 规则页展示已注册版本、条款、来源映射与宿主验证证据。
- 恢复键为 `game_id + version + session_id`，仅在 404 清除；事件游标与已展示事件一起持久化，并以 `after=` 续读。
- `/api/games` 的对局摘要统一为严格 DTO（ADR-0019）：七个 playtest 键恒存在，`evidence ∈ {reference, verification, agent_playtest}`；前端类型恢复必填，不再用可选字段掩盖契约漂移。

## 自动证据

| 检查 | 结果 |
|---|---|
| `cd frontend && npm run build` | 通过（`tsc -b && vite build`） |
| `uv run --frozen pytest -q` | 全绿（含 ADR-0019 DTO 回归） |
| `uv run --frozen python scripts/doctor.py --url …` | 全部 PASS |
| `uv run --frozen python scripts/e2e_smoke.py …` | 32 checks passed |
| `uv run --frozen python scripts/m5_acceptance.py --browsers chromium,firefox,webkit` | **153/153 checks passed** |

## 浏览器验收（真实 Chromium + Firefox + WebKit）

一键入口 `scripts/m5_acceptance.py`：发布冻结的 G2 组合玩法 → 启动真实 uvicorn → 用宿主门槛把设计会话置于 `verified`（不需要模型）→ Playwright 跑 `full` 阶段 → **重启后端** → 跑 `recover` 阶段。每个浏览器一个独立数据目录。CI 以 `browser` job 执行（`needs: [python, frontend]`）。

`full` 阶段（每浏览器 45 项）：

1. **三个组合玩法完整闭环**：`ui-alpha` / `ui-gamma` / `ui-eta` 走「确认 → 注册 → 开始试玩 → 对局结束（FINISHED）」，全程无未捕获 JS 异常。
2. **规则核对**：版本 chip、条款来源、完整动作输入形状；库页展示组合玩法的 `v1` 与 `composed_rules`。
3. **对局中刷新**：同一 `session_id`/`version`/`revision` 恢复，事件游标保留。
4. **409 冲突**：另一写入者推进 revision 后，本标签页提交显示「局面已更新（409）」，会话保留、revision 刷到对方值。
5. **断网重试**：`setOffline` 后显示「无法连接本机服务」；恢复网络用同一 `request_id` 重试，revision 恰好 +1（WebKit 不强制离线时记录为跳过，重试路径仍由 `request_id` 用例覆盖）。
6. **`request_id` 语义**：同一 key 两次请求响应逐字节相同、revision 只 +1；动作/输入/revision 变化生成新 key。
7. **非法输入**：缺失必填 → `missing_action_input`；未知牌 → `card_not_available`；需选牌时提交按钮在选中前禁用。
8. **事件分页**：`limit=2` 时 `cursor === len(events) === 2` 且 `has_more=true`，下一页游标单调递增。
9. **旧游标 / 事件日志截断**：写入 `cursor=999999` 的陈旧缓存并刷新，前端从 0 重建，游标与展示都等于服务端总数，不保留幻影事件。
10. **发布中断**：产物已写、会话提交丢失（清 `context.published`）后重试，`idempotent=true` 复用同一版本，且只存在一个版本。
11. **多标签页竞争**：两标签页同时 `confirm` 恰好一个拿到 409；同时 `publish` 只产生一个版本、会话只注册一次。
12. **隐藏牌**：`crazy_eights` 对手手牌 API 为空且 `hidden_count=5`；DOM 牌面数 = 本家手牌 + 公共牌。
13. **响应式**：1440×900 / 720px / 390×844 均无横向溢出、动作表单可见；≤720px 移动导航显示。

`recover` 阶段（每浏览器 6 项）：**重启后端**后刷新仍恢复同一 `session_id`/`version`/`revision`；持久化游标等于服务端事件总数，已展示事件数也等于总数（不丢不重），无未捕获异常；随后把对局走到结束（revision 12，FINISHED）。

截图（本地 `artifacts/m5-acceptance/<run>/shots-<browser>/`，不提交）：设计注册、规则页、对局中、三档视口、重启恢复、终局。

复现：

```powershell
cd frontend; npm ci; npx playwright install chromium firefox webkit
cd ..
uv run --frozen python scripts/m5_acceptance.py --browsers chromium,firefox,webkit
```

## 验收发现并修复的问题

- **组合玩法的 `/api/games` 摘要缺少 playtest 字段导致整页崩溃**（ADR-0019）：产物只返回 `{ok: true}`，`LibraryPage`/`RulesPage` 对缺失数组调用 `.join/.length` 抛 `Cannot read properties of undefined`，整个 React 树卸载。接口探测发现不了，只有真实渲染暴露。修复：统一为严格 DTO，产物摘要从验证凭据重建，前端类型必填。
- **`request_id` 复用范围**：工作台键为 `{action_id, input_values, revision}`，设计回合为 `{message, revision}`；只有完全相同请求的重试才复用。
- **事件 cursor 与展示协调持久化**：游标与已展示事件一起存（`pocker-events:<id>`，最近 400 条）；续读先回填历史再拉增量，缓存游标超过服务端总数时从 0 重建。

## 已知边界

- 前端不实现任何玩法逻辑；非法选择由后端 422 拒绝，前端仅做数量提示。
- `beta-shed`（场景 B）只在自带 `SCENARIO_B_SEEDS` 下通过，冻结发布路径用默认种子，故无法 `publish`；属后端门槛既有语义。
- 0.4 内置玩法保留字段式回退分支，随旧计划退役删除。
- 浏览器验收会下载真实浏览器并启动真实服务，因此是独立 CI job，不进入快速单测阶段。
