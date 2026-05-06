# DeerFlow 2.0 学习路径

> 目标读者：AI Agent 开发工程师，希望通过学习 DeerFlow 掌握企业级 Agent 系统的设计与实现能力。
> 预计总时长：2-3 周（每天 2-3 小时）

---

## 前置知识

开始前确保你熟悉以下内容：

- Python 3.12+ 异步编程（async/await、asyncio）
- LangChain 基础（ChatModel、Tool、Chain）
- LangGraph 基础（StateGraph、Node、Edge）— 不熟的话先看 [LangGraph 官方教程](https://langchain-ai.github.io/langgraph/)
- FastAPI 基础（路由、依赖注入、中间件）
- Docker 基本概念

---

## 阶段一：跑起来，用起来（第 1 天）

**目标**：在本地启动 DeerFlow，通过 Web UI 体验所有核心功能，建立感性认识。

### 步骤

1. **启动服务**

   ```bash
   git clone https://github.com/bytedance/deer-flow.git
   cd deer-flow
   make setup     # 交互式配置向导，填写你的 LLM API key
   make dev       # 启动所有服务
   ```

   访问 <http://localhost:2026>

2. **体验核心功能**（按顺序）

   | 功能 | 怎么操作 | 观察什么 |
   |------|---------|---------|
   | 普通对话 | 发一条消息 | 流式响应、自动标题生成 |
   | 工具调用 | 问"搜索一下最新的 Python 发布版本" | tool call 过程、搜索结果返回 |
   | 文件上传 | 上传一个 PDF | 文件自动转换、agent 能引用内容 |
   | Plan Mode | 设置中开启 Plan Mode，给一个复杂任务 | 任务拆解成 todo list、逐步执行 |
   | Sub-Agent | 开启 Sub-Agent，让 agent 做一个多步骤研究任务 | 看到多个 sub-agent 并行执行、最终汇总 |
   | Memory | 多轮对话后，查看 Settings > Memory | 看到自动提取的用户偏好和事实 |

3. **阅读项目概览**

   - `README_zh.md` — 中文介绍，快速了解项目定位
   - `backend/README.md` — 后端架构图和核心组件列表
   - `backend/CLAUDE.md` — **最重要的架构文档**，563 行，覆盖每个子系统

### 本阶段产出

- [ ] 服务能正常启动和停止
- [ ] 体验过所有 6 个核心功能
- [ ] 能回答：DeerFlow 的 4 个进程分别是什么？各自监听什么端口？

---

## 阶段二：理解架构主线（第 2-4 天）

**目标**：理解一条用户消息从发出到收到回复，经过了哪些组件。这是后面所有学习的基础。

### 2.1 请求路由（0.5 天）

阅读顺序：

| 文件 | 关注点 |
|------|--------|
| `docker/nginx/nginx.local.conf` | nginx 如何把请求分发到 3 个后端 |
| `backend/langgraph.json` | LangGraph Server 的入口声明 |

**关键理解**：

```
用户请求 → nginx(:2026)
  ├── /api/langgraph/*  → LangGraph Server(:2024)  — agent 交互、流式响应
  ├── /api/*            → Gateway API(:8001)        — 模型列表、文件上传、memory 等
  └── /*                → Frontend(:3000)            — 页面
```

### 2.2 Agent 构建（1 天）

阅读顺序（按顺序读）：

| # | 文件 | 行数 | 关注点 |
|---|------|------|--------|
| 1 | `backend/langgraph.json` | 14 | 入口：`deerflow.agents:make_lead_agent` |
| 2 | `backend/packages/harness/deerflow/agents/lead_agent/agent.py` | ~300 | `make_lead_agent()` 如何组装 agent：模型、工具、中间件、system prompt |
| 3 | `backend/packages/harness/deerflow/agents/thread_state.py` | ~150 | ThreadState schema — agent 在线程间传递哪些状态 |
| 4 | `backend/packages/harness/deerflow/agents/lead_agent/prompt.py` | ~200 | System prompt 模板：skills、memory、工作目录如何注入 |

**关键理解**：

- `make_lead_agent()` 是一切的核心入口，它调用 `create_react_agent()` 并附加中间件链
- 工具通过 `get_available_tools()` 动态组装（config 工具 + MCP + 内置 + sub-agent 工具）
- 模型通过 `create_chat_model()` 工厂方法按名称创建，支持 thinking/vision 切换

### 2.3 中间件链（1.5 天）— 最重要的设计模式

先读这个文档，建立全局视图：

| 文件 | 说明 |
|------|------|
| `backend/docs/middleware-execution-flow.md` | **必读**。完整的中间件执行流程、时序图、洋葱模型分析 |

然后按执行顺序读中间件源码（每个文件约 50-150 行）：

| # | 中间件 | 文件 | 钩子 | 学习价值 |
|---|--------|------|------|---------|
| 0 | ThreadData | `middlewares/thread_data_middleware.py` | before_agent | 理解线程隔离目录 |
| 1 | Uploads | `middlewares/uploads_middleware.py` | before_agent | 文件注入 |
| 2 | Sandbox | `middlewares/sandbox_middleware.py` | before_agent + after_agent | **唯一对称的中间件**（获取/释放） |
| 3 | DanglingToolCall | `middlewares/dangling_tool_call_middleware.py` | after_model | 处理中断的工具调用 |
| 8 | Title | `middlewares/title_middleware.py` | after_model | 自动生成标题 |
| 9 | Memory | `middlewares/memory_middleware.py` | after_agent | 异步记忆入队 |
| 12 | LoopDetection | `middlewares/loop_detection_middleware.py` | after_model | **循环检测** — 企业 Agent 必备 |
| 13 | Clarification | `middlewares/clarification_middleware.py` | after_model | 主动中断流程 |

> 中间件文件都在 `backend/packages/harness/deerflow/agents/middlewares/` 下

**关键理解**：

- 中间件不是洋葱模型，是管道模型。大部分中间件只用一个钩子
- `before_*` 正序执行（0→N），`after_*` 反序执行（N→0）
- 列表最后的 ClarificationMiddleware 的 `after_model` 最先执行
- 硬依赖只有两处：ThreadData 必须在 Sandbox 之前；Clarification 必须在最后

### 本阶段产出

- [ ] 能画出：一条用户消息 → agent 响应的完整数据流
- [ ] 能回答：14 个中间件分别做什么？执行顺序是什么？
- [ ] 能回答：为什么中间件是管道模型而不是洋葱模型？

---

## 阶段三：核心子系统深入（第 5-9 天）

每个子系统独立学习，可以按兴趣调整顺序。

### 3.1 工具系统（1 天）

| 文件 | 关注点 |
|------|--------|
| `backend/packages/harness/deerflow/sandbox/tools.py` | 5 个内置工具实现：bash、ls、read_file、write_file、str_replace |
| `backend/packages/harness/deerflow/tools/builtins/` | present_files、ask_clarification、view_image |
| `backend/packages/harness/deerflow/reflection/__init__.py` | `resolve_variable()` — 如何通过字符串路径动态加载类 |
| `config.example.yaml` 的 `tools:` 段 | 工具配置格式 |

**学习重点**：

- 虚拟路径系统：agent 看到的 `/mnt/user-data/workspace` 如何映射到物理路径
- `resolve_variable("deerflow.sandbox.tools:bash_tool")` 这种动态加载模式
- 工具分组（web、file:read、file:write、bash）的权限控制思路

### 3.2 Sub-Agent 系统（1 天）

| 文件 | 关注点 |
|------|--------|
| `backend/packages/harness/deerflow/subagents/executor.py` | 异步执行引擎 |
| `backend/packages/harness/deerflow/subagents/registry.py` | Agent 注册表 |
| `backend/packages/harness/deerflow/subagents/builtins/` | 内置 agent 定义（general-purpose、bash） |
| `backend/packages/harness/deerflow/agents/middlewares/subagent_limit_middleware.py` | 并发限制 |

**学习重点**：

- 双线程池设计：`_scheduler_pool`(3 workers) + `_execution_pool`(3 workers)
- `task()` 工具调用 → SubagentExecutor → 后台线程 → 轮询 5s → SSE 事件
- 最多 3 个并发 sub-agent，15 分钟超时
- Sub-Agent 只有 4 个中间件（ThreadData、Sandbox、Guardrail、ToolErrorHandling）

### 3.3 Memory 系统（1 天）

| 文件 | 关注点 |
|------|--------|
| `backend/packages/harness/deerflow/agents/memory/updater.py` | LLM 驱动的记忆提取和更新 |
| `backend/packages/harness/deerflow/agents/memory/queue.py` | 去抖动更新队列 |
| `backend/packages/harness/deerflow/agents/memory/prompt.py` | 记忆提取的 prompt 模板 |
| `backend/tests/test_memory_updater.py` | 回归测试 |

**学习重点**：

- Memory 数据结构：userContext、facts（带 confidence 分数和 category）
- 去抖动（30s）+ 去重 + 批量更新的工程实践
- 原子写入（temp file + rename）保证数据安全
- 下一轮对话时，top 15 facts + context 注入 system prompt 的 `<memory>` 标签

### 3.4 Sandbox 系统（1 天）

| 文件 | 关注点 |
|------|--------|
| `backend/packages/harness/deerflow/sandbox/sandbox.py` | 抽象 Sandbox 接口 |
| `backend/packages/harness/deerflow/sandbox/local/` | 本地文件系统实现 |
| `backend/packages/harness/deerflow/community/aio_sandbox/` | Docker 容器隔离实现 |
| `backend/docs/PATH_EXAMPLES.md` | 虚拟路径详解 |

**学习重点**：

- Provider 模式：`acquire()` → `get()` → `release()` 生命周期
- 虚拟路径翻译：`replace_virtual_path()` / `replace_virtual_paths_in_command()`
- `str_replace` 工具的并发安全：按 `(sandbox.id, path)` 作用域序列化

### 3.5 配置系统（0.5 天）

| 文件 | 关注点 |
|------|--------|
| `config.example.yaml` | 完整配置模板（902 行），逐段阅读 |
| `backend/packages/harness/deerflow/config/` | 配置解析和缓存逻辑 |
| `backend/docs/CONFIGURATION.md` | 配置详解 |

**学习重点**：

- `$VAR` 环境变量解析
- mtime 变化自动热加载（不需要重启）
- `config_version` 版本追踪和 `make config-upgrade` 升级机制
- 配置查找优先级：显式参数 > 环境变量 > 当前目录 > 父目录

### 本阶段产出

- [ ] 能解释：工具动态加载的实现方式
- [ ] 能解释：sub-agent 并发控制的完整流程
- [ ] 能解释：memory 从提取到注入的全链路
- [ ] 能解释：sandbox 虚拟路径的映射关系

---

## 阶段四：工程化实践（第 10-12 天）

### 4.1 Harness/App 分层设计（1 天）

| 文件 | 关注点 |
|------|--------|
| `backend/docs/HARNESS_APP_SPLIT.md` | **必读**。拆分设计文档，解释为什么这样分层 |
| `backend/tests/test_harness_boundary.py` | CI 强制执行的边界检查 |
| `backend/pyproject.toml` | workspace 配置 |
| `backend/packages/harness/pyproject.toml` | harness 包的依赖声明 |

**学习重点**：

- 单向依赖：`app.*` → `deerflow.*` 允许，反向禁止
- Harness 是可发布包（`deerflow-harness`），App 是项目内部代码
- uv workspace 机制：`packages/harness` 是 workspace member
- **这是企业级项目模块化的标准实践**

### 4.2 流式输出设计（1 天）

| 文件 | 关注点 |
|------|--------|
| `backend/docs/STREAMING.md` | **必读**。两条流式路径的设计决策、LangGraph stream_mode 语义 |

**学习重点**：

- 为什么两条路径（Gateway async/HTTP vs DeerFlowClient sync/in-process）无法合并
- LangGraph 三种 stream_mode：`values`（state 快照）、`messages`（token delta）、`custom`（自定义事件）
- `seen_ids` / `streamed_ids` / `counted_usage_ids` 三个去重集合各管什么
- StreamBridge：asyncio Queue + SSE 消费者

### 4.3 嵌入式 Client（0.5 天）

| 文件 | 关注点 |
|------|--------|
| `backend/packages/harness/deerflow/client.py` | DeerFlowClient 完整实现 |
| `backend/tests/test_client.py` | 77 个单元测试 + Gateway 一致性测试 |

**学习重点**：

- 不启动 HTTP 服务，直接在进程内调用 agent
- 所有返回值与 Gateway API 的 Pydantic 模型对齐（`TestGatewayConformance`）
- `checkpointer` 参数支持跨进程持久化

### 4.4 MCP 集成（0.5 天）

| 文件 | 关注点 |
|------|--------|
| `backend/packages/harness/deerflow/mcp/` | MCP 客户端、工具缓存 |
| `backend/docs/MCP_SERVER.md` | MCP 配置指南 |
| `extensions_config.example.json` | MCP 配置模板 |

**学习重点**：

- 懒加载 + mtime 缓存失效
- 支持 stdio、SSE、HTTP 三种传输
- OAuth token 流程（client_credentials、refresh_token）

### 本阶段产出

- [ ] 能解释：为什么 harness/app 要分开，CI 如何强制执行
- [ ] 能解释：两条流式路径各自服务谁，为什么不合并
- [ ] 能用 DeerFlowClient 写一个简单的 Python 脚本调用 agent

---

## 阶段五：动手仿写（第 13-15 天）

**目标**：通过仿写巩固所学，构建你自己的 mini agent framework。

### 练习 1：最小 Agent（半天）

用 LangGraph 写一个能调工具的 agent：

```
用户输入 → StateGraph → LLM 调用 → 工具执行 → 返回结果
```

参考：`backend/packages/harness/deerflow/agents/lead_agent/agent.py`

### 练习 2：加中间件（1 天）

逐步给你的 agent 加中间件（参考 DeerFlow 的实现）：

1. **ThreadDataMiddleware** — 给每个线程创建独立目录
2. **LoopDetectionMiddleware** — 检测 LLM 是否在重复调用同一个工具
3. **MemoryMiddleware** — 把对话存到 JSON 文件，下一轮注入 system prompt

### 练习 3：加 Sub-Agent（1 天）

实现一个简单的 sub-agent 系统：

1. Lead agent 可以调用 `delegate_task(description)` 工具
2. 后台线程执行子任务
3. 子任务完成后结果返回给 lead agent

参考：`backend/packages/harness/deerflow/subagents/executor.py`

### 练习 4（进阶）：加配置热加载

实现一个简单的配置系统：

- 从 YAML 文件读取模型配置
- 文件变化时自动重载（mtime 检测）
- 不需要重启服务

参考：`backend/packages/harness/deerflow/config/`

---

## 推荐阅读清单

按优先级排序，标 ★ 为必读：

| 优先级 | 文档 | 内容 |
|--------|------|------|
| ★ | `backend/CLAUDE.md` | 最完整的架构文档（563 行） |
| ★ | `backend/docs/middleware-execution-flow.md` | 中间件执行流程 |
| ★ | `backend/docs/HARNESS_APP_SPLIT.md` | 分层设计 |
| ★ | `backend/docs/STREAMING.md` | 流式输出设计 |
| | `backend/docs/ARCHITECTURE.md` | 架构详解（484 行） |
| | `backend/docs/CONFIGURATION.md` | 配置详解 |
| | `backend/docs/FILE_UPLOAD.md` | 文件上传 |
| | `backend/docs/PATH_EXAMPLES.md` | 虚拟路径示例 |
| | `backend/docs/plan_mode_usage.md` | Plan Mode |
| | `backend/docs/summarization.md` | 上下文压缩 |
| | `backend/docs/MCP_SERVER.md` | MCP 集成 |
| | `backend/docs/GUARDRAILS.md` | 安全护栏 |

---

## 企业应用对照表

学完 DeerFlow 后，你掌握的技能对应的实际企业场景：

| DeerFlow 模块 | 对应企业场景 | 你能做什么 |
|---------------|-------------|-----------|
| 中间件链 | 任何 Agent 框架的横切关注点处理 | 为 agent 加安全检查、限流、审计、上下文管理 |
| Sub-Agent 编排 | 多 Agent 协作系统 | 设计并实现 lead-worker 模式的多 agent 系统 |
| Sandbox 隔离 | Agent 安全执行环境 | 让 agent 在隔离环境中执行代码、操作文件 |
| Memory 系统 | 个性化 AI 助手 | 实现跨会话记忆、用户画像积累 |
| 工具动态加载 | 企业系统集成 | 通过配置文件接入内部 API、数据库、工具 |
| 配置热加载 | 运维友好设计 | 不重启服务即可更新模型、工具、参数 |
| Harness/App 分层 | 框架级项目架构 | 设计可复用的 agent 框架 + 可替换的应用层 |
| MCP 集成 | 外部工具生态 | 让 agent 通过标准协议使用外部工具 |
| 流式输出 | 实时交互体验 | 实现 SSE/WebSocket 流式响应 |

---

## 学习建议

1. **先跑后读**：先让 DeerFlow 跑起来体验功能，再读代码。有体感后看代码事半功倍。
2. **按数据流读**：跟着一条消息的流转路径读代码，不要随机翻文件。
3. **重点读中间件**：这是 DeerFlow 最核心的设计模式，也是最容易迁移到你自己的项目的东西。
4. **边读边写**：阶段五的仿写练习不要跳过。读懂和写出来是两个层次。
5. **关注设计决策**：`STREAMING.md` 和 `HARNESS_APP_SPLIT.md` 里解释了"为什么这样设计"而不只是"代码怎么写"，这些决策思维比代码本身更有价值。
