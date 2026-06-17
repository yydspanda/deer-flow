<!-- project-overview progress: stage=done depth=full updated=2026-05-15 -->

# DeerFlow 2.0 项目总览

> 先看全貌，再钻细节。这份文档解决一个问题：**这个项目整体上在做什么？各部分怎么分工？怎么协作的？**

---

## 一句话概括

DeerFlow 2.0 是一个开源的 **Super Agent 开发框架**：你选模型，它提供完整的运行基础设施（Harness），你只写业务逻辑。

```
你的业务逻辑（SOUL.md + config.yaml）
        ↓
DeerFlow 2.0（Harness 层：编排、工具、记忆、安全、沙箱...）
        ↓
任何兼容的大模型（OpenAI / Claude / DeepSeek / vLLM / MindIE...）
```

---

## 项目结构鸟瞰

```
deer-flow/
├── config.yaml                         ← 主配置（gitignored，从 config.example.yaml 复制）
├── extensions_config.json              ← MCP 服务器 + Skills 配置（gitignored）
├── skills/public/                      ← 21 个内置技能
├── backend/                            ← Python 后端
│   ├── packages/harness/deerflow/      ← ★ 核心框架包（import deerflow.*）
│   ├── app/                            ← 应用层（import app.*）
│   ├── tests/                          ← 165 个测试文件
│   └── docs/                           ← 后端文档
├── frontend/                           ← Next.js 16 前端（React 19 + TypeScript）
├── docker/                             ← nginx + Docker Compose + provisioner
├── scripts/                            ← 19 个运维脚本
├── Makefile                            ← 统一命令入口（275 行）
└── .github/workflows/                  ← 5 个 CI workflow
```

---

## 架构分层

```
                          用户
                           │
                    ┌──────▼──────┐
                    │   nginx     │  :2026  统一入口、反向代理、SSE 流式
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
      ┌───────▼──────┐ ┌──▼─────────┐ ┌▼────────────┐
      │  Frontend    │ │  Gateway   │ │  (embedded)  │
      │  Next.js     │ │  FastAPI   │ │  Agent       │
      │  :3000       │ │  :8001     │ │  Runtime     │
      └──────────────┘ └─────┬──────┘ └──────┬───────┘
                              │               │
                    ┌─────────▼───────────────▼┐
                    │  Harness (deerflow.*)      │  ← 核心框架
                    │  Agent 编排 + 工具 + 记忆  │
                    │  + 沙箱 + 安全 + 配置       │
                    └────────────┬───────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  大模型 API              │
                    │  (OpenAI/Claude/DeepSeek │
                    │   /vLLM/MindIE/...)      │
                    └─────────────────────────┘
```

| 进程 | 端口 | 框架 | 职责 | 启动 |
|------|------|------|------|------|
| nginx | 2026 | C | 统一入口，路由分发，SSE 流式 | `make dev` |
| Frontend | 3000 | Next.js 16 + React 19 | 页面渲染，用户交互 | `make dev` |
| Gateway | 8001 | FastAPI | API 路由、认证、文件上传、Memory CRUD | `make dev` |
| Agent Runtime | (embedded in Gateway) | LangGraph | Agent 编排循环、工具调用、流式输出 | `make dev` |

> **注意**：DeerFlow 2.0 把 Agent Runtime 嵌入 Gateway 进程，不再独立跑 LangGraph Server。`langgraph.json` 仅用于 LangGraph Studio。

### nginx 路由规则

```
/api/langgraph/*   → Gateway(:8001) embedded agent runtime
/api/*             → Gateway(:8001) FastAPI 路由
/*                 → Frontend(:3000)
```

---

## Backend 分层边界（铁律）

```
app.*（应用层：Gateway API + IM 渠道）
  ↓ 可以 import
deerflow.*（框架层 / harness：可发布的 Python 包）
  ↑ 绝对不能 import app.*
  （CI: tests/test_harness_boundary.py 用 AST 扫描强制检查）
```

**为什么这样分**：
- `deerflow.*` 是可发布的 Python 包，可以被任何项目复用
- `app.*` 是 DeerFlow 特有的业务代码（API 路由、认证、渠道集成）
- 单向依赖保证框架的独立性和可测试性

---

## Harness 层（`deerflow.*`）— 19 个子系统

