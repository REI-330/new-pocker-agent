# 游戏生成与智能体评估参考论文

更新时间：2026-09-14

这份清单保存项目当前采用的研究参考。论文中的方法是设计依据，不代表仓库已经实现了相应能力。当前优先落地的是“编译后的游戏规则 → 多策略、多随机种子模拟 → 可复查的行为指标”；后续可以在这个反馈之上增加候选规则搜索和约束修复。

## 论文与项目启示

| 论文 | 主要方法 | 对本项目的直接启示 |
| --- | --- | --- |
| [Evaluating and Enhancing LLMs Agent based on Theory of Mind in Guandan: A Multi-Player Cooperative-Competitive Game under Imperfect Information (arXiv:2408.02559)](https://arxiv.org/abs/2408.02559) | 将状态解释、Theory of Mind 规划、计划评估拆开；用外部动作推荐器缩小很大的合法动作空间。 | 游戏执行引擎应提供结构化观察和合法动作；策略层与规则层分离；复杂游戏先筛选动作，再让智能体规划。 |
| [LLM-Hanabi: Benchmarking Belief Reasoning in Cooperative Games with Imperfect Information (arXiv:2510.04980)](https://arxiv.org/abs/2510.04980) | 在 Hanabi 中同时测游戏得分与一阶、二阶 Theory of Mind，并保存动作理由。 | 评估不能只有“跑通/失败”；应保留逐局结果和决策轨迹，为以后分析合作、信息推断和策略理由留接口。 |
| [DSGBench: A Diverse Strategic Game Benchmark for Evaluating LLM-based Agents in Complex Decision-Making Environments (arXiv:2503.06047)](https://arxiv.org/abs/2503.06047) | 用统一 Gym 接口覆盖战略规划、实时决策、社会推理、团队协作和适应学习，并记录决策上下文与结果。 | `GamePlan`/解释器应形成统一环境接口；评估报告需要声明策略、随机种子、上下文和结果，才能横向比较不同游戏。 |
| [GTBench: Uncovering the Strategic Reasoning Limitations of LLMs via Game-Theoretic Evaluations (arXiv:2402.12348)](https://arxiv.org/abs/2402.12348) | 按完全/不完全信息、动态/静态、随机/确定性组织博弈环境，并用收益、Elo、regret 等指标评估。 | 游戏元数据以后应表达信息结构与随机性；完成率只能检查可执行性，策略质量需要收益或 regret 一类指标。复杂提示推理不保证带来更好策略。 |
| [Simulation-Driven Balancing of Competitive Game Levels with Generative Artificial Intelligence (arXiv:2503.18748)](https://arxiv.org/abs/2503.18748) | 生成候选内容，用策略智能体反复模拟，再按胜率和和局情况提供奖励并继续搜索。 | 第一阶段先建立稳定、可复现的模拟评估；第二阶段才能把指标接入候选 `ComposedRulesIR` 的搜索与修复。观测到的平衡性依赖所选策略。 |
| [MarioGPT: Open-Ended Text2Level Generation through Large Language Models (arXiv:2302.05981)](https://arxiv.org/abs/2302.05981) | 用文本表示关卡，以语言模型作变异算子，以 novelty search 探索候选，再用外部 A* 智能体检查可玩性。 | JSON/IR 可以作为语言无关的候选表示；模型负责提出候选，确定性编译器和外部执行器负责检查，搜索过程不应依赖模型自评。 |

PDF 入口：

- [2408.02559](https://arxiv.org/pdf/2408.02559)
- [2510.04980](https://arxiv.org/pdf/2510.04980)
- [2503.06047](https://arxiv.org/pdf/2503.06047)
- [2402.12348](https://arxiv.org/pdf/2402.12348)
- [2503.18748](https://arxiv.org/pdf/2503.18748)
- [2302.05981](https://arxiv.org/pdf/2302.05981)

## 在本项目中的组合方式

```text
用户规则描述
  → 模型生成一个或多个 ComposedRulesIR 候选
  → 约束检查与确定性编译
  → GamePlan
  → Interpreter × 策略集合 × 随机种子集合
  → 完成率、失败类型、胜负分布、座位公平性、先手优势、局长
  → 保留候选 / 进入约束修复 / 交给用户确认
```

需要始终区分三类结论：

1. **规则正确性**：类型、约束、不变量和回放验证是否通过。
2. **观测到的游戏行为**：在明确的策略和随机种子下，是否完成、谁获胜、耗时多少、是否存在明显座位偏差。
3. **策略强度**：某个智能体是否会博弈，需要单独的收益、regret、Elo 或针对具体游戏的指标。

当前模拟评估属于第 2 类。它可以发现死局、策略无法行动、极端先手优势和结果退化，但不能仅凭随机策略的胜率宣称游戏已经平衡。

## 当前代码入口

编译得到 `GamePlan` 后，可以直接调用模拟评估器：

```python
from pocker_agent.core import core_registry, evaluate_simulations

report = evaluate_simulations(
    plan,
    core_registry(),
    strategies={"baseline": baseline_policy},
    seeds=(0, 1, 7, 23, 42),
)
result = report.as_dict()
```

`result["runs"]` 保留每个策略与随机种子对应的终局和错误；聚合字段用于比较候选规则。调用方应连同策略名称、随机种子和 `policy_dependent` 一起保存，避免把一次实验结果解释成普遍结论。
