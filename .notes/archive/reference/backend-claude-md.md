# backend/CLAUDE.md 中文详解笔记

> 来源：`backend/CLAUDE.md`（569 行），这是给 AI 编码助手的架构文档，也是 DeerFlow 最完整的后端文档。
> 这里是中文翻译 + 详解，方便理解。

---

## 一、项目概览

DeerFlow 是一个基于 LangGraph 的 AI 超级 Agent 系统。后端提供的核心能力：

- 沙箱执行（Agent 可以跑代码、写文件，但在隔离环境里）
- 持久记忆（跨对话记住用户偏好）
- 子 Agent 委派（复杂任务拆分给 sub-agent 并行执行）
- 可扩展工具集成（MCP、社区工具、自定义工具）
- 所有操作按线程隔离

---

## 二、架构 — 4 个进程

### 进程一览

| 进程 | 端口 | 本质 | 干什么 |
|------|------|------|--------|
| **nginx** | 2026 | C 语言 Web 服务器 | 统一入口，反向代理，分发请求 |
| **LangGraph Server** | 2024 | Python 进程 | Agent 运行时（标准模式） |
| **Gateway API** | 8001 | Python FastAPI（Uvicorn 跑） | API 服务 + 嵌入 Agent 运行时 |
| **Frontend** | 3000 | Node.js Next.js | Web UI 页面 |

还有一个可选进程：
- **Provisioner**（:8002）：只在 sandbox 配置为 Kubernetes/provisioner 模式时启动

### 为什么是这些进程

**为什么需要 nginx？**
- 没有它：前端 :3000，API :8001，用户要记两个端口，还有跨域问题
- 有了它：用户只访问 :2026，nginx 内部分发，无跨域，还能加 SSL/限流/缓存
- 类比：nginx 是前台接待，把客人领到不同的厨师面前

**为什么用 nginx 不用 Uvicorn？**
- Uvicorn 是 Python ASGI 服务器，只能跑 Python 应用（厨师）
- nginx 是独立的 C 程序，不管后端是 Python 还是 Node.js 都能转发（前台接待）
- **两个都用了**，各干各的：Uvicorn 跑 Gateway，nginx 做反向代理

**为什么 Gateway 要嵌入 Agent 运行时？**

| 模式 | 怎么启动 | Agent 跑在哪 | 进程数 |
|------|---------|-------------|--------|
| 标准模式 | `make dev` | LangGraph Server(:2024) 独立进程 | 4 个 |
| Gateway 模式（概念） | Docker 或嵌入式 | 嵌入 Gateway(:8001) | 3 个 |

注意：Gateway 模式是一个概念性设计，`make dev` 实际上 4 个进程都启动，但 nginx 把 `/api/langgraph/*` 路由到 Gateway 的嵌入运行时。没有单独的 `make dev-pro` 命令。

### SSL/HTTPS

- **HTTP** = 明文传输，像寄明信片，谁都能偷看
- **HTTPS** = 加密传输，像寄密封信封，只有收件人能看
- **SSL 证书** = 加密的钥匙，nginx 负责管理
- 本地开发用 HTTP 就行，生产环境必须 HTTPS
- nginx 统一处理 SSL，后端代码不用改

### nginx 路由规则

```
/api/langgraph/* → Gateway(:8001) 嵌入的 agent 运行时（重写为 /api/*）
/api/*           → Gateway(:8001) 的 FastAPI 路由
/*               → Frontend(:3000) 页面
```

---

## 三、项目目录结构

