"""yyds: 压缩前钩子 — 消息被删之前，把关键信息"抢救"到记忆队列。

【大白话讲清楚】
  SummarizationMiddleware 压缩对话时，旧消息会被 RemoveAll 全部删掉。
  这些旧消息里可能有用户偏好、关键决策等重要信息。
  这个钩子在删除前被触发，把旧消息"抢救"到记忆队列，让记忆系统提取关键信息存到 memory.json。

  关键设计：用 add_nowait()（0s 延迟）而非 add()（30s 延迟）。
  因为消息马上就要被 RemoveAll 删掉了，等 30 秒就来不及了。

【具体例子】
  用户和 Agent 聊了 50 轮，压缩触发，即将删除前 88 条消息。

  正常流程：
    SummarizationMiddleware._fire_hooks() → 调 memory_flush_hook(event)
    → event.messages_to_summarize = [0-87]（即将被删的消息）
    → 过滤掉工具调用等无关消息，只保留用户输入和 AI 最终回复
    → 检测用户说了"不对"（correction）还是"对，就是这样"（reinforcement）
    → 入队（add_nowait，0s 延迟）→ 记忆系统异步提取关键信息存到 memory.json

  记忆关闭时：
    memory.enabled = False → 直接 return，不抢救

  只有工具调用没有对话时：
    过滤后没有 user_messages 或 assistant_messages → 直接 return，不值得存

【在钩子链路中的位置】
  这是 BeforeSummarizationHook 的唯一实现，注册链路：
    ① 定义接口：summarization_middleware.py BeforeSummarizationHook(Protocol)
    ② 注册钩子：agent.py:153-155  hooks.append(memory_flush_hook)
    ③ 触发钩子：summarization_middleware.py _fire_hooks() → for hook in hooks: hook(event)
    ④ 本文件执行：memory_flush_hook(event) → 过滤 → 检测信号 → 入队

---

Hooks fired before summarization removes messages from state.
"""

from __future__ import annotations

from deerflow.agents.memory.message_processing import detect_correction, detect_reinforcement, filter_messages_for_memory
from deerflow.agents.memory.queue import get_memory_queue
from deerflow.agents.middlewares.summarization_middleware import SummarizationEvent
from deerflow.config.memory_config import get_memory_config
from deerflow.runtime.user_context import resolve_runtime_user_id


def memory_flush_hook(event: SummarizationEvent) -> None:
    """yyds: 把即将被压缩删掉的消息"抢救"到记忆队列。

    具体例子（第 50 轮对话，即将删除消息 [0-87]）：
      ① 记忆系统关了？→ 直接 return
      ② 过滤消息：去掉工具调用等无关消息，只保留用户输入和 AI 最终回复
         → [0-87] 过滤后可能只剩 20 条有价值的对话
      ③ 检测信号：用户最后几条消息里有没有说"不对"（correction）或"对，就是这样"（reinforcement）
         → 这些信号影响记忆系统怎么处理（correction 会覆盖旧记忆，reinforcement 会加强）
      ④ 入队：add_nowait（0s 延迟，不能等，消息马上要被删了）
    """
    if not get_memory_config().enabled or not event.thread_id:  # yyds: 记忆系统关了 or 没有 thread_id → 不抢救
        return

    filtered_messages = filter_messages_for_memory(list(event.messages_to_summarize))  # yyds: 过滤掉工具调用、文件上传等，只保留有价值的对话
    user_messages = [message for message in filtered_messages if getattr(message, "type", None) == "human"]
    assistant_messages = [message for message in filtered_messages if getattr(message, "type", None) == "ai"]
    if not user_messages or not assistant_messages:  # yyds: 没有用户输入或 AI 回复 → 不值得存
        return

    correction_detected = detect_correction(filtered_messages)  # yyds: 用户说了"不对""你理解错了"等纠正信号 → 记忆系统会覆盖旧记忆
    reinforcement_detected = not correction_detected and detect_reinforcement(filtered_messages)  # yyds: 用户说了"对，就是这样""完全正确"等肯定信号 → 加强记忆。优先级低于 correction
    user_id = resolve_runtime_user_id(event.runtime)
    queue = get_memory_queue()
    queue.add_nowait(  # yyds: 0s 延迟入队（不是 add() 的 30s 延迟）。消息马上要被 RemoveAll 删了，等不了
        thread_id=event.thread_id,
        messages=filtered_messages,
        agent_name=event.agent_name,
        user_id=user_id,
        correction_detected=correction_detected,
        reinforcement_detected=reinforcement_detected,
    )
