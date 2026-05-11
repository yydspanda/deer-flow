# yyds: Summarization 钩子 —— 消息被删除前"抢救"到 memory 队列
# yyds: 触发时机：SummarizationMiddleware 删除旧消息前调用
# yyds: 关键：用 add_nowait()（0s 延迟）而非 add()（30s 延迟），因为消息马上就要被删除了
# yyds: 流程：过滤消息 → 检测 correction/reinforcement 信号 → 入队立即处理
"""Hooks fired before summarization removes messages from state."""

from __future__ import annotations

from deerflow.agents.memory.message_processing import detect_correction, detect_reinforcement, filter_messages_for_memory
from deerflow.agents.memory.queue import get_memory_queue
from deerflow.agents.middlewares.summarization_middleware import SummarizationEvent
from deerflow.config.memory_config import get_memory_config
from deerflow.runtime.user_context import resolve_runtime_user_id


# yyds: 将即将被 summarization 删除的消息"抢救"到 memory 队列，用 add_nowait 立即处理
def memory_flush_hook(event: SummarizationEvent) -> None:
    if not get_memory_config().enabled or not event.thread_id:
        return

    filtered_messages = filter_messages_for_memory(list(event.messages_to_summarize))
    user_messages = [message for message in filtered_messages if getattr(message, "type", None) == "human"]
    assistant_messages = [message for message in filtered_messages if getattr(message, "type", None) == "ai"]
    if not user_messages or not assistant_messages:
        return

    correction_detected = detect_correction(filtered_messages)
    reinforcement_detected = not correction_detected and detect_reinforcement(filtered_messages)
    user_id = resolve_runtime_user_id(event.runtime)
    queue = get_memory_queue()
    queue.add_nowait(
        thread_id=event.thread_id,
        messages=filtered_messages,
        agent_name=event.agent_name,
        user_id=user_id,
        correction_detected=correction_detected,
        reinforcement_detected=reinforcement_detected,
    )
