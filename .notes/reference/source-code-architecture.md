# DeerFlow 源码学习手册

> 阶段二 Day 2 成果。基于 `backend/packages/harness/deerflow/` 源码分析。
> 核心问题：一条用户消息从发出到收到回复，经过了哪些文件的哪些函数？

---

## 一、`_make_lead_agent()` —— 一切从这里开始

这是 LangGraph 的入口（`langgraph.json` 声明），所有子系统在这里交汇。

```
make_lead_agent(config)                          ← langgraph.json 入口
  │
  └── _make_lead_agent(config, app_config)       ← agent.py:318
      │
      ├──① _get_runtime_config(config)           ← agent.py:27  提取运行时参数
      │   返回: {thinking_enabled, model_name, is_plan_mode, subagent_enabled...}
      │
      ├──② _resolve_model_name(name)             ← agent.py:36  模型名解析+回退
      │   读: app_config.models[0].name（默认模型）
      │   返回: "deepseek-v4" 之类的字符串
      │
      ├──③ create_chat_model(name, thinking)     ← models/factory.py:50  创建LLM实例
      │   │
      │   ├── config.get_model_config(name)      → 找到模型配置
      │   ├── resolve_class(model_config.use)    → 反射：字符串 → Python 类
      │   │   例: "langchain_openai:ChatOpenAI"  → ChatOpenAI 类
      │   ├── 处理 thinking_enabled/disabled     → 切换思考模式
      │   └── model_class(**settings)            → 实例化 LLM
      │   返回: BaseChatModel 实例
      │
      ├──④ get_available_tools(model_name...)    ← tools/tools.py:36  组装工具列表
      │   │
      │   ├── resolve_variable(tool.use)         → config.yaml 的工具
      │   ├── BUILTIN_TOOLS                      → present_files, ask_clarification
      │   ├── get_cached_mcp_tools()             → MCP 工具
      │   ├── build_invoke_acp_agent_tool()      → ACP 工具
      │   └── 去重合并
      │   返回: list[BaseTool]
      │
      ├──⑤ _build_middlewares(config, model_name) ← agent.py:238  组装中间件链
      │   │
      │   ├── build_lead_runtime_middlewares()   ← tool_error_handling_middleware.py:129
      │   │   └── _build_runtime_middlewares()   ← 同文件 :70
      │   │       ├── ThreadDataMiddleware       # 0
      │   │       ├── UploadsMiddleware          # 1
      │   │       ├── SandboxMiddleware          # 2
      │   │       ├── DanglingToolCallMiddleware # 3
      │   │       ├── LLMErrorHandlingMiddleware # 4
      │   │       ├── GuardrailMiddleware        # 5（可选）
      │   │       ├── SandboxAuditMiddleware     # 6
      │   │       └── ToolErrorHandlingMiddleware # 7
      │   │
      │   ├── _create_summarization_middleware()  # 8（可选）
      │   ├── TodoMiddleware                     # 9（plan mode）
      │   ├── TokenUsageMiddleware               # 10（可选）
      │   ├── TitleMiddleware                    # 11
      │   ├── MemoryMiddleware                   # 12
      │   ├── ViewImageMiddleware                # 13（vision 模型）
      │   ├── DeferredToolFilterMiddleware       # 14（tool_search）
      │   ├── SubagentLimitMiddleware             # 15（subagent）
      │   ├── LoopDetectionMiddleware            # 16
      │   └── ClarificationMiddleware            # 17（必须最后）
      │   返回: list[AgentMiddleware]
      │
      ├──⑥ apply_prompt_template(...)             ← prompt.py:748  生成 system prompt
      │   │
      │   ├── _get_memory_context()              → 读 memory.json，生成 <memory> 块
      │   ├── get_skills_prompt_section()        → 扫描 SKILL.md，生成 <skill_system> 块
      │   ├── get_deferred_tools_prompt_section() → 生成 <available-deferred-tools> 块
      │   ├── _build_subagent_section()          → subagent 使用指南
      │   ├── get_agent_soul()                   → 自定义 agent 的 SOUL.md
      │   └── SYSTEM_PROMPT_TEMPLATE.format(...) → 拼接完整 prompt
      │   返回: str（完整 system prompt）
      │
      └──⑦ create_agent(                         ← langchain.agents（LangGraph 工厂）
            model=③,
            tools=④,
            middleware=⑤,
            system_prompt=⑥,
            state_schema=ThreadState,
          )
          返回: CompiledStateGraph ← 这就是可执行的 agent
```