| 目录 | 职责 |
|------|------|
| `agents/lead_agent/` | Agent 入口：`make_lead_agent()` + system prompt |
| `agents/middlewares/` | 18 个中间件（管道模型） |
| `agents/memory/` | 记忆提取、队列、存储 |
| `sandbox/` | 沙箱执行环境（Local/Docker/Provisioner） |
| `tools/` | 工具组装 + 内置工具（8 个） |
| `subagents/` | Sub-Agent 异步执行引擎 |
| `models/` | 模型工厂 + 8 个 Provider |
| `mcp/` | MCP 协议集成 |
| `config/` | 27 个配置 schema 模块 |
| `skills/` | 技能发现、解析、安装、安全扫描 |
| `guardrails/` | 工具调用前授权 |
| `persistence/` | SQLAlchemy 数据持久化 + Alembic 迁移 |
| `uploads/` | 文件上传管理 |
| `runtime/` | RunManager + StreamBridge + Checkpointer |
| `reflection/` | 动态加载（字符串→代码对象） |
| `tracing/` | LangSmith + Langfuse 追踪 |
| `community/` | 9 个社区工具 Provider |
| `utils/` | 工具函数 |
| `client.py` | 嵌入式 Python Client |

---

## App 层（`app.*`）— 2 大模块

| 目录 | 职责 |
|------|------|
| `gateway/` | FastAPI 应用 + 16 个 API router + 认证 + CSRF |
| `channels/` | 7 个 IM 渠道集成（飞书/Telegram/Slack/钉钉/微信/企业微信/Discord） |

---

## 中间件链（18 个，管道模型）

**管道模型，不是洋葱模型**。大部分中间件只挂一个钩子。

```
请求 → before_agent（正序 0→N）: ThreadData → Uploads → Sandbox(acquire)
     → LLM 调用
     → after_model（反序 N→0）: LoopDetection → Title → Clarification
     → 工具执行（如有 tool_calls）
     → after_agent（反序 N→0）: LLMError → Memory → Sandbox(release)
     → 返回响应
```

硬依赖只有两处：ThreadData 必须在 Sandbox 之前；Clarification 必须在最后。

---

## 四种模式

| | Flash | Thinking | Pro | Ultra |
|--|-------|----------|-----|-------|
| 模型 | 默认 | 默认 + thinking | 默认 + thinking | 默认 + thinking |
| Plan | 无 | 无 | Todo List | Todo List |
| 执行 | ReAct | ReAct（更深） | 按 Todo 逐步 | Lead→Sub-Agent 并行 |
| 并发 | 无 | 无 | 无 | Sub-Agent 最多 3 并发 |

---

## 核心数据流

### 一条用户消息的完整旅程

```
1. 用户在浏览器输入消息
2. Frontend: useThreadStream() 构建 messages 数组
   → thread.submit({messages: [HumanMessage]}, {config: {thinking_enabled, is_plan_mode, ...}})
   → LangGraph SDK 发 POST /api/langgraph/threads/{id}/runs/stream
   [frontend/src/core/threads/hooks.ts:518-563]

3. nginx: rewrite /api/langgraph/* → /api/* → proxy_pass Gateway(:8001)
   [docker/nginx/nginx.local.conf:108-141]

4. Gateway: stream_run() 路由处理
   → start_run() 创建 RunRecord + asyncio.create_task(run_agent(...))
   [app/gateway/routers/thread_runs.py:124-149]
   [app/gateway/services.py:248-353]

5. Worker: run_agent() 在后台 Task 中执行
   → agent_factory(config) = make_lead_agent(config)
   → agent.astream(graph_input, stream_mode=["messages","custom"])
   [deerflow/runtime/runs/worker.py:128-411]

6. make_lead_agent() 组装 agent
   → create_chat_model() 选模型
   → get_available_tools() 加载工具
   → _build_middlewares() 组装中间件链
   → apply_prompt_template() 组装 system prompt（含 memory + skills）
   [agents/lead_agent/agent.py:438-571]

7. 中间件链执行：
   ┌─ before_agent（正序）──────────────────────────┐
   │ ThreadData: 绑定线程目录，设置工作路径            │
   │ Uploads: 处理上传文件引用                        │
   │ Sandbox: acquire 沙箱资源（lazy，首次工具调用时）│
   │ DynamicContext: 注入日期 + memory 提醒           │
   │ Todo: (Plan mode) 注入 write_todos 工具         │
   └─────────────────────────────────────────────────┘
                         ↓
   ┌─ LLM 调用 ─────────────────────────────────────┐
   │ Summarization: (可选) 压缩上下文                 │
   │ DeferredToolFilter: (可选) 隐藏延迟工具          │
   │ 模型返回 AIMessage（可能含 tool_calls）          │
   └─────────────────────────────────────────────────┘
                         ↓
   ┌─ after_model（反序）────────────────────────────┐
   │ LoopDetection: 检查是否死循环（hash tool_calls） │
   │ Clarification: 拦截需要澄清的请求（必须最后）     │
   └─────────────────────────────────────────────────┘
                         ↓（有 tool_calls → 工具执行，回到 LLM）
                         ↓（无 tool_calls → 继续）
   ┌─ after_agent（反序）────────────────────────────┐
   │ Title: 第一轮自动生成对话标题                     │
   │ Memory: 异步触发记忆提取（入队 30s debounce）    │
   │ Sandbox: release 沙箱资源                       │
   └─────────────────────────────────────────────────┘

8. StreamBridge: publish() 每个 chunk → asyncio.Condition 通知 SSE consumer
   [deerflow/runtime/stream_bridge/memory.py:75-132]

9. SSE consumer: bridge.subscribe() → format_sse() → SSE 帧 → nginx 透传 → Frontend
   [app/gateway/services.py:356-387]
```

