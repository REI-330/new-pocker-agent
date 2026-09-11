# Pocker Agent — Web demo

通过自然语言澄清规则，生成受约束的纸牌 DSL，确认后模拟、与电脑试玩，导出离线游戏。

## 启动

Windows / Python 3.11+ / Node.js：

~~~powershell
uv sync
uv run uvicorn pocker_agent.api:app --app-dir src --host 127.0.0.1 --port 8000
~~~

另一个终端：

~~~powershell
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
~~~

打开 http://127.0.0.1:5173 。本地单用户 demo；后端只监听回环地址，使用单个 worker。

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

详见 [DSL contract](docs/dsl.md)。当前支持按阶段执行的出牌、摸牌、弃牌、跳过、逐轮比较最高牌或手牌数量计分。
新增可执行规则族：24点等四张牌算式练习（目标、牌值、运算符可配置）、无下注21点、同花/同点接牌及疯狂八基础变体。
不支持完整斗地主、德州扑克、桥牌、抽乌龟，也不支持任意牌型识别、下注和多人联网。模型会说明缺少的机制，不能用同名比大小游戏替代。
模型必须对不支持的要求澄清，结构校验拒绝未知字段和动作。
旧v0.1玩法保留展示所有手牌的演示模式；新版21点隐藏庄家暗牌，接牌隐藏对手手牌。电脑使用确定性合法动作策略，算式模拟使用精确求解器。

## 离线导出

下载 ZIP，解压后打开 index.html，无需后端、API Key、CDN 或网络。
游戏采用 seed=7 的固定发牌练习局，合法决策路径由同一个 Python 引擎编译，浏览器只播放已验证状态。
最多 2000 个决策节点，超过限制会显示原因；在线游戏不受此导出限制。
牌面、许可证、DSL 都包含在 ZIP 内。
离线导出目前仅覆盖旧v0.1阶段规则。新版算式、21点和接牌通过本机Web执行并支持保存恢复，界面会明确标记暂不支持离线导出。

## 验证与开源复用

~~~powershell
uv run pytest -q
npm run build --prefix frontend
~~~

- [架构](docs/architecture.md)
- [代码审查和验收](docs/review-and-acceptance.md)
- [开源来源与许可证](THIRD_PARTY_NOTICES.md)
- [项目进度](PROJECT.md)
- [常见玩法验收记录](docs/mainstream-games-review.md)

使用当前已保存的模型和Key运行真实自然语言回归（不修改配置）：

~~~powershell
uv run python -X utf8 scripts/benchmark_games.py --timeout 45
~~~

测试集为 benchmarks/common_games.json。脚本核对独立的规则字段期望，再用多个种子执行实际引擎；结果写入被Git忽略的artifacts目录。明确不支持的案例单独标记，不算作生成成功。
