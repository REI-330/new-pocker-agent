# ADR-0006: 收窄原始 Plan 入口，发布只能经过组合 IR 与验证服务

- 日期：2026-09-12
- 状态：accepted（**实现状态：未实现**，M3 落地验证服务后一并收紧）
- 决策者：架构 owner

## 背景

`compose_plan` 现在有两个来源：

```python
if provided is not None:
    plan = GamePlan.model_validate(provided)          # source = "agent_compose"
elif state.ir is not None and is_host_compiled(state.ir):
    plan = host_compile(state.ir)                     # source = "host_compile"
```

`agent_compose` 是当前**唯一**能表达新机制的通道，但它只做两件事：`GamePlan.model_validate` + `Interpreter(plan, registry)`（检查 operation 存在）。**没有任何 IR 对照**——`REQUIRED_AXES` 只用于能力检查，从不用于校验计划。

于是存在两个方向相反的问题：

1. 新玩法只能从这个没有裁判的入口走；
2. `finalize` 主要信任 `playtest.ok`，而 playtest 只证明「能跑完、可重放、wait 全覆盖」，**不证明「实现了用户描述的规则」**。

这不是假想风险。`3ce565a` 修掉的 500 就是这条路径的产物：一个 `kind: poker` 的 IR 配了一份缺失 `committed` 的 plan，结构合法、operation 全部存在，直到运行时才炸。

## 决策

**发布路径只允许：`ComposedRulesIR`/`KnownRulesIR` → 编译 → 验证服务 → 不可变 artifact。**

1. 公开设计路径**移除** `compose_plan(plan=<任意 JSON>)` 的直接发布能力。
2. 原始 Plan 仅可用于：开发期验证、导入审查、故障注入。**不能绕过组合 IR 与同一验证服务进入游戏库。**
3. `finalize`/`publish` 不接受调用方传入的 `{ok: true}`；发布接口只接受宿主保存的 `verification_id`。
4. 任一 `finalize` 调用都由**同一个服务**重新检查当前前置条件，不依赖模型是否按提示词调用过某个工具（顺序由状态机强制，不由提示词强制）。
5. 保留「模型声明的 requires/ensures 只能**收紧**约束，不能覆盖宿主契约」；`state.update` 的全写集不是组合 IR 的逃生口——组合编译器只生成已声明字段的写入。

## 备选方案与为何不选

| 方案 | 优点 | 为何不选 |
|---|---|---|
| 保留 raw Plan 入口，只加一层 `contract_check` | 表达力不降，改动小 | 仍然存在第二份手写语义（模型写图），裁判要面对任意图；意图无法反推 |
| 完全删除 `agent_compose` | 最彻底 | 开发期诊断与故障注入仍需要它；且删除要先有替代路径 |
| 让模型自带 `verification` 声明 | 灵活 | 模型自证等于没有门槛（§9 红线） |
| 用提示词要求「必须先 capability_check」 | 零成本 | 提示词不是安全约束；当前 8 族轴全 stable，绕过与否今天**不可观测**，一旦引入 experimental 轴就变成真 bug |

## 后果

- 正面：只剩一条发布路径，裁判的对象从「任意节点图」变成「声明式组合 + 轨迹」。
- 正面：消除「构造成功 ≠ 可信」的歧义——`finalize` 的含义收窄为「尝试冻结技术产物」。
- 负面 / 代价：模型表达力下降；开发期调试原始 Plan 需要额外的（非发布）入口。
- 负面：这是对 v0.4 文档中「Agent 直接产候选 Plan」的**收窄**，需要同步改文档，否则文档与实现不一致（§9 红线）。
- **强制方式**：架构测试断言发布服务不接受 raw Plan 与调用方证据；反例测试「只提交 `{ok:true}` 报告」必须被拒绝；「未调用 capability_check 的 finalize」必须失败。

## 迁移与删除

- 顺序：**先有 M3 的验证服务，再收窄入口**。反过来做会让 G2 在 M2～M3 之间没有可用发布路径。
- 迁移期 `compose_plan(plan=...)` 保留但标注 `diagnostic_only`，并在同一交付包内给出删除 PR；不得长期并存两套语义。
- 删除完成的判据：`compose_plan` 不再接受 `plan` 参数，且 `finalize` 只接受 `verification_id`。
