"""yyds: Sub-Agent 任务委派工具 — Ultra 模式的核心，Lead Agent 的"分身术"。

【大白话讲清楚】
  Lead Agent 一个人干不完所有事。Ultra 模式下，它可以"分身"——
  把子任务派给专门的 sub-agent 去做，自己做总指挥。

  sub-agent 在后台线程池里跑，task_tool 每 5 秒轮询一次结果，
  同时通过 SSE 把进度推给前端（task_started → task_running → task_completed）。

  三个关键设计：
    ① 递归防护：sub-agent 不能再创建 sub-agent（get_available_tools(subagent_enabled=False)）
    ② 技能白名单取交集：sub-agent 只能用父 agent 允许的技能的子集
    ③ 双重超时：线程池 timeout + 轮询 timeout（config.timeout_seconds + 60s 缓冲）

【具体例子】
  用户："帮我调研 LangGraph 和 CrewAI，然后写对比报告"

  Lead Agent 拆成两个 sub-agent 任务：
    task(description="调研 LangGraph", prompt="搜索 LangGraph 架构特点...",
         subagent_type="general-purpose")
    task(description="调研 CrewAI", prompt="搜索 CrewAI 架构特点...",
         subagent_type="general-purpose")

  每个 sub-agent 独立执行：
    → 后台线程池启动
    → 每 5 秒轮询 → SSE 推送 task_running 事件
    → 完成 → SSE 推送 task_completed → Lead Agent 拿到结果
    → Lead Agent 汇总两个结果，写最终报告

  异常流程 A（bash sub-agent 被禁）：
    task(subagent_type="bash", prompt="rm -rf /")
    → is_host_bash_allowed() 返回 False
    → 返回 "Error: Host bash is disabled" → 不执行

  异常流程 B（超时）：
    sub-agent 执行了 20 分钟还没完成
    → config.timeout_seconds=900（15分钟）+ 60s 缓冲 = 960s
    → 轮询超过 (960/5)=192 次 → 判定超时
    → SSE 推送 task_timed_out → Lead Agent 拿到超时错误

  异常流程 C（用户取消）：
    用户在 UI 上取消了任务
    → asyncio.CancelledError 被捕获
    → request_cancel_background_task() 通知 sub-agent 停
    → 等 sub-agent 到达终态 → 报告 token 用量 → 清理

【在链中的位置】
  调用者：Lead Agent（Ultra 模式，subagent_enabled=True）
  注册位置：tools.py 的 SUBAGENT_TOOLS（条件加载）
  下游：SubagentExecutor → 后台线程池 → 独立的 Agent 实例
  SSE 事件：task_started / task_running / task_completed / task_failed / task_cancelled / task_timed_out

---
Task tool for delegating work to subagents.
"""

import asyncio
import logging
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, Annotated, Any, cast

from langchain.tools import InjectedToolCallId, tool
from langchain_core.callbacks import BaseCallbackManager
from langgraph.config import get_stream_writer

from deerflow.config import get_app_config
from deerflow.sandbox.security import LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE, is_host_bash_allowed
from deerflow.subagents import SubagentExecutor, get_available_subagent_names, get_subagent_config
from deerflow.subagents.config import resolve_subagent_model_name
from deerflow.subagents.executor import (
    SubagentStatus,
    cleanup_background_task,
    get_background_task_result,
    request_cancel_background_task,
)
from deerflow.tools.types import Runtime

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

# yyds: sub-agent 的 token 用量缓存（按 tool_call_id 索引）
#   sub-agent 执行完后，token 用量先缓存到这里，
#   TokenUsageMiddleware 后续从缓存取走，写回父 agent 的 AIMessage
_subagent_usage_cache: dict[str, dict[str, int]] = {}


