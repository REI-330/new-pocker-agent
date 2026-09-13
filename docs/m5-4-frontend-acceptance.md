# M5-4 前端迁移与验收记录

> 关联：ADR-0016（M5 API 冻结）、ADR-0017（状态机与冲突语义）、ADR-0018（前端消费契约）、`docs/frontend-design-v0.4.md`。

## 迁移内容

- `shared/api/types.ts` / `client.ts` 覆盖 M5 冻结接口：版本列表与详情、通用动作、有界事件游标、设计会话/运行/验证/确认/发布。
- 工作台以 `ZoneView` / `ActionForm` / `EventTimeline` 泛化渲染 `view().zones` 与 `view().actions`；所有玩法都走通用 `POST /actions`。
- 设计页实现 `澄清 / 组合 / 验证 / 待核对 / 已注册` 状态机、运行轮询与 `verify → confirm → publish`。
- 规则页展示已注册版本、条款、来源映射与宿主验证证据。
- 恢复键为 `game_id + version + session_id`，仅在 404 清除；事件游标与已展示事件一起持久化，并以 `after=` 续读。

## 自动证据

| 检查 | 结果 |
|---|---|
| `cd frontend && npm run build` | 通过（`tsc -b && vite build`） |
| `uv run --frozen pytest -q` | 490 passed |
| `uv run --frozen python scripts/doctor.py --url …` | 全部 PASS，且服务资产与工作区一致 |
| `uv run --frozen python scripts/e2e_smoke.py …` | 32 checks passed |
| 接口探测（48 checks） | 通过 |
| **浏览器验收 `full` 阶段（35 checks）** | **通过** |
| **浏览器验收 `recover` 阶段（6 checks）** | **通过** |

## 浏览器验收（Playwright + Chromium，真实页面操作）

用真实浏览器驱动构建产物 `http://127.0.0.1:8807`，共 **41 项**检查通过。为了让设计页走到 `verify → confirm → publish`，先用 `/api/designs/{id}/update` 写入组合 IR、再调用 `/api/designs/{id}/verify`（宿主正式门槛，不需要模型），把三个会话置于 `verified`；浏览器随后完成确认、注册与试玩。

`full` 阶段（35 checks）：

1. **三个组合玩法完整闭环**：`ui-alpha` / `ui-gamma` / `ui-eta` 分别走「确认当前规则 → 注册版本 → 开始试玩 → 对局结束」（FINISHED），全程无未捕获页面异常。
2. **规则核对**：规则页渲染版本 chip、条款来源与完整动作输入形状。
3. **对局中刷新**：同一 `session_id` / `version` / `revision` 恢复，事件游标保留（不丢、不重）。
4. **409 冲突**：另一写入者把会话推进一个 revision 后，本标签页提交仍显示「局面已更新（409）」提示，会话保留且 revision 刷新到对方的值。
5. **断网重试**：`context.setOffline(true)` 后提交显示「无法连接本机服务」；恢复网络后用同一 `request_id` 重试，revision 恰好 +1（幂等，不重复执行）。
6. **request_id 语义**：同一 key 连续两次请求返回逐字节相同响应且 revision 只 +1；动作/revision 变化会生成新 key。
7. **非法输入**：缺失必填 → `missing_action_input`；未知牌 → `card_not_available`；需要选牌时提交按钮在选中前禁用。
8. **事件分页**：`limit=2` 时 `cursor === len(events) === 2` 且 `has_more=true`，下一页游标单调递增。
9. **隐藏牌**：`crazy_eights` 对手手牌 API 返回空数组且 `hidden_count=5`；页面标记「隐藏手牌」，DOM 中牌面图片数 = 本家手牌 + 公共牌，没有对手牌泄漏。
10. **响应式**：1440×900 / 720px / 390×844 三档均无横向溢出、动作表单可见；≤720px 时移动导航显示、桌面导航隐藏。

`recover` 阶段（6 checks）：**重启后端**后刷新页面，仍恢复同一 `session_id` / `version` / `revision`；持久化游标等于服务端事件总数，已展示事件数也等于总数（证明既未丢失也未重复），且无未捕获异常；随后在恢复的会话中把对局走到结束（revision 12，FINISHED）。

截图（本地 `artifacts/m5-4-shots/`，不提交）：`design-ui-*-registered-1440`、`workspace-ui-*-start-1440`、`rules-theta-draw-1440`、`workspace-midgame-1440`、`workspace-1440x900|720|390x844`、`workspace-after-restart-recovered-1440`、`workspace-finished-1440`。

复现方式（本地）：发布组合玩法（`artifacts/m5-4_publish.py`）→ 启动
`POCKER_AGENT_DATA_DIR=<repo>/artifacts/m5-4-runtime uv run --frozen python scripts/run_web.py --skip-build --port 8807`
→ 播种已验证设计会话（`artifacts/m5-4_seed_designs.py`）→
`node artifacts/m5-4-browser.mjs <url> <designs.json> full …`，重启后端后再跑 `recover`。

## 浏览器验收发现并修复的问题

- **组合玩法的 `/api/games` 摘要缺少 playtest 字段导致整页崩溃**：内置玩法返回完整 playtest 报告，而已注册产物只返回 `{ok: true}`。`LibraryPage` / `RulesPage` 直接调用 `playtest.seeds.join(...)`、`playtest.checks.length`，在列出组合玩法时抛出 `Cannot read properties of undefined (reading 'join')`，整个 React 树卸载、导航消失。修复：`Playtest` 的列表字段改为可选，并在所有使用处回退为空数组。这是接口探测无法发现、只有真实渲染才暴露的缺陷。
- **`request_id` 复用范围**：工作台动作的复用键为 `{action_id, input_values, revision}`，设计回合为 `{message, revision}`；只有完全相同请求的**重试**才复用，动作/文本或 revision 变化即生成新 ID。
- **事件 cursor 与展示协调**：游标、已展示事件与该会话缓存一起读写（`pocker-events:<id>`，保留最近 400 条）。恢复时先回填已展示事件，再以 `after=<cursor>` 拉取增量；若缓存游标超过服务端总数则从 0 重建，避免「从旧游标续读却丢失历史展示」。

## 已知边界

- 前端不实现任何玩法逻辑；非法选择由后端 422 拒绝，前端仅做数量提示。
- `beta-shed`（场景 B）只在自带 `SCENARIO_B_SEEDS` 下通过，冻结的发布路径使用默认种子，因此无法通过 `publish`；这是后端门槛的既有语义，不是前端缺陷。
- 0.4 内置玩法保留字段式回退分支，随旧计划退役删除。
- 浏览器验收脚本位于本地 `artifacts/`（含 Playwright 依赖与服务编排），不进入 CI；结果与复现步骤记录在本文件。
