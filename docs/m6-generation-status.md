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
| 真实模型盲测（修复评测器前） | `… --base-url …` | 0/8；失败集中在 `output_too_long` 与 `invalid_ir`（15 个字段错误） |
| 真实模型盲测（修复协议后） | `… --base-url http://127.0.0.1:8000 --browser` | **0/8**：反例 2/2；可表达组合 0/8，全部 `generation_budget`。模型把 12/12 次决策花在 `describe_mechanism` 上（多次 `unknown_tool:*` / `unknown_operation:*`），从未提交 `propose_ir` |
| 浏览器 smoke | `… --browser` | 仅对成功注册的产物运行；当前无成功产物，跳过 |

### 当前阻塞（模型协议，不是判定器）

`deepseek-v4-flash`（经该中转地址）在本协议下**不足以完成设计工具调用**：它会反复用错误的参数探测 `describe_mechanism`，耗尽决策/修复预算，而不是产出 `propose_ir` 的 ComposedRulesIR。这不是玩法专属代码问题，也不是宿主缺陷；ADR-0020 已将其诚实记录为 `generation_budget`，G2 陌生组合成功率仍为 **未达标**。

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

1. 换用/配置一个**工具调用遵循度更高**的模型（本机或云端）后，重跑 8 正例 + 2 反例（含 `--browser`）；
2. 若继续用当前模型，需要在其之上加一层受限的决策解析/重试策略，但这属于模型适配，不能改玩法机制；
3. 归档可追溯的真实运行证据，再判断 G2 准出。

判定器与证据完整性已修复；M6 不通过的原因现在是模型侧的协议遵循，而不是评测口径。
