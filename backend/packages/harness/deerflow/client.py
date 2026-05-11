# yyds:
# 本文件实现了 DeerFlowClient —— DeerFlow 的嵌入式 Python 客户端。
# 核心设计理念：CLI-first（命令行优先），无需启动 LangGraph Server 或 Gateway API 即可直接调用 Agent。
#
# 架构概览：
#   DeerFlowClient
#     ├── 同步 API（stream / chat）：内部直接调用 agent.stream()，调用方无需接触 asyncio
#     ├── 线程管理：list_threads / get_thread，通过 checkpointer 持久化多轮对话状态
#     ├── 配置查询：list_models / list_skills / get_memory 等，与 Gateway API 返回格式一致
#     ├── MCP & Skills 管理：动态更新 extensions_config.json 并热重载
#     ├── 文件上传：支持 PDF/PPT/Excel/Word 自动转换为 Markdown
#     └── Artifacts：读取 Agent 生成的产物文件
#
# 关键设计决策：
#   1. Agent 延迟创建（lazy init）—— 首次调用时才构建，配置变更时自动重建
#   2. 流式事件类型与 LangGraph SSE 协议对齐，方便 HTTP/嵌入式模式切换
#   3. 不复用 Gateway 的 run_agent 流水线，因为同步生成器 vs async、
#      进程内直接传递 Python 对象 vs JSON/SSE 序列化等差异使得共享代码得不偿失
#   4. 消息去重：messages 模式和 values 模式会产生重叠内容，通过 seen_ids / streamed_ids 去重

"""DeerFlowClient — Embedded Python client for DeerFlow agent system.

Provides direct programmatic access to DeerFlow's agent capabilities
without requiring LangGraph Server or Gateway API processes.

Usage:
    from deerflow.client import DeerFlowClient

    client = DeerFlowClient()
    response = client.chat("Analyze this paper for me", thread_id="my-thread")
    print(response)

    # Streaming
    for event in client.stream("hello"):
        print(event)
"""

import asyncio
import json
import logging
import mimetypes
import os
import shutil
import tempfile
import uuid
from collections.abc import Generator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from deerflow.agents.lead_agent.agent import _build_middlewares
from deerflow.agents.lead_agent.prompt import apply_prompt_template
from deerflow.agents.thread_state import ThreadState
from deerflow.config.agents_config import AGENT_NAME_PATTERN
from deerflow.config.app_config import get_app_config, reload_app_config
from deerflow.config.extensions_config import ExtensionsConfig, SkillStateConfig, get_extensions_config, reload_extensions_config
from deerflow.config.paths import get_paths
from deerflow.models import create_chat_model
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.skills.storage import get_or_new_skill_storage
from deerflow.tracing import build_tracing_callbacks, inject_langfuse_metadata
from deerflow.uploads.manager import (
    claim_unique_filename,
    delete_file_safe,
    enrich_file_listing,
    ensure_uploads_dir,
    get_uploads_dir,
    list_files_in_dir,
    upload_artifact_url,
    upload_virtual_path,
)

logger = logging.getLogger(__name__)


StreamEventType = Literal["values", "messages-tuple", "custom", "end"]


# yyds: 流式事件的数据结构，与 LangGraph SSE 协议的 event type 对齐，用于统一的流式消费接口
@dataclass
class StreamEvent:
    """A single event from the streaming agent response.

    Event types align with the LangGraph SSE protocol:
        - ``"values"``: Full state snapshot (title, messages, artifacts).
        - ``"messages-tuple"``: Per-message update (AI text, tool calls, tool results).
        - ``"end"``: Stream finished.

    Attributes:
        type: Event type.
        data: Event payload. Contents vary by type.
    """

    type: StreamEventType
    data: dict[str, Any] = field(default_factory=dict)