```
deer-flow/
├── Makefile                  # 根命令（check, install, dev, stop）
├── config.yaml               # 主配置文件
├── extensions_config.json    # MCP 服务器和技能配置
├── backend/                  # 后端应用（本目录）
│   ├── Makefile              # 后端专用命令（dev, gateway, lint）
│   ├── langgraph.json        # LangGraph Studio 图配置
│   ├── packages/
│   │   └── harness/          # deerflow-harness 包（import 前缀: deerflow.*）
│   │       └── deerflow/
│   │           ├── agents/           # Agent 系统
│   │           │   ├── lead_agent/   # 主 Agent（工厂函数 + 系统提示词）
│   │           │   ├── middlewares/  # 18 个中间件组件
│   │           │   ├── memory/       # 记忆提取、队列、提示词
│   │           │   └── thread_state.py # 线程状态 schema
│   │           ├── sandbox/          # 沙箱执行系统
│   │           │   ├── local/        # 本地文件系统实现
│   │           │   ├── sandbox.py    # 抽象 Sandbox 接口
│   │           │   ├── tools.py      # bash, ls, read/write/str_replace
│   │           │   └── middleware.py # 沙箱生命周期管理
│   │           ├── subagents/        # 子 Agent 委派系统
│   │           │   ├── builtins/     # 内置 agent（general-purpose, bash）
│   │           │   ├── executor.py   # 后台执行引擎
│   │           │   └── registry.py   # Agent 注册表
│   │           ├── tools/builtins/   # 内置工具（present_files, ask_clarification, view_image）
│   │           ├── mcp/              # MCP 集成（工具、缓存、客户端）
│   │           ├── models/           # 模型工厂，支持 thinking/vision
│   │           ├── skills/           # 技能发现、加载、解析
│   │           ├── config/           # 配置系统
│   │           ├── community/        # 社区工具（tavily, jina_ai, firecrawl, image_search, aio_sandbox）
│   │           ├── reflection/       # 动态模块加载（resolve_variable, resolve_class）
│   │           ├── utils/            # 工具函数（网络、可读性）
│   │           └── client.py         # 嵌入式 Python 客户端（DeerFlowClient）
│   ├── app/                  # 应用层（import 前缀: app.*）
│   │   ├── gateway/          # FastAPI Gateway API
│   │   │   ├── app.py        # FastAPI 应用
│   │   │   └── routers/      # 路由模块（models, mcp, memory, skills, uploads 等）
│   │   └── channels/         # IM 平台集成（飞书、Slack、Telegram、钉钉）
│   ├── tests/                # 测试套件
│   └── docs/                 # 文档
├── frontend/                 # Next.js 前端应用
└── skills/                   # Agent 技能目录
    ├── public/               # 公共技能（已提交 git）
    └── custom/               # 自定义技能（gitignore）
```

---

## 四、Harness/App 分层

### Harness 为什么叫这个名字

**Harness = 驾具/挽具**，套在马身上控制马的皮带。

- 🐎 马 = Agent（AI 模型，强大但不可控）
- 皮带 = Harness（框架，控制 Agent 的行为）

DeerFlow 把它叫 harness，意思是"这个包是控制 Agent 的框架，不是 Agent 本身"。

### 分层规则

```
deerflow.*（harness 包）         app.*（应用层）
├── agents/                      ├── gateway/    ← FastAPI 路由
├── sandbox/                     └── channels/   ← 飞书/Slack/Telegram
├── models/
├── tools/                       app 可以 import deerflow ✅
├── mcp/                         deerflow 不能 import app ❌
├── config/                      （CI 强制执行）
└── client.py
```

| 层 | 路径 | Import 前缀 | 可发布 | 说明 |
|---|---|---|---|---|
| **Harness** | `packages/harness/deerflow/` | `deerflow.*` | 是（pip install deerflow-harness） | 通用 Agent 框架 |
| **App** | `app/` | `app.*` | 否 | DeerFlow 项目自己的业务代码 |

**依赖方向**：App → deerflow 允许，deerflow → App 禁止。由 `tests/test_harness_boundary.py` 在 CI 里强制执行。

**为什么要分层**：harness 是可发布的框架包，别人可以基于它搭自己的 Agent 系统。app 是 DeerFlow 项目自己的业务代码。分开了才能开源 harness 而不暴露业务代码。

---

## 五、Agent 系统

### Lead Agent（主 Agent）

入口：`make_lead_agent(config)` — 注册在 `langgraph.json` 里

组装过程：
1. 动态选模型：`create_chat_model()` — 支持 thinking/vision
2. 加载工具：`get_available_tools()` — 合并 sandbox + 内置 + MCP + 社区 + sub-agent 工具
3. 生成 system prompt：`apply_prompt_template()` — 注入 skills、memory、subagent 指令
4. 附加中间件链

### ThreadState（线程状态）

扩展了 `AgentState`，额外包含：

| 字段 | 说明 |
|------|------|
| `sandbox` | 当前沙箱实例 |
| `thread_data` | 线程目录信息 |
| `title` | 对话标题 |
| `artifacts` | 产物（去重） |
| `todos` | Plan Mode 任务列表 |
| `uploaded_files` | 上传的文件 |
| `viewed_images` | 查看过的图片 |

### 运行时配置（通过 config.configurable）