**一句话**：`make_lead_agent` 做了 4 件事 —— 创建模型、组装工具、排中间件、拼 prompt，然后扔给 LangGraph 的 `create_agent` 产出一个可执行图。

---

## 二、请求进来后的执行流

Agent 组装好后，用户消息进来时的执行过程：

```
用户发消息 → Gateway(:8001) → LangGraph runtime
  │
  │  LangGraph 调用 CompiledStateGraph.stream/invoke
  │
  ├── before_agent（正序 0→17）
  │   [0]  ThreadDataMiddleware    → 创建线程目录
  │   [1]  UploadsMiddleware       → 注入上传文件
  │   [2]  SandboxMiddleware       → acquire sandbox
  │   [3-7] 不挂 before_agent
  │
  ├── before_model（反序 17→0）
  │   [8]  SummarizationMiddleware → 检查 token，超阈值则压缩旧消息
  │                              详见下方"Summarization 决策树"
  │
  ├── LLM 调用（create_chat_model 创建的实例）
  │   model.bind_tools(tools) 已注入工具 schema
  │   system_prompt 已注入 skills/memory
  │   返回: AIMessage（可能含 tool_calls）
  │
  ├── after_model（反序 17→0）
  │   [17] ClarificationMiddleware → 拦截 ask_clarification？
  │   [16] LoopDetectionMiddleware → 重复调用检测
  │   [15] SubagentLimitMiddleware  → 截断多余 task
  │   [14] DeferredToolFilter      → 隐藏延迟工具
  │   [13] ViewImageMiddleware     → 注入图片
  │   [12] MemoryMiddleware        → 排队更新记忆
  │   [11] TitleMiddleware         → 第一轮生成标题
  │   [10] TokenUsageMiddleware    → 记录 token
  │   [9]  TodoMiddleware          → plan mode 任务
  │
  ├── 工具执行（如有 tool_calls）
  │   [7]  ToolErrorHandlingMiddleware.wrap_tool_call() 包裹每个工具调用
  │   [6]  SandboxAuditMiddleware  → 审计日志
  │   [5]  GuardrailMiddleware     → 安全检查
  │   实际执行工具函数 → 返回 ToolMessage
  │
  ├── after_agent（反序 17→0）
  │   [2]  SandboxMiddleware       → release sandbox
  │   [12] MemoryMiddleware        → 触发记忆更新（异步）
  │
  └── 返回结果 → SSE 流式 → 前端渲染
```

---

## 三、Tool / MCP / Skill —— 大模型的三种能力来源

> 详细分析见 [`.notes/tool-mcp-skill.md`](./tool-mcp-skill.md)

一句话总结：

| 概念 | 本质 | 大模型怎么用 | 配置在哪 |
|------|------|-------------|---------|
| **Tool** | Python 函数，有 JSON Schema | function calling 直接调用 | `config.yaml` 的 `tools:` |
| **MCP** | 远程服务器的工具，转成 Tool | 和 Tool 一样，function calling | `extensions_config.json` 的 `mcpServers:` |
| **Skill** | Markdown 文档，工作指南 | read_file 读取，然后按指南操作 | `extensions_config.json` 的 `skills:` |

**Tool 和 MCP 是大模型的"手"（执行动作），Skill 是大模型的"教材"（教它怎么做）。**

---

## 四、按文件归类的调用依赖

```
agent.py（核心编排）
  ├── models/factory.py              → create_chat_model()      创建 LLM
  ├── tools/tools.py                 → get_available_tools()    组装工具
  ├── middlewares/tool_error_handling_middleware.py
  │   └── build_lead_runtime_middlewares()  前8个基础中间件
  ├── lead_agent/prompt.py           → apply_prompt_template()  生成 prompt
  ├── config/app_config.py           → get_app_config()         读配置
  ├── config/agents_config.py        → load_agent_config()      读自定义agent配置
  ├── agents/thread_state.py         → ThreadState              状态 schema
  └── langchain.agents               → create_agent()           LangGraph 工厂

factory.py（核心编排的 SDK 版）
  ├── 同样的中间件链，但是通过 RuntimeFeatures 声明式组装
  └── 给第三方用，不需要 config.yaml

prompt.py（prompt 组装）
  ├── agents/memory/updater.py       → 读 memory.json
  ├── skills/storage/                → 扫描 SKILL.md
  ├── skills/types.py                → Skill 数据结构
  └── config/agents_config.py        → 读 SOUL.md

models/factory.py（模型创建）
  ├── reflection/__init__.py         → resolve_class()  反射加载
  ├── config/app_config.py           → 模型配置
  └── tracing/                       → 挂 tracing callbacks
```

