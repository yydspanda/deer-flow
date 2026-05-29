"""yyds: 工具装配中心 — Agent 能用哪些工具，全由这个文件决定。

【大白话讲清楚】
  Agent 不是天生什么工具都能用的。这个函数就像一个"工具箱管理员"，
  根据四个条件决定往工具箱里放什么：
    1. config.yaml 里配了什么工具？（用户自定义的）
    2. 哪些内置工具要装？（present_files、ask_clarification 必装，其他看条件）
    3. MCP 外接工具有没有？（需要第三方包，可能装了也可能没装）
    4. ACP 外部 Agent 有没有？（配置了才有）

  然后按名字去重，优先级：配置工具 > 内置工具 > MCP工具 > ACP工具。

【具体例子】
  Ultra 模式 + GPT-4o（支持 vision）+ 3 个 MCP 服务器 + tool_search 开启：

  get_available_tools(
      model_name="gpt-4o",
      subagent_enabled=True,
  )
  → 返回工具列表：
    [bash_tool, read_file, write_file, ...]     ← config.yaml 配的
    [present_files, ask_clarification,           ← 始终装
     task,                                        ← subagent_enabled=True
     view_image,                                  ← gpt-4o 支持 vision
     tool_search]                                 ← tool_search 配置开启
    [mcp_tool_1, mcp_tool_2, mcp_tool_3]        ← MCP 缓存（被 tool_search 延迟，不直接给 LLM）

  Flash 模式 + 纯文本模型 + 无 MCP：
  → 只有 [config 工具, present_files, ask_clarification]

【在链中的位置】
  调用者：agent.py 的 _make_lead_agent() → get_available_tools() → 绑定到 Agent
  调用者：task_tool → sub-agent 也调用 get_available_tools(subagent_enabled=False) 防递归

---
Get all available tools from config.
"""

import logging

from langchain.tools import BaseTool

from deerflow.config import get_app_config
from deerflow.config.app_config import AppConfig
from deerflow.reflection import resolve_variable
from deerflow.sandbox.security import is_host_bash_allowed
from deerflow.tools.builtins import ask_clarification_tool, present_file_tool, task_tool, view_image_tool
from deerflow.tools.builtins.tool_search import get_deferred_registry
from deerflow.tools.sync import make_sync_tool_wrapper

logger = logging.getLogger(__name__)

# yyds: 始终加载的内置工具 — 不管什么模式都有
BUILTIN_TOOLS = [
    present_file_tool,  # yyds: Agent 创建文件后展示给用户（写入 state["artifacts"]）
    ask_clarification_tool,  # yyds: Agent 不确定时向用户提问（被 ClarificationMiddleware 拦截）
]

# yyds: Sub-Agent 工具 — 只在 Ultra 模式（subagent_enabled=True）加载
SUBAGENT_TOOLS = [
    task_tool,  # yyds: Lead Agent 派任务给 sub-agent（后台线程池执行，每 5s 轮询）
]


def _is_host_bash_tool(tool: object) -> bool:
    """yyds: 这个工具是不是"在宿主机上跑 bash"的？

    为什么需要判断：LocalSandbox 开启时（sandbox.use=LocalSandboxProvider），
    bash 应该在沙箱里跑，不是直接在宿主机上跑。
    → 如果 allow_host_bash=false（默认），就把 config 里 group="bash" 或
      use 指向 bash_tool 的工具过滤掉，不让 Agent 用。

    两条判断规则：
      ① tool.group == "bash" → 按 group 名匹配
      ② tool.use == "deerflow.sandbox.tools:bash_tool" → 按具体实现类匹配

    config.yaml 里的对应配置：
      tools:
        - name: bash
          group: bash                              ← 匹配规则①
          use: deerflow.sandbox.tools:bash_tool    ← 匹配规则②
    """
    group = getattr(tool, "group", None)
    use = getattr(tool, "use", None)
    if group == "bash":
        return True
    if use == "deerflow.sandbox.tools:bash_tool":
        return True
    return False