| 配置项 | 说明 |
|--------|------|
| `thinking_enabled` | 启用模型深度思考 |
| `model_name` | 选择特定 LLM 模型 |
| `is_plan_mode` | 启用 TodoList 中间件（Plan Mode） |
| `subagent_enabled` | 启用子 Agent 委派工具 |

---

## 六、中间件链（18 个）

严格按顺序组装，before_* 正序执行（0→N），after_* 反序执行（N→0）。

| # | 中间件 | 钩子 | 做什么 | 必须/可选 |
|---|--------|------|--------|----------|
| 1 | **ThreadDataMiddleware** | before_agent | 创建线程隔离目录。路径：`backend/.deer-flow/users/{user_id}/threads/{thread_id}/user-data/{workspace,uploads,outputs}`。无认证时 user_id 默认 "default" | 必须 |
| 2 | **UploadsMiddleware** | before_agent | 追踪并注入新上传的文件到对话 | 必须 |
| 3 | **SandboxMiddleware** | before + after | 获取沙箱，存 sandbox_id 到状态。唯一对称中间件（acquire/release） | 必须 |
| 4 | **DanglingToolCallMiddleware** | after_model | 处理中断的工具调用 — 用户中断后 AIMessage 有 tool_calls 但没有对应的 ToolMessage，注入占位符 | 必须 |
| 5 | **LLMErrorHandlingMiddleware** | after_model | 把 LLM 调用失败转为可恢复的错误消息 | 必须 |
| 6 | **GuardrailMiddleware** | before_tool | 工具调用前安全检查（可选，需 config 里 guardrails.enabled） | 可选 |
| 7 | **SandboxAuditMiddleware** | before_tool | 审计沙箱内的 shell/文件操作，安全日志 | 可选 |
| 8 | **ToolErrorHandlingMiddleware** | after_tool | 把工具异常转为错误 ToolMessage，不让整个运行崩溃 | 必须 |
| 9 | **SummarizationMiddleware** | after_model | 接近 token 上限时压缩上下文 | 可选 |
| 10 | **TodoListMiddleware** | after_model | Plan Mode 任务追踪，提供 write_todos 工具 | 可选（plan_mode） |
| 11 | **TokenUsageMiddleware** | after_agent | 记录 token 用量指标 | 可选 |
| 12 | **TitleMiddleware** | after_model | 第一轮对话后自动生成标题 | 必须 |
| 13 | **MemoryMiddleware** | after_agent | 异步队列，排队更新记忆 | 必须 |
| 14 | **ViewImageMiddleware** | before_model | 注入 base64 图片数据（仅 vision 模型） | 条件（vision） |
| 15 | **DeferredToolFilterMiddleware** | after_model | 隐藏延迟工具的 schema | 可选 |
| 16 | **SubagentLimitMiddleware** | after_model | 截断多余的 task 调用，强制最多 3 个并发 | 可选（subagent） |
| 17 | **LoopDetectionMiddleware** | after_model | 检测重复工具调用循环，强制停止 | 必须 |
| 18 | **ClarificationMiddleware** | after_model | 拦截 ask_clarification 调用，通过 Command(goto=END) 中断流程。**必须在最后** | 必须（最后） |

**关键理解**：
- 管道模型，不是洋葱模型
- 硬依赖：ThreadData 必须在 Sandbox 前；Clarification 必须在最后
- 大部分中间件只挂一个钩子

---

## 七、配置系统

### config.yaml

**配置版本管理**：
- `config.example.yaml` 有 `config_version` 字段
- 启动时比较用户版本 vs 示例版本，过时则警告
- 缺少 config_version = 版本 0
- `make config-upgrade` 自动合并缺失字段

**配置缓存与热加载**：
- `get_app_config()` 缓存解析后的配置
- 文件 mtime 变化时自动重载
- 不需要重启服务

**配置查找优先级**：
1. 显式 `config_path` 参数
2. `DEER_FLOW_CONFIG_PATH` 环境变量
3. 当前目录（backend/）的 config.yaml
4. 父目录（项目根）的 config.yaml — **推荐位置**

**环境变量**：`$` 开头的值会被解析为环境变量（如 `$OPENAI_API_KEY`）

### extensions_config.json

配置 MCP 服务器和技能，查找优先级同上（`DEER_FLOW_EXTENSIONS_CONFIG_PATH` 环境变量）。

### config.yaml 关键字段

