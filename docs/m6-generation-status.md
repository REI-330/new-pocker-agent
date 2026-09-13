# M6 生成验收状态

> 关联：ADR-0020（盲测生成与证据归档）、`docs/development-plan-phase2.md` §6/§8/§9。

## 交付物

| 交付物 | 作用 |
|---|---|
| `docs/adr/0020-m6-blind-generation-and-evidence.md` | 冻结冻结面、用例格式、运行路径、失败分类、准出阈值与证据归档 |
| `benchmarks/g2_blind_cases.json` | 8 条可表达组合 + 2 条缺机制反例；只含自然语言目标、可表达判定与独立预期，不含 IR/Plan |
| `scripts/m6_generation_acceptance.py` | 真实模型盲测入口：逐个用例走 `messages → verify → confirm → publish → 完整对局`，记录用量/哈希/验证 ID/失败分类，写出 `artifacts/g2/<rev>/<run-id>/` |
| `tests/test_m6_generation_flow.py` | ScriptedModel 端到端流程回归（只证明链路，不计生成成绩） |

## 当前结果

| 运行 | 命令 | 结果 |
|---|---|---|
| ScriptedModel 流程（flow only） | `uv run --frozen python scripts/m6_generation_acceptance.py --scripted` | 8/8 composed + 2/2 反例，全部走完 `verify → confirm → publish → finished` |
| 真实模型盲测 | `uv run --frozen python scripts/m6_generation_acceptance.py --base-url http://127.0.0.1:8012` | **阻塞：未配置模型**（计划 §9.3：未验证项不得计入通过） |
| 已知族回归 | `uv run --frozen python scripts/benchmark_generation.py --scripted` | 见该命令输出（与陌生组合成功率分栏统计） |

真实盲测需要先保存模型配置（`模型设置` 页面或 `POCKER_AGENT_*`），再启动服务运行上述命令；结果与 `git rev-parse HEAD`、冻结指纹、provider/model、预算用量一起写入证据目录。

## 四个指标（分开统计）

- 所需机制存在：8/8（评审者事先判定可表达）
- IR 可编译：以真实运行报告为准
- 生成并验证通过：以真实运行报告为准
- 浏览器真正可玩：浏览器验收见 `docs/m5-4-frontend-acceptance.md`

在真实盲测完成前，G2 不得宣称「陌生组合成功率达标」。
