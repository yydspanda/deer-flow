"""yyds: 记忆 Provider 基类 — 定义"记忆后端必须实现什么接口"。

【大白话讲清楚】
  Hermes 的记忆系统支持两种后端：
    1. 内置后端：MEMORY.md / USER.md 文件存储（永远存在）
    2. 外部后端：Honcho / Mem0 / Hindsight 等（通过插件加载，最多一个）

  不管用哪种后端，都必须实现这个 ABC 定义的接口。
  MemoryManager 不关心后端是什么，只管调用接口。

  核心生命周期（按时间顺序）：
    initialize()          → 连接、建表、预热（agent 启动时调一次）
    system_prompt_block() → 给 system prompt 提供静态文本
    prefetch(query)       → 每轮 API 调用前，召回相关记忆
    sync_turn(user, asst) → 每轮结束后，把对话持久化到后端
    handle_tool_call()    → 处理模型发起的记忆工具调用（如 memory search）
    shutdown()            → 清理连接

  额外钩子（可选实现）：
    on_session_switch  → session_id 变了（压缩/resume/branch）
    on_pre_compress    → 即将压缩上下文前，抢救要丢的信息
    on_memory_write    → 内置 memory 工具写入时，同步镜像到外部后端
    on_delegation      → 子 agent 完成任务后，父 agent 的后端收到通知

【在链中的位置】
  plugins/memory/<name>/__init__.py  实现 MemoryProvider
       ↓ 注册到
  MemoryManager.add_provider()       编排层（agent/memory_manager.py）
       ↓ 被调用
  run_agent.py                       在 AIAgent.__init__ 里初始化
  conversation_loop.py               在每轮循环里调 prefetch / sync_turn

【加载条件】
  内置后端（builtin）永远加载。
  外部后端通过 config.yaml 的 memory.provider 激活。
  MemoryManager 限制：最多一个外部后端（防止打架）。
"""