| 字段 | 说明 |
|------|------|
| `models[]` | LLM 模型配置（use 类路径、supports_thinking、supports_vision 等） |
| `tools[]` | 工具配置（use 变量路径、group） |
| `tool_groups[]` | 工具逻辑分组 |
| `sandbox.use` | 沙箱提供者类路径 |
| `skills.path` / `skills.container_path` | 技能目录的主机路径和容器路径 |
| `title` | 自动标题生成 |
| `summarization` | 上下文压缩 |
| `subagents.enabled` | 子 Agent 开关 |
| `memory` | 记忆系统配置 |

---

## 八、Gateway API（app/gateway/）

FastAPI 应用，端口 8001，健康检查 `GET /health`。

### 路由一览

| 路由 | 功能 | 关键端点 |
|------|------|---------|
| `/api/models` | 模型管理 | 列表、详情 |
| `/api/mcp` | MCP 配置 | 读取、更新（保存到 extensions_config.json） |
| `/api/skills` | 技能管理 | 列表、详情、启用/禁用、安装 |
| `/api/memory` | 记忆管理 | 读取、重载、配置、状态 |
| `/api/threads/{id}/uploads` | 文件上传 | 上传（自动转 PDF/PPT/Excel/Word）、列表、删除 |
| `/api/threads/{id}` | 线程管理 | 删除（清理本地线程数据） |
| `/api/threads/{id}/artifacts` | 产物服务 | 下载文件（HTML/SVG 强制下载防 XSS） |
| `/api/threads/{id}/suggestions` | 建议生成 | 生成后续问题 |
| `/api/threads/{id}/runs` | 对话运行 | 创建、流式、等待、取消、消息列表 |
| `/api/threads/{id}/runs/{rid}/feedback` | 反馈 | 点赞/点踩、统计 |
| `/api/runs` | 无状态运行 | 流式、等待、消息列表 |

---

## 九、沙箱系统

### 接口与 Provider 模式

```
Sandbox（抽象接口）
├── execute_command()  # 执行命令
├── read_file()        # 读文件
├── write_file()       # 写文件
└── list_dir()         # 列目录

SandboxProvider（提供者模式）
├── acquire()   # 获取沙箱
├── get()       # 获取沙箱实例
└── release()   # 释放沙箱
```

### 两种实现

| 实现 | 路径 | 说明 |
|------|------|------|
| `LocalSandboxProvider` | `sandbox/local/` | 单例，本地文件系统，无隔离 |
| `AioSandboxProvider` | `community/aio_sandbox/` | Docker 容器隔离 |

### 虚拟路径系统

Agent 看到的路径 vs 实际物理路径：

| Agent 看到 | 实际路径 |
|-----------|---------|
| `/mnt/user-data/workspace` | `backend/.deer-flow/users/{user_id}/threads/{thread_id}/user-data/workspace` |
| `/mnt/user-data/uploads` | `.../user-data/uploads` |
| `/mnt/user-data/outputs` | `.../user-data/outputs` |
| `/mnt/skills` | `deer-flow/skills/` |

翻译函数：`replace_virtual_path()` / `replace_virtual_paths_in_command()`

### 沙箱工具

| 工具 | 说明 |
|------|------|
| `bash` | 执行命令（带路径翻译和错误处理） |
| `ls` | 列目录（树形，最多 2 层） |
| `read_file` | 读文件（支持行范围） |
| `write_file` | 写文件（支持追加，自动创建目录） |
| `str_replace` | 字符串替换（单次/全部）；按 (sandbox.id, path) 序列化保证并发安全 |

---

## 十、子 Agent 系统

### 执行引擎

- **双线程池**：`_scheduler_pool`(3 workers) + `_execution_pool`(3 workers)
- **最大并发**：3 个（由 SubagentLimitMiddleware 强制）
- **超时**：15 分钟
- **轮询间隔**：5 秒

### 执行流程

```
Lead Agent 调用 task() 工具
  → SubagentExecutor 创建
  → 后台线程执行子任务
  → 每 5 秒轮询状态
  → SSE 事件推送（task_started → task_running → task_completed/task_failed/task_timed_out）
  → 结果返回给 Lead Agent
```

### 内置 Agent

| Agent | 说明 |
|-------|------|
| `general-purpose` | 通用 Agent，拥有除 task 外的所有工具 |
| `bash` | 命令专家，专门执行 bash 命令 |

---

## 十一、工具系统

`get_available_tools()` 按优先级组装工具：