def _token_usage_cache_enabled(app_config: "AppConfig | None") -> bool:
    """yyds: 检查 config.yaml 的 token_usage.enabled 是否开启。"""
    if app_config is None:
        try:
            app_config = get_app_config()
        except FileNotFoundError:
            return False
    return bool(getattr(getattr(app_config, "token_usage", None), "enabled", False))


def _cache_subagent_usage(tool_call_id: str, usage: dict | None, *, enabled: bool = True) -> None:
    """yyds: 缓存 sub-agent 的 token 用量（如果开启的话）。"""
    if enabled and usage:
        _subagent_usage_cache[tool_call_id] = usage


def pop_cached_subagent_usage(tool_call_id: str) -> dict | None:
    """yyds: TokenUsageMiddleware 调这个取走缓存的 token 用量。"""
    return _subagent_usage_cache.pop(tool_call_id, None)


def _is_subagent_terminal(result: Any) -> bool:
    """yyds: sub-agent 是否到了终态（可以安全清理了）。

    四个终态：COMPLETED / FAILED / CANCELLED / TIMED_OUT
    或者有 completed_at 时间戳（兜底）。
    """
    return result.status in {SubagentStatus.COMPLETED, SubagentStatus.FAILED, SubagentStatus.CANCELLED, SubagentStatus.TIMED_OUT} or getattr(result, "completed_at", None) is not None


async def _await_subagent_terminal(task_id: str, max_polls: int) -> Any | None:
    """yyds: 轮询等待 sub-agent 到达终态（用于取消后等最终 token 用量）。

    最多轮询 max_polls 次，每次等 5 秒。
    到达终态 → 返回 result；超时 → 返回 None。
    """
    for _ in range(max_polls):
        result = get_background_task_result(task_id)
        if result is None:
            return None
        if _is_subagent_terminal(result):
            return result
        await asyncio.sleep(5)
    return None


async def _deferred_cleanup_subagent_task(task_id: str, trace_id: str, max_polls: int) -> None:
    """yyds: 延迟清理 — sub-agent 被取消后，等它到达终态再清理资源。

    为什么需要延迟？取消只是发了个信号，sub-agent 可能还在跑。
    等它真的停了再清理，避免资源泄露。
    """
    cleanup_poll_count = 0
    while True:
        result = get_background_task_result(task_id)
        if result is None:
            return
        if _is_subagent_terminal(result):
            cleanup_background_task(task_id)
            return
        if cleanup_poll_count >= max_polls:
            logger.warning(f"[trace={trace_id}] Deferred cleanup for task {task_id} timed out after {cleanup_poll_count} polls")
            return
        await asyncio.sleep(5)
        cleanup_poll_count += 1


def _log_cleanup_failure(cleanup_task: asyncio.Task[None], *, trace_id: str, task_id: str) -> None:
    if cleanup_task.cancelled():
        return

    exc = cleanup_task.exception()
    if exc is not None:
        logger.error(f"[trace={trace_id}] Deferred cleanup failed for task {task_id}: {exc}")


def _schedule_deferred_subagent_cleanup(task_id: str, trace_id: str, max_polls: int) -> None:
    """yyds: 创建一个后台 asyncio Task 来做延迟清理（不阻塞当前流程）。"""
    logger.debug(f"[trace={trace_id}] Scheduling deferred cleanup for cancelled task {task_id}")
    cleanup_task = asyncio.create_task(_deferred_cleanup_subagent_task(task_id, trace_id, max_polls))
    cleanup_task.add_done_callback(lambda task: _log_cleanup_failure(task, trace_id=trace_id, task_id=task_id))


