# Pocker Agent 开发总览

本文面向需要继续开发、评审或维护 Pocker Agent 的开发者，说明项目解决什么问题、核心代码如何协作、关键设计为什么这样做，以及 CI 和 M6 真实验收如何判断质量。

## 1. 项目功能

Pocker Agent 将自然语言描述的纸牌玩法转换成可验证、可发布、可实际运行的游戏版本。

用户可以：

- 用自然语言描述一套玩法；
- 在设计会话中澄清规则、提交规则草案并修复验证错误；
- 由宿主编译和验证规则；
- 在确认后发布带版本号的游戏 artifact；
- 创建对局，通过通用 action API 输入动作；
- 查看有界事件流、恢复刷新前的会话和事件游标；
- 使用前端动态渲染牌区、动作表单、事件时间线和设计生命周期。

项目的目标不是为每个玩法增加一个专属引擎，而是让新玩法复用同一套规则 IR、编译器、解释器、验证器和通用前端。

## 2. 总体架构

```text
自然语言玩法
    │
    ▼
设计会话 API
    │
    ▼
run_design_loop
    │  模型只返回一个 JSON 元工具决策
    ▼
DesignService.dispatch
    │  宿主执行工具并生成 observation
    ▼
ComposedRulesIR
    │
    ▼
确定性规则编译器
    │
    ▼
GamePlan
    │
    ▼
Interpreter + Registry
    │
    ├── verify_game / playtest
    ├── confirm / publish
    └── SessionStore + 通用 action API
```

代码按职责分为四层：

- `src/pocker_agent/core/`：规则 IR、zones、actions、编译器、解释器、验证器和 artifact。
- `src/pocker_agent/agent/`：设计会话、模型循环、元工具、运行记录和状态机。
- `src/pocker_agent/app.py`：HTTP API、依赖装配和应用启动恢复。
- `frontend/src/`：API 客户端、设计生命周期、通用牌区、动作表单和事件时间线。

## 3. 规则运行核心

### 3.1 IR、Plan 和 Interpreter

模型提交的是规则 IR，而不是可以直接执行的 Python 或任意运行时代码。宿主把 IR 编译成 `GamePlan`，再由 `Interpreter` 执行。

这样做有三个目的：

1. IR 可以做结构校验、能力检查和哈希绑定；
2. Plan 的执行操作只能来自核心 registry；
3. 运行时不需要信任模型，也不会因为模型生成代码而扩大权限边界。

`generation_source` 由真实编译路径写入，不能由模型自报。当前发布产物需要绑定 IR hash、Plan hash、verification 凭据和 registry 合约 hash。

### 3.2 通用动作

每个 `GamePlan` 暴露 `ActionDescriptor`。descriptor 描述：

- action id；
- 输入字段；
- 输入类型；
- 牌区和作用域；
- 最小/最大选择数；
- 当前玩家是否可见。

运行时 API 根据 descriptor 校验输入，再交给 Interpreter 作最终判断。前端不按玩法名称写分支，也不直接调用游戏专属动作。

### 3.3 视图和隐藏信息

`view()` 为指定 viewer 生成投影：

- 公共牌区可见；
- owner-only 牌区只显示给拥有者；
- hidden 牌区不泄露牌面，只提供必要的 `hidden_count`；
- 动作列表由当前状态动态生成。

因此，隐藏牌安全同时由后端投影、事件边界和前端渲染三层保证。

## 4. Agent 设计链路

核心实现位于：

- `src/pocker_agent/agent/design_loop.py`
- `src/pocker_agent/agent/design_service.py`
- `src/pocker_agent/agent/design_store.py`
- `src/pocker_agent/agent/design_runs.py`
- `src/pocker_agent/llm.py`

模型只能调用设计元工具：

```text
list_capabilities
describe_mechanism
propose_ir
patch_ir
capability_check
compose_plan
validate_plan
simulate
verify_game
inspect_failure
finalize
ask_user
unsupported
```

一次设计回合的基本过程是：

1. 创建或恢复持久化设计会话；
2. 把用户消息和受限历史放入模型上下文；
3. 模型返回一个 JSON 决策；
4. 宿主解析工具名和参数；
5. `DesignService` 执行工具并以结构化 observation 返回结果；
6. observation 追加到模型上下文；
7. 重复直到生成候选 artifact、明确 unsupported 或耗尽预算。

模型不能直接：

- 执行游戏；
- 写数据库；
- 修改宿主验证策略；
- 注册可玩的版本；
- 用自己的文字宣称验证通过。

### 4.1 状态和发布门禁

设计状态为：

```text
draft → diagnosed → compiled → verified → awaiting_confirmation → registered
                                      └──────────────→ failed
```

`finalize` 只生成候选 artifact。只有当前 IR 存在匹配的验证证据时，`confirm` 才能成功；`publish` 负责正式注册版本。

所有持久化写入都带 revision。并发写入返回 409，而不是静默覆盖。`request_id` 用于重放相同请求，运行中的重复请求会明确返回冲突。