1. **配置工具** — 从 config.yaml 通过 `resolve_variable()` 加载
2. **MCP 工具** — 从启用的 MCP 服务器（懒加载 + mtime 缓存失效）
3. **内置工具**：
   - `present_files` — 展示输出文件给用户（仅 /mnt/user-data/outputs）
   - `ask_clarification` — 请求澄清（被 ClarificationMiddleware 拦截）
   - `view_image` — 读取图片为 base64（仅 vision 模型）
   - `setup_agent` — 创建新自定义 Agent
   - `update_agent` — 自定义 Agent 自我更新
4. **子 Agent 工具**：`task` — 委派任务

### 社区工具

| 工具 | 说明 |
|------|------|
| `tavily/` | Web 搜索（默认 5 结果）+ 网页抓取（4KB 限制） |
| `jina_ai/` | 通过 Jina Reader API 抓取网页 |
| `firecrawl/` | 通过 Firecrawl API 爬网页 |
| `image_search/` | DuckDuckGo 图片搜索 |

---

## 十二、MCP 系统

- 使用 `langchain-mcp-adapters` 的 `MultiServerMCPClient` 管理多服务器
- **懒加载**：首次使用时才加载工具（`get_cached_mcp_tools()`）
- **缓存失效**：通过 mtime 检测配置文件变化
- **传输方式**：stdio（命令行）、SSE、HTTP
- **OAuth**：支持 client_credentials 和 refresh_token 流程
- **运行时更新**：Gateway API 保存到 extensions_config.json，LangGraph 通过 mtime 检测

---

## 十三、技能系统

- **位置**：`deer-flow/skills/{public,custom}/`
- **格式**：目录 + `SKILL.md`（YAML frontmatter：name, description, license, allowed-tools）
- **加载**：`load_skills()` 递归扫描 SKILL.md，从 extensions_config.json 读启用状态
- **注入**：启用的技能列表写入 agent system prompt
- **安装**：`POST /api/skills/install` 解压 .skill ZIP 到 custom/

---

## 十四、模型工厂

`create_chat_model(name, thinking_enabled)` 工厂方法：

- 通过反射（resolve_variable）从配置实例化 LLM
- 支持 `thinking_enabled` 标志 + 每个模型的 `when_thinking_enabled` 覆盖
- 支持 vLLM 风格的 thinking 切换
- 支持 `supports_vision` 标志
- `$` 开头的配置值解析为环境变量
- 缺少 provider 模块时给出可操作的安装提示

---

## 十五、IM 渠道系统（app/channels/）

把外部消息平台（飞书、Slack、Telegram、钉钉）桥接到 DeerFlow Agent。

### 组件

| 组件 | 说明 |
|------|------|
| `message_bus.py` | 异步发布/订阅中心 |
| `store.py` | JSON 文件持久化（channel:chat → thread_id 映射） |
| `manager.py` | 核心调度器（创建线程、路由命令、流式/等待） |
| `base.py` | 抽象 Channel 基类 |
| `service.py` | 管理所有渠道的生命周期 |
| `slack.py` / `feishu.py` / `telegram.py` / `dingtalk.py` | 平台特定实现 |

### 消息流程

```
外部平台 → Channel 实现 → MessageBus.publish_inbound()
  → ChannelManager 消费队列
  → 创建/查找线程
  → 调用 agent（飞书用 stream 流式，Slack/Telegram 用 wait 等完成）
  → 发布出站消息 → channel 回调 → 平台回复
```

### 支持的命令

`/new`（新建对话）、`/status`（状态）、`/models`（模型列表）、`/memory`（记忆）、`/help`（帮助）

---

## 十六、记忆系统

### 组件

| 组件 | 说明 |
|------|------|
| `updater.py` | LLM 驱动的记忆提取和更新 |
| `queue.py` | 去抖动队列（30s、per-thread 去重） |
| `prompt.py` | 提取 prompt 模板 |
| `storage.py` | 文件存储，按用户隔离 |

### 按用户隔离

- 记忆存储在 `{base_dir}/users/{user_id}/memory.json`
- 无认证时 user_id 默认 "default"
- 每个 Agent 也有独立记忆：`{base_dir}/users/{user_id}/agents/{agent_name}/memory.json`

### 数据结构