"""Abstract base class for pluggable memory providers.

Memory providers give the agent persistent recall across sessions.
The MemoryManager enforces a one-external-provider limit to prevent
tool schema bloat and conflicting memory backends.

External providers (Honcho, Hindsight, Mem0, etc.) are registered
and managed via MemoryManager. Only one external provider runs at a
time.

Registration:
  Plugins ship in plugins/memory/<name>/ and are activated via
  the memory.provider config key.

Lifecycle (called by MemoryManager, wired in run_agent.py):
  initialize()          — connect, create resources, warm up
  system_prompt_block()  — static text for the system prompt
  prefetch(query)        — background recall before each turn
  sync_turn(user, asst)  — async write after each turn
  get_tool_schemas()     — tool schemas to expose to the model
  handle_tool_call()     — dispatch a tool call
  shutdown()             — clean exit

Optional hooks (override to opt in):
  on_turn_start(turn, message, **kwargs) — per-turn tick with runtime context
  on_session_end(messages)               — end-of-session extraction
  on_session_switch(new_session_id, **kwargs) — mid-process session_id rotation
  on_pre_compress(messages) -> str       — extract before context compression
  on_memory_write(action, target, content, metadata=None) — mirror built-in memory writes
  on_delegation(task, result, **kwargs)  — parent-side observation of subagent work
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MemoryProvider(ABC):
    """Abstract base class for memory providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this provider (e.g. 'builtin', 'honcho', 'hindsight')."""

    # -- Core lifecycle (implement these) ------------------------------------

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this provider is configured, has credentials, and is ready.

        Called during agent init to decide whether to activate the provider.
        Should not make network calls — just check config and installed deps.
        """

    @abstractmethod
    def initialize(self, session_id: str, **kwargs) -> None:
        """Initialize for a session.

        Called once at agent startup. May create resources (banks, tables),
        establish connections, start background threads, etc.

        kwargs always include:
          - hermes_home (str): The active HERMES_HOME directory path. Use this
            for profile-scoped storage instead of hardcoding ``~/.hermes``.
          - platform (str): "cli", "telegram", "discord", "cron", etc.

        kwargs may also include:
          - agent_context (str): "primary", "subagent", "cron", or "flush".
            Providers should skip writes for non-primary contexts (cron system
            prompts would corrupt user representations).
          - agent_identity (str): Profile name (e.g. "coder"). Use for
            per-profile provider identity scoping.
          - agent_workspace (str): Shared workspace name (e.g. "hermes").
          - parent_session_id (str): For subagents, the parent's session_id.
          - user_id (str): Platform user identifier (gateway sessions).
        """

    def system_prompt_block(self) -> str:
        """Return text to include in the system prompt.

        Called during system prompt assembly. Return empty string to skip.
        This is for STATIC provider info (instructions, status). Prefetched
        recall context is injected separately via prefetch().
        """
        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Recall relevant context for the upcoming turn.

        Called before each API call. Return formatted text to inject as
        context, or empty string if nothing relevant. Implementations
        should be fast — use background threads for the actual recall
        and return cached results here.

        session_id is provided for providers serving concurrent sessions
        (gateway group chats, cached agents). Providers that don't need
        per-session scoping can ignore it.
        """
        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Queue a background recall for the NEXT turn.

        Called after each turn completes. The result will be consumed
        by prefetch() on the next turn. Default is no-op — providers
        that do background prefetching should override this.
        """

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Persist a completed turn to the backend.

        Called after each turn. Should be non-blocking — queue for
        background processing if the backend has latency.
        """

    @abstractmethod
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Return tool schemas this provider exposes.

        Each schema follows the OpenAI function calling format:
        {"name": "...", "description": "...", "parameters": {...}}

        Return empty list if this provider has no tools (context-only).
        """

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        """Handle a tool call for one of this provider's tools.

        Must return a JSON string (the tool result).
        Only called for tool names returned by get_tool_schemas().
        """
        raise NotImplementedError(f"Provider {self.name} does not handle tool {tool_name}")

    def shutdown(self) -> None:
        """Clean shutdown — flush queues, close connections."""

    # -- Optional hooks (override to opt in) ---------------------------------

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """Called at the start of each turn with the user message.

        Use for turn-counting, scope management, periodic maintenance.

        kwargs may include: remaining_tokens, model, platform, tool_count.
        Providers use what they need; extras are ignored.
        """

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Called when a session ends (explicit exit or timeout).

        Use for end-of-session fact extraction, summarization, etc.
        messages is the full conversation history.

        NOT called after every turn — only at actual session boundaries
        (CLI exit, /reset, gateway session expiry).
        """

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        **kwargs,
    ) -> None:
        """Called when the agent switches session_id mid-process.

        Fires on ``/resume``, ``/branch``, ``/reset``, ``/new`` (CLI), the
        gateway equivalents, and context compression — any path that
        reassigns ``AIAgent.session_id`` without tearing the provider down.

        Providers that cache per-session state in ``initialize()``
        (``_session_id``, ``_document_id``, accumulated turn buffers,
        counters) should update or reset that state here so subsequent
        writes land in the correct session's record.

        Parameters
        ----------
        new_session_id:
            The session_id the agent just switched to.
        parent_session_id:
            The previous session_id, if meaningful — set for ``/branch``
            (fork lineage), context compression (continuation lineage),
            and ``/resume`` (the session we're leaving). Empty string
            when no lineage applies.
        reset:
            ``True`` when this is a genuinely new conversation, not a
            resumption of an existing one. Fired by ``/reset`` / ``/new``.
            Providers should flush accumulated per-session buffers
            (``_session_turns``, ``_turn_counter``, etc.) when this is
            set. ``False`` for ``/resume`` / ``/branch`` / compression
            where the logical conversation continues under the new id.

        Default is no-op for backward compatibility.
        """

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Called before context compression discards old messages.

        Use to extract insights from messages about to be compressed.
        messages is the list that will be summarized/discarded.

        Return text to include in the compression summary prompt so the
        compressor preserves provider-extracted insights. Return empty
        string for no contribution (backwards-compatible default).
        """
        return ""

    def on_delegation(self, task: str, result: str, *,
                      child_session_id: str = "", **kwargs) -> None:
        """Called on the PARENT agent when a subagent completes.

        The parent's memory provider gets the task+result pair as an
        observation of what was delegated and what came back. The subagent
        itself has no provider session (skip_memory=True).

        task: the delegation prompt
        result: the subagent's final response
        child_session_id: the subagent's session_id
        """

    def get_config_schema(self) -> List[Dict[str, Any]]:
        """Return config fields this provider needs for setup.

        Used by 'hermes memory setup' to walk the user through configuration.
        Each field is a dict with:
          key:         config key name (e.g. 'api_key', 'mode')
          description: human-readable description
          secret:      True if this should go to .env (default: False)
          required:    True if required (default: False)
          default:     default value (optional)
          choices:     list of valid values (optional)
          url:         URL where user can get this credential (optional)
          env_var:     explicit env var name for secrets (default: auto-generated)

        Return empty list if no config needed (e.g. local-only providers).
        """
        return []

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        """Write non-secret config to the provider's native location.

        Called by 'hermes memory setup' after collecting user inputs.
        ``values`` contains only non-secret fields (secrets go to .env).
        ``hermes_home`` is the active HERMES_HOME directory path.

        Providers with native config files (JSON, YAML) should override
        this to write to their expected location. Providers that use only
        env vars can leave the default (no-op).

        All new memory provider plugins MUST implement either:
        - save_config() for native config file formats, OR
        - use only env vars (in which case get_config_schema() fields
          should all have ``env_var`` set and this method stays no-op).
        """

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Called when the built-in memory tool writes an entry.

        action: 'add', 'replace', or 'remove'
        target: 'memory' or 'user'
        content: the entry content
        metadata: structured provenance for the write, when available. Common
          keys include ``write_origin``, ``execution_context``, ``session_id``,
          ``parent_session_id``, ``platform``, and ``tool_name``.

        Use to mirror built-in memory writes to your backend.
        """