---

## 五、文件重要度排序

| 优先级 | 文件 | 行数 | 为什么重要 |
|--------|------|------|-----------|
| ⭐⭐⭐ | `agents/lead_agent/agent.py` | 415 | **核心编排**，所有子系统交汇点 |
| ⭐⭐⭐ | `agents/lead_agent/prompt.py` | 806 | system prompt 怎么拼的 |
| ⭐⭐⭐ | `tools/tools.py` | 175 | 工具怎么组装的 |
| ⭐⭐ | `models/factory.py` | 157 | LLM 怎么创建的 |
| ⭐⭐ | `agents/middlewares/tool_error_handling_middleware.py` | 167 | 前8个基础中间件在这里组装 |
| ⭐⭐ | `agents/factory.py` | 374 | SDK 版本，声明式组装 |
| ⭐ | `agents/thread_state.py` | ~100 | 状态 schema |
| ⭐ | `reflection/__init__.py` | ~50 | resolve_variable/resolve_class |

---

## 六、关键配置门控

| 控制什么 | 门控条件 | 代码位置 |
|---------|---------|---------|
| Config 工具 | `config.yaml` `tools:` + `groups` 过滤 | `tools.py:59` |
| MCP 工具 | `extensions_config.json` `mcpServers.*.enabled` | `tools.py:119` |
| Deferred tool search | `config.yaml` `tool_search.enabled` | `tools.py:126` |
| Task/subagent 工具 | 运行时参数 `subagent_enabled` | `tools.py:91` |
| View image 工具 | 模型的 `supports_vision` 标志 | `tools.py:101` |
| Skill manage 工具 | `config.yaml` `skill_evolution.enabled` | `tools.py:85-88` |
| ACP agent 工具 | `config.yaml` `acp_agents:` 非空 | `tools.py:153` |
| Skills 在 prompt | `extensions_config.json` `skills.*.enabled` + agent 白名单 | `prompt.py:606-636` |
| 自定义 agent tool_groups | Agent 的 `config.yaml` `tool_groups:` | `agent.py:401` |
| 自定义 agent skills | Agent 的 `config.yaml` `skills:` | `agent.py:411` |

---

## 七、源码学习方法论：三遍法

> 知道"做什么" ≠ 理解"怎么做"。三遍法的目标是从"看地图"到"自己开路"。

### 第一遍：画调用关系图（1-2小时）—— 你刚完成了

**不要逐行读代码。** 目标是回答：一条消息进来，先后经过了哪些文件的哪些函数？

**操作方法**：
1. 从入口开始：`langgraph.json` → `deerflow.agents:make_lead_agent`
2. 打开 `agent.py`，只看 `make_lead_agent()` 和 `_make_lead_agent()`
3. 每个函数只看三件事：**import 了谁、调了谁、返回什么**
4. 画一张草图，不需要理解内部实现

**产出**：本文档第一章的调用关系图。你现在已经在做第二遍了。

### 第二遍：日志调试法（2-3小时）

`make dev` 是热重载的（uvicorn `--reload`），最快的方法是**加日志 + 网页操作 + 看输出**。

#### 找到日志输出

```bash
tail -f logs/gateway.log          # Gateway 日志
# 或直接看终端输出（make dev 前台模式）
```

#### 在关键位置加日志

保存文件后 uvicorn 自动重载（等 2-3 秒），刷新网页发一条消息，日志里就能看到。