def _ensure_sync_invocable_tool(tool: BaseTool) -> BaseTool:
    """yyds: 给只有 async 版本的工具补一个 sync 版本。

    为什么需要：LangChain 的 BaseTool 有两个执行入口：
      - tool.func：同步调用入口（普通函数）
      - tool.coroutine：异步调用入口（async 函数）

    有些工具（比如 MCP 工具、skill_manage_tool）只实现了 coroutine（async），
    但 LangGraph 的某些调用路径走的是 sync（调 func）。
    → 如果 func 是 None 但 coroutine 不是 None，就给它补一个同步包装器。

    补的 wrapper 就是 sync.py 里的 make_sync_tool_wrapper()：
      没有事件循环 → asyncio.run()
      有事件循环 → 丢到线程池里 asyncio.run()
    """
    if getattr(tool, "func", None) is None and getattr(tool, "coroutine", None) is not None:
        tool.func = make_sync_tool_wrapper(tool.coroutine, tool.name)
    return tool


def get_available_tools(
    groups: list[str] | None = None,
    include_mcp: bool = True,
    model_name: str | None = None,
    subagent_enabled: bool = False,
    *,
    app_config: AppConfig | None = None,
) -> list[BaseTool]:
    """yyds: Agent 工具箱的总入口 — 从四个来源收集工具，按优先级合并去重。

    参数详解：
      groups: 工具分组过滤。
        → 对应 config.yaml 的 tool_groups（web / file:read / file:write / bash）
        → None = 不过滤，加载所有分组的工具
        → ["web"] = 只加载 group="web" 的工具
        → 用在哪：自定义 Agent 的 config.yaml 可以设 tool_groups 限制可用工具范围

      include_mcp: 是否包含 MCP 外接工具。
        → True = 从缓存加载 MCP 工具
        → 一般都是 True，除非明确不需要

      model_name: 模型名称。
        → 用来判断是否加载 view_image_tool（只有 supports_vision=True 的模型才装）
        → None = 用 config.yaml 里第一个模型

      subagent_enabled: 是否加载 Sub-Agent 工具（task_tool）。
        → True = Ultra 模式，Lead Agent 可以派任务给 sub-agent
        → False = Flash/Thinking/Pro 模式，或者 sub-agent 内部调用时（防递归）

      app_config: 应用配置对象。
        → 传了就用传入的（比如 sub-agent 继承父 agent 的配置）
        → None = 调 get_app_config() 从 config.yaml 重新读

    四个来源（按优先级从高到低）：
      ① 配置加载工具：config.yaml → tools 列表 → resolve_variable 反射加载
      ② 内置工具：present_files + ask_clarification 必装，其他看条件
      ③ MCP 工具：从缓存取，受 include_mcp 参数控制
      ④ ACP 工具：外部 Agent 调用，config.yaml 里 acp_agents 配置了才有

    去重规则：同名工具只保留第一个出现的（即优先级高的）。
    命名冲突会导致 LLM 收到重复的 function schema，报 "not a valid tool" 错误。

    执行步骤：
      ① 读 config.yaml 的 tools 列表，按 groups 过滤
      ② 安全过滤：sandbox.allow_host_bash=false 时移除宿主机 bash
      ③ 反射加载：把 "deerflow.sandbox.tools:bash_tool" 字符串变成 BaseTool 对象
      ④ 名称冲突检测：config name ≠ tool.name 时警告
      ⑤ 给 async-only 工具补 sync wrapper
      ⑥ 条件装内置工具：skill_manage / task / view_image / tool_search
      ⑦ 加载 MCP 缓存工具（tool_search 开启则注册到延迟注册中心）
      ⑧ 加载 ACP 工具
      ⑨ 按名称去重
    """
    # ── ① 读配置 ──────────────────────────────────────────────────
    # get_app_config() 会从 config.yaml 读配置并缓存（支持热重载）
    # config.tools 就是 config.yaml 里 tools: 列表解析出来的 ToolConfig 对象列表
    config = app_config or get_app_config()

    # 按 groups 过滤：groups=None 时加载全部，否则只加载匹配分组的工具
    # 比如 groups=["web"] 只加载 web_search、web_fetch 等 group="web" 的工具
    tool_configs = [tool for tool in config.tools if groups is None or tool.group in groups]

    # ── ② 安全过滤：LocalSandbox 下移除宿主机 bash ──────────────
    # is_host_bash_allowed() 检查 config.yaml 的 sandbox.allow_host_bash
    # 默认 false → 不让 Agent 直接在宿主机上跑 bash 命令（安全风险）
    # 只有 Docker/AIO 沙箱或者显式开启 allow_host_bash 时才允许
    if not is_host_bash_allowed(config):
        tool_configs = [tool for tool in tool_configs if not _is_host_bash_tool(tool)]

    # ── ③ 反射加载：字符串 → BaseTool 对象 ───────────────────────
    # config.yaml 里 tools 的 use 字段是字符串，如 "deerflow.sandbox.tools:bash_tool"
    # resolve_variable() 通过反射（importlib）找到这个类/变量并实例化
    # 返回 [(ToolConfig, BaseTool), ...] 元组列表
    loaded_tools_raw = [(cfg, resolve_variable(cfg.use, BaseTool)) for cfg in tool_configs]

    # ── ④ 名称冲突检测 ────────────────────────────────────────────
    # config.yaml 里 name: "bash" 但工具类的 .name 属性可能是 "run_bash"
    # → LLM 收到的 schema 里叫 "bash"，但运行时路由认的是 "run_bash" → 报错
    # 这就是 issue #1803：配置名和工具自身名不一致导致 "not a valid tool"
    for cfg, loaded in loaded_tools_raw:
        if cfg.name != loaded.name:
            logger.warning(
                "Tool name mismatch: config name %r does not match tool .name %r (use: %s). The tool's own .name will be used for binding.",
                cfg.name,
                loaded.name,
                cfg.use,
            )

    # ── ⑤ 给 async-only 工具补 sync wrapper ───────────────────────
    # MCP 工具通常只有 async 版本，这里统一补上 sync 入口
    loaded_tools = [_ensure_sync_invocable_tool(t) for _, t in loaded_tools_raw]

    # ── ⑥ 条件装内置工具 ──────────────────────────────────────────
    builtin_tools = BUILTIN_TOOLS.copy()  # yyds: 先装两个必装的

    # skill_manage_tool：Agent 自管理技能（创建/编辑/删除 skill）
    # 只在 config.yaml 的 skill_evolution.enabled=true 时装
    # 对应 config:
    #   skill_evolution:
    #     enabled: false  ← 默认关
    skill_evolution_config = getattr(config, "skill_evolution", None)
    if getattr(skill_evolution_config, "enabled", False):
        from deerflow.tools.skill_manage_tool import skill_manage_tool

        builtin_tools.append(skill_manage_tool)

    # task_tool：Sub-Agent 委派工具
    # 只在 subagent_enabled=True 时装（Ultra 模式）
    # sub-agent 内部调用时传 subagent_enabled=False → 防止递归嵌套
    if subagent_enabled:
        builtin_tools.extend(SUBAGENT_TOOLS)
        logger.info("Including subagent tools (task)")

    # view_image_tool：图片查看工具
    # 只在模型支持 vision 时装（config.yaml 里 supports_vision: true）
    # 比如 GPT-4o 支持 → 装；纯文本模型不支持 → 不装
    if model_name is None and config.models:
        model_name = config.models[0].name  # yyds: 没指定就用配置里的第一个模型
    model_config = config.get_model_config(model_name) if model_name else None
    if model_config is not None and model_config.supports_vision:
        builtin_tools.append(view_image_tool)
        logger.info(f"Including view_image_tool for model '{model_name}' (supports_vision=True)")

    # ── ⑦ 加载 MCP 工具（从缓存，不实时连接）────────────────────
    # MCP = Model Context Protocol，外接工具的标准协议
    # 工具在应用启动时预加载到缓存，这里只是从缓存取，不实时连接
    # 用 ExtensionsConfig.from_file() 读磁盘而不是用内存里的 config，
    # 因为 Gateway API 可能在另一个进程里改了 extensions 配置
    mcp_tools = []

    if include_mcp:
        try:
            from deerflow.config.extensions_config import ExtensionsConfig
            from deerflow.mcp.cache import get_cached_mcp_tools

            extensions_config = ExtensionsConfig.from_file()
            if extensions_config.get_enabled_mcp_servers():
                mcp_tools = get_cached_mcp_tools()
                if mcp_tools:
                    logger.info(f"Using {len(mcp_tools)} cached MCP tool(s)")

                    # tool_search 模式：MCP 工具不直接给 LLM 的 schema
                    # 而是：
                    #   1. 注册到 DeferredToolRegistry（延迟注册中心）
                    #   2. 给 LLM 装 tool_search 工具
                    #   3. LLM 先看到工具名列表，需要时调用 tool_search 获取完整 schema
                    # 好处：100 个 MCP 工具不会把 LLM 上下文撑爆
                    if config.tool_search.enabled:
                        from deerflow.tools.builtins.tool_search import DeferredToolRegistry, set_deferred_registry
                        from deerflow.tools.builtins.tool_search import tool_search as tool_search_tool

                        # yyds: 复用已有 registry（防 issue #2884）
                        # sub-agent 内部调用 get_available_tools 时，
                        # 如果每次都重建 registry，会丢掉父 agent 已经 promote 的工具
                        # → LLM 看得到工具名但调不了（因为 schema 被重新隐藏了）
                        # ContextVar 保证了：同一个请求/图运行内共享 registry，不同请求隔离
                        existing_registry = get_deferred_registry()
                        if existing_registry is None:
                            registry = DeferredToolRegistry()
                            for t in mcp_tools:
                                registry.register(t)
                            set_deferred_registry(registry)
                            logger.info(f"Tool search active: {len(mcp_tools)} tools deferred")
                        else:
                            mcp_tool_names = {t.name for t in mcp_tools}
                            still_deferred = len(existing_registry)
                            promoted_count = max(0, len(mcp_tool_names) - still_deferred)
                            logger.info(f"Tool search active (preserved promotions): {still_deferred} tools deferred, {promoted_count} already promoted")
                        builtin_tools.append(tool_search_tool)
        except ImportError:
            logger.warning("MCP module not available. Install 'langchain-mcp-adapters' package to enable MCP tools.")
        except Exception as e:
            logger.error(f"Failed to get cached MCP tools: {e}")

    # ── ⑧ 加载 ACP 工具（外部 Agent 调用）────────────────────────
    # ACP = Agent Client Protocol，调用外部 Agent 的标准协议
    # config.yaml 里 acp_agents 配置了才有（默认注释掉）
    # 比如：claude_code、codex 等外部 Agent
    acp_tools: list[BaseTool] = []
    try:
        from deerflow.tools.builtins.invoke_acp_agent_tool import build_invoke_acp_agent_tool

        if app_config is None:
            from deerflow.config.acp_config import get_acp_agents

            acp_agents = get_acp_agents()
        else:
            acp_agents = getattr(config, "acp_agents", {}) or {}
        if acp_agents:
            acp_tools.append(build_invoke_acp_agent_tool(acp_agents))
            logger.info(f"Including invoke_acp_agent tool ({len(acp_agents)} agent(s): {list(acp_agents.keys())})")
    except Exception as e:
        logger.warning(f"Failed to load ACP tool: {e}")

    logger.info(f"Total tools loaded: {len(loaded_tools)}, built-in tools: {len(builtin_tools)}, MCP tools: {len(mcp_tools)}, ACP tools: {len(acp_tools)}")

    # ── ⑨ 按名称去重 ──────────────────────────────────────────────
    # 优先级：loaded_tools（配置） > builtin_tools（内置） > mcp_tools（MCP） > acp_tools（ACP）
    # 同名工具只保留第一个出现的（优先级高的胜出）
    # 为什么去重：同名工具会让 LLM 收到重复 function schema，报 "not a valid tool"（issue #1803）
    # Deduplicate by tool name — config-loaded tools take priority, followed by
    # built-ins, MCP tools, and ACP tools.  Duplicate names cause the LLM to
    # receive ambiguous or concatenated function schemas (issue #1803).
    all_tools = [_ensure_sync_invocable_tool(t) for t in loaded_tools + builtin_tools + mcp_tools + acp_tools]
    seen_names: set[str] = set()
    unique_tools: list[BaseTool] = []
    for t in all_tools:
        if t.name not in seen_names:
            unique_tools.append(t)
            seen_names.add(t.name)
        else:
            logger.warning(
                "Duplicate tool name %r detected and skipped — check your config.yaml and MCP server registrations (issue #1803).",
                t.name,
            )
    return unique_tools