### 工具调用（bash 命令为例）

```
1. LLM 返回 AIMessage(tool_calls=[{name:"bash", args:{command:"ls -la"}}])

2. LangGraph ToolNode 按 name 分发到 bash_tool

3. 中间件包装（外→内）：
   SandboxAuditMiddleware → classify_command() 三级分类
     block → 返回错误 ToolMessage（不执行）
     warn  → 执行 + 追加警告
     pass  → 正常执行
   ToolErrorHandlingMiddleware → try/except，异常转 ToolMessage 错误

4. bash_tool 执行：
   → ensure_sandbox_initialized() 懒获取沙箱
   → replace_virtual_paths() 映射虚拟路径→真实路径
     /mnt/user-data/workspace → .deer-flow/threads/{thread_id}/
   → sandbox.execute_command(command)
   → mask_local_paths() 真实路径换回虚拟路径
   → truncate(output, 20K chars)
   [sandbox/tools.py:1235-1281]

5. ToolMessage 返回 → state["messages"] → LLM 再次调用
```

### Sub-Agent 委派（Ultra 模式）

```
1. Lead Agent 调用 task(description="调研 X", prompt="详细说明...")
   [tools/builtins/task_tool.py:151-392]

2. SubagentExecutor.execute_async() → 提交到 _scheduler_pool
   ThreadPoolExecutor(max_workers=3)，最多 3 个并发 Sub-Agent
   writer({"type":"task_started"}) 发 SSE 事件

3. 后台：独立 asyncio.EventLoop → _create_agent()（仅 4 个中间件）
   → agent.astream() 执行子任务 → 结果存入全局 dict

4. 主线程轮询：每 5s 查一次 → task_running / task_completed SSE 事件

5. 结果作为 ToolMessage 返回给 LLM → Lead Agent 继续决策
```

### Memory 提取与注入

```
写入路径：
  MemoryMiddleware.after_agent()
    → filter_messages() 只保留 HumanMessage + 最终 AIMessage（省 token）
    → detect_correction/reinforcement() 检测信号
    → queue.add() 入队（30s debounce，去重）
      ↓
  MemoryUpdater.update_memory()
    → 加载当前 memory.json + 格式化对话
    → model.invoke() LLM 提取/更新记忆
    → LLM 返回 JSON: {workContext, newFacts, factsToRemove}
    → _apply_updates() 合并：新增 facts、删除 facts、更新 workContext
    → save() 原子写入（temp file + rename）

读取路径：
  DynamicContextMiddleware.before_agent()
    → _get_memory_context() 读 memory.json
    → format_memory_for_injection() 取 top 15 facts
    → 注入 <memory>workContext + facts</memory>
    → ID swap trick 插入到用户消息前面
```

---

## 15 大功能子系统

### 1. 工具系统（Tool System）

```
get_available_tools() 四源合并（按优先级去重）：
  config.yaml 工具 > 内置工具 > MCP 工具 > ACP 工具
```

动态加载：`resolve_variable("module.path:var")` 字符串→代码对象。异步工具自动包装同步 wrapper。LocalSandbox 激活时隐藏 host bash 工具。去重按 `.name`，config 工具优先。

### 2. 沙箱系统（Sandbox）

