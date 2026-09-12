# G2 能力子集与机制审计（M0 交付物）

> 状态：M0 核对结果，**不含已实现的新能力**。本文件记录「现在真正能做什么」，用于约束 G2 的范围。
> 基线：`3ce565a`；审计数据目录：`artifacts/g2-runtime`（干净库，实测 8 个内置玩法）。
> 关联：ADR-0005、ADR-0006、`docs/g2-acceptance-scenarios.md`。

## 1. 方法与新增的强制检查

轴声明的 `mechanisms` 过去是**无人校验的自由字符串**。M0 把它变成可核对的声明：

```python
mechanism_problems(registry)   # core/capability.py
```

逐条检查 `tool.<name>[.<operation>]` 是否在注册表中真实存在，并由
`tests/test_architecture_invariants.py::test_every_axis_claim_points_at_a_real_operation` 强制。
非注册表引用（`state.*` / `plan.*` / `view(...)` / `axis.*` / `protocol.*`）不在该检查范围，由其它不变量覆盖。

### 1.1 审计发现的虚假声明（已修）

审计不是在假设风险，它当场抓到两处**标为 `stable` 却指向不存在机制**的声明：

| 轴 | 原声明 | 问题 | 修正 |
|---|---|---|---|
| `pattern_lang` | `tool.shedding` | **没有 `shedding` 工具** | `tool.pattern.match` / `tool.pattern.choices` / `tool.matching.play` |
| `team` | `tool.trick.team_winners` | `trick` 只有 `legal` / `play`，**没有 `team_winners` operation** | `tool.trick.play` / `state.teams` |

这正是「语料库标签覆盖不等于可运行覆盖」的具体形态：一个玩法可以因为「所需轴都 stable」而被判为可表达，
而那条轴的声明指向一个不存在的 operation。

## 2. 轴的**实际**覆盖边界（stable ≠ 标题所暗示的全部）

下表是核对后的准确含义。凡标题原先暗示、实际不支持的，必须在 `Capability.note` 写明。

| 轴 | 状态 | 真正由什么支撑 | **不覆盖** |
|---|---|---|---|
| `sequential_turn` | stable | `plan.wait` / `plan.branch` / `state.current_player` | 同时行动（见 `simultaneous`） |
| `exact_expression` | stable | `exact_expression.solve` / `.validate` | 任意数学表达式 |
| `score_settle` | stable | `score_settle.call` / `winner_resolve.call` / `state.finished` | 非零和与多池结算的**策略**（由规则显式声明） |
| `pattern_lang` | stable | `pattern.*` + `matching.play` | 任意牌型识别（仅参数化 match/choices/beats/classify） |
| `rank_compare` | stable | `rank_compare.call` / `deck.deal` | 牌型（那是 `hand_rank`） |
| `hand_rank` | stable | `hand_rank.best` / `.compare` | 五张以外的牌型体系 |
| `point_total` | stable | `point_total.total` / `.settle` | 21 点以外的点数体系 |
| `info_set` | stable | `state.private_hands` / `view(viewer)` | **多视角权限**；当前 API 固定 `viewer=player-1` |
| `hidden_draw` | stable | `hidden_draw.askable` / `.ask` | **按隐藏位置盲抽**；只支持按点数询问 |
| `turn_adapter` | stable | `trick.legal` / `trick.play` | **竞叫 / 优先级**；`trick` 没有叫牌轮与叫品级别 |
| `trigger` | stable | `trigger.apply` | 通用反应栈、无限递归触发 |
| `team` | stable | `trick.play` / `state.teams` | 队伍相关的一切非墩牌计分 |
| `betting` | stable | `betting.legal` / `.act` / `ledger.settle` | 边池策略（`ledger.pots` 支持，但由规则决定） |
| `ledger` | stable | `ledger.commit` / `.settle` / `state.stacks` | — |
| `simultaneous` | planned | — | 同时行动 / 实时抢牌 |
| `layout` | planned | — | 牌区自动布局 / 耐心游戏 |
| `macro` | planned | 宿主侧内联已实现，但**模型无生成/注册宏的元工具** | Agent 创作宏 |
| `sandbox` | planned | — | 隔离代码执行（G2 明确排除） |

