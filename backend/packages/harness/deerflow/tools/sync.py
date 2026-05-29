"""yyds: 异步→同步桥接 — 让 async 工具能在同步调用路径下工作。

【大白话讲清楚】
  LangGraph 有两条调用路径：async 和 sync。大部分工具是 async 的（比如 MCP 工具、task_tool），
  但有些场景下 LangGraph 走的是 sync 路径（比如某些旧的 agent 调用方式）。

  问题：async 函数不能直接在 sync 代码里调用。直接 asyncio.run()？
  → 如果当前已经有一个 event loop 在跑（比如在 FastAPI 里），asyncio.run() 会报错：
    "This event loop is already running"。

  解决方案：
    如果没有 event loop → 直接 asyncio.run()，简单省事
    如果有 event loop 在跑 → 把 async 函数丢到另一个线程的 event loop 里跑，
    当前线程阻塞等结果（future.result()）

【具体例子】
  场景 A — 纯脚本调用（没有 event loop）：
    sync_wrapper(bash_tool, "ls")
    → asyncio.run() 在当前线程创建新 loop → 执行 → 返回结果 ✅

  场景 B — 在 FastAPI 里调用（已有 event loop）：
    # 当前线程的 event loop 正在跑 FastAPI
    sync_wrapper(mcp_tool, "search")
    → asyncio.get_running_loop() 拿到当前 loop
    → 把 mcp_tool 丢到线程池的另一个线程 → 那边 asyncio.run() → 执行
    → 当前线程阻塞等 future.result() → 返回结果 ✅
    → 不会报 "event loop already running"

【在链中的位置】
  调用者：tools.py 的 _ensure_sync_invocable_tool()
    → 检测到工具只有 coroutine 没有 func → 调用 make_sync_tool_wrapper() 补上 func
    → 之后 sync 路径就能正常调用这个工具了

---
Utilities for invoking async tools from synchronous agent paths.
"""

import asyncio
import atexit
import concurrent.futures
import contextvars
import functools
import logging
from collections.abc import Callable
from typing import Any, get_type_hints

from langchain_core.runnables import RunnableConfig

logger = logging.getLogger(__name__)

# yyds: 共享线程池，专门给 sync wrapper 用的。
#   max_workers=10：同时最多 10 个工具在后台线程跑 async
#   thread_name_prefix="tool-sync"：日志里能看到是哪个线程
#   atexit 注册：进程退出时不等线程结束（wait=False），避免卡住
_SYNC_TOOL_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=10, thread_name_prefix="tool-sync")

atexit.register(lambda: _SYNC_TOOL_EXECUTOR.shutdown(wait=False))


def _get_runnable_config_param(func: Callable[..., Any]) -> str | None:
    """Return the coroutine parameter that expects LangChain RunnableConfig."""
    if isinstance(func, functools.partial):
        func = func.func

    try:
        type_hints = get_type_hints(func)
    except Exception:
        return None

    for name, type_ in type_hints.items():
        if type_ is RunnableConfig:
            return name
    return None


def make_sync_tool_wrapper(coro: Callable[..., Any], tool_name: str) -> Callable[..., Any]:
    """yyds: 把一个 async 函数包装成 sync 函数 — 让 async 工具在 sync 路径下也能调用。

    两层判断：
      ① 没有正在跑的 event loop → asyncio.run() 直接执行（最简单）
      ② 有 event loop 在跑 → 丢到线程池 → 那边 asyncio.run() → 阻塞等结果

    为什么要线程池？不能直接 loop.run_until_complete() 吗？
      → 不行。如果当前 loop 正在跑（比如 FastAPI），run_until_complete() 会报错。
      → 线程池里的线程有自己的 event loop，不会冲突。

    Args:
        coro: Async callable backing a LangChain tool.
        tool_name: Tool name used in error logs.

    Returns:
        A sync callable suitable for ``BaseTool.func``.

    Notes:
        If ``coro`` declares a ``RunnableConfig`` parameter, this wrapper
        exposes ``config: RunnableConfig`` so LangChain can inject runtime
        config and then forwards it to the coroutine's detected config
        parameter. This covers DeerFlow's current config-sensitive tools, such
        as ``invoke_acp_agent``.
    """
    config_param = _get_runnable_config_param(coro)

    def run_coroutine(*args: Any, **kwargs: Any) -> Any:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        try:
            if loop is not None and loop.is_running():
                # ② 有 loop 在跑 → 丢到线程池，那边 asyncio.run()
                #    线程池的线程没有自己的 loop，asyncio.run() 会创建新的
                #    future.result() 阻塞当前线程等结果
                context = contextvars.copy_context()
                future = _SYNC_TOOL_EXECUTOR.submit(context.run, lambda: asyncio.run(coro(*args, **kwargs)))
                return future.result()
            # ① 没有 loop → 直接 asyncio.run()
            return asyncio.run(coro(*args, **kwargs))
        except Exception as e:
            logger.error("Error invoking tool %r via sync wrapper: %s", tool_name, e, exc_info=True)
            raise

    if config_param:

        def sync_wrapper(*args: Any, config: RunnableConfig = None, **kwargs: Any) -> Any:
            if config is not None or config_param not in kwargs:
                kwargs[config_param] = config
            return run_coroutine(*args, **kwargs)

        return sync_wrapper

    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        return run_coroutine(*args, **kwargs)

    return sync_wrapper