```
Provider 模式：acquire(thread_id) → get(id) → release(id)
  LocalSandbox: 直接跑在宿主机，虚拟路径映射
    /mnt/user-data/workspace → .deer-flow/threads/{thread_id}/
  DockerSandbox: 容器隔离
  SandboxMiddleware: 唯一对称中间件（before acquire + after release）
  FileOperationLock: 按 (sandbox_id, path) 并发锁（WeakValueDictionary）
```

7 个沙箱工具：bash、ls、glob、grep、read_file、write_file、str_replace。

### 3. 记忆系统（Memory）

```
写入：对话 → filter(省token) → queue.add(30s debounce, 去重)
  → MemoryUpdater → model.invoke() LLM 提取 JSON
  → _apply_updates() 合并 → 原子写入（temp + rename）

读取：DynamicContextMiddleware → load memory.json → top 15 facts
  → <memory>workContext + facts</memory> 注入 system prompt
```

数据结构：`{user: {workContext, personalContext, topOfMind}, facts: [{id, content, category, confidence}]}`

correction/reinforcement 信号：不是代码层覆盖/加强，是给 LLM prompt 加 "IMPORTANT: 用户纠正/肯定了"，LLM 自己决定存什么删什么。

### 4. Sub-Agent 系统

```
task_tool → SubagentExecutor.execute_async()
  → _scheduler_pool(3线程) 限制并发
  → _isolated_subagent_loop 独立 asyncio.EventLoop（daemon 线程）
  → _create_agent() 精简中间件（ThreadData + Sandbox + Guardrail + ToolError）
  → 协作式取消（cancel_event per astream chunk）
  → 主线程 5s 轮询 get_background_task_result()
  → SSE: task_started / task_running / task_completed
```

15 分钟默认超时。task 工具在 denylist 中防止递归嵌套。

### 5. 模型工厂（Model Factory）

```
create_chat_model() 工厂方法
  → resolve_class(config.use) 动态加载 Provider
  → thinking 模式按 Provider 差异化：
    Anthropic: native thinking param
    OpenAI: extra_body.thinking
    vLLM: chat_template_kwargs
  → 自动加载 Claude Code / Codex CLI 凭证
  → 附加 tracing callbacks
```

8 个 Provider：OpenAI、Claude、DeepSeek、MiniMax、Codex、vLLM、MindIE、Ollama。

### 6. 配置系统（Config）

```
get_app_config() 单例 + mtime 热加载
  查找优先级：显式参数 > DEER_FLOW_CONFIG_PATH 环境变量 > 当前目录 > 父目录
  $VAR 环境变量递归解析
  config_version 版本追踪（make config-upgrade 合并新字段）
  ContextVar 覆盖机制（测试用）
  27 个子配置 schema
```

热加载机制：每次 `get_app_config()` 检查文件 mtime，变化则重新解析。

### 7. 技能系统（Skills）

```
SKILL.md YAML frontmatter → Skill 对象
  allowed-tools 白名单 → ToolPolicy 过滤工具
  安装：ZIP → safe_extract(拒绝 path traversal/symlink/zip bomb)
    → LLM 安全扫描(allow/warn/block)，失败默认 block
```

关键规则：一旦任何技能声明了 `allowed-tools`，没有声明的技能贡献零工具。

### 8. 安全护栏（Guardrails）

```
GuardrailMiddleware → wrap_tool_call 拦截每个工具调用
  GuardrailProvider.evaluate() → allow/deny
  AllowlistProvider: 简单白名单/黑名单
  deny → 返回错误 ToolMessage（不是异常），LLM 可以自适应
  fail_closed=True: Provider 崩溃 = 拒绝
```

### 9. 持久化（Persistence）

```
SQLAlchemy async engine + Alembic 迁移
  SQLite: WAL 模式 + 外键约束（默认）
  PostgreSQL: asyncpg + 连接池 + 自动建库（生产）
  memory: 纯内存（测试用），所有 repo 必须判 None
```

### 10. 文件上传（Uploads）

```
per-thread 目录：users/{uid}/threads/{tid}/user-data/uploads/
  normalize_filename() 剥目录 + 防穿越
  O_NOFOLLOW 防符号链接攻击（POSIX）
  claim_unique_filename() 碰撞加 _N 后缀
  markitdown 自动转换（PDF/DOCX/PPTX → Markdown）
```

### 11. 嵌入式 Client（DeerFlowClient）

