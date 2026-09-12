# ADR-0008: 验证证据与版本绑定（GameArtifact / VerificationResult）

- 日期：2026-09-12
- 状态：accepted（**实现状态：部分实现**，M3 进行中）
  - 已实现：`core/artifacts.py`（`VerificationResult`/`GameArtifact` 与内容绑定的 `verification_id`）；`core/verify/` 发布服务（规范化 IR → 能力解析 → 编译 → 动态验证 → 独立 `contract_check` → 产出不可变 artifact）；输入日志录制与重放（逐步 `state['input']` + 每操作状态摘要，事件与状态分别比较）；`SessionStore.record_verification`/`register_artifact`/`verify_and_register` 与按版本恢复；正式策略含目标分支 `goal_first`；类型化不变量（牌区守恒、得分边界、视角安全、终局可解释）；变异测试（改分值/赢家/轮数/可见性/比较位置均由独立监测器拒绝）；`act` 内存与 SQLite 均为原子提交（机器人失败回滚、revision 不变），`request_id` 重复提交去重（持久化，有界）。
  - 未实现：`binding_id`/`tool_type`；旧 `core_agent_plans` 行迁移为 artifact；Agent/API 消费者切换到 artifact 路径。
- 决策者：架构 owner

## 背景

当前注册路径是：

```python
if not playtest_report.get("ok"):
    raise ValueError("plan_not_playtested:" + game_id)
```

`register_plan` **信任调用方传来的 `playtest_report`**。同时 `finalize` 的门槛是「有 plan + 有 report + `report.ok`」。这套机制在 `3ce565a` 之后是安全的，因为报告只能由宿主内部的 playtest 产生——但它是**靠调用约定**成立，不是靠数据结构成立。

更根本的问题：**playtest 证明的东西比它看起来少。** 它证明「这个 plan 在测试策略下能跑完、可重放、wait 全覆盖」，不证明：

- IR 声明的轮数是否真的实现；
- IR 的胜负条件是否生效；
- 计分规则是否一致；
- 隐藏信息是否真的隐藏；
- IR 声明的动作是否都存在。

也就是 §8.3 要求的四个指标被混在了一起：`所需机制都存在` / `该 IR 可编译` / `生成并验证通过` / `浏览器真正可玩`。27/31 的标签覆盖也不能当陌生生成成功率。

`3ce565a` 已经补了必要的一半：`plan_fingerprint`、session↔plan 绑定、`plan_changed`/`plan_already_registered`/`game_id_reserved`。缺的是另一半：**证据本身要成为一等对象，并绑定到具体产物版本。**

## 决策

**注册对象升级为不可变 `GameArtifact`，验证结果由宿主保存并绑定哈希。**

```text
GameArtifact
  game_id / version
  generation_source = known_parameters | composed_rules     ← 由实际编译路径记录
  normalized_ir + ir_hash
  compiled_plan + plan_hash + source_map                    ← source_map 由编译器生成
  compiler_version / registry_contract_hash / runtime_version
  expanded_macro_hashes / ui_schema_version
  verification_id / approval_ir_hash
```

1. **发布接口接受 `verification_id`，不接受调用方传来的 `{ok: true}`。** 诊断模拟可用自选 seed；正式门禁的种子、策略、不变量由宿主决定。
2. `VerificationResult` 绑定以上哈希 + 策略版本 + 种子列表 + 覆盖项 + 结论，由宿主保存。
3. **IR / 宏 / 编译器 / 工具契约任一变化 ⇒ 旧凭据失效**，必须重新验证（`verification_stale`）。
4. `source_map` **由编译器生成**，模型不能自行宣称映射成功。
5. 同一玩法的新规则创建**新版本**，不覆盖旧版本；已存在的内置 ID 保护（`game_id_reserved`）继续保留。
6. 检查与写入必须在**同一 SQLite 事务**中，并有唯一键约束。
7. 会话按不可变版本恢复；没有运行版本可恢复时明确报错（`plan_changed`），**不自动改用新计划**。
8. 用户规则确认与技术验证可以先后独立完成，但发布时二者必须指向**同一个 `ir_hash`**；Agent 不能替用户调用确认接口。

## 备选方案与为何不选

| 方案 | 优点 | 为何不选 |
|---|---|---|
| 继续传 `{ok: true}` 报告 | 零改动 | 证据与产物无绑定，改 IR 后可复用旧结论 |
| 只用 `game_id` 绑定（不加哈希） | 简单 | 同名不同内容无法区分，正是 ADR-0007 要解决的问题 |
| 让模型或调用方提供验证结果 | 灵活 | 等于模型自证（§9 红线） |
| 把 `contract_check` 做成「检查 trace 里是否打印了条款文本」 | 容易实现 | 那不是契约检查，是字符串匹配；变异测试会立刻暴露 |
| 每步都做完整验证 | 最安全 | 代价过高；诊断（自选 seed）与门禁（宿主 seed）必须分开 |

## 后果

- 正面：四类指标（机制存在 / 可编译 / 验证通过 / 浏览器可玩）在数据结构上分开，可以分别汇报，不能互相冒充。
- 正面：`verification_stale` 让「改一点规则再复用旧验证」在机制上不可能。
- 负面 / 代价：`SessionStore` 需要新表与事务边界；注册路径变长（artifact 组装）。
- 负面：`contract_check` 需要**独立于编译器**实现监测器，否则同一个编译函数既产实现又产「独立答案」，变异测试会失效。
- **强制方式**：反例测试表（§8.2）逐条成为可执行用例——伪造报告、过期 verification_id、写死赢家、只走放弃路径、对手牌泄漏、半步提交；变异测试（改倍率/赢家/轮数/可见性/触发顺序）必须被拒绝。

## 迁移与删除

- 现有 `core_agent_plans` 行（`{plan, playtest, title, fingerprint}`）在 M3 迁移为 artifact 行；旧行缺少 `verification_id`，**不得**被当作已验证，必须重新验证后生成新版本。
- 迁移期的兼容读取必须与 ADR-0007 的 Schema 迁移一并测试。
- 删除判据：注册路径不存在任何接受调用方报告的分支。