| 想看什么 | 在哪加 | 加什么 |
|---------|--------|--------|
| 组装了哪些工具 | `tools/tools.py` 末尾 | 已有 `logger.info`，不用加 |
| 中间件链顺序 | `agent.py` 的 `_build_middlewares()` 返回前 | `logger.info(f"Middleware chain: {[type(m).__name__ for m in middlewares]}")` |
| 请求进入中间件 | 某个中间件的 `before_agent` 方法第一行 | `logger.info(f"before_agent called, state keys: {list(state.keys())}")` |
| LLM 返回了什么 | 某个中间件的 `after_model` 方法 | `logger.info(f"after_model, tool_calls: {[tc['name'] for tc in response.tool_calls]}")` |
| 工具被调用了 | `sandbox/tools.py` 的 `bash_tool` 第一行 | `logger.info(f"bash_tool called: {command}")` |
| Memory 更新 | `memory/queue.py` 入队处 | `logger.info(f"Memory enqueued, thread: {thread_id}")` |

#### 实操示例：看中间件执行顺序

在 `agent.py` 的 `_build_middlewares()` 函数末尾（`return middlewares` 前面）加一行：

```python
logger.info(f"=== Middleware chain ===\n" + "\n".join(f"  [{i}] {type(m).__name__}" for i, m in enumerate(middlewares)))
```

保存 → 刷新网页 → 发一条消息 → 看日志：

```
=== Middleware chain ===
  [0] ThreadDataMiddleware
  [1] UploadsMiddleware
  [2] SandboxMiddleware
  [3] DanglingToolCallMiddleware
  [4] LLMErrorHandlingMiddleware
  [5] SandboxAuditMiddleware
  [6] ToolErrorHandlingMiddleware
  [7] SummarizationMiddleware
  [8] TitleMiddleware
  [9] MemoryMiddleware
  [10] LoopDetectionMiddleware
  [11] ClarificationMiddleware
```

**加的日志是临时调试用，调试完删掉，不要提交到 Git。**

### 第三遍：写测试用例直接调用（2-3小时）

最深入的学法——自己传参数、自己调用、自己验证。不需要启动服务。

在 `backend/tests/` 下写测试：

```python
# backend/tests/test_my_learning.py

def test_tool_assembly():
    """验证 get_available_tools 组装了哪些工具"""
    from deerflow.tools import get_available_tools
    from deerflow.config import get_app_config

    config = get_app_config()
    tools = get_available_tools(app_config=config)

    tool_names = [t.name for t in tools]
    print(f"\n=== Tools loaded ({len(tools)}) ===")
    for name in tool_names:
        print(f"  - {name}")

    assert "present_files" in tool_names
    assert "ask_clarification" in tool_names

def test_middleware_chain():
    """验证中间件链的组装顺序"""
    from deerflow.agents.middlewares.tool_error_handling_middleware import build_lead_runtime_middlewares
    from deerflow.config import get_app_config

    config = get_app_config()
    middlewares = build_lead_runtime_middlewares(app_config=config)

    print(f"\n=== Base middlewares ({len(middlewares)}) ===")
    for i, m in enumerate(middlewares):
        print(f"  [{i}] {type(m).__name__}")

    assert type(middlewares[0]).__name__ == "ThreadDataMiddleware"
    assert type(middlewares[-1]).__name__ == "ToolErrorHandlingMiddleware"

def test_model_creation():
    """验证模型能被创建"""
    from deerflow.models import create_chat_model
    from deerflow.config import get_app_config

    config = get_app_config()
    model = create_chat_model(app_config=config)
    print(f"\n=== Model created ===")
    print(f"  type: {type(model).__name__}")
    print(f"  model_name: {model.model_name}")
```

运行：

```bash
cd backend && PYTHONPATH=. uv run pytest tests/test_my_learning.py -v -s
```

`-s` 让 print 输出到终端。

### 三遍法总结

| 阶段 | 做什么 | 时间 | 深度 | 产出 |
|------|--------|------|------|------|
| **第一遍** | 画调用关系图，只看函数签名 | 1-2h | 知道"谁调谁" | 调用关系草图 |
| **第二遍** | 加日志 + 网页操作 + 看输出 | 2-3h | 看到"数据长什么样" | 亲眼见过数据流 |
| **第三遍** | 写测试直接调用 harness API | 2-3h | 能"自己构造输入验证" | 可运行的测试 |

**不要一开始就试图读懂每一行。先知道地图（第一遍），再走一遍路线（第二遍），最后自己开一条路（第三遍）。**

---

## 八、Summarization 中间件详解

> 源码：`agents/middlewares/summarization_middleware.py`（607 行）
> 挂钩位置：`before_model`（LLM 调用之前检查，不是 after_model）
> 为什么挂 before_model：需要在 LLM 看到消息之前就压缩好，避免 token 超限

