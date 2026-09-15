# Pocker Agent — Web demo

通过自然语言澄清规则，生成受约束的纸牌 DSL，确认后模拟、与电脑试玩。

## 启动

Windows / Python 3.11+ / Node.js：

~~~powershell
uv sync
uv run python scripts\run_web.py
~~~

打开 http://127.0.0.1:8000 。这一条命令会构建前端并由后端单端口托管（端口被占用时会自动顺延并打印实际端口）。
本地单用户 demo；后端只监听回环地址，使用单个 worker。

前端开发模式（热更新）需要两个终端：

~~~powershell
uv run python -m uvicorn pocker_agent.app:app --host 127.0.0.1 --port 8000
~~~

~~~powershell
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
~~~

打开 http://127.0.0.1:5173 。

## 模型配置

在页面填写任意兼容 Chat Completions 的 API 地址、API Key 和模型 ID。
域名根地址自动补 /v1；明确填写 /api/v2 等路径则原样保留。
获取模型列表和测试连接只使用临时草稿，只有“保存配置”改变 Agent 使用的正式配置。
修改配置后需要保存或撤销才能发送玩法；避免表单与实际调用的配置不一致。
模型发现可选；不支持 /models 的服务可以手动填写模型名称。
空 Key 表示复用同一 API 地址的已保存 Key；改变 API 地址必须重新填写 Key。
保存不代表上游模型一定可用，使用“测试连接”验证真实调用。

配置及牌局存于 %LOCALAPPDATA%/PockerAgent/pocker.db；Key 单独保存在系统凭据库（Windows Credential Manager）。
可用 POCKER_AGENT_DATA_DIR 更改数据目录。浏览器只保存对话、DSL 和牌局 ID，不保存 Key。
刷新和后端重启都可恢复已保存配置及牌局。系统凭据库不可用会明确报错，不回退明文存储。

## 可执行规则

正式规则格式见 [GameRules 1.0](docs/game-rules-1.0.md)，旧 DSL 说明见 [DSL contract](docs/dsl.md)。当前支持按阶段执行的出牌、摸牌、弃牌、跳过、逐轮比较最高牌或手牌数量计分。
新增可执行规则族：24点等四张牌算式练习（目标、牌值、运算符可配置）、无下注21点、同花/同点接牌及疯狂八基础变体。
当前已有两人五张牌、单轮无上限下注和牌型摊牌机制，但它不是德州扑克：没有公共牌街、盲注和多轮下注。完整斗地主、德州扑克、桥牌、抽乌龟和多人联网仍不支持。模型会说明缺少的机制，不能用同名简化游戏替代。
模型必须对不支持的要求澄清，结构校验拒绝未知字段和动作。
旧v0.1玩法保留展示所有手牌的演示模式；新版21点隐藏庄家暗牌，接牌隐藏对手手牌。电脑使用确定性合法动作策略，算式模拟使用精确求解器。

## 离线导出（未实现）

v0.4 **没有**导出功能：仓库里不存在导出 ZIP 或离线 index.html 的实现与入口。
当前唯一可玩路径是本机 Web（构建产物由后端托管），必须有后端在运行。
保留本节标题只为明确说明该能力不存在，避免与旧版的说明混淆。

## 运行 v0.4 应用

一条命令（构建前端 + 启动应用；端口被占用时自动换端口）：

~~~powershell
uv run python scripts\run_web.py
# 浏览器打开 http://127.0.0.1:8000
~~~

也可以（等价，走 PowerShell）：

~~~powershell
powershell -ExecutionPolicy Bypass -File scripts\run_web.ps1
~~~

或者手动：

~~~powershell
# 1) 构建前端（产物由后端单端口托管）
npm run build --prefix frontend
# 2) 启动 v0.4 应用
uv run python -m uvicorn pocker_agent.app:app --host 127.0.0.1 --port 8000
# 浏览器打开 http://127.0.0.1:8000
~~~

真实 HTTP 端到端（需要服务已启动）：

~~~powershell
uv run python scripts/e2e_smoke.py http://127.0.0.1:8000
~~~

启动不成功时的诊断（会逐项检查并自测 HTTP）：

~~~powershell
uv run python scripts\doctor.py --serve
~~~

检查一个**已经在运行**的服务是否与当前代码一致（陈旧进程会让前端看起来坏掉）：

~~~powershell
uv run python scripts\doctor.py --url http://127.0.0.1:8000
~~~

前端开发模式：`npm run dev --prefix frontend`，并设置 `VITE_API_URL=http://127.0.0.1:8000`。

## 验证与开源复用

~~~powershell
uv run pytest -q
npm run build --prefix frontend
~~~

- [架构](docs/architecture.md)
- [GameRules 1.0 与机制合同](docs/game-rules-1.0.md)
- [v0.4 开发设计](docs/development-design-v0.4.md)
- [v0.4 执行内核](docs/core-v0.4.md)
- [工程规范（阶段门/测试/Review/防膨胀）](docs/engineering-standards.md)
- [v0.3 可回收清单](docs/salvage-from-v0.3.md)
- [架构决策记录](docs/adr/)
- [代码审查和验收](docs/review-and-acceptance.md)
- [开源来源与许可证](THIRD_PARTY_NOTICES.md)
- [项目进度](PROJECT.md)
- [常见玩法验收记录](docs/mainstream-games-review.md)

使用当前已保存的模型和Key运行真实自然语言回归（不修改配置）：

~~~powershell
uv run python -X utf8 scripts/benchmark_games.py --timeout 45
~~~

测试集为 benchmarks/common_games.json。脚本核对独立的规则字段期望，再用多个种子执行实际引擎；结果写入被Git忽略的artifacts目录。明确不支持的案例单独标记，不算作生成成功。