```
DeerFlowClient: 不起 HTTP 也能调 agent（进程内）
  stream() 同步生成器，双去重（seen_ids + streamed_ids）
  chat() 累积 delta → 返回最终 AI 消息文本
  agent 按 config key 懒创建 + 缓存
  API 兼容 Gateway（list_models, get_memory, install_skill...）
```

不是 Gateway 的 wrapper，是并行的同步进程内消费者。77 个单元测试保证 API 一致性。

### 12. MCP 集成

```
ExtensionsConfig.from_file() 读 extensions_config.json
  → MultiServerMCPClient(stdio/sse/http)
  → 工具缓存 + mtime 失效检测
  → OAuth 拦截器支持
  → 可注册到 DeferredToolRegistry 懒加载
```

每次 `get_available_tools()` 都从磁盘读最新配置，确保 Gateway API 修改能立即生效。

### 13. 追踪（Tracing）

```
build_tracing_callbacks() 工厂
  → LangSmith: LangChainTracer(project=...)
  → Langfuse: LangfuseCallbackHandler(单例)
  → 附加到每个 model 实例
  初始化失败 = RuntimeError（fail-loud，不像 Guardrails 的 fail-closed）
```

### 14. Gateway（app/gateway/）

```
FastAPI + lifespan 管理
  AuthMiddleware: JWT cookie → request.state.user + ContextVar
  CSRFMiddleware: 双重提交 token
  start_run(): validate model → create RunRecord → asyncio.create_task(run_agent)
  SSE: StreamBridge publish → subscribe → format_sse → nginx 透传
  Last-Event-ID 重放支持
  16 个 API router
```

configurable/context 双路径兼容 LangGraph 新旧版本。

### 15. IM 渠道（app/channels/）

```
Channel(ABC) → InboundMessage → MessageBus(asyncio.Queue)
  → ChannelManager dispatch → LangGraph SDK → OutboundMessage → Channel.send()
  三层配置覆盖：default_session > channel_layer > user_layer
  Semaphore 限制并发(max 5)
  7 个平台：飞书/Telegram/Slack/钉钉/微信/企业微信/Discord
```

流式更新最小间隔 0.35s，防止 IM 频率限制。

---

## 关键设计模式

| 模式 | 哪里用了 |
|------|---------|
| **动态加载 (resolve_variable/resolve_class)** | 工具、模型、沙箱 Provider、护栏 Provider、MCP 拦截器、渠道类 |
| **单例 + 懒初始化** | Config、Paths、沙箱 Provider、MCP 缓存、Memory 存储/队列、Tracing |
| **工厂方法** | 模型工厂、Tracing 工厂、Agent 工厂 |
| **Provider/Strategy** | 沙箱(Local/Docker)、护栏(Allowlist/自定义)、持久化(SQLite/Postgres/memory)、Tracing(LangSmith/Langfuse) |
| **中间件/管道** | 18 个 Agent 中间件、Auth 中间件、CSRF 中间件 |
| **Debounce Queue** | Memory 更新队列 |
| **原子写入 (temp+rename)** | Memory 存储、Client 配置、Upload |
| **Cache-Aside + mtime** | Config 热加载、MCP 工具缓存、Memory 存储 |
| **线程池 sync/async 桥接** | 工具 sync wrapper、Memory updater、Sub-Agent scheduler、MCP lazy init |

---

## 注意事项（Gotchas）