def _find_usage_recorder(runtime: Any) -> Any | None:
    """yyds: 从 runtime.config["callbacks"] 里找到 token 用量记录器。

    找到的是 RunJournal 回调，它有 record_external_llm_usage_records 方法，
    可以把 sub-agent 的 token 用量汇总到父 agent 的运行记录里。

    LangChain may pass ``config["callbacks"]`` in three different shapes:

    - ``None`` (no callbacks registered): no recorder.
    - A plain ``list[BaseCallbackHandler]``: iterate it directly.
    - A ``BaseCallbackManager`` instance (e.g. ``AsyncCallbackManager`` on async
      tool runs): managers are not iterable, so we unwrap ``.handlers`` first.

    Any other shape (e.g. a single handler object accidentally passed without a
    list wrapper) cannot be iterated safely; treat it as "no recorder" rather
    than raise.
    """
    if runtime is None:
        return None
    config = getattr(runtime, "config", None)
    if not isinstance(config, dict):
        return None
    callbacks = config.get("callbacks")
    if isinstance(callbacks, BaseCallbackManager):
        callbacks = callbacks.handlers
    if not callbacks:
        return None
    if not isinstance(callbacks, list):
        return None
    for cb in callbacks:
        if hasattr(cb, "record_external_llm_usage_records"):
            return cb
    return None


def _summarize_usage(records: list[dict] | None) -> dict | None:
    """yyds: 把多条 token 用量记录汇总成一条（input/output/total 之和）。

    sub-agent 可能调了多次 LLM，每次一条记录 → 汇总成一条给 SSE 事件。
    """
    if not records:
        return None
    return {
        "input_tokens": sum(r.get("input_tokens", 0) or 0 for r in records),
        "output_tokens": sum(r.get("output_tokens", 0) or 0 for r in records),
        "total_tokens": sum(r.get("total_tokens", 0) or 0 for r in records),
    }


def _report_subagent_usage(runtime: Any, result: Any) -> None:
    """yyds: 把 sub-agent 的 token 用量报告给父 agent 的 RunJournal。

    防重复：usage_reported=True 表示已经报告过了，不会再报。
    """
    if getattr(result, "usage_reported", True):
        return
    records = getattr(result, "token_usage_records", None) or []
    if not records:
        return
    journal = _find_usage_recorder(runtime)
    if journal is None:
        logger.debug("No usage recorder found in runtime callbacks — subagent token usage not recorded")
        return
    try:
        journal.record_external_llm_usage_records(records)
        result.usage_reported = True
    except Exception:
        logger.warning("Failed to report subagent token usage", exc_info=True)


def _get_runtime_app_config(runtime: Any) -> "AppConfig | None":
    """yyds: 从 runtime.context 里取 app_config（如果有）。"""
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        app_config = context.get("app_config")
        if app_config is not None:
            return cast("AppConfig", app_config)
    return None


def _merge_skill_allowlists(parent: list[str] | None, child: list[str] | None) -> list[str] | None:
    """yyds: 技能白名单取交集 — sub-agent 只能用父 agent 允许的技能。

    三个规则：
      父 None → 子的列表（父不限制，用子的）
      子 None → 父的列表（子不限制，用父的）
      都有值 → 取交集（子想用的 ∩ 父允许的）

    例子：
      父 = ["web-search", "data-analysis", "code-gen"]
      子 = ["web-search", "image-gen"]
      → 交集 = ["web-search"]（image-gen 父没允许，不能用）
    """
    if parent is None:
        return child
    if child is None:
        return list(parent)

    parent_set = set(parent)
    return [skill for skill in child if skill in parent_set]


