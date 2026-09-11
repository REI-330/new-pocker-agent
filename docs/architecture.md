# 架构（当前实现）

React 工作台：
- api.ts：HTTP 请求、错误信息和公共类型。
- ModelSettings：临时表单、完整模型列表、手动模型名、测试与保存。
- GameTable：开源 SVG 牌面、选牌、电脑回合结果、分数、重开与刷新。
- main.tsx：对话 → 提案 → 确认 → 模拟 → 试玩 → 导出；修改规则会取消旧确认并清除旧衍生结果。

FastAPI：
- configuration.py：校验、规范化 URL、正式配置快照；SQLite 元数据 + keyring 系统凭据。
- llm.py：复用 OpenAI Python SDK 的网络、鉴权与错误处理；服务地址、模型没有白名单。
- agent.py：完整 JSON Schema + 明确 DSL 执行语义；澄清、生成和修复。
- models.py / validation.py：严格模型、引用验证和边界。
- game_rules.py：v0.2 可执行规则族、明确的玩家配置、从执行配置生成中文规则条款。
- executors.py：运行时和模拟共享的引擎选择及恢复入口。
- engine.py：保留 v0.1 阶段规则引擎，兼容既有比大小和摸牌/出牌规则。
- family_engines.py：算式练习、无下注21点、同花/同点接牌的确定性状态机。
- arithmetic.py：使用标准库 AST / Fraction 验证算式，穷举精确解，无 eval、无浮点近似。
- runtime.py：单人电脑回合、SQLite 牌局恢复、revision 并发保护。
- simulation.py：同一引擎的确定性模拟。
- exporting.py：同一引擎编译有限离线决策树，打包 HTML、DSL、纸牌与许可证。
- storage.py：有事务且明确关闭连接的 SQLite 边界。

自然语言生成：Agent 根据清楚的玩法机制选择相应 schema；不明确的描述仍交给模型澄清。结构错误最多修复一次，修复请求携带用户对话。规则提案先进行引擎启动检查，页面条款从最终 DSL 生成，避免模型摘要和执行配置脱节。明确要求24点却返回比大小时会被机制校验拦下。

这不等同于证明所有自然语言语义：已知需求的语义回归由 benchmarks/common_games.json 的独立字段期望和真实模型脚本负责；用户仍需确认规则变体。测试区分实际可玩和明确不支持。

新版动作请求增加 expression / declared_suit。非法动作在状态副本上失败，不写回原会话。21点和接牌的公开视图隐藏对手牌、暗牌总点数及可推导牌序的seed；内部存储保留完整状态。新规则族目前通过本机Web执行，离线有限决策树导出仅适用于v0.1。

配置 GET 不返回 Key。发现模型 / 测试连接不修改已保存状态。每次 Agent 请求取得完整不可变配置快照。
持久化目录默认位于 Windows 本地应用数据目录，排除于仓库之外。当前仅支持本机单用户单进程部署。