### 4.2 Deadline 和运行回收

设计回合有决策数、修复数、估算 token、输出大小和 wall-clock 预算。模型调用接收剩余时间；迟到的模型结果不会继续 dispatch 或修改设计状态。

应用启动时，`DesignRunStore.recover_stale()` 会把崩溃遗留的 `running` 运行标记为失败，避免前端永久显示运行中。

## 5. HTTP API 与前端

主要 API 包括：

```text
POST /api/designs
POST /api/designs/{id}/messages
POST /api/designs/{id}/verify
POST /api/designs/{id}/confirm
POST /api/designs/{id}/publish

POST /api/sessions
POST /api/sessions/{id}/actions
GET  /api/sessions/{id}/events?after=&viewer=&limit=
GET  /api/games
```

前端以 `game_id + version + session_id` 作为恢复键，并持久化每个 session 的事件 cursor。只在服务返回 404 时清除恢复信息；409、运行中、验证失败和确认失效都作为可恢复状态展示。

通用前端组件包括：

- `ZoneView`：根据 `view().zones` 渲染牌区；
- `ActionForm`：根据 action descriptor 生成输入控件；
- `EventTimeline`：按 cursor 增量读取事件；
- 设计生命周期 tracker：澄清、组合、验证、待确认、已注册；
- 冲突和离线重试逻辑：重用相同 `request_id`，避免重复推进 revision。

## 6. 为什么这样设计

### 不让模型直接生成代码

直接执行模型代码无法可靠验证权限、状态变化和隐藏信息。声明式 IR 可以被宿主解析、编译、审计和重放。

### 不为新玩法新增专属引擎

专属引擎会让每个玩法拥有不同的输入、事件、验证和前端路径，最终无法比较证据。统一的 zones、actions、effects、flow 和 terminal 机制能让陌生玩法复用同一条运行链路。

### 验证和发布分离

验证是宿主事实，确认是用户决策，发布是注册动作。三者分离后，模型无法自证，IR 变化也会自动使旧确认失效。

### 独立证据评估

M6 不接受 `finalized` 或动作数量作为成功证明。`scripts/m6_evidence.py` 根据用例自己的预期检查牌区结构、终局类型、action count、隐藏投影、generation source 和哈希绑定。

## 7. CI 和验收思路

CI 位于 `.github/workflows/ci.yml`，分为三个 job。

### Python job

执行冻结依赖安装、Ruff、架构不变量和全量 pytest。测试重点是：

- 核心解释器和规则编译；
- registry 与能力轴；
- Agent 元工具和预算行为；
- 设计会话、revision、request_id 和状态机；
- M5 API、事件 cursor、SSRF 和版本绑定；
- M6 evidence evaluator 和 scripted generation flow。

### Frontend job

执行 `npm ci` 和 `npm run build`，验证 TypeScript、API 类型、React 页面和 Vite 构建。

### Browser job

安装 Chromium、Firefox、WebKit，启动真实服务，运行 `scripts/m5_acceptance.py`。它通过真实页面验证设计发布、建局、动态动作、刷新恢复、冲突重试和多浏览器行为。

### M6 真实模型验收

M6 真实模型不放进普通 CI，因为它依赖外部模型地址、网络、密钥和较长运行时间。使用：

```bash
uv run --frozen python scripts/m6_generation_acceptance.py --browser
```

脚本会冻结代码、提示词、工具表和用例的指纹，运行 8 个 composed 正例和至少 2 个 unsupported 反例，再分别统计：

```text
ir_compilable
generated_and_verified
runtime_playable
browser_playable
evidence_verified
```

真实模型成绩不能用 scripted model 替代。scripted model 只证明工具链路和验收脚本自身可用。

## 8. 当前状态和已知限制

当前确定性 CI 已通过的部分包括：

- Python 测试套件；
- Ruff；
- 前端构建；
- 真实服务 doctor 检查；
- M5 浏览器验收链路。

当前 M6 真实模型报告中：

- composed 正例：`0/8`；
- unsupported 反例：`2/2`；
- 冻结面：通过；
- 失败分类：主要为 `generation_budget`。

这表示当前主要瓶颈是模型对 JSON 工具协议和 `ComposedRulesIR` 的遵循能力，而不是运行时判定器误判。此前报告还发现部分用例超过 180 秒，已增加模型返回后、dispatch 前后 deadline 检查；该修复需要重新运行完整 M6 才能最终确认。

## 9. 开发和验证命令

```bash
# 后端测试
uv run --frozen pytest -q

# 静态检查
uvx ruff check src tests scripts

# 前端构建
npm run build --prefix frontend

# 真实服务检查
uv run --frozen python scripts/doctor.py --serve

# M5 浏览器验收
uv run --frozen python scripts/m5_acceptance.py --browsers chromium,firefox,webkit

# M6 真实模型盲测
uv run --frozen python scripts/m6_generation_acceptance.py --browser
```

交付时应同时记录：代码 revision、启动命令、模型配置、数据目录、测试命令、真实服务地址、报告路径和工作区是否干净。