@tool("task", parse_docstring=True)
async def task_tool(
    runtime: Runtime,
    description: str,
    prompt: str,
    subagent_type: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> str:
    """yyds: Sub-Agent 委派工具 — Lead Agent 把子任务交给专门的 sub-agent。

    内置 sub-agent 类型：
      general-purpose — 万能型，能推理能调工具，适合复杂多步任务
      bash — 命令执行专家，只在 allow_host_bash 或沙箱环境下可用

    还可以在 config.yaml 的 subagents.custom_agents 里定义自定义类型。

    参数：
      description: 简短描述（3-5 个词），用于日志和前端显示
      prompt: 给 sub-agent 的任务描述，越具体越好
      subagent_type: sub-agent 类型（"general-purpose" / "bash" / 自定义名称）

    例子：
      task(description="搜索 LangGraph", prompt="搜索 LangGraph 架构...",
           subagent_type="general-purpose")
      → sub-agent 在后台执行 → 每 5s 轮询 → SSE 推进度 → 返回结果

    执行步骤：
      ① 获取 sub-agent 配置 + 安全检查
      ② 从 runtime 提取父 agent 上下文（沙箱/线程/模型/技能）
      ③ 技能白名单取交集
      ④ 获取 sub-agent 的工具列表（subagent_enabled=False，防递归）
      ⑤ 创建 SubagentExecutor → execute_async() 后台启动
      ⑥ 轮询循环：每 5s 检查结果 → SSE 推进度 → 终态时返回

    ---
    Delegate a task to a specialized subagent that runs in its own context.

    Subagents help you:
    - Preserve context by keeping exploration and implementation separate
    - Handle complex multi-step tasks autonomously
    - Execute commands or operations in isolated contexts

    Built-in subagent types:
    - **general-purpose**: A capable agent for complex, multi-step tasks that require
      both exploration and action. Use when the task requires complex reasoning,
      multiple dependent steps, or would benefit from isolated context.
    - **bash**: Command execution specialist for running bash commands. This is only
      available when host bash is explicitly allowed or when using an isolated shell
      sandbox such as `AioSandboxProvider`.

    Additional custom subagent types may be defined in config.yaml under
    `subagents.custom_agents`. Each custom type can have its own system prompt,
    tools, skills, model, and timeout configuration. If an unknown subagent_type
    is provided, the error message will list all available types.

    When to use this tool:
    - Complex tasks requiring multiple steps or tools
    - Tasks that produce verbose output
    - When you want to isolate context from the main conversation
    - Parallel research or exploration tasks

    When NOT to use this tool:
    - Simple, single-step operations (use tools directly)
    - Tasks requiring user interaction or clarification

    Args:
        description: A short (3-5 word) description of the task for logging/display. ALWAYS PROVIDE THIS PARAMETER FIRST.
        prompt: The task description for the subagent. Be specific and clear about what needs to be done. ALWAYS PROVIDE THIS PARAMETER SECOND.
        subagent_type: The type of subagent to use. ALWAYS PROVIDE THIS PARAMETER THIRD.
    """
    # ── ① 获取配置 + 安全检查 ──────────────────────────────────────
    runtime_app_config = _get_runtime_app_config(runtime)
    cache_token_usage = _token_usage_cache_enabled(runtime_app_config)
    available_subagent_names = get_available_subagent_names(app_config=runtime_app_config) if runtime_app_config is not None else get_available_subagent_names()

    # 获取 sub-agent 的配置（超时、模型、技能等）
    config = get_subagent_config(subagent_type, app_config=runtime_app_config) if runtime_app_config is not None else get_subagent_config(subagent_type)
    if config is None:
        available = ", ".join(available_subagent_names)
        return f"Error: Unknown subagent type '{subagent_type}'. Available: {available}"

    # bash 类型 sub-agent 需要额外检查：宿主机 bash 是否被允许
    if subagent_type == "bash":
        host_bash_allowed = is_host_bash_allowed(runtime_app_config) if runtime_app_config is not None else is_host_bash_allowed()
        if not host_bash_allowed:
            return f"Error: {LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE}"

    # ── ② 从 runtime 提取父 agent 上下文 ──────────────────────────
    overrides: dict = {}

    sandbox_state = None
    thread_data = None
    thread_id = None
    parent_model = None
    trace_id = None
    metadata: dict = {}

    if runtime is not None:
        # 提取沙箱状态（sub-agent 需要共享同一个沙箱）
        sandbox_state = runtime.state.get("sandbox")
        # 提取工作目录（sub-agent 需要共享同一个工作空间）
        thread_data = runtime.state.get("thread_data")
        # 提取线程 ID
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id is None:
            thread_id = runtime.config.get("configurable", {}).get("thread_id")

        # 提取父 agent 的模型名（sub-agent 可以继承）
        metadata = runtime.config.get("metadata", {})
        parent_model = metadata.get("model_name")

        # 生成或复用 trace_id（分布式追踪用）
        trace_id = metadata.get("trace_id") or str(uuid.uuid4())[:8]

    # ── ③ 技能白名单取交集 ──────────────────────────────────────────
    parent_available_skills = metadata.get("available_skills")
    if parent_available_skills is not None:
        overrides["skills"] = _merge_skill_allowlists(list(parent_available_skills), config.skills)

    if overrides:
        config = replace(config, **overrides)

    # ── ④ 获取 sub-agent 的工具列表（防递归）─────────────────────
    from deerflow.tools import get_available_tools

    parent_tool_groups = metadata.get("tool_groups")
    resolved_app_config = runtime_app_config
    if config.model == "inherit" and parent_model is None and resolved_app_config is None:
        resolved_app_config = get_app_config()
    effective_model = resolve_subagent_model_name(config, parent_model, app_config=resolved_app_config)

    # subagent_enabled=False → sub-agent 不会再拿到 task_tool → 无法递归嵌套
    available_tools_kwargs = {
        "model_name": effective_model,
        "groups": parent_tool_groups,
        "subagent_enabled": False,
    }
    if resolved_app_config is not None:
        available_tools_kwargs["app_config"] = resolved_app_config
    tools = get_available_tools(**available_tools_kwargs)

    # ── ⑤ 创建 SubagentExecutor → 后台启动 ─────────────────────────
    executor_kwargs = {
        "config": config,
        "tools": tools,
        "parent_model": parent_model,
        "sandbox_state": sandbox_state,
        "thread_data": thread_data,
        "thread_id": thread_id,
        "trace_id": trace_id,
    }
    if resolved_app_config is not None:
        executor_kwargs["app_config"] = resolved_app_config
    executor = SubagentExecutor(**executor_kwargs)

    # execute_async() 把任务丢到后台线程池，立即返回 task_id
    task_id = executor.execute_async(prompt, task_id=tool_call_id)

    # ── ⑥ 轮询循环 ──────────────────────────────────────────────────
    poll_count = 0
    last_status = None
    last_message_count = 0
    # 超时安全网：配置的超时 + 60s 缓冲，每 5s 检查一次
    max_poll_count = (config.timeout_seconds + 60) // 5

    logger.info(f"[trace={trace_id}] Started background task {task_id} (subagent={subagent_type}, timeout={config.timeout_seconds}s, polling_limit={max_poll_count} polls)")

    writer = get_stream_writer()
    # 告诉前端：任务开始了
    writer({"type": "task_started", "task_id": task_id, "description": description})

    try:
        while True:
            result = get_background_task_result(task_id)

            # sub-agent 任务消失了（不该发生）
            if result is None:
                logger.error(f"[trace={trace_id}] Task {task_id} not found in background tasks")
                writer({"type": "task_failed", "task_id": task_id, "error": "Task disappeared from background tasks"})
                cleanup_background_task(task_id)
                return f"Error: Task {task_id} disappeared from background tasks"

            # 状态变化时记日志
            if result.status != last_status:
                logger.info(f"[trace={trace_id}] Task {task_id} status: {result.status.value}")
                last_status = result.status

            # 有新的 AI 消息 → 推给前端（task_running 事件）
            ai_messages = result.ai_messages or []
            current_message_count = len(ai_messages)
            if current_message_count > last_message_count:
                for i in range(last_message_count, current_message_count):
                    message = ai_messages[i]
                    writer(
                        {
                            "type": "task_running",
                            "task_id": task_id,
                            "message": message,
                            "message_index": i + 1,
                            "total_messages": current_message_count,
                        }
                    )
                    logger.info(f"[trace={trace_id}] Task {task_id} sent message #{i + 1}/{current_message_count}")
                last_message_count = current_message_count

            # 检查终态
            usage = _summarize_usage(getattr(result, "token_usage_records", None))
            if result.status == SubagentStatus.COMPLETED:
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                writer({"type": "task_completed", "task_id": task_id, "result": result.result, "usage": usage})
                logger.info(f"[trace={trace_id}] Task {task_id} completed after {poll_count} polls")
                cleanup_background_task(task_id)
                return f"Task Succeeded. Result: {result.result}"
            elif result.status == SubagentStatus.FAILED:
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                writer({"type": "task_failed", "task_id": task_id, "error": result.error, "usage": usage})
                logger.error(f"[trace={trace_id}] Task {task_id} failed: {result.error}")
                cleanup_background_task(task_id)
                return f"Task failed. Error: {result.error}"
            elif result.status == SubagentStatus.CANCELLED:
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                writer({"type": "task_cancelled", "task_id": task_id, "error": result.error, "usage": usage})
                logger.info(f"[trace={trace_id}] Task {task_id} cancelled: {result.error}")
                cleanup_background_task(task_id)
                return "Task cancelled by user."
            elif result.status == SubagentStatus.TIMED_OUT:
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                writer({"type": "task_timed_out", "task_id": task_id, "error": result.error, "usage": usage})
                logger.warning(f"[trace={trace_id}] Task {task_id} timed out: {result.error}")
                cleanup_background_task(task_id)
                return f"Task timed out. Error: {result.error}"

            # 还在跑，等 5 秒再查
            await asyncio.sleep(5)
            poll_count += 1

            # Polling timeout as a safety net (in case thread pool timeout doesn't work)
            # Set to execution timeout + 60s buffer, in 5s poll intervals
            # This catches edge cases where the background task gets stuck
            if poll_count > max_poll_count:
                timeout_minutes = config.timeout_seconds // 60
                logger.error(f"[trace={trace_id}] Task {task_id} polling timed out after {poll_count} polls (should have been caught by thread pool timeout)")
                _report_subagent_usage(runtime, result)
                usage = _summarize_usage(getattr(result, "token_usage_records", None))
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                writer({"type": "task_timed_out", "task_id": task_id, "usage": usage})
                # The task may still be running in the background. Signal cooperative
                # cancellation and schedule deferred cleanup to remove the entry from
                # _background_tasks once the background thread reaches a terminal state.
                request_cancel_background_task(task_id)
                _schedule_deferred_subagent_cleanup(task_id, trace_id, max_poll_count)
                return f"Task polling timed out after {timeout_minutes} minutes. This may indicate the background task is stuck. Status: {result.status.value}"

    # ── 用户取消时的处理 ──────────────────────────────────────────────
    except asyncio.CancelledError:
        # 通知 sub-agent 停下来
        request_cancel_background_task(task_id)

        # 等它到达终态（用 asyncio.shield 防止被二次取消打断）
        # 这样能拿到最终的 token 用量快照
        terminal_result = None
        try:
            terminal_result = await asyncio.shield(_await_subagent_terminal(task_id, max_poll_count))
        except asyncio.CancelledError:
            pass

        # 报告 token 用量（即使超时也要报）
        final_result = terminal_result or get_background_task_result(task_id)
        if final_result is not None:
            _report_subagent_usage(runtime, final_result)
        if final_result is not None and _is_subagent_terminal(final_result):
            cleanup_background_task(task_id)
        else:
            # 还没到终态 → 创建延迟清理任务，后台等它停了再清理
            _schedule_deferred_subagent_cleanup(task_id, trace_id, max_poll_count)
        _subagent_usage_cache.pop(tool_call_id, None)
        raise
    except Exception:
        _subagent_usage_cache.pop(tool_call_id, None)
        raise