`turn_adapter` 与 `hidden_draw` 的标题已在 M0 修正，不再暗示竞叫与盲抽。

## 3. 必须在 M1 拆开的隐式行为（有代码位置）

「操作不够正交」不是抽象评价，而是可定位的耦合：机制工具**替规则决定了玩法级后果**。

| 位置 | 隐式行为 | 为什么阻塞 G2 |
|---|---|---|
| `tools.py:454` | `MatchingTool.play`：`if not hand: state.update(finished=True, winners=[hand_index])` | 场景 B 明确要求「出完手牌本身不结束」；终局必须由规则声明，不能由移牌操作内置 |
| `tools.py:398` | `TrickTool.play`：`if trick_index >= tricks_total: finished=True, winners=team_winners(...)` | 墩数上限与队伍胜负由工具决定，规则无法表达「打到 X 分才结束」 |
| `tools.py` `MatchingTool.draw(..., recycle=True)` | 牌堆耗尽的回收策略内置为默认真 | 规范要求「牌堆耗尽及回收策略由规则显式决定」 |
| `hidden_tools.py:82` | `HiddenDrawTool.discard_pairs`：`scores[player] += removed`，写入固定 key `pairs` | 场景 C 要「每对加 **2** 分」并使用自己的计分；当前是每对 +1 且 key 写死 |
| `hidden_tools.py`（模块 docstring） | `HiddenDrawTool.ask`：对手没有该牌时**自动摸牌** | 「取牌失败后摸牌」是 Go Fish 的玩法规则，不是取牌机制；场景 C 不需要它 |
| `trigger_tools.py` | `trigger.apply` 写入 `skip`/`direction`/`active_suit` | 效果表是数据，但**消费时机**（何时判终局、何时 skip）必须由规则显式排序 |

**结论**：三个验收场景里，B（空手不结束）和 C（对子计分 2 分、双区域选择）**当前无法表达**。
这不是模型能力问题，是机制粒度问题——与 §4「`turn_adapter=stable` 不等于已实现竞叫」同一性质。

## 4. G2 可用的机制子集（本轮核对后）

**可依赖**：`deck`（含显式牌值）、`state`、`logic`、`pattern`（match/choices/describe/classify/beats）、
`rank_compare`、`score_settle`、`winner_resolve`、`matching`（拆分后）、`trigger.apply`（时机由规则声明）、
`ledger`、`betting`、`hand_rank`、`point_total`、`hidden_draw`（拆分后）、`solvable_deal`、`exact_expression`。

**明确排除（G2 不做，需求返回 `unsupported`）**：同时行动 / 实时抢牌、网络多人与权限、沙箱代码执行、
通用反应栈、无限递归触发、耐心游戏自动布局、未复核的完整桥牌 / 斗地主 / 德州扑克。

## 5. 能力事实（M0 实测记录）

| 项 | 值 |
|---|---|
| 基线 | `3ce565a8b503591a5682c8242623015563f9cf31`（工作区 clean） |
| 数据目录 | `artifacts/g2-runtime`（专用，不复用用户对局） |
| 干净库玩法数 | **8**（全部 `playtest.ok=true`，无 agent 注册残留） |
| `doctor.py --url` | 全项 PASS，服务 code fingerprint 与工作区一致 |
| `e2e_smoke.py` | **28 项通过**（含未配置模型时的安全失败分支） |
| 全量测试 | **202 passed** |
| 轴的虚假声明 | 2 处（已修，并加了非空检验证明检测器有效） |

> 说明：M0 之前文档中的「27/31 覆盖率」是**语料标签覆盖**，不能作为「陌生玩法生成成功率」。
> §8.3 要求四个指标分开统计，本文件是其中「所需机制是否存在」的基线。
