# DeerFlow 2.0 学习路径

> 前提文档：`.notes/project-overview.md`
> 架构图：`.notes/reference/architecture-diagram.md`（增量更新，每完成一个模块追加）
> 设计模式：`.notes/reference/design-patterns.md`（Part 1: AI Agent 核心模式 8 个 + Part 2: 通用模式 4 个）

## 模块清单

| # | 模块 | 核心目录 | 状态 |
|---|------|---------|------|
| 1 | Agent 构建 | `agents/lead_agent/` | ✅ |
| 2 | 中间件链 | `agents/middlewares/` | ✅ |
| 3 | 工具系统 | `tools/` | ✅ |
| 4 | Sandbox 沙箱 | `sandbox/` | ✅ |
| 5 | Memory 记忆 | `agents/memory/` | ✅ |
| 6 | Sub-Agent | `subagents/` | ✅ |
| 7 | Guardrails 安全护栏 | `guardrails/` | ✅ |
| 8 | 模型工厂 | `models/` | ✅ |
| 9 | Skills 技能系统 | `skills/` | ✅ |
| 10 | Persistence 持久化 | `persistence/` | ✅ 快速通过 |
| 11 | Uploads 文件上传 | `uploads/` | ✅ 快速通过 |
| 12 | 配置系统 | `config/` | ✅ |
| 13 | 流式输出 / Client | `client.py` + `runtime/` | ✅ |
| 14 | MCP 集成 | `mcp/` | ✅ |
| 15 | Gateway + Auth | `app/gateway/` | ✅ 快速通过 |
| 16 | IM Channels | `app/channels/` | ✅ |
| 17 | Tracing | `tracing/` | ✅ 快速通过 |
| 18 | Reflection 动态加载 | `reflection/` | ✅ 快速通过 |

## 进度日志