# yyds: 核心客户端类 —— 封装 Agent 创建、对话流、线程管理、配置查询、文件上传等全部能力，
# 提供 Pythonic 的同步 API，可通过 `from deerflow import DeerFlowClient` 直接编程调用
class DeerFlowClient:
    """Embedded Python client for DeerFlow agent system.

    Provides direct programmatic access to DeerFlow's agent capabilities
    without requiring LangGraph Server or Gateway API processes.

    Note:
        Multi-turn conversations require a ``checkpointer``. Without one,
        each ``stream()`` / ``chat()`` call is stateless — ``thread_id``
        is only used for file isolation (uploads / artifacts).

        The system prompt (including date, memory, and skills context) is
        generated when the internal agent is first created and cached until
        the configuration key changes. Call :meth:`reset_agent` to force
        a refresh in long-running processes.

    Example::

        from deerflow.client import DeerFlowClient

        client = DeerFlowClient()

        # Simple one-shot
        print(client.chat("hello"))

        # Streaming
        for event in client.stream("hello"):
            print(event.type, event.data)

        # Configuration queries
        print(client.list_models())
        print(client.list_skills())
    """

    # yyds: 延迟初始化构造器 —— 仅加载配置和保存参数，Agent 在首次调用时按需构建（避免 import 时就触发 LLM 连接）
    def __init__(
        self,
        config_path: str | None = None,
        checkpointer=None,
        *,
        model_name: str | None = None,
        thinking_enabled: bool = True,
        subagent_enabled: bool = False,
        plan_mode: bool = False,
        agent_name: str | None = None,
        available_skills: set[str] | None = None,
        middlewares: Sequence[AgentMiddleware] | None = None,
        environment: str | None = None,
    ):
        """Initialize the client.

        Loads configuration but defers agent creation to first use.

        Args:
            config_path: Path to config.yaml. Uses default resolution if None.
            checkpointer: LangGraph checkpointer instance for state persistence.
                Required for multi-turn conversations on the same thread_id.
                Without a checkpointer, each call is stateless.
            model_name: Override the default model name from config.
            thinking_enabled: Enable model's extended thinking.
            subagent_enabled: Enable subagent delegation.
            plan_mode: Enable TodoList middleware for plan mode.
            agent_name: Name of the agent to use.
            available_skills: Optional set of skill names to make available. If None (default), all scanned skills are available.
            middlewares: Optional list of custom middlewares to inject into the agent.
            environment: Deployment environment label that ends up in
                ``langfuse_tags`` (e.g. ``"production"`` / ``"staging"``).
                When ``None`` the worker/client falls back to the
                ``DEER_FLOW_ENV`` or ``ENVIRONMENT`` env vars. Pass an
                explicit value for programmatic callers that do not want
                env-var coupling.
        """
        if config_path is not None:
            reload_app_config(config_path)
        self._app_config = get_app_config()

        if agent_name is not None and not AGENT_NAME_PATTERN.match(agent_name):
            raise ValueError(f"Invalid agent name '{agent_name}'. Must match pattern: {AGENT_NAME_PATTERN.pattern}")

        self._checkpointer = checkpointer
        self._model_name = model_name
        self._thinking_enabled = thinking_enabled
        self._subagent_enabled = subagent_enabled
        self._plan_mode = plan_mode
        self._agent_name = agent_name
        self._available_skills = set(available_skills) if available_skills is not None else None
        self._middlewares = list(middlewares) if middlewares else []
        self._environment = environment

        # Lazy agent — created on first call, recreated when config changes.
        self._agent = None
        self._agent_config_key: tuple | None = None

        # yyds: 强制重建 Agent —— 长运行进程中外部变更（如 memory 更新、skill 安装）后调用以刷新系统提示词和工具集

    def reset_agent(self) -> None:
        """Force the internal agent to be recreated on the next call.

        Use this after external changes (e.g. memory updates, skill
        installations) that should be reflected in the system prompt
        or tool set.
        """
        self._agent = None
        self._agent_config_key = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    # yyds: 原子写入 JSON —— 先写临时文件再 replace，防止写入中途崩溃导致配置文件损坏
    @staticmethod
    def _atomic_write_json(path: Path, data: dict) -> None:
        """Write JSON to *path* atomically (temp file + replace)."""
        fd = tempfile.NamedTemporaryFile(
            mode="w",
            dir=path.parent,
            suffix=".tmp",
            delete=False,
        )
        try:
            json.dump(data, fd, indent=2)
            fd.close()
            Path(fd.name).replace(path)
        except BaseException:
            fd.close()
            Path(fd.name).unlink(missing_ok=True)
            raise

        # yyds: 构建 RunnableConfig —— 将 thread_id、model、thinking 等运行参数打包为 LangGraph agent 所需的配置对象

    def _get_runnable_config(self, thread_id: str, **overrides) -> RunnableConfig:
        """Build a RunnableConfig for agent invocation."""
        configurable = {
            "thread_id": thread_id,
            "model_name": overrides.get("model_name", self._model_name),
            "thinking_enabled": overrides.get("thinking_enabled", self._thinking_enabled),
            "is_plan_mode": overrides.get("plan_mode", self._plan_mode),
            "subagent_enabled": overrides.get("subagent_enabled", self._subagent_enabled),
        }
        return RunnableConfig(
            configurable=configurable,
            recursion_limit=overrides.get("recursion_limit", 100),
        )

        # yyds: Agent 懒加载 + 配置缓存 —— 根据模型名、thinking、plan_mode 等参数生成 cache key，
        # 仅在 key 变化时重建 Agent，避免每次调用都重新创建（系统提示词、工具集等构建成本高）

    def _ensure_agent(self, config: RunnableConfig):
        """Create (or recreate) the agent when config-dependent params change."""
        cfg = config.get("configurable", {})
        key = (
            cfg.get("model_name"),
            cfg.get("thinking_enabled"),
            cfg.get("is_plan_mode"),
            cfg.get("subagent_enabled"),
            self._agent_name,
            frozenset(self._available_skills) if self._available_skills is not None else None,
        )

        if self._agent is not None and self._agent_config_key == key:
            return

        thinking_enabled = cfg.get("thinking_enabled", True)
        model_name = cfg.get("model_name")
        subagent_enabled = cfg.get("subagent_enabled", False)
        max_concurrent_subagents = cfg.get("max_concurrent_subagents", 3)

        kwargs: dict[str, Any] = {
            # attach_tracing=False because ``stream()`` injects tracing
            # callbacks at the graph invocation root so a single embedded run
            # produces one trace with correct session_id / user_id propagation.
            # Attaching them again on the model would emit duplicate spans.
            "model": create_chat_model(name=model_name, thinking_enabled=thinking_enabled, attach_tracing=False),
            "tools": self._get_tools(model_name=model_name, subagent_enabled=subagent_enabled),
            "middleware": _build_middlewares(config, model_name=model_name, agent_name=self._agent_name, custom_middlewares=self._middlewares),
            "system_prompt": apply_prompt_template(
                subagent_enabled=subagent_enabled,
                max_concurrent_subagents=max_concurrent_subagents,
                agent_name=self._agent_name,
                available_skills=self._available_skills,
            ),
            "state_schema": ThreadState,
        }
        checkpointer = self._checkpointer
        if checkpointer is None:
            from deerflow.runtime.checkpointer import get_checkpointer

            checkpointer = get_checkpointer()
        if checkpointer is not None:
            kwargs["checkpointer"] = checkpointer

        self._agent = create_agent(**kwargs)
        self._agent_config_key = key
        logger.info("Agent created: agent_name=%s, model=%s, thinking=%s", self._agent_name, model_name, thinking_enabled)

        # yyds: 延迟导入工具列表 —— 避免模块级循环依赖（deerflow.tools 可能反向引用 client 相关模块）

    @staticmethod
    def _get_tools(*, model_name: str | None, subagent_enabled: bool):
        """Lazy import to avoid circular dependency at module level."""
        from deerflow.tools import get_available_tools

        return get_available_tools(model_name=model_name, subagent_enabled=subagent_enabled)

        # yyds: 序列化 tool_calls —— 将 LangChain 内部的 tool_calls 结构转为与 Gateway API 一致的线格式

    @staticmethod
    def _serialize_tool_calls(tool_calls) -> list[dict]:
        """Reshape LangChain tool_calls into the wire format used in events."""
        return [{"name": tc["name"], "args": tc["args"], "id": tc.get("id")} for tc in tool_calls]

        # yyds: 提取消息的 additional_kwargs（如 reasoning_content 等扩展字段），仅在有值时返回

    @staticmethod
    def _serialize_additional_kwargs(msg) -> dict[str, Any] | None:
        """Copy message additional_kwargs when present."""
        additional_kwargs = getattr(msg, "additional_kwargs", None)
        if isinstance(additional_kwargs, dict) and additional_kwargs:
            return dict(additional_kwargs)
        return None

        # yyds: 构建 AI 文本流式事件 —— 携带 token delta、usage 统计和 additional_kwargs 增量

    @staticmethod
    def _ai_text_event(msg_id: str | None, text: str, usage: dict | None, additional_kwargs: dict[str, Any] | None = None) -> "StreamEvent":
        """Build a ``messages-tuple`` AI text event."""
        data: dict[str, Any] = {"type": "ai", "content": text, "id": msg_id}
        if usage:
            data["usage_metadata"] = usage
        if additional_kwargs:
            data["additional_kwargs"] = additional_kwargs
        return StreamEvent(type="messages-tuple", data=data)

        # yyds: 构建 AI 工具调用事件 —— 当 Agent 决定调用工具时发出，包含工具名、参数和调用 ID

    @staticmethod
    def _ai_tool_calls_event(msg_id: str | None, tool_calls, additional_kwargs: dict[str, Any] | None = None) -> "StreamEvent":
        """Build a ``messages-tuple`` AI tool-calls event."""
        data: dict[str, Any] = {
            "type": "ai",
            "content": "",
            "id": msg_id,
            "tool_calls": DeerFlowClient._serialize_tool_calls(tool_calls),
        }
        if additional_kwargs:
            data["additional_kwargs"] = additional_kwargs
        return StreamEvent(type="messages-tuple", data=data)

        # yyds: 构建工具结果事件 —— 将 ToolMessage（工具执行返回值）转为统一的流式事件格式

    @staticmethod
    def _tool_message_event(msg: ToolMessage) -> "StreamEvent":
        """Build a ``messages-tuple`` tool-result event from a ToolMessage."""
        return StreamEvent(
            type="messages-tuple",
            data={
                "type": "tool",
                "content": DeerFlowClient._extract_text(msg.content),
                "name": msg.name,
                "tool_call_id": msg.tool_call_id,
                "id": msg.id,
            },
        )

        # yyds: 消息序列化 —— 将 LangChain Message 对象转为纯 dict，用于 values 事件中的完整状态快照

    @staticmethod
    def _serialize_message(msg) -> dict:
        """Serialize a LangChain message to a plain dict for values events."""
        if isinstance(msg, AIMessage):
            d: dict[str, Any] = {"type": "ai", "content": msg.content, "id": getattr(msg, "id", None)}
            if msg.tool_calls:
                d["tool_calls"] = DeerFlowClient._serialize_tool_calls(msg.tool_calls)
            if getattr(msg, "usage_metadata", None):
                d["usage_metadata"] = msg.usage_metadata
            if additional_kwargs := DeerFlowClient._serialize_additional_kwargs(msg):
                d["additional_kwargs"] = additional_kwargs
            return d
        if isinstance(msg, ToolMessage):
            d = {
                "type": "tool",
                "content": DeerFlowClient._extract_text(msg.content),
                "name": getattr(msg, "name", None),
                "tool_call_id": getattr(msg, "tool_call_id", None),
                "id": getattr(msg, "id", None),
            }
            if additional_kwargs := DeerFlowClient._serialize_additional_kwargs(msg):
                d["additional_kwargs"] = additional_kwargs
            return d
        if isinstance(msg, HumanMessage):
            d = {"type": "human", "content": msg.content, "id": getattr(msg, "id", None)}
            if additional_kwargs := DeerFlowClient._serialize_additional_kwargs(msg):
                d["additional_kwargs"] = additional_kwargs
            return d
        if isinstance(msg, SystemMessage):
            d = {"type": "system", "content": msg.content, "id": getattr(msg, "id", None)}
            if additional_kwargs := DeerFlowClient._serialize_additional_kwargs(msg):
                d["additional_kwargs"] = additional_kwargs
            return d
        return {"type": "unknown", "content": str(msg), "id": getattr(msg, "id", None)}

        # yyds: 从消息 content 中提取纯文本 —— 处理 str、list[str]（delta 块）和 list[dict]（结构化块）三种格式，
        # 对短 chunk-like 字符串用空拼接（避免破坏 JSON/代码片段），对完整文本块用换行拼接

    @staticmethod
    def _extract_text(content) -> str:
        """Extract plain text from AIMessage content (str or list of blocks).

        String chunks are concatenated without separators to avoid corrupting
        token/character deltas or chunked JSON payloads. Dict-based text blocks
        are treated as full text blocks and joined with newlines to preserve
        readability.
        """
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            if content and all(isinstance(block, str) for block in content):
                chunk_like = len(content) > 1 and all(isinstance(block, str) and len(block) <= 20 and any(ch in block for ch in '{}[]":,') for block in content)
                return "".join(content) if chunk_like else "\n".join(content)

            pieces: list[str] = []
            pending_str_parts: list[str] = []

            def flush_pending_str_parts() -> None:
                if pending_str_parts:
                    pieces.append("".join(pending_str_parts))
                    pending_str_parts.clear()

            for block in content:
                if isinstance(block, str):
                    pending_str_parts.append(block)
                elif isinstance(block, dict):
                    flush_pending_str_parts()
                    text_val = block.get("text")
                    if isinstance(text_val, str):
                        pieces.append(text_val)

            flush_pending_str_parts()
            return "\n".join(pieces) if pieces else ""
        return str(content)

    # ------------------------------------------------------------------
    # Public API — threads
    # ------------------------------------------------------------------

    # yyds: 列出最近 N 条线程 —— 遍历 checkpointer 的所有 checkpoint，按 thread_id 聚合，
    # 并按创建时间降序排列，确保返回最新的对话记录
    def list_threads(self, limit: int = 10) -> dict:
        """List the recent N threads.

        Args:
            limit: Maximum number of threads to return. Default is 10.

        Returns:
            Dict with "thread_list" key containing list of thread info dicts,
            sorted by thread creation time descending.
        """
        checkpointer = self._checkpointer
        if checkpointer is None:
            from deerflow.runtime.checkpointer.provider import get_checkpointer

            checkpointer = get_checkpointer()

        thread_info_map = {}

        for cp in checkpointer.list(config=None, limit=limit):
            cfg = cp.config.get("configurable", {})
            thread_id = cfg.get("thread_id")
            if not thread_id:
                continue

            ts = cp.checkpoint.get("ts")
            checkpoint_id = cfg.get("checkpoint_id")

            if thread_id not in thread_info_map:
                channel_values = cp.checkpoint.get("channel_values", {})
                thread_info_map[thread_id] = {
                    "thread_id": thread_id,
                    "created_at": ts,
                    "updated_at": ts,
                    "latest_checkpoint_id": checkpoint_id,
                    "title": channel_values.get("title"),
                }
            else:
                # Explicitly compare timestamps to ensure accuracy when iterating over unordered namespaces.
                # Treat None as "missing" and only compare when existing values are non-None.
                if ts is not None:
                    current_created = thread_info_map[thread_id]["created_at"]
                    if current_created is None or ts < current_created:
                        thread_info_map[thread_id]["created_at"] = ts

                    current_updated = thread_info_map[thread_id]["updated_at"]
                    if current_updated is None or ts > current_updated:
                        thread_info_map[thread_id]["updated_at"] = ts
                        thread_info_map[thread_id]["latest_checkpoint_id"] = checkpoint_id
                        channel_values = cp.checkpoint.get("channel_values", {})
                        thread_info_map[thread_id]["title"] = channel_values.get("title")

        threads = list(thread_info_map.values())
        threads.sort(key=lambda x: x.get("created_at") or "", reverse=True)

        return {"thread_list": threads[:limit]}

        # yyds: 获取单个线程的完整检查点历史 —— 包括所有节点执行记录（messages、artifacts、pending_writes），
        # 按时间戳全局排序以避免子图命名空间导致的部分乱序问题

    def get_thread(self, thread_id: str) -> dict:
        """Get the complete thread record, including all node execution records.

        Args:
            thread_id: Thread ID.

        Returns:
            Dict containing the thread's full checkpoint history.
        """
        checkpointer = self._checkpointer
        if checkpointer is None:
            from deerflow.runtime.checkpointer.provider import get_checkpointer

            checkpointer = get_checkpointer()

        config = {"configurable": {"thread_id": thread_id}}
        checkpoints = []

        for cp in checkpointer.list(config):
            channel_values = dict(cp.checkpoint.get("channel_values", {}))
            if "messages" in channel_values:
                channel_values["messages"] = [self._serialize_message(m) if hasattr(m, "content") else m for m in channel_values["messages"]]

            cfg = cp.config.get("configurable", {})
            parent_cfg = cp.parent_config.get("configurable", {}) if cp.parent_config else {}

            checkpoints.append(
                {
                    "checkpoint_id": cfg.get("checkpoint_id"),
                    "parent_checkpoint_id": parent_cfg.get("checkpoint_id"),
                    "ts": cp.checkpoint.get("ts"),
                    "metadata": cp.metadata,
                    "values": channel_values,
                    "pending_writes": [{"task_id": w[0], "channel": w[1], "value": w[2]} for w in getattr(cp, "pending_writes", [])],
                }
            )

        # Sort globally by timestamp to prevent partial ordering issues caused by different namespaces (e.g., subgraphs)
        checkpoints.sort(key=lambda x: x["ts"] if x["ts"] else "")

        return {"thread_id": thread_id, "checkpoints": checkpoints}

    # ------------------------------------------------------------------
    # Public API — conversation
    # ------------------------------------------------------------------

    # yyds: 核心流式对话方法 —— 订阅 LangGraph 的 values + messages + custom 三种 stream mode，
    # 通过 seen_ids / streamed_ids 双重去重机制避免 messages 模式和 values 模式的消息重复投递，
    # 并累积 usage 统计在 end 事件中一次性返回。同步生成器设计使得调用方无需接触 asyncio。
    def stream(
        self,
        message: str,
        *,
        thread_id: str | None = None,
        **kwargs,
    ) -> Generator[StreamEvent, None, None]:
        """Stream a conversation turn, yielding events incrementally.

        Each call sends one user message and yields events until the agent
        finishes its turn. A ``checkpointer`` must be provided at init time
        for multi-turn context to be preserved across calls.

        Event types align with the LangGraph SSE protocol so that
        consumers can switch between HTTP streaming and embedded mode
        without changing their event-handling logic.

        Token-level streaming
        ~~~~~~~~~~~~~~~~~~~~~
        This method subscribes to LangGraph's ``messages`` stream mode, so
        ``messages-tuple`` events for AI text are emitted as **deltas** as
        the model generates tokens, not as one cumulative dump at node
        completion.  Each delta carries a stable ``id`` — consumers that
        want the full text must accumulate ``content`` per ``id``.
        ``chat()`` already does this for you.

        Tool calls and tool results are still emitted once per logical
        message.  ``values`` events continue to carry full state snapshots
        after each graph node finishes; AI text already delivered via the
        ``messages`` stream is **not** re-synthesized from the snapshot to
        avoid duplicate deliveries.

        Why not reuse Gateway's ``run_agent``?
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        Gateway (``runtime/runs/worker.py``) has a complete streaming
        pipeline: ``run_agent`` → ``StreamBridge`` → ``sse_consumer``.  It
        looks like this client duplicates that work, but the two paths
        serve different audiences and **cannot** share execution:

        * ``run_agent`` is ``async def`` and uses ``agent.astream()``;
          this method is a sync generator using ``agent.stream()`` so
          callers can write ``for event in client.stream(...)`` without
          touching asyncio.  Bridging the two would require spinning up
          an event loop + thread per call.
        * Gateway events are JSON-serialized by ``serialize()`` for SSE
          wire transmission.  This client yields in-process stream event
          payloads directly as Python data structures (``StreamEvent``
          with ``data`` as a plain ``dict``), without the extra
          JSON/SSE serialization layer used for HTTP delivery.
        * ``StreamBridge`` is an asyncio-queue decoupling producers from
          consumers across an HTTP boundary (``Last-Event-ID`` replay,
          heartbeats, multi-subscriber fan-out).  A single in-process
          caller with a direct iterator needs none of that.

        So ``DeerFlowClient.stream()`` is a parallel, sync, in-process
        consumer of the same ``create_agent()`` factory — not a wrapper
        around Gateway.  The two paths **should** stay in sync on which
        LangGraph stream modes they subscribe to; that invariant is
        enforced by ``tests/test_client.py::test_messages_mode_emits_token_deltas``
        rather than by a shared constant, because the three layers
        (Graph, Platform SDK, HTTP) each use their own naming
        (``messages`` vs ``messages-tuple``) and cannot literally share
        a string.

        Args:
            message: User message text.
            thread_id: Thread ID for conversation context. Auto-generated if None.
            **kwargs: Override client defaults (model_name, thinking_enabled,
                plan_mode, subagent_enabled, recursion_limit).

        Yields:
            StreamEvent with one of:
            - type="values"          data={"title": str|None, "messages": [...], "artifacts": [...]}
            - type="custom"          data={...}
            - type="messages-tuple"  data={"type": "ai", "content": <delta>, "id": str}
            - type="messages-tuple"  data={"type": "ai", "content": <delta>, "id": str, "usage_metadata": {...}}
            - type="messages-tuple"  data={"type": "ai", "content": "", "id": str, "tool_calls": [...]}
            - type="messages-tuple"  data={"type": "ai", "content": "", "id": str, "additional_kwargs": {...}}
            - type="messages-tuple"  data={"type": "tool", "content": str, "name": str, "tool_call_id": str, "id": str}
            - type="end"             data={"usage": {"input_tokens": int, "output_tokens": int, "total_tokens": int}}
        """
        if thread_id is None:
            thread_id = str(uuid.uuid4())

        config = self._get_runnable_config(thread_id, **kwargs)

        # Inject tracing callbacks and Langfuse trace metadata at the graph
        # invocation root so the embedded client matches the gateway worker's
        # behaviour: a single ``stream()`` produces one trace with all node /
        # LLM / tool calls nested under it, and the trace carries the reserved
        # ``langfuse_session_id`` / ``langfuse_user_id`` keys that the Langfuse
        # CallbackHandler lifts onto the root trace's ``sessionId`` / ``userId``.
        tracing_callbacks = build_tracing_callbacks()
        if tracing_callbacks:
            existing_callbacks = list(config.get("callbacks") or [])
            config["callbacks"] = [*existing_callbacks, *tracing_callbacks]

        configurable = config.get("configurable") or {}
        inject_langfuse_metadata(
            config,
            thread_id=thread_id,
            user_id=get_effective_user_id(),
            assistant_id=self._agent_name or "lead-agent",
            model_name=configurable.get("model_name") or self._model_name,
            environment=self._environment or os.environ.get("DEER_FLOW_ENV") or os.environ.get("ENVIRONMENT"),
        )

        self._ensure_agent(config)

        state: dict[str, Any] = {"messages": [HumanMessage(content=message)]}
        context = {"thread_id": thread_id}
        if self._agent_name:
            context["agent_name"] = self._agent_name

        seen_ids: set[str] = set()
        # Cross-mode handoff: ids already streamed via LangGraph ``messages``
        # mode so the ``values`` path skips re-synthesis of the same message.
        streamed_ids: set[str] = set()
        # The same message id carries identical cumulative ``usage_metadata``
        # in both the final ``messages`` chunk and the values snapshot —
        # count it only on whichever arrives first.
        counted_usage_ids: set[str] = set()
        sent_additional_kwargs_by_id: dict[str, dict[str, Any]] = {}
        cumulative_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

        def _account_usage(msg_id: str | None, usage: Any) -> dict | None:
            """Add *usage* to cumulative totals if this id has not been counted.

            ``usage`` is a ``langchain_core.messages.UsageMetadata`` TypedDict
            or ``None``; typed as ``Any`` because TypedDicts are not
            structurally assignable to plain ``dict`` under strict type
            checking.  Returns the normalized usage dict (for attaching
            to an event) when we accepted it, otherwise ``None``.
            """
            if not usage:
                return None
            if msg_id and msg_id in counted_usage_ids:
                return None
            if msg_id:
                counted_usage_ids.add(msg_id)
            input_tokens = usage.get("input_tokens", 0) or 0
            output_tokens = usage.get("output_tokens", 0) or 0
            total_tokens = usage.get("total_tokens", 0) or 0
            cumulative_usage["input_tokens"] += input_tokens
            cumulative_usage["output_tokens"] += output_tokens
            cumulative_usage["total_tokens"] += total_tokens
            return {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
            }

        def _unsent_additional_kwargs(msg_id: str | None, additional_kwargs: dict[str, Any] | None) -> dict[str, Any] | None:
            if not additional_kwargs:
                return None
            if not msg_id:
                return additional_kwargs

            sent = sent_additional_kwargs_by_id.setdefault(msg_id, {})
            delta = {key: value for key, value in additional_kwargs.items() if sent.get(key) != value}
            if not delta:
                return None

            sent.update(delta)
            return delta

        for item in self._agent.stream(
            state,
            config=config,
            context=context,
            stream_mode=["values", "messages", "custom"],
        ):
            if isinstance(item, tuple) and len(item) == 2:
                mode, chunk = item
                mode = str(mode)
            else:
                mode, chunk = "values", item

            if mode == "custom":
                yield StreamEvent(type="custom", data=chunk)
                continue

            if mode == "messages":
                # LangGraph ``messages`` mode emits ``(message_chunk, metadata)``.
                if isinstance(chunk, tuple) and len(chunk) == 2:
                    msg_chunk, _metadata = chunk
                else:
                    msg_chunk = chunk

                msg_id = getattr(msg_chunk, "id", None)

                if isinstance(msg_chunk, AIMessage):
                    text = self._extract_text(msg_chunk.content)
                    additional_kwargs = self._serialize_additional_kwargs(msg_chunk)
                    counted_usage = _account_usage(msg_id, msg_chunk.usage_metadata)
                    sent_additional_kwargs = False

                    if text:
                        if msg_id:
                            streamed_ids.add(msg_id)
                        additional_kwargs_delta = _unsent_additional_kwargs(msg_id, additional_kwargs)
                        yield self._ai_text_event(
                            msg_id,
                            text,
                            counted_usage,
                            additional_kwargs_delta,
                        )
                        sent_additional_kwargs = bool(additional_kwargs_delta)

                    if msg_chunk.tool_calls:
                        if msg_id:
                            streamed_ids.add(msg_id)
                        additional_kwargs_delta = None if sent_additional_kwargs else _unsent_additional_kwargs(msg_id, additional_kwargs)
                        yield self._ai_tool_calls_event(
                            msg_id,
                            msg_chunk.tool_calls,
                            additional_kwargs_delta,
                        )

                elif isinstance(msg_chunk, ToolMessage):
                    if msg_id:
                        streamed_ids.add(msg_id)
                    yield self._tool_message_event(msg_chunk)
                continue

            # mode == "values"
            messages = chunk.get("messages", [])

            for msg in messages:
                msg_id = getattr(msg, "id", None)
                if msg_id and msg_id in seen_ids:
                    continue
                if msg_id:
                    seen_ids.add(msg_id)

                # Already streamed via ``messages`` mode; only (defensively)
                # capture usage here and skip re-synthesizing the event.
                if msg_id and msg_id in streamed_ids:
                    if isinstance(msg, AIMessage):
                        _account_usage(msg_id, getattr(msg, "usage_metadata", None))
                        additional_kwargs = self._serialize_additional_kwargs(msg)
                        additional_kwargs_delta = _unsent_additional_kwargs(msg_id, additional_kwargs)
                        if additional_kwargs_delta:
                            # Metadata-only follow-up: ``messages-tuple`` has no
                            # dedicated attribution event, so clients should
                            # merge this empty-content AI event by message id
                            # and ignore it for text rendering.
                            yield self._ai_text_event(msg_id, "", None, additional_kwargs_delta)
                    continue

                if isinstance(msg, AIMessage):
                    counted_usage = _account_usage(msg_id, msg.usage_metadata)
                    additional_kwargs = self._serialize_additional_kwargs(msg)
                    sent_additional_kwargs = False

                    if msg.tool_calls:
                        additional_kwargs_delta = _unsent_additional_kwargs(msg_id, additional_kwargs)
                        yield self._ai_tool_calls_event(
                            msg_id,
                            msg.tool_calls,
                            additional_kwargs_delta,
                        )
                        sent_additional_kwargs = bool(additional_kwargs_delta)

                    text = self._extract_text(msg.content)
                    if text:
                        additional_kwargs_delta = None if sent_additional_kwargs else _unsent_additional_kwargs(msg_id, additional_kwargs)
                        yield self._ai_text_event(
                            msg_id,
                            text,
                            counted_usage,
                            additional_kwargs_delta,
                        )
                    elif msg_id:
                        additional_kwargs_delta = None if sent_additional_kwargs else _unsent_additional_kwargs(msg_id, additional_kwargs)
                        if not additional_kwargs_delta:
                            continue
                        # See the metadata-only follow-up convention above.
                        yield self._ai_text_event(msg_id, "", None, additional_kwargs_delta)

                elif isinstance(msg, ToolMessage):
                    yield self._tool_message_event(msg)

            # Emit a values event for each state snapshot
            yield StreamEvent(
                type="values",
                data={
                    "title": chunk.get("title"),
                    "messages": [self._serialize_message(m) for m in messages],
                    "artifacts": chunk.get("artifacts", []),
                },
            )

        yield StreamEvent(type="end", data={"usage": cumulative_usage})

        # yyds: 简单对话接口 —— 包装 stream()，按消息 ID 累积所有 delta 块，只返回最后一条 AI 消息的完整文本。
        # 用 list.append + join 代替 str += str，避免长回复时的 O(n²) 拼接开销。

    def chat(self, message: str, *, thread_id: str | None = None, **kwargs) -> str:
        """Send a message and return the final text response.

        Convenience wrapper around :meth:`stream` that accumulates delta
        ``messages-tuple`` events per ``id`` and returns the text of the
        **last** AI message to complete.  Intermediate AI messages (e.g.
        planner drafts) are discarded — only the final id's accumulated
        text is returned.  Use :meth:`stream` directly if you need every
        delta as it arrives.

        Args:
            message: User message text.
            thread_id: Thread ID for conversation context. Auto-generated if None.
            **kwargs: Override client defaults (same as stream()).

        Returns:
            The accumulated text of the last AI message, or empty string
            if no AI text was produced.
        """
        # Per-id delta lists joined once at the end — avoids the O(n²) cost
        # of repeated ``str + str`` on a growing buffer for long responses.
        chunks: dict[str, list[str]] = {}
        last_id: str = ""
        for event in self.stream(message, thread_id=thread_id, **kwargs):
            if event.type == "messages-tuple" and event.data.get("type") == "ai":
                msg_id = event.data.get("id") or ""
                delta = event.data.get("content", "")
                if delta:
                    chunks.setdefault(msg_id, []).append(delta)
                    last_id = msg_id
        return "".join(chunks.get(last_id, ()))

    # ------------------------------------------------------------------
    # Public API — configuration queries
    # ------------------------------------------------------------------

    # yyds: 列出配置中所有可用模型 —— 返回格式与 Gateway API ModelsListResponse 一致，包含 thinking/reasoning 支持信息
    def list_models(self) -> dict:
        """List available models from configuration.

        Returns:
            Dict with "models" key containing list of model info dicts,
            matching the Gateway API ``ModelsListResponse`` schema.
        """
        token_usage_enabled = getattr(getattr(self._app_config, "token_usage", None), "enabled", False)
        if not isinstance(token_usage_enabled, bool):
            token_usage_enabled = False

        return {
            "models": [
                {
                    "name": model.name,
                    "model": getattr(model, "model", None),
                    "display_name": getattr(model, "display_name", None),
                    "description": getattr(model, "description", None),
                    "supports_thinking": getattr(model, "supports_thinking", False),
                    "supports_reasoning_effort": getattr(model, "supports_reasoning_effort", False),
                }
                for model in self._app_config.models
            ],
            "token_usage": {"enabled": token_usage_enabled},
        }

        # yyds: 列出所有技能（skills） —— 从 SkillStorage 加载，可选只返回已启用的，格式与 Gateway API 对齐

    def list_skills(self, enabled_only: bool = False) -> dict:
        """List available skills.

        Args:
            enabled_only: If True, only return enabled skills.

        Returns:
            Dict with "skills" key containing list of skill info dicts,
            matching the Gateway API ``SkillsListResponse`` schema.
        """
        return {
            "skills": [
                {
                    "name": s.name,
                    "description": s.description,
                    "license": s.license,
                    "category": s.category,
                    "enabled": s.enabled,
                }
                for s in get_or_new_skill_storage().load_skills(enabled_only=enabled_only)
            ]
        }

        # yyds: 获取当前用户的记忆数据 —— 委托给 memory.updater，记忆用于跨对话的个性化上下文保持

    def get_memory(self) -> dict:
        """Get current memory data.

        Returns:
            Memory data dict (see src/agents/memory/updater.py for structure).
        """
        from deerflow.agents.memory.updater import get_memory_data

        return get_memory_data(user_id=get_effective_user_id())

        # yyds: 导出记忆数据 —— 用于备份或迁移到其他实例

    def export_memory(self) -> dict:
        """Export current memory data for backup or transfer."""
        from deerflow.agents.memory.updater import get_memory_data

        return get_memory_data(user_id=get_effective_user_id())

        # yyds: 导入并持久化记忆数据 —— 从外部来源恢复完整的记忆状态

    def import_memory(self, memory_data: dict) -> dict:
        """Import and persist full memory data."""
        from deerflow.agents.memory.updater import import_memory_data

        return import_memory_data(memory_data, user_id=get_effective_user_id())

        # yyds: 按名称查询单个模型的配置详情 —— 返回 None 表示未找到

    def get_model(self, name: str) -> dict | None:
        """Get a specific model's configuration by name.

        Args:
            name: Model name.

        Returns:
            Model info dict matching the Gateway API ``ModelResponse``
            schema, or None if not found.
        """
        model = self._app_config.get_model_config(name)
        if model is None:
            return None
        return {
            "name": model.name,
            "model": getattr(model, "model", None),
            "display_name": getattr(model, "display_name", None),
            "description": getattr(model, "description", None),
            "supports_thinking": getattr(model, "supports_thinking", False),
            "supports_reasoning_effort": getattr(model, "supports_reasoning_effort", False),
        }

    # ------------------------------------------------------------------
    # Public API — MCP configuration
    # ------------------------------------------------------------------

    # yyds: 获取 MCP（Model Context Protocol）服务器配置 —— 返回所有已注册的 MCP 服务器及其连接参数
    def get_mcp_config(self) -> dict:
        """Get MCP server configurations.

        Returns:
            Dict with "mcp_servers" key mapping server name to config,
            matching the Gateway API ``McpConfigResponse`` schema.
        """
        config = get_extensions_config()
        return {"mcp_servers": {name: server.model_dump() for name, server in config.mcp_servers.items()}}

        # yyds: 更新 MCP 配置 —— 原子写入 extensions_config.json，清除 Agent 缓存并热重载配置，
        # 确保下次调用时 Agent 使用新的 MCP 工具集

    def update_mcp_config(self, mcp_servers: dict[str, dict]) -> dict:
        """Update MCP server configurations.

        Writes to extensions_config.json and reloads the cache.

        Args:
            mcp_servers: Dict mapping server name to config dict.
                Each value should contain keys like enabled, type, command, args, env, url, etc.

        Returns:
            Dict with "mcp_servers" key, matching the Gateway API
            ``McpConfigResponse`` schema.

        Raises:
            OSError: If the config file cannot be written.
        """
        config_path = ExtensionsConfig.resolve_config_path()
        if config_path is None:
            raise FileNotFoundError("Cannot locate extensions_config.json. Set DEER_FLOW_EXTENSIONS_CONFIG_PATH or ensure it exists in the project root.")

        current_config = get_extensions_config()

        config_data = {
            "mcpServers": mcp_servers,
            "skills": {name: {"enabled": skill.enabled} for name, skill in current_config.skills.items()},
        }

        self._atomic_write_json(config_path, config_data)

        self._agent = None
        self._agent_config_key = None
        reloaded = reload_extensions_config()
        return {"mcp_servers": {name: server.model_dump() for name, server in reloaded.mcp_servers.items()}}

    # ------------------------------------------------------------------
    # Public API — skills management
    # ------------------------------------------------------------------

    # yyds: 按名称查询单个技能的详细信息
    def get_skill(self, name: str) -> dict | None:
        """Get a specific skill by name.

        Args:
            name: Skill name.

        Returns:
            Skill info dict, or None if not found.
        """
        from deerflow.skills.storage import get_or_new_skill_storage

        skill = next((s for s in get_or_new_skill_storage().load_skills(enabled_only=False) if s.name == name), None)
        if skill is None:
            return None
        return {
            "name": skill.name,
            "description": skill.description,
            "license": skill.license,
            "category": skill.category,
            "enabled": skill.enabled,
        }

        # yyds: 更新技能启用状态 —— 修改 extensions_config.json 后强制重建 Agent，因为工具集会随技能启停变化

    def update_skill(self, name: str, *, enabled: bool) -> dict:
        """Update a skill's enabled status.

        Args:
            name: Skill name.
            enabled: New enabled status.

        Returns:
            Updated skill info dict.

        Raises:
            ValueError: If the skill is not found.
            OSError: If the config file cannot be written.
        """
        from deerflow.skills.storage import get_or_new_skill_storage

        skills = get_or_new_skill_storage().load_skills(enabled_only=False)
        skill = next((s for s in skills if s.name == name), None)
        if skill is None:
            raise ValueError(f"Skill '{name}' not found")

        config_path = ExtensionsConfig.resolve_config_path()
        if config_path is None:
            raise FileNotFoundError("Cannot locate extensions_config.json. Set DEER_FLOW_EXTENSIONS_CONFIG_PATH or ensure it exists in the project root.")

        extensions_config = get_extensions_config()
        extensions_config.skills[name] = SkillStateConfig(enabled=enabled)

        config_data = {
            "mcpServers": {n: s.model_dump() for n, s in extensions_config.mcp_servers.items()},
            "skills": {n: {"enabled": sc.enabled} for n, sc in extensions_config.skills.items()},
        }

        self._atomic_write_json(config_path, config_data)

        self._agent = None
        self._agent_config_key = None
        reload_extensions_config()

        updated = next((s for s in get_or_new_skill_storage().load_skills(enabled_only=False) if s.name == name), None)
        if updated is None:
            raise RuntimeError(f"Skill '{name}' disappeared after update")
        return {
            "name": updated.name,
            "description": updated.description,
            "license": updated.license,
            "category": updated.category,
            "enabled": updated.enabled,
        }

        # yyds: 从 .skill 归档文件安装技能 —— 支持 ZIP 格式的技能包导入

    def install_skill(self, skill_path: str | Path) -> dict:
        """Install a skill from a .skill archive (ZIP).

        Args:
            skill_path: Path to the .skill file.

        Returns:
            Dict with success, skill_name, message.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the file is invalid.
        """
        return get_or_new_skill_storage().install_skill_from_archive(skill_path)

    # ------------------------------------------------------------------
    # Public API — memory management
    # ------------------------------------------------------------------

    # yyds: 强制从磁盘重新加载记忆数据 —— 使缓存失效，获取最新的记忆状态
    def reload_memory(self) -> dict:
        """Reload memory data from file, forcing cache invalidation.

        Returns:
            The reloaded memory data dict.
        """
        from deerflow.agents.memory.updater import reload_memory_data

        return reload_memory_data(user_id=get_effective_user_id())

        # yyds: 清除所有持久化的记忆数据 —— 慎用，不可逆

    def clear_memory(self) -> dict:
        """Clear all persisted memory data."""
        from deerflow.agents.memory.updater import clear_memory_data

        return clear_memory_data(user_id=get_effective_user_id())

        # yyds: 手动创建单条记忆事实 —— 用于程序化注入用户偏好、上下文等长期知识

    def create_memory_fact(self, content: str, category: str = "context", confidence: float = 0.5) -> dict:
        """Create a single fact manually."""
        from deerflow.agents.memory.updater import create_memory_fact

        return create_memory_fact(content=content, category=category, confidence=confidence)

        # yyds: 按 ID 删除单条记忆事实

    def delete_memory_fact(self, fact_id: str) -> dict:
        """Delete a single fact from memory by fact id."""
        from deerflow.agents.memory.updater import delete_memory_fact

        return delete_memory_fact(fact_id)

        # yyds: 更新单条记忆事实 —— 仅更新传入的字段，未传入的字段保持原值不变

    def update_memory_fact(
        self,
        fact_id: str,
        content: str | None = None,
        category: str | None = None,
        confidence: float | None = None,
    ) -> dict:
        """Update a single fact manually, preserving omitted fields."""
        from deerflow.agents.memory.updater import update_memory_fact

        return update_memory_fact(
            fact_id=fact_id,
            content=content,
            category=category,
            confidence=confidence,
        )

        # yyds: 获取记忆系统的运行配置 —— 包括启用状态、存储路径、去抖时间、最大事实数、注入限制等

    def get_memory_config(self) -> dict:
        """Get memory system configuration.

        Returns:
            Memory config dict.
        """
        from deerflow.config.memory_config import get_memory_config

        config = get_memory_config()
        return {
            "enabled": config.enabled,
            "storage_path": config.storage_path,
            "debounce_seconds": config.debounce_seconds,
            "max_facts": config.max_facts,
            "fact_confidence_threshold": config.fact_confidence_threshold,
            "injection_enabled": config.injection_enabled,
            "max_injection_tokens": config.max_injection_tokens,
        }

        # yyds: 获取记忆状态总览 —— 配置 + 当前数据的组合视图

    def get_memory_status(self) -> dict:
        """Get memory status: config + current data.

        Returns:
            Dict with "config" and "data" keys.
        """
        return {
            "config": self.get_memory_config(),
            "data": self.get_memory(),
        }

    # ------------------------------------------------------------------
    # Public API — file uploads
    # ------------------------------------------------------------------

    # yyds: 上传文件到线程目录 —— 先批量校验文件存在性和文件名唯一性，避免部分上传的尴尬状态；
    # 对 PDF/PPT/Excel/Word 自动调用 asyncio.run 转换为 Markdown，已处于事件循环中时用线程池执行
    def upload_files(self, thread_id: str, files: list[str | Path]) -> dict:
        """Upload local files into a thread's uploads directory.

        For PDF, PPT, Excel, and Word files, they are also converted to Markdown.

        Args:
            thread_id: Target thread ID.
            files: List of local file paths to upload.

        Returns:
            Dict with success, files, message — matching the Gateway API
            ``UploadResponse`` schema.

        Raises:
            FileNotFoundError: If any file does not exist.
            ValueError: If any supplied path exists but is not a regular file.
        """
        from deerflow.utils.file_conversion import CONVERTIBLE_EXTENSIONS, convert_file_to_markdown

        # Validate all files upfront to avoid partial uploads.
        resolved_files = []
        seen_names: set[str] = set()
        has_convertible_file = False
        for f in files:
            p = Path(f)
            if not p.exists():
                raise FileNotFoundError(f"File not found: {f}")
            if not p.is_file():
                raise ValueError(f"Path is not a file: {f}")
            dest_name = claim_unique_filename(p.name, seen_names)
            resolved_files.append((p, dest_name))
            if not has_convertible_file and p.suffix.lower() in CONVERTIBLE_EXTENSIONS:
                has_convertible_file = True

        uploads_dir = ensure_uploads_dir(thread_id)
        uploaded_files: list[dict] = []

        conversion_pool = None
        if has_convertible_file:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                conversion_pool = None
            else:
                import concurrent.futures

                # Reuse one worker when already inside an event loop to avoid
                # creating a new ThreadPoolExecutor per converted file.
                conversion_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        def _convert_in_thread(path: Path):
            return asyncio.run(convert_file_to_markdown(path))

        try:
            for src_path, dest_name in resolved_files:
                dest = uploads_dir / dest_name
                shutil.copy2(src_path, dest)

                info: dict[str, Any] = {
                    "filename": dest_name,
                    "size": str(dest.stat().st_size),
                    "path": str(dest),
                    "virtual_path": upload_virtual_path(dest_name),
                    "artifact_url": upload_artifact_url(thread_id, dest_name),
                }
                if dest_name != src_path.name:
                    info["original_filename"] = src_path.name

                if src_path.suffix.lower() in CONVERTIBLE_EXTENSIONS:
                    try:
                        if conversion_pool is not None:
                            md_path = conversion_pool.submit(_convert_in_thread, dest).result()
                        else:
                            md_path = asyncio.run(convert_file_to_markdown(dest))
                    except Exception:
                        logger.warning(
                            "Failed to convert %s to markdown",
                            src_path.name,
                            exc_info=True,
                        )
                        md_path = None

                    if md_path is not None:
                        info["markdown_file"] = md_path.name
                        info["markdown_path"] = str(uploads_dir / md_path.name)
                        info["markdown_virtual_path"] = upload_virtual_path(md_path.name)
                        info["markdown_artifact_url"] = upload_artifact_url(thread_id, md_path.name)

                uploaded_files.append(info)
        finally:
            if conversion_pool is not None:
                conversion_pool.shutdown(wait=True)

        return {
            "success": True,
            "files": uploaded_files,
            "message": f"Successfully uploaded {len(uploaded_files)} file(s)",
        }

        # yyds: 列出线程上传目录中的所有文件 —— 包含文件大小、虚拟路径、artifact URL 等元信息

    def list_uploads(self, thread_id: str) -> dict:
        """List files in a thread's uploads directory.

        Args:
            thread_id: Thread ID.

        Returns:
            Dict with "files" and "count" keys, matching the Gateway API
            ``list_uploaded_files`` response.
        """
        uploads_dir = get_uploads_dir(thread_id)
        result = list_files_in_dir(uploads_dir)
        return enrich_file_listing(result, thread_id)

        # yyds: 删除线程中的指定上传文件 —— 同时清理原始文件和转换后的 Markdown 文件

    def delete_upload(self, thread_id: str, filename: str) -> dict:
        """Delete a file from a thread's uploads directory.

        Args:
            thread_id: Thread ID.
            filename: Filename to delete.

        Returns:
            Dict with success and message, matching the Gateway API
            ``delete_uploaded_file`` response.

        Raises:
            FileNotFoundError: If the file does not exist.
            PermissionError: If path traversal is detected.
        """
        from deerflow.utils.file_conversion import CONVERTIBLE_EXTENSIONS

        uploads_dir = get_uploads_dir(thread_id)
        return delete_file_safe(uploads_dir, filename, convertible_extensions=CONVERTIBLE_EXTENSIONS)

    # ------------------------------------------------------------------
    # Public API — artifacts
    # ------------------------------------------------------------------

    # yyds: 读取 Agent 生成的产物文件 —— 通过虚拟路径解析实际文件位置，防止路径遍历攻击，
    # 返回 (文件字节, MIME 类型) 元组，用于 HTTP 下载或本地处理
    def get_artifact(self, thread_id: str, path: str) -> tuple[bytes, str]:
        """Read an artifact file produced by the agent.

        Args:
            thread_id: Thread ID.
            path: Virtual path (e.g. "mnt/user-data/outputs/file.txt").

        Returns:
            Tuple of (file_bytes, mime_type).

        Raises:
            FileNotFoundError: If the artifact does not exist.
            ValueError: If the path is invalid.
        """
        try:
            actual = get_paths().resolve_virtual_path(thread_id, path, user_id=get_effective_user_id())
        except ValueError as exc:
            if "traversal" in str(exc):
                from deerflow.uploads.manager import PathTraversalError

                raise PathTraversalError("Path traversal detected") from exc
            raise
        if not actual.exists():
            raise FileNotFoundError(f"Artifact not found: {path}")
        if not actual.is_file():
            raise ValueError(f"Path is not a file: {path}")

        mime_type, _ = mimetypes.guess_type(actual)
        return actual.read_bytes(), mime_type or "application/octet-stream"
