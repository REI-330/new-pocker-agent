# GameRules 1.0 与 MechanismContract 1.0

`GameRules 1.0` 是用户确认、设计会话保存、验证和发布共同使用的 JSON
规则文档。JSON Schema 是跨语言契约，Pydantic 只是当前 Python 宿主的实现。
解释器不读取 GameRules；宿主先把它确定性编译为内部 `GamePlan`，再由
`Interpreter` 执行。

正式链路只有五个入口：

```python
parse_rules(payload)
normalize_rules(payload)
compile_rules(rules, registry)
verify_rules(rules, registry)
publish_rules(rules, approval)
```

其中 `normalize_rules` 是 0.4 八类 IR 与 0.5 `ComposedRulesIR` 的兼容导入
边界。设计会话写入时立即保存为 `kind: game_rules`；后续编译、验证、确认、
发布与版本详情均使用这份规范化文档。模型提交原始 `GamePlan` 会收到
`raw_plan_not_accepted`。

## 文档结构

- `game_id`、`rules_version`、`meta` 定义规则身份和说明。
- `participants` 定义座位、角色和队伍分区。
- `components` 定义牌组、牌区、对象池、棋盘与资源。
- `state` 为状态声明类型、作用域和可见性。
- `mechanisms` 固定机制名称、语义版本和配置。
- `execution` 保存确定性编译 profile 及其声明式规则。
- `budget.step_limit` 限制执行计划的最大步数。

`participants`、`components` 和 `state` 不是展示外壳。编译时会从可执行规则
重新推导它们；不一致的文档以 `canonical_declaration_mismatch` 拒绝。
机制版本或集合与实际计划不一致也会拒绝。

## 机制合同

`MechanismContract 1.0` 为每个机制固定：

- 配置 Schema 与语义版本；
- 操作输入、输出 Schema；
- 状态读集合和写集合；
- 前置条件、后置条件；
- 原子失败语义与确定性要求。

解释器在调用前后检查输入输出，并比较调用前后的状态字段。未声明写入、
错误输出和配置不匹配都会触发动作级回滚。

## 身份与门禁

规则文档、计划和机制注册表分别计算哈希。验证凭据同时绑定规则哈希、计划
哈希、机制合同哈希、编译器版本、策略和随机种子。发布时用户审批必须提交
当前规则哈希；规则任何变化都会使旧验证和旧审批失效。

Schema 位于 `schemas/`，三个可编译示例位于 `examples/rules/`。当前
`arithmetic-0.4`、`war-0.4` 等 profile 表示兼容导入后仍借用旧的确定性降层
器；它们不是新的存储格式。`composed-1.0` 是新组合规则的正式 profile。
旧降层器只有在对应玩法能由通用机制组合完整表达并通过固定种子行为对照后
才能移除。