| 字段 | 说明 |
|------|------|
| **User Context** | `workContext`、`personalContext`、`topOfMind`（1-3 句总结） |
| **History** | `recentMonths`、`earlierContext`、`longTermBackground` |
| **Facts** | 离散事实，包含 `id`、`content`、`category`（偏好/知识/上下文/行为/目标）、`confidence`(0-1)、`createdAt`、`source` |

### 工作流

```
1. MemoryMiddleware 过滤消息（只保留用户输入 + AI 最终回复）
2. 入队（带 user_id），去抖动 30s，per-thread 去重
3. 后台线程调 LLM 提取事实和上下文更新
4. 原子写入（temp file + rename），跳过重复事实
5. 下次对话注入 top 15 facts + context 到 <memory> 标签
```

### 配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `debounce_seconds` | 30 | 去抖动等待时间 |
| `max_facts` | 100 | 最大事实数 |
| `fact_confidence_threshold` | 0.7 | 事实置信度阈值 |
| `max_injection_tokens` | 2000 | 注入 prompt 的 token 上限 |

---

## 十七、嵌入式客户端（DeerFlowClient）

不启动 HTTP 服务，直接在进程内调用 agent。所有返回值与 Gateway API 的 Pydantic 模型对齐。

### 核心方法

| 方法 | 说明 |
|------|------|
| `chat(message, thread_id)` | 同步调用，返回最终 AI 文本 |
| `stream(message, thread_id)` | 流式调用，yield StreamEvent |

### StreamEvent 类型

| 类型 | 说明 |
|------|------|
| `"values"` | 完整状态快照（标题、消息、产物） |
| `"messages-tuple"` | 逐 chunk 更新（AI 文本是 delta，按 id 拼接） |
| `"custom"` | 自定义事件 |
| `"end"` | 流结束（带累计 token 用量） |

### 与 Gateway 等价的方法

模型、MCP、技能、记忆、上传、产物 — 所有 Gateway API 功能都有对应方法。

### 一致性测试

`TestGatewayConformance`（77 个单元测试）：每个客户端方法的返回值都通过 Gateway 的 Pydantic 模型校验。如果 Gateway 加了必填字段而客户端没提供，Pydantic 抛 ValidationError，CI 拦截。

---

## 十八、反射系统

动态加载模块：

| 函数 | 说明 | 示例 |
|------|------|------|
| `resolve_variable(path)` | 导入模块并返回变量 | `resolve_variable("deerflow.sandbox.tools:bash_tool")` |
| `resolve_class(path, base_class)` | 导入并校验类 | 确保是某个基类的子类 |

这是 config.yaml 里 `use: "deerflow.sandbox.local:LocalSandboxProvider"` 这种配置能工作的基础。

---

## 十九、关键特性

### 文件上传

- 端点：`POST /api/threads/{thread_id}/uploads`
- 支持自动转换：PDF、PPT、Excel、Word（通过 markitdown）
- 文件存储在线程隔离目录
- Agent 通过 UploadsMiddleware 接收文件列表

### Plan Mode

- 通过 `config.configurable.is_plan_mode = True` 控制
- 提供 `write_todos` 工具做任务追踪
- 一次一个 in_progress 任务，实时更新

### 上下文压缩

- 当接近 token 上限时自动触发
- 触发条件：token 数、消息数、占最大输入的比例
- 保留最近消息，压缩旧消息

### 视觉支持

- 模型配置 `supports_vision: true`
- ViewImageMiddleware 处理图片
- view_image 工具加入工具集
- 图片自动转 base64 注入状态

---

## 二十、命令速查

### 根目录

```bash
make check      # 检查系统要求（Node/pnpm/uv/nginx）
make install    # 安装所有依赖
make dev        # 启动所有服务（4 个进程）
make stop       # 停止所有服务
```

### 后端目录

```bash
make install    # 安装后端依赖
make dev        # 跑 Gateway（端口 8001，带热重载）
make gateway    # 只跑 Gateway（端口 8001）
make test       # 跑所有后端测试
make lint       # ruff 检查
make format     # ruff 格式化
```

### 所有启动模式

| | 本地前台 | 本地守护进程 | Docker 开发 | Docker 生产 |
|---|---|---|---|---|
| **开发** | `make dev` | `make dev-daemon` | `make docker-start` | — |
| **生产** | `make start` | `make start-daemon` | — | `make up` |

---

## 二十一、代码风格

- 用 `ruff` 做 lint 和 format
- 行宽：240 字符
- Python 3.12+，带类型注解
- 双引号，空格缩进
