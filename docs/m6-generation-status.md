# M6 生成验收状态

> 关联：ADR-0020（盲测生成与证据归档）、`docs/development-plan-phase2.md` §6/§8/§9。

## 交付物

| 交付物 | 作用 |
|---|---|
| `docs/adr/0020-m6-blind-generation-and-evidence.md` | 冻结面、用例格式、运行路径、失败分类、准出阈值、证据归档 |
| `benchmarks/g2_blind_cases.json` | 8 条可表达组合 + 2 条缺机制反例；只含目标、可表达判定与**结构化独立预期** |
| `scripts/m6_evidence.py` | 独立 evaluator：按用例预期核对产物与轨迹（牌区/可见性/终局/行动预算/隐藏投影/哈希绑定） |
| `scripts/m6_generation_acceptance.py` | 真实模型盲测入口；集成 evaluator、完整依赖冻结、报告元数据与异常收集 |
| `frontend/acceptance/m6-artifact-smoke.mjs` | 每个已注册产物的真实浏览器 smoke（`browser_playable`） |
| `tests/test_m6_generation_flow.py`、`tests/test_m6_evidence.py` | 脚本链路回归 + evaluator 反例回归 |

## 当前结果

| 运行 | 命令 | 结果 |
|---|---|---|
| ScriptedModel 流程 | `… --scripted` | 2/8 通过独立 evaluator、2/2 反例；其余 6 条因预期不符被 evaluator 拒绝（证明 evaluator 有效） |
| 真实模型盲测 | `… --base-url http://127.0.0.1:8000` | **未通过**：反例 2/2；可表达组合 0/8（模型 `propose_ir` 的 ComposedRulesIR 未通过校验） |
| 浏览器 smoke | `… --base-url … --browser` | 仅对成功注册的产物运行；当前无成功产物 |

## 已修复的判定器与证据问题（本轮）

- **独立 evaluator**：`finalized` 不再等于成功；用例的 `terminal/zones/hidden/预算` 都被核对（此前 8 条用例可共用同一 IR 被判成功）。
- **完整依赖冻结**：机制/编译器/验证器/提示词/工具表/用例文件都做前后哈希比较；缺失或变化即运行无效。
- **用例校验**：固定 8 正例 + ≥2 反例、ID 唯一、`evidence` 完整，`--cases` 不能绕过。
- **证据元数据**：命令、`sys.executable`、导入路径、数据目录、端口、`/health` code fingerprint、提示/工具指纹、seed；tokens 明确标注为循环估算。
- **异常收集**：单条用例的传输/超时/JSON 错误独立捕获为 `transport`，不中断整轮；`finally` 保证报告落盘。
- **可玩拆分**：`runtime_playable`（HTTP）与 `browser_playable`（真实浏览器）分开统计。
- **运行目录**：UUID + `mkdir(exist_ok=false)`，重复运行不覆盖。

## 四个指标（分开统计）

以真实运行报告 `report.json` 的 `metrics` 为准，`targets_met` 在 `frozen_surface_unchanged=false` 或反例未达 2/2 时恒为 false。在真实盲测达到 **≥6/8 且 2/2 反例** 之前，G2 不得宣称「陌生组合成功率达标」。

## 下一步（按 ADR-0020 顺序）

1. 改进模型协议：给 `propose_ir` 更强的 ComposedRulesIR 结构提示，并在 repair observation 中回传首批校验错误；
2. 重跑 8 正例 + 2 反例（含 `--browser`）；
3. 归档可追溯的真实运行证据，再判断 G2 准出。