### 决策树（每次 LLM 调用前执行）

```
总 token 超阈值了？
├─ 没有 → 返回 None（不压缩）
└─ 超了 → 切割点（cutoff_index）在哪？
    ├─ cutoff ≤ 0（消息太少，没法切）→ 返回 None（放弃压缩）
    └─ 合法 → 执行压缩流程 ↓
```

### 压缩流程

```
① 基类分区：按 cutoff_index 切一刀
     messages[0..cutoff)       → to_summarize（要压缩的旧消息）
     messages[cutoff..end]     → preserved（保留的新消息）

② Skill Rescue：从旧消息里抢救 skill 内容
     ├─ AIMessage 只调了 skill 工具     → 整条救回 preserved
     ├─ AIMessage 同时调了 skill 和其他   → 拆成两条（skill 救回，其余继续压缩）
     └─ 超预算（太多/太大）              → 放弃保护，照常压缩

③ 保护动态上下文提醒（日期/记忆注入的隐藏消息）不被压掉

④ 触发钩子：memory_flush_hook 把即将被压掉的消息存到记忆系统

⑤ LLM 生成摘要（把剩余的旧消息压缩成一段话）

⑥ 返回 LangGraph state 操作：
     [RemoveMessage(REMOVE_ALL),  ← 清空所有旧消息（LangGraph 指令，不发给 LLM）
      HumanMessage(summary),       ← 摘要（name="summary"，前端不展示）
      skill 消息,                   ← 被抢救回来的 skill 原文
      最近消息]                      ← preserved 区原样保留
```

### 切割点（cutoff_index）怎么算

```python
# 基类 SummarizationMiddleware._determine_cutoff_index()
if keep.kind in {"tokens", "fraction"}:
    token_based_cutoff = self._find_token_based_cutoff(messages)
    if token_based_cutoff is not None:
        return token_based_cutoff
    return self._find_safe_cutoff(messages, DEFAULT_MESSAGES_TO_KEEP)
return self._find_safe_cutoff(messages, keep.value)
```

`_find_safe_cutoff` 的逻辑：
- 如果 `len(messages) <= messages_to_keep` → 返回 0（消息总数不超过保留数，没东西可切）
- 否则：`target_cutoff = len(messages) - messages_to_keep`，然后找安全切割点（不切断 AI/Tool 消息对）

### cutoff ≤ 0 的实际场景

消息很少但每条都巨大（如 tool 返回超长内容），总数 ≤ 保留数，系统"保护性放弃"，不压缩。
最终可能触发 API token 超限报错（但实际很少发生，因为有 bash_output_max_chars 等截断保护）。

### Skill Rescue 为什么需要

skill 文件（SKILL.md）是 agent 的工作指南。如果被压缩成摘要，agent 就"忘了"怎么干活。
所以必须把 skill 原文从压缩区抢救出来，原样保留在上下文里。

关键参数（config.yaml）：
- `preserve_recent_skill_count: 5` — 最多保护 5 个 skill bundle
- `preserve_recent_skill_tokens: 25000` — 被保护 skill 总 token 上限
- `preserve_recent_skill_tokens_per_skill: 5000` — 单个 skill token 上限

### 时序示例

```
压缩前（100 条消息、50000 token，阈值 15564 token，保留最后 10 条）：

  [0] HumanMessage: "帮我调研 LangGraph"
  [1] AIMessage: tool_calls=[read_file("/mnt/skills/research/SKILL.md")]
  [2] ToolMessage: "research skill 完整内容..."（3000 token）
  [3-89] 对话和搜索结果...
  [90-99] 最近 10 条对话

压缩过程：
  ① cutoff = 90（保留最后 10 条）
  ② Skill Rescue：消息 [1,2] 是 skill bundle → 拉到 preserved
  ③ to_summarize = [0, 3, 4, ..., 89]（跳过了 [1,2]）
  ④ LLM 生成摘要："用户想调研 LangGraph，已搜索了初步资料..."
  ⑤ 返回：[RemoveAll, 摘要, AIMessage(skill), ToolMessage(skill), 消息90-99]

LLM 看到的最终消息：
  [摘要, AIMessage(read_file skill), ToolMessage(skill内容), 最近10条消息]
```
