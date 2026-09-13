# ADR-0020: M6 盲测生成与证据归档

- 日期：2026-09-14
- 状态：accepted（**实现状态：工具与用例已实现；真实模型盲测待模型配置**）
- 决策者：架构 owner
- 关联：ADR-0008（证据与版本绑定）、ADR-0016/0017（M5 契约）、ADR-0019（playtest DTO 与浏览器验收）、`docs/development-plan-phase2.md` §6/§8/§9

## 背景

G2 的主张是：**不修改任何专属代码**，用真实模型把自然语言组合成可验证、可注册、可试玩的新玩法。M1–M5 已经冻结机制、编译器、设计工具、生命周期与前端。剩下的是**证伪**这一步：证明新增玩法不需要新增 Python/前端/注册表/提示词分支，并诚实记录失败。

开发计划 §8.3 要求：盲测为机制冻结后新增的 8 条可表达组合 + ≥2 条缺机制反例；分母包含所有事先判定可表达的请求；模型误报 unsupported、超预算、人工改 Plan 都算失败；四个指标（机制存在 / IR 可编译 / 生成并验证通过 / 浏览器真正可玩）分开统计。§9.3 要求每次记录附 `git rev-parse HEAD`、启动命令、解释器路径、code fingerprint、实际用量等。

本 ADR 冻结 M6 的做法与判断标准，避免事后调整口径。

## 决策

### 1. 冻结面与指纹

- 盲测运行前记录一组**冻结文件**的内容哈希：通用机制（`core/` 下工具、`zones/matching/scoring/terminal` 等）、编译器（`core/rules/`）、设计提示词与工具表（`agent/design_loop.py`、`agent/design_service.py`、`agent/meta_tools.py`）。
- 运行期间这些文件**不得改动**；报告写入冻结指纹。任一哈希变化即视为「不是同一冻结面」，该次盲测作废。
- 盲测用例与预期结果**不与实现同处**：`benchmarks/g2_blind_cases.json` 只含自然语言目标、事先判定的可表达性、以及独立预期（动作/终局），不含 IR、Plan 或答案字段。

### 2. 用例格式

```json
{
  "id": "blind-01",
  "goal": "自然语言描述",
  "expect": "composed" | "unsupported",
  "evidence": {"min_actions": 2, "terminal": "highest_score"},
  "missing": ["simultaneous"]
}
```

- `expect=composed` 的用例由评审者在进入运行前判定为「当前机制可表达」；`expect=unsupported` 的用例判定为「缺少机制」。
- `evidence` 是独立预期，用于核对产物是否真按描述结束，而不是只看 `finalized`。
- 分母 = 全部 `expect=composed` 用例（8 条）+ 全部反例（≥2 条）；任何被排除的用例都要在报告中说明。

### 3. 运行路径

`scripts/m6_generation_acceptance.py` 对每个用例走完整链路：

1. `POST /api/designs` 建会话，`POST /messages` 让真实模型驱动到终态（`finalized/unsupported/error/budget_exhausted`），超过预算或轮数即停；
2. `finalized` → `verify`（宿主门槛）→ 用户 `confirm` → `publish` 注册不可变版本；
3. 用注册版本开局，走通用 `POST /actions` 打到 `finished`，并断言不少于独立预期的行动数；
4. 记录 provider/model、提示指纹、预算与 `used`（decisions/tokens/seconds）、artifact/IR 哈希、`verification_id`、失败分类。

`--scripted` 模式用注入的 ScriptedModel 在进程内跑同一段代码，仅用于证明工具本身可用，**不计入生成能力成绩**（计划 §M6.4）。

### 4. 失败分类（互斥）

| 分类 | 含义 |
|---|---|
| `success` | 生成、验证、注册、完整对局全部通过 |
| `correctly_unsupported` | 反例被正确拒绝（`unsupported`） |
| `interpretation` | 模型只提问/理解偏差，盲测输入不足以推进；或反例被误判为可玩 |
| `missing_feature` | 可表达用例被模型误报 `unsupported` |
| `composition` | 组合/编译阶段失败（`compiler_error`、结构错误） |
| `validation` | 生成的 IR 未通过正式门槛，或 confirm/publish 被拒 |
| `generation_budget` | 达到决策/token/时间预算 |
| `UI` | 已注册但浏览器/对局无法打到正常结束 |

分类不允许「一律归因模型」；宿主侧问题（工具、编译、验证、UI）用对应分类。

### 5. 准出目标与四个指标分离

- 开发集 3/3 全链路（M5 已完成）。
- **盲测目标：≥6/8 成功，2/2 反例正确拒绝。**
- 四个指标分别报告：所需机制存在 / IR 可编译 / 生成并验证通过 / 浏览器真正可玩。
- 已知族回归与陌生组合成功率**分栏统计**，不合并成单一百分比。
- 所有已注册产物必须有当前有效验证凭据；已知错误被标可玩数量必须为 0。
- 支持范围的诚实收缩允许降低覆盖率，但需 ADR 与反例支撑。

### 6. 证据归档

每次运行写出 `artifacts/g2/<git-rev>/<run-id>/`：

- `report.json`：逐用例记录、分类、用量、哈希、`verification_id`；
- `summary.md`：阈值判定、失败清单、模型信息、启动命令、解释器路径与 code fingerprint；
- 需要时附服务日志与浏览器截图（浏览器验收由 ADR-0019 的脚本另行产生）。

`git rev-parse HEAD`、dirty 状态、Python/Node 版本、`POCKER_AGENT_DATA_DIR`、实际端口与模型 provider/model 都进入报告。

## 备选方案与为何不选

| 方案 | 优点 | 为何不选 |
|---|---|---|
| 用预写 IR/Plan 注入当作生成结果 | 快、稳 | 计划 §8.1 X2/X4 明确禁止；等于自证 |
| 把 ScriptedModel 结果计入成功率 | 无需模型 | §M6.4 明确 ScriptedModel 只测流程 |
| 只看 `finalized` 判定成功 | 简单 | 缺少「可编译/验证/可玩」分层，掩盖 verification 与 UI 失败 |
| 失败一律记为模型问题 | 归因简单 | 掩盖宿主侧缺陷；分类要求区分 composition/validation/UI |
| 共享一次模型调用覆盖多条用例 | 省成本 | 每条用例必须独立会话与独立证据 |

## 后果

- 正面：G2 的成功/失败口径与证据格式被冻结，盲测结果不可被事后重定义。
- 正面：四个指标分离，覆盖率下降也能被诚实记录。
- 负面：一次完整盲测需要真实模型与预算（≤24 决策/回合、≤180 秒/轮）。
- 负面：无模型时只能跑 `--scripted` 流程，真实盲测必须标为阻塞。
- **强制方式**：`tests/test_m6_generation_flow.py` 证明 `--scripted` 链路可用；冻结哈希在报告中记录；真实盲测由 `scripts/m6_generation_acceptance.py` 的退出码与阈值判定决定，未运行则明确标阻塞。

## 迁移

- 现有 `scripts/benchmark_generation.py`（已知族、`IR_TEMPLATES`）继续作为**已知族回归**，与陌生组合成功率分栏。
- 真实模型盲测首次运行需要保存模型配置；在未配置前，M6 状态为「工具就绪、真实成绩未验证」。
