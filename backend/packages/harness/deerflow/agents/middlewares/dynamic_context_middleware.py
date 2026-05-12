"""yyds: 动态上下文注入中间件 — 把记忆和当前日期注入到对话中，同时保持 system prompt 静态以复用前缀缓存。

【做什么】在 Agent 执行前，注入 <system-reminder> 到第一条 HumanMessage，
   内容包含用户记忆（memory）和当前日期（current_date）。
【为什么存在】system prompt 是静态的（为了前缀缓存复用，跨用户/跨会话共享），
   但"当前日期"和"用户记忆"是动态的，不能写进静态 prompt。
   所以用一条 HumanMessage 承载动态内容，插入到第一条用户消息前面。
【在链中的位置】before_agent 阶段执行。
【关键设计】
   - 首轮注入：完整 <system-reminder>（记忆 + 日期），冻结在第一条消息中，后续轮次不再改
   - 跨午夜检测：如果对话跨越午夜，在当前轮次注入轻量日期更新提醒
   - ID 交换技巧：reminder_msg 继承原始消息的 ID（让 add_messages 原地替换），
     user_msg 用 `{id}__user` 派生 ID（追加到 reminder 后面）
   - hide_from_ui=True：前端不展示这条系统提醒消息
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)


# Upper bound (seconds) for a single _inject() offload.  If the warm-up at
# gateway startup failed silently, the first request may still hit a cold
# tiktoken BPE download that blocks until the OS TCP timeout (~26 min).
# This cap ensures the request degrades gracefully instead of hanging.
_INJECT_TIMEOUT_SECONDS = 5.0

_DATE_RE = re.compile(r"<current_date>([^<]+)</current_date>")
_DYNAMIC_CONTEXT_REMINDER_KEY = "dynamic_context_reminder"
_SUMMARY_MESSAGE_NAME = "summary"
_DATE_RE = re.compile(r"<current_date>([^<]+)</current_date>")  # yyds: 从消息内容提取日期的正则
_DYNAMIC_CONTEXT_REMINDER_KEY = "dynamic_context_reminder"  # yyds: additional_kwargs 中的标记键
_SUMMARY_MESSAGE_NAME = "summary"  # yyds: 摘要消息的 name，不对其注入上下文



def _extract_date(content: str) -> str | None:
    """yyds: 从消息内容中提取 <current_date> 的值。"""
    m = _DATE_RE.search(content)
    return m.group(1) if m else None


def is_dynamic_context_reminder(message: object) -> bool:
    """yyds: 判断消息是否是本中间件注入的系统提醒（通过 additional_kwargs 标记识别）。"""
    return isinstance(message, HumanMessage) and bool(message.additional_kwargs.get(_DYNAMIC_CONTEXT_REMINDER_KEY))


def _last_injected_date(messages: list) -> str | None:
    """yyds: 反向扫描消息列表，找到最近一次注入的日期（用于跨午夜检测）。"""
    for msg in reversed(messages):
        if is_dynamic_context_reminder(msg):
            content_str = msg.content if isinstance(msg.content, str) else str(msg.content)
            return _extract_date(content_str)
    return None


def _is_user_injection_target(message: object) -> bool:
    """yyds: 判断消息是否可以接收动态上下文注入（排除系统提醒和摘要消息）。"""
    return isinstance(message, HumanMessage) and not is_dynamic_context_reminder(message) and message.name != _SUMMARY_MESSAGE_NAME


class DynamicContextMiddleware(AgentMiddleware):
    """yyds: 动态上下文注入中间件 — 把记忆+日期注入对话，保持 system prompt 静态。

    两种注入模式：
      首轮：完整注入（记忆 + 日期）→ 冻结在第一条 HumanMessage，后续不改
      跨午夜：轻量注入（仅日期更新）→ 在当前轮次 HumanMessage 前面
    """

    def __init__(self, agent_name: str | None = None, *, app_config: AppConfig | None = None):
        super().__init__()
        self._agent_name = agent_name
        self._app_config = app_config

    def _build_full_reminder(self) -> str:
        """yyds: 构建完整的 <system-reminder>（记忆 + 日期）。
        记忆注入受 memory.injection_enabled 控制；日期始终包含。
        """
        from deerflow.agents.lead_agent.prompt import _get_memory_context

        # Memory injection is gated by injection_enabled; date is always included.
        injection_enabled = self._app_config.memory.injection_enabled if self._app_config else True
        memory_context = _get_memory_context(self._agent_name, app_config=self._app_config) if injection_enabled else ""
        current_date = datetime.now().strftime("%Y-%m-%d, %A")

        lines: list[str] = ["<system-reminder>"]
        if memory_context:
            lines.append(memory_context.strip())
            lines.append("")  # blank line separating memory from date
        lines.append(f"<current_date>{current_date}</current_date>")
        lines.append("</system-reminder>")

        return "\n".join(lines)

    def _build_date_update_reminder(self) -> str:
        """yyds: 构建轻量日期更新提醒（跨午夜时使用，不含记忆）。"""
        current_date = datetime.now().strftime("%Y-%m-%d, %A")
        return "\n".join(
            [
                "<system-reminder>",
                f"<current_date>{current_date}</current_date>",
                "</system-reminder>",
            ]
        )

    @staticmethod
    def _make_reminder_and_user_messages(original: HumanMessage, reminder_content: str) -> tuple[HumanMessage, HumanMessage]:
        """yyds: ID 交换技巧 — 生成 (系统提醒消息, 用户消息) 对。

        reminder_msg 继承原始消息的 ID → add_messages 会原地替换（位置不变）
        user_msg 用 `{id}__user` 派生 ID → 追加到 reminder 后面
        hide_from_ui=True → 前端不展示系统提醒，只展示用户消息
        """
        stable_id = original.id or str(uuid.uuid4())
        reminder_msg = HumanMessage(
            content=reminder_content,
            id=stable_id,
            additional_kwargs={"hide_from_ui": True, _DYNAMIC_CONTEXT_REMINDER_KEY: True},
        )
        user_msg = HumanMessage(
            content=original.content,
            id=f"{stable_id}__user",
            name=original.name,
            additional_kwargs=original.additional_kwargs,
        )
        return reminder_msg, user_msg

    def _inject(self, state) -> dict | None:
        """yyds: 核心注入逻辑 — 三种情况：
        1. last_date 为 None（首轮）→ 注入完整提醒（记忆+日期）
        2. last_date == 当前日期 → 无需注入
        3. last_date != 当前日期（跨午夜）→ 注入轻量日期更新
        """
        messages = list(state.get("messages", []))
        if not messages:
            return None

        current_date = datetime.now().strftime("%Y-%m-%d, %A")
        last_date = _last_injected_date(messages)
        logger.debug(
            "DynamicContextMiddleware._inject: msg_count=%d last_date=%r current_date=%r",
            len(messages),
            last_date,
            current_date,
        )

        if last_date is None:
            # ── First turn: inject full reminder as a separate HumanMessage ─────
            first_idx = next((i for i, m in enumerate(messages) if _is_user_injection_target(m)), None)
            if first_idx is None:
                return None
            full_reminder = self._build_full_reminder()
            logger.info(
                "DynamicContextMiddleware: injecting full reminder (len=%d, has_memory=%s) into first HumanMessage id=%r",
                len(full_reminder),
                "<memory>" in full_reminder,
                messages[first_idx].id,
            )
            reminder_msg, user_msg = self._make_reminder_and_user_messages(messages[first_idx], full_reminder)
            return {"messages": [reminder_msg, user_msg]}

        if last_date == current_date:
            # ── Same day: nothing to do ──────────────────────────────────────────
            return None

        # ── Midnight crossed: inject date-update reminder as a separate HumanMessage ──
        last_human_idx = next((i for i in reversed(range(len(messages))) if _is_user_injection_target(messages[i])), None)
        if last_human_idx is None:
            return None

        reminder_msg, user_msg = self._make_reminder_and_user_messages(messages[last_human_idx], self._build_date_update_reminder())
        logger.info("DynamicContextMiddleware: midnight crossing detected — injected date update before current turn")
        return {"messages": [reminder_msg, user_msg]}

    @override
    def before_agent(self, state, runtime: Runtime) -> dict | None:
        return self._inject(state)

    @override
    async def abefore_agent(self, state, runtime: Runtime) -> dict | None:
        # _inject() performs synchronous file I/O (memory JSON loading) and
        # potentially blocking network calls (tiktoken encoding download on
        # first use).  Offload to a thread so the event loop is never blocked
        # — a blocking call here starves all concurrent HTTP handlers (auth,
        # SSE heartbeats, etc.).  See issue #3402.
        #
        # Bounded timeout: if startup warm-up failed silently (e.g. network
        # blip during deploy), the first request's cold tiktoken download can
        # block for tens of minutes (OS TCP timeout).  Time-box injection so
        # the request degrades gracefully (no memory context) rather than
        # hanging.
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._inject, state),
                timeout=_INJECT_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "DynamicContextMiddleware: injection timed out (%.1fs); skipping memory/date injection for this turn",
                _INJECT_TIMEOUT_SECONDS,
            )
            return None
