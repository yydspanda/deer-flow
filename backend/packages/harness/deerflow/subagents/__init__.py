"""yyds: Sub-Agent 子系统 — Lead Agent 将复杂任务委托给独立运行的子 Agent。

═════════════════════════════════════════════════════════════════════
【大白话：解决什么问题】
═════════════════════════════════════════════════════════════════════
  Lead Agent 遇到复杂任务（比如"帮我部署这个项目"），
  如果自己一步步执行，会占用主对话的上下文窗口（工具调用、输出结果都很长）。

  Sub-Agent 解决这个问题：
    Lead Agent 说："把这个任务交给一个 sub-agent 去做"
    → sub-agent 在独立线程 + 独立 event loop 里跑
    → 有自己的工具集、中间件、token 预算
    → 跑完后把最终结果返回给 Lead Agent
    → Lead Agent 的主对话保持干净

  类比：Lead Agent 是项目经理，Sub-Agent 是外包团队。
    项目经理下任务（task_tool）→ 外包团队独立干活 → 交回结果。

═════════════════════════════════════════════════════════════════════
【有哪些 Sub-Agent？两个内置 + 用户自定义】
═════════════════════════════════════════════════════════════════════
  ① general-purpose（全能型）：
    - 工具：继承父 Agent 所有工具
    - 禁用：task（防递归）+ ask_clarification + present_files
    - 适合：复杂多步骤任务（探索+修改+推理）
    - max_turns=100

  ② bash（命令执行型）：
    - 工具：只有 sandbox 工具（bash/ls/read_file/write_file/str_replace）
    - 适合：一系列相关的 bash 命令（git/npm/docker/构建部署）
    - max_turns=60
    - 不可用时隐藏：sandbox 不允许 host bash → 从列表中移除

  ③ 用户自定义（config.yaml custom_agents 段）：
    - 用户可以在配置文件里定义自己的 sub-agent
    - 指定 name/description/system_prompt/tools/model/timeout 等
    - 和内置 sub-agent 共享同一套执行机制

═════════════════════════════════════════════════════════════════════
【全流程：从 task_tool 到结果返回】
═════════════════════════════════════════════════════════════════════

  ┌─────────────────────────────────────────────────────────────┐
  │ 阶段一：Lead Agent 下任务                                    │
  │                                                             │
  │ task_tool(task_desc, subagent_type="general-purpose")       │
  │   → 从 registry 获取 SubagentConfig                         │
  │   → 获取工具列表 + 过滤（白名单/黑名单/技能）                 │
  │   → 创建 SubagentExecutor(config, tools, sandbox_state)     │
  │   → executor.execute_async(task)                            │
  │     → 创建 SubagentResult(PENDING)                          │
  │     → 存入 _background_tasks[task_id]                       │
  │     → 提交到 _scheduler_pool 线程池                          │
  │     → 返回 task_id                                          │
  │                                                             │
  │ task_tool 拿到 task_id，每 5 秒轮询：                        │
  │   get_background_task_result(task_id)                       │
  │   → 返回 SubagentResult（status + ai_messages）             │
  │   → 通过 SSE 推送进度给前端                                  │
  └─────────────────────────────────────────────────────────────┘
                              │
                              ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ 阶段二：Sub-Agent 执行（executor.py ★核心）                   │
  │                                                             │
  │ _scheduler_pool 线程里：                                     │
  │   → copy_context() 保留父线程 ContextVar（trace_id 等）      │
  │   → 提交到 _isolated_subagent_loop（持久化 event loop）      │
  │                                                             │
  │ _aexecute() 在 isolated loop 上跑：                          │
  │   Step 1: _build_initial_state(task)                        │
  │     → 加载 skills（白名单过滤）                              │
  │     → 过滤工具（skill allowed_tools）                        │
  │     → 构建消息：[SystemMessage(prompt+skills), HumanMsg(task)]│
  │     → 注入父 Agent 的 sandbox_state + thread_data            │
  │                                                             │
  │   Step 2: _create_agent(tools)                              │
  │     → resolve model（inherit → 用父 Agent 的模型）           │
  │     → build_subagent_runtime_middlewares（精简版中间件链）    │
  │     → create_agent(model, tools, middlewares)                │
  │                                                             │
  │   Step 3: agent.astream(state, stream_mode="values")         │
  │     → 逐 chunk 迭代                                         │
  │     → 每个 chunk 检查 cancel_event（协作式取消）             │
  │     → 收集 AI 消息（去重）到 result.ai_messages              │
  │     → token 用量通过 SubagentTokenCollector 收集             │
  │                                                             │
  │   Step 4: 提取最终结果                                       │
  │     → 找最后一个 AIMessage → result.result                   │
  │     → result.status = COMPLETED                              │
  └─────────────────────────────────────────────────────────────┘
                              │
                              ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ 阶段三：结果回传                                             │
  │                                                             │
  │ task_tool 轮询到 status=COMPLETED：                          │
  │   → 读取 result.result（最终文本）                           │
  │   → 读取 result.ai_messages（过程消息）                      │
  │   → SSE 推送 task_completed 给前端                           │
  │   → cleanup_background_task(task_id) 清理                   │
  │   → Lead Agent 拿到结果，继续对话                            │
  └─────────────────────────────────────────────────────────────┘

═════════════════════════════════════════════════════════════════════
【关键设计决策】
═════════════════════════════════════════════════════════════════════
  ① 递归防护：disallowed_tools=["task"]
    sub-agent 不能再调 task_tool，否则会无限递归。

  ② 线程隔离：_isolated_subagent_loop
    持久化 event loop 在 daemon 线程里跑，避免每次执行创建新 loop。
    和主 event loop 完全隔离，互不干扰。

  ③ 并发限制：_scheduler_pool(max_workers=3)
    最多 3 个 sub-agent 同时调度。

  ④ 超时控制：默认 900s（15 分钟）
    future.result(timeout=900)，超时 → cancel_event.set() → 协作式取消。

  ⑤ ContextVar 传播：copy_context()
    提交到线程池时用 context.run() 包裹，确保子线程能看到 trace_id 等。

  ⑥ 配置覆盖：三层合并
    内置 config → config.yaml custom_agents → config.yaml agents 段 per-agent override

═════════════════════════════════════════════════════════════════════
【代码结构】
═════════════════════════════════════════════════════════════════════
  subagents/
  ├── __init__.py              模块索引 + 公共 API 导出
  ├── config.py                SubagentConfig 数据类 + 模型解析
  ├── registry.py              ★ 注册表（三层配置合并 + 可用性过滤）
  ├── executor.py              ★★★ 核心执行器（826 行）
  ├── token_collector.py       Token 用量收集（LangChain Callback）
  └── builtins/
      ├── general_purpose.py   通用 sub-agent（tools=None，继承所有）
      └── bash_agent.py        Bash sub-agent（只有 sandbox 工具）

  建议阅读顺序：
    1. config.py          — SubagentConfig 字段 + 模型解析优先级
    2. registry.py        — 注册 + 覆盖机制
    3. builtins/          — 两个内置 Agent（各 54 行，很简短）
    4. token_collector.py — Token 收集（63 行）
    5. executor.py        — ★★★ 核心执行器（最后看，最复杂）

---
Subagent subsystem for delegating tasks to independent child agents.
"""

from .config import SubagentConfig
from .executor import SubagentExecutor, SubagentResult
from .registry import get_available_subagent_names, get_subagent_config, list_subagents

__all__ = [
    "SubagentConfig",
    "SubagentExecutor",
    "SubagentResult",
    "get_available_subagent_names",
    "get_subagent_config",
    "list_subagents",
]