### Agent 构建 — 2026-05-15
- 核心文件：`agents/lead_agent/agent.py`、`agents/lead_agent/prompt.py`、`agents/thread_state.py`
- 对话 check：通过
- 架构图更新：待画
- 设计模式：Agent 中间件拦截链(#1)

### 中间件链 — 2026-05-15
- 核心文件：`agents/middlewares/` 下 18 个中间件源码
- 对话 check：通过（含 TodoMiddleware、SummarizationMiddleware、LLMErrorHandling 详细讲解）
- 架构图更新：待画
- 设计模式：Agent 中间件拦截链(#1)、压缩前记忆抢救(#3)

### 配置系统 — 2026-05-15
- 核心文件：`config/app_config.py`、`config/paths.py`
- 对话 check：通过（热重载机制已深度掌握，写了 `.notes/hot-reload-mechanism.md`）
- 架构图更新：待画
- 设计模式：线程安全懒单例(#11)、ContextVar 请求隔离(#12)

### 工具系统 — 2026-05-18
- 核心文件：`tools/` 下 11 个文件
  - types.py（Runtime 泛型定义）
  - tools.py（★ 四源合并 + 条件加载）
  - sync.py（async→sync 桥接）
  - builtins/present_file_tool.py（最简内置工具）
  - builtins/clarification_tool.py（追问工具 + 中间件配合）
  - builtins/task_tool.py（★ Sub-Agent 委派，最复杂，437 行）
  - builtins/tool_search.py（延迟工具发现 + DeferredToolRegistry）
  - builtins/view_image_tool.py（条件加载，需 vision 模型）
  - builtins/setup_agent_tool.py（bootstrap 模式创建自定义 Agent）
  - builtins/update_agent_tool.py（自定义 Agent 自我更新）
  - skill_manage_tool.py（技能 CRUD，懒加载防循环依赖）
- 对话 check：通过（四源合并/条件加载/延迟发现/sync桥接/递归防护）
- 架构图更新：已画
- 设计模式：延迟工具发现(#2)、ContextVar 请求隔离(#12)

### Memory 记忆 — 2026-05-20
- 核心文件：`agents/memory/` 下 7 个文件
  - `__init__.py`（模块索引 + 全流程串讲：两条入口→三步走→一条出口）
  - `message_processing.py`（消息过滤 + correction/reinforcement 信号检测）
  - `queue.py`（30s 去抖动队列 + add vs add_nowait）
  - `prompt.py`（MEMORY_UPDATE_PROMPT 模板 + format_memory_for_injection token 预算）
  - `updater.py`（★★★ 核心三步走：prepare→invoke→finalize，_apply_updates 合并逻辑）
  - `storage.py`（FileMemoryStorage 缓存 + 原子写入 .tmp→rename）
  - `summarization_hook.py`（压缩前抢救钩子，add_nowait(0s)）
- 四层法注释：全部完成
- 对话 check：通过（全流程/add vs add_nowait/_apply_updates 覆盖 vs 增删/token 预算）
- 辅助文档：`.notes/modules/memory-flow-guide.md`（Mermaid 流程图 + 6 个 Q&A）
- 架构图更新：已追加
- 设计模式：压缩前记忆抢救(#3)、信号驱动记忆更新(#4)、Token 预算分配(#5)、去抖动队列(#9)、原子文件写入(#10)、线程安全懒单例(#11)

### Sub-Agent — 2026-05-21
- 核心文件：`subagents/` 下 8 个文件（1269 行）
  - `__init__.py`（模块索引 + 全流程串讲，四层法 docstring）
  - `config.py`（SubagentConfig 数据类 + 模型解析三级优先级）
  - `registry.py`（★★ 注册表，三层配置合并：内置 → custom_agents → per-agent override）
  - `executor.py`（★★★ 核心执行器 826 行：线程池 + 持久化 event loop + astream + 协作式取消）
  - `token_collector.py`（Sub-agent token 用量收集，LangChain Callback）
  - `builtins/general_purpose.py`（通用 sub-agent，tools=None 继承所有）
  - `builtins/bash_agent.py`（Bash sub-agent，只有 sandbox 五件套）
  - `builtins/__init__.py`（BUILTIN_SUBAGENTS dict 注册）
- 四层法注释：全部完成
- 对话 check：通过（递归防护/工具集区别/isolated loop/协作式取消/配置覆盖优先级）
- 架构图更新：已追加
- 设计模式：Sub-Agent 委派隔离(#6)、三层 Agent 配置(#8)、线程安全懒单例(#11)

### 模型工厂 — 2026-05-29
- 核心文件：`models/` 下 10 个文件（2237 行）
  - `factory.py`（★★★ 核心工厂：配置解析 + 思维模式切换 + 实例化）
  - `credential_loader.py`（★★ API Key 加载：环境变量/文件/OAuth）
  - `patched_openai.py`（Gemini thought_signature 补丁）
  - 其余 provider 按需看（适配器模式）
- 四层法注释：factory.py 完成
- 对话 check：通过（工厂流程/思维模式参数切换/适配器模式用途）

### Guardrails 安全护栏 — 2026-05-28
- 核心文件：`guardrails/` 下 4 个文件（207 行）
  - `__init__.py`（模块索引 + 公共 API 导出）
  - `provider.py`（Protocol 接口 + GuardrailRequest/Decision/Reason 数据结构）
  - `builtin.py`（AllowlistProvider 白名单/黑名单实现）
  - `middleware.py`（GuardrailMiddleware 拦截 wrap_tool_call + fail_closed）
- 四层法注释：全部完成
- 对话 check：通过（Protocol vs 实现/白名单优先/fail_closed/GraphBubbleUp 透传/条件加载）
- 设计模式：护栏自适应拒绝(#7)
- 额外学习：ContextVar 完整链路（auth_middleware 写入 → resolve_user_id 读取 → Sub-Agent copy_context 跨线程 → Memory 队列提前捕获）

### Sandbox 沙箱 — 2026-05-18
- 核心文件：`sandbox/` 下 9 个文件
  - sandbox.py（抽象基类，7 个操作）
  - sandbox_provider.py（工厂 + 单例，acquire/get/release）
  - middleware.py（SandboxMiddleware 懒初始化）
  - tools.py（★★★ 1600 行，7 个 @tool + 路径安全 + 虚拟路径翻译）
  - security.py（local 模式禁 bash 开关）
  - exceptions.py / search.py / file_operation_lock.py（辅助）
  - local/local_sandbox.py（本地沙箱实现）
- 学习方式：应用方视角，不深挖实现细节
- 对话 check：通过（7 个能力/local vs Docker/lazy_init/虚拟路径映射/str_replace）
- 架构图更新：已追加
- 设计模式：线程安全懒单例(#11)

### Skills 技能系统 — 2026-05-29
- 核心文件：`skills/` 下 9 个文件（~1250 行）
  - `__init__.py`（模块索引 + 建议阅读顺序）
  - `types.py`（Skill 数据类 + SkillCategory 枚举 + 容器路径映射）
  - `parser.py`（★ SKILL.md 解析：正则提取 YAML → safe_load → 构造 Skill）
  - `validation.py`（frontmatter 验证：白名单字段 + kebab-case + 防 XSS）
  - `tool_policy.py`（allowed-tools 并集过滤 + Protocol 鸭子类型）
  - `installer.py`（★★ ZIP 安装：5 道安全防线 + 原子部署 + 同步/异步桥接）
  - `security_scanner.py`（★★ LLM 安全审查：语义级提示注入检测 + fail-closed）
  - `storage/skill_storage.py`（抽象基类 + 模板方法 load_skills）
  - `storage/local_skill_storage.py`（本地文件系统实现 + 原子写入）
  - `storage/__init__.py`（单例工厂 + 反射创建）
- 四层法注释：全部完成
- 对话 check：通过（技能如何赋能 Agent / allowed-tools 并集策略 / ZIP 安装 5 道防线 / fail-closed 4 处体现）
- 学习结论：模块属基础设施层（安装工程），非 Agent 核心编排逻辑

### Persistence 持久化 — 2026-05-29
- 核心文件：`persistence/` 下 21 个文件（~1787 行）
  - `engine.py`（异步引擎管理：SQLite/Postgres/Memory 三后端）
  - `base.py`（DeclarativeBase + auto to_dict）
  - `json_compat.py`（跨方言 JSON 查询：SQLite json_extract vs Postgres ->>）
  - 5 张表：threads_meta / runs / run_events / feedback / users
  - 每个实体子包：抽象基类 → SQL 实现 → 内存备选 → 工厂函数
- 学习方式：快速通过，不逐文件注释（CRUD 工程，非 Agent 核心逻辑）
- 学习结论：做自己的框架时直接用现成 ORM，不需要参考 DeerFlow 的封装

### 流式输出 / Client — 2026-05-29
- 核心文件：`client.py`（1415 行，单文件）
  - DeerFlowClient：嵌入式 Python 客户端，无需 Gateway 直接调用 Agent
  - stream()：同步生成器，订阅 LangGraph 三种 stream mode（values/messages/custom）
  - chat()：包装 stream()，按 msg_id 累积 delta，返回最后一条 AI 文本
  - 去重机制：seen_ids + streamed_ids + counted_usage_ids 三重去重
  - Agent 延迟创建：_ensure_agent() 按 cache key 判断是否重建
- 学习方式：逐段阅读 + 对话检查
- 对话 check：通过（三种 stream mode / 去重机制 / 为什么不复用 Gateway）
- 关键启示：嵌入式和 HTTP 两种模式应对齐事件类型；多 stream mode 天然重复需去重；Agent 延迟创建+配置缓存

### MCP 集成 — 2026-05-29
- 核心文件：`mcp/` 下 5 个文件（888 行）
  - `tools.py`（核心：发现 MCP 工具 → 转为 LangChain BaseTool → 持久会话包装）
  - `session_pool.py`（持久会话池：LRU + (server,thread_id) 隔离 + 跨事件循环安全）
  - `cache.py`（工具缓存 + mtime 热重载，多进程配置同步）
  - `oauth.py`（OAuth token 管理 + 拦截器链注入）
  - `client.py`（配置转换：stdio/sse/http 三种传输协议）
- 学习方式：阅读 + 对话检查（消费方视角，不深挖实现）
- 学习结论：MCP 是消费方关系，用 langchain-mcp-adapters 库即可，不需要自己实现底层

### IM Channels — 2026-05-29
- 核心文件：`app/channels/` 下 14 个文件（6088 行）
  - `base.py`（Channel ABC：start/stop/send，适配器模式）
  - `message_bus.py`（异步发布订阅：InboundMessage/OutboundMessage/MessageBus）
  - `manager.py`（★★★ ChannelManager 1024行：收消息→调Agent→回消息，流式/命令/文件双向传递）
  - 具体实现：Slack/Discord/Telegram/WeChat/WeCom/Feishu/DingTalk
- 学习方式：阅读核心架构文件
- 关键架构：Channel(适配器) → MessageBus(解耦) → ChannelManager(调度) → Agent
- 学习结论：做企业 Agent 框架必接 IM，核心模式值得抄

---

## 全部 18 个模块学习完成 ✅