| 坑 | 影响 | 原因 |
|----|------|------|
| Event Loop 地狱 | 死锁或 httpx 连接池冲突 | 工具/Memory/Sub-Agent/MCP 各有自己的线程池和事件循环桥接策略 |
| 单例重置顺序 | 运行时状态错乱 | Config → 子配置 → checkpointer → store 必须按序重置 |
| 虚拟路径泄漏 | 真实路径暴露给 LLM | 错误消息/堆栈/二进制输出可能绕过 mask_local_paths |
| 工具名不一致 (#1803) | "not a valid tool" 错误 | config name vs tool .name 可以不一致，只 log 不强制 |
| 协作式取消局限 | Sub-Agent 10分钟工具调用无法中断 | 取消只在 astream chunk 边界检查，默认超时 900s |
| 跨进程配置一致性 | Gateway 和 Agent 间的配置有短暂不一致 | 靠文件 mtime 检测，有竞态窗口 |
| Memory debounce 丢数据 | 进程崩溃 30s 内的记忆丢失 | Timer 是 daemon 线程，进程退出时不等 |
| 中间件隐式依赖 | Sandbox 拿不到目录 | ThreadData 必须在 Sandbox 之前，编译时看不出来 |

---

## 共享状态

| 资源 | 位置 | 消费者 |
|------|------|--------|
| `AppConfig` 单例 | `config/app_config.py` | 所有子系统 |
| `Paths` 单例 | `config/paths.py` | Sandbox、Uploads、Memory、Persistence |
| `SandboxProvider` 单例 | `sandbox/sandbox_provider.py` | Sandbox 中间件、Tools |
| MCP 工具缓存 | `mcp/cache.py` | Tool 系统 |
| Memory 存储单例 | `agents/memory/storage.py` | Memory updater、Client |
| Memory 队列单例 | `agents/memory/queue.py` | Memory 中间件 |
| 后台任务 dict | `subagents/executor.py` | Sub-Agent executor |
| 文件操作锁 | `sandbox/file_operation_lock.py` | str_replace 工具 |
| 持久化引擎 | `persistence/engine.py` | Gateway、Auth |

---

## 外部依赖

| 依赖 | 用途 | 版本/要求 |
|------|------|----------|
| Python | 后端语言 | 3.12+ |
| uv | Python 包管理 + workspace | 替代 pip |
| LangGraph | Agent 编排框架 | 核心依赖 |
| LangChain | 工具、模型抽象 | 核心依赖 |
| FastAPI | Gateway API 框架 | 核心依赖 |
| SQLAlchemy + Alembic | ORM + 迁移 | async 引擎 |
| Node.js | 前端运行时 | 22+ |
| pnpm | 前端包管理 | 10.26.2 |
| Next.js | 前端框架 | 16 |
| React | UI 框架 | 19 |
| nginx | 反向代理 + SSE 流式 | 必需 |
| Docker | 沙箱隔离（可选） | 可选 |
| SQLite / PostgreSQL | 数据持久化 | 自动选择 |

---

## 测试体系

```
backend/tests/                    ← 165 个测试文件
  ├── test_*_middleware.py         中间件测试（每种中间件独立测试）
  ├── test_*_executor.py           执行引擎测试
  ├── test_*_config.py             配置解析测试
  ├── test_*_router.py             API 路由测试
  ├── test_*_channel.py            渠道集成测试
  ├── test_harness_boundary.py     ★ CI 边界检查（AST 扫描）
  ├── test_client.py               77 个 Client 单元测试 + Gateway 一致性测试
  └── ...
```

```bash
cd backend && PYTHONPATH=. uv run pytest tests/ -v              # 全部后端测试
cd backend && PYTHONPATH=. uv run pytest tests/test_foo.py -v   # 单个文件
cd backend && uv run ruff check .                                 # Lint
```

---

## 从总览到深入学习

### 推荐路径

```
第 1 步：跑通 → make dev，体验四种模式（Flash/Thinking/Pro/Ultra）
第 2 步：追数据流 → 用户消息 → nginx → Gateway → make_lead_agent() → 中间件 → 响应
第 3 步：逐模块深入 → 用 /codebase-learning 按依赖顺序学每个子系统
第 4 步：理解设计模式 → 动态加载 / Provider / 中间件管道 / Debounce Queue
第 5 步：动手仿写 → 用 LangGraph 从零写一个 mini agent framework
```

下一步：使用 `/codebase-learning` 逐模块深入学习，或用 `/annotate-code` 给关键文件加注释，或用 `/tech-explain` 深度理解某个概念。

### 关键入口文件

| 想理解 | 从这个文件开始 |
|--------|-------------|
| 整体入口 | `backend/langgraph.json` → `deerflow.agents:make_lead_agent` |
| Agent 组装 | `agents/lead_agent/agent.py` |
| System Prompt | `agents/lead_agent/prompt.py` |
| 中间件链 | `agent.py` 里的 `_build_middlewares()` |
| 工具加载 | `tools/tools.py` 的 `get_available_tools()` |
| 模型创建 | `models/factory.py` 的 `create_chat_model()` |
| 沙箱执行 | `sandbox/tools.py` 的 `bash_tool()` |
| 记忆提取 | `agents/memory/updater.py` |
| Sub-Agent | `subagents/executor.py` |
| 配置总入口 | `config/app_config.py` |
| Gateway API | `app/gateway/app.py` |
| 渠道集成 | `app/channels/manager.py` |
| 嵌入式 Client | `client.py` |
| 动态加载 | `reflection/__init__.py` |
| 安全护栏 | `guardrails/middleware.py` |
