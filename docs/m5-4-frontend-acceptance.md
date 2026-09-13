# M5-4 前端迁移与验收记录

> 关联：ADR-0016（M5 API 冻结）、ADR-0017（状态机与冲突语义）、ADR-0018（前端消费契约）、`docs/frontend-design-v0.4.md`。

## 迁移内容

- `shared/api/types.ts` / `client.ts` 覆盖 M5 冻结接口：版本列表与详情、通用动作、有界事件游标、设计会话/运行/验证/确认/发布。
- 工作台以 `ZoneView` / `ActionForm` / `EventTimeline` 泛化渲染 `view().zones` 与 `view().actions`；所有玩法都走通用 `POST /actions`。
- 设计页实现 `澄清 / 组合 / 验证 / 待核对 / 已注册` 状态机、运行轮询与 `verify → confirm → publish`。
- 规则页展示已注册版本、条款、来源映射与宿主验证证据。
- 恢复键为 `game_id + version + session_id`，仅在 404 清除；事件游标按会话持久化并以 `after=` 续读。

## 自动证据

| 检查 | 结果 |
|---|---|
| `cd frontend && npm run build` | 通过（`tsc -b && vite build`） |
| `uv run --frozen pytest -q` | 490 passed |
| `uv run --frozen python scripts/doctor.py --url …` | 全部 PASS，且服务资产与工作区一致 |
| `uv run --frozen python scripts/e2e_smoke.py …` | 32 checks passed |
| `artifacts/m5-4_frontend_probe.py …` | 48 checks passed |

`m5-4_frontend_probe.py` 在真实服务上发布 `alpha-pairs`、`gamma-market`、`eta-two` 三个组合玩法
（`artifacts/m5-4_publish.py`，通过 `SessionStore.verify_and_register` + 宿主默认门槛），并对每个玩法断言：
版本列表/详情、会话含 `zones` + `actions`、描述符 `card_selection` 的 `options` 都是可见牌、
通用动作在 `revision` 上推进并返回 `new_events`、事件游标可续读、旧 revision 返回 409；
另断言设计会话 `draft`、无 IR 时 `verify` 的 `propose_ir_first` 观测、`confirm`/`publish` 的 422 与 404。

> 说明：`beta-shed`（场景 B）只在自带 `SCENARIO_B_SEEDS` 下通过，冻结的发布路径使用默认种子，
> 因此无法通过 `publish`；这是后端门槛的既有语义，不是前端缺陷。

## 浏览器手工验收步骤（无浏览器自动化，需人工执行）

启动：`$env:POCKER_AGENT_DATA_DIR="<repo>/artifacts/m5-4-runtime"`，
`uv run --frozen python scripts/run_web.py --skip-build --port 8807`，打开 `http://127.0.0.1:8807`。

1. **玩法库 → 规则 → 试玩（组合玩法）**：打开 `gamma-market`，规则页应显示「已注册版本」与
   `v1`，条款含两个 `card_selection` 输入（`hand` 与 `market`）；点「用这个版本试玩」进入工作台，
   牌区显示 `hand-*` 与 `market`，动作表单要求各选一张牌；提交后 revision +1、事件时间线新增一条。
2. **第二个玩法**：对 `alpha-pairs` 重复一次，确认同一工作台无需任何专属布局即可选牌出手并计分。
3. **第三个玩法**：对 `eta-two` 重复一次；`draw`/`pass` 无输入，表单只有一个提交按钮。
4. **恢复**：工作台中刷新浏览器，应恢复到同一 `session_id`/`version`/`revision`；重启后端再刷新仍恢复。
5. **冲突**：在另一个标签页对同一会话提交动作，回到原标签页提交，应显示「局面已更新（409）」并刷新到最新状态。
6. **隐藏信息**：对 `crazy_eights`/`uno` 开局，对手手牌只显示背面与数量。
7. **设计页**：进入「新建玩法」，用预置描述发送（未配置模型时应提示到「模型设置」保存）；配置模型后
   依次完成验证、确认、注册，阶段条应依次点亮「组合 → 验证 → 待核对 → 已注册」，注册后可「开始试玩」。
8. **窄屏**：在 1440×900、720px、390×844 三个宽度各完成一次选牌与提交，确认底部移动导航与动作栏可用。

## 已知边界

- 前端不实现任何玩法逻辑；非法选择由后端 422 拒绝，前端仅做数量提示。
- 0.4 内置玩法保留字段式回退分支，随旧计划退役删除。
