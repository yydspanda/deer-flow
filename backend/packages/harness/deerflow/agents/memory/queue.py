"""yyds: Memory 更新队列 — 攒够一批再调 LLM，省钱的去抖动设计。

【大白话讲清楚】
  Memory 更新要调 LLM（贵），不能每轮对话都调。
  这个队列解决"什么时候调"的问题：

  问题 A — 用户连说 5 轮，每轮都调 LLM 太浪费：
    → 去抖动：每次 add() 都重置 30 秒倒计时。
      用户说了第 1 轮 → 开始倒计时 30s
      用户说了第 2 轮（10s 后）→ 倒计时重新开始
      ...
      用户停了 30s → 倒计时到期 → 一次性处理这批对话
    5 轮对话只调 1 次 LLM，不是 5 次。

  问题 B — SummarizationMiddleware 要删消息了，等不了 30 秒：
    → add_nowait()：0 秒倒计时，立即处理。
      因为消息马上被 RemoveAll 删掉，30 秒后消息就不存在了。

  问题 C — 同一个对话的旧 context 被多次入队：
    → 去重：同 thread_id 的旧 context 被新 context 替换。
      因为新消息包含旧消息的内容，不需要存两份。

【具体例子】
  正常去抖动：
    10:00:00  用户说第 1 轮 → add() → 定时器设为 10:00:30
    10:00:10  用户说第 2 轮 → add() → 定时器重置为 10:00:40
    10:00:25  用户说第 3 轮 → add() → 定时器重置为 10:00:55
    10:00:55  定时器到期 → 取出 thread-123 的 context（第 3 轮的，前两次被替换了）
              → 调 MemoryUpdater.update_memory() 一次

  抢救路径：
    10:05:00  SummarizationMiddleware 触发，即将删除消息 [0-87]
              → memory_flush_hook() → add_nowait() → 0s 定时器
              → 立即 _process_queue() → 调 MemoryUpdater
              → LLM 从即将被删的消息中提取关键信息 → 存到 memory.json
              → 然后旧消息才被安全删除

  批处理多个 thread：
    thread-A 的 context 和 thread-B 的 context 都在队列里
    → _process_queue() 逐个处理，每个间隔 0.5s（避免 API 限流）

---
Memory update queue with debounce mechanism.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from deerflow.config.memory_config import get_memory_config

logger = logging.getLogger(__name__)


@dataclass
class ConversationContext:
    """yyds: 一条待处理的对话上下文 — 攒在队列里的"包裹"。

    包含：
      - messages: 对话消息（给 LLM 提取用的原始数据）
      - thread_id: 哪个对话（去重用的 key）
      - agent_name / user_id: 记忆要存到哪个文件
      - correction_detected: 用户说了"不对"吗？（影响 LLM prompt）
      - reinforcement_detected: 用户说了"对"吗？（影响 LLM prompt）
    """

    thread_id: str
    messages: list[Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    agent_name: str | None = None
    user_id: str | None = None
    correction_detected: bool = False
    reinforcement_detected: bool = False


class MemoryUpdateQueue:
    """yyds: 去抖动队列 — 攒够一批再调 LLM 更新 memory。

    完整生命周期：

    有人往队列里塞对话
      │
      │ add()（30s 去抖动）
      │ add_nowait()（0s 立即处理）
      │
      ▼
    _enqueue_locked() 入队
      ├─ 同 thread_id 的旧 context → 新替换旧的
      └─ 合并 correction/reinforcement 信号（只要有一条是 True 就保持 True）

    定时器到期
      │
      ▼
    _process_queue()
      ├─ 队列空？→ 直接返回
      ├─ 正在处理中？→ 重新调度 0s 定时器（等当前处理完再试）
      └─ 取出所有 context → 逐个调 MemoryUpdater.update_memory()
          每个间隔 0.5s（避免 API 限流）
    """

    def __init__(self):
        self._queue: list[ConversationContext] = []
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._processing = False  # yyds: 防止两个线程同时 _process_queue

    @staticmethod
    def _queue_key(
        thread_id: str,
        user_id: str | None,
        agent_name: str | None,
    ) -> tuple[str, str | None, str | None]:
        """Return the debounce identity for a memory update target."""
        return (thread_id, user_id, agent_name)

    def add(
        self,
        thread_id: str,
        messages: list[Any],
        agent_name: str | None = None,
        user_id: str | None = None,
        correction_detected: bool = False,
        reinforcement_detected: bool = False,
    ) -> None:
        """yyds: 去抖动入队 — 每次都重置 30s 定时器。

        谁调的？MemoryMiddleware.after_agent()，每轮对话结束后。

        为什么每次都重置定时器？
          用户可能在连续说话。第 1 轮设 30s 定时器，
          第 2 轮来了说明用户还没说完 → 重置等用户说完。
          只有用户停了 30s（确实说完了），才触发处理。

        user_id 为什么要在入队时捕获？
          user_id 存在 ContextVar 里，ContextVar 不跨线程传播。
          定时器到期后在另一个线程跑 _process_queue()，那时 ContextVar 已经丢失了。
          所以入队时就取出来，存在 ConversationContext 里带过去。
        """
        config = get_memory_config()
        if not config.enabled:
            return

        with self._lock:
            self._enqueue_locked(
                thread_id=thread_id,
                messages=messages,
                agent_name=agent_name,
                user_id=user_id,
                correction_detected=correction_detected,
                reinforcement_detected=reinforcement_detected,
            )
            self._reset_timer()

        logger.info("Memory update queued for thread %s, queue size: %d", thread_id, len(self._queue))

    def add_nowait(
        self,
        thread_id: str,
        messages: list[Any],
        agent_name: str | None = None,
        user_id: str | None = None,
        correction_detected: bool = False,
        reinforcement_detected: bool = False,
    ) -> None:
        """yyds: 立即处理入队 — 0 秒定时器，不等。

        谁调的？memory_flush_hook()，消息即将被删除时。

        为什么不等？
          SummarizationMiddleware 马上要 RemoveAll 删消息了。
          等 30 秒后消息已经不存在了，LLM 没东西可提取。
          所以设 0s 定时器：入队后立即触发 _process_queue()。
        """
        config = get_memory_config()
        if not config.enabled:
            return

        with self._lock:
            self._enqueue_locked(
                thread_id=thread_id,
                messages=messages,
                agent_name=agent_name,
                user_id=user_id,
                correction_detected=correction_detected,
                reinforcement_detected=reinforcement_detected,
            )
            self._schedule_timer(0)

        logger.info("Memory update queued for immediate processing on thread %s, queue size: %d", thread_id, len(self._queue))

    def _enqueue_locked(
        self,
        *,
        thread_id: str,
        messages: list[Any],
        agent_name: str | None,
        user_id: str | None,
        correction_detected: bool,
        reinforcement_detected: bool,
    ) -> None:
        """yyds: 入队核心 — 去重 + 信号合并。

        去重：同 thread_id 的旧 context 被新 context 替换。
          为什么？新消息包含旧消息的内容（messages 越来越长），
          存新的一份就够了，旧的是冗余。

        信号合并：新旧 context 的 correction/reinforcement 取 OR。
          为什么？旧 context 标记了 correction=True，新 context 没有，
          不能因为新消息没纠正信号就丢失旧消息的纠正信号。
        """
        queue_key = self._queue_key(thread_id, user_id, agent_name)
        existing_context = next(
            (context for context in self._queue if self._queue_key(context.thread_id, context.user_id, context.agent_name) == queue_key),
            None,
        )
        merged_correction_detected = correction_detected or (existing_context.correction_detected if existing_context is not None else False)
        merged_reinforcement_detected = reinforcement_detected or (existing_context.reinforcement_detected if existing_context is not None else False)
        context = ConversationContext(
            thread_id=thread_id,
            messages=messages,
            agent_name=agent_name,
            user_id=user_id,
            correction_detected=merged_correction_detected,
            reinforcement_detected=merged_reinforcement_detected,
        )

        self._queue = [context for context in self._queue if self._queue_key(context.thread_id, context.user_id, context.agent_name) != queue_key]
        self._queue.append(context)

    def _reset_timer(self) -> None:
        """Reset the debounce timer."""
        config = get_memory_config()
        self._schedule_timer(config.debounce_seconds)  # yyds: 默认 30s，可在 config.yaml 配

        logger.debug("Memory update timer set for %ss", config.debounce_seconds)

    def _schedule_timer(self, delay_seconds: float) -> None:
        """Schedule queue processing after the provided delay."""
        if self._timer is not None:
            self._timer.cancel()

        self._timer = threading.Timer(
            delay_seconds,
            self._process_queue,
        )
        self._timer.daemon = True  # yyds: daemon 线程，进程退出时不会卡住
        self._timer.start()

    def _process_queue(self) -> None:
        """yyds: 处理队列 — 取出所有 context，逐个调 MemoryUpdater。

        逐个处理，每个间隔 0.5s：
          为什么？批量调 LLM API 可能触发限流（429 Too Many Requests）。
          0.5s 间隔让 API 有喘息空间。

        正在处理中又来新任务怎么办？
          → 重新调度 0s 定时器，等当前批处理完再处理新来的。
        """
        from deerflow.agents.memory.updater import MemoryUpdater

        with self._lock:
            if self._processing:
                self._schedule_timer(0)  # yyds: 当前正在处理，等会儿再来
                return

            if not self._queue:
                return

            self._processing = True
            contexts_to_process = self._queue.copy()
            self._queue.clear()
            self._timer = None

        logger.info("Processing %d queued memory updates", len(contexts_to_process))

        try:
            updater = MemoryUpdater()

            for context in contexts_to_process:
                try:
                    logger.info("Updating memory for thread %s", context.thread_id)
                    success = updater.update_memory(
                        messages=context.messages,
                        thread_id=context.thread_id,
                        agent_name=context.agent_name,
                        correction_detected=context.correction_detected,
                        reinforcement_detected=context.reinforcement_detected,
                        user_id=context.user_id,
                    )
                    if success:
                        logger.info("Memory updated successfully for thread %s", context.thread_id)
                    else:
                        logger.warning("Memory update skipped/failed for thread %s", context.thread_id)
                except Exception as e:
                    logger.error("Error updating memory for thread %s: %s", context.thread_id, e)

                if len(contexts_to_process) > 1:
                    time.sleep(0.5)  # yyds: 避免连续调 LLM API 触发限流

        finally:
            with self._lock:
                self._processing = False

    def flush(self) -> None:
        """yyds: 同步刷队列 — 取消定时器，立即同步处理完。

        谁用？测试代码、优雅关闭。确保所有排队任务都处理完再退出。
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

        self._process_queue()

    def flush_nowait(self) -> None:
        """Start queue processing immediately in a background thread."""
        with self._lock:
            self._schedule_timer(0)

    def clear(self) -> None:
        """Clear the queue without processing.

        This is useful for testing.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._queue.clear()
            self._processing = False

    @property
    def pending_count(self) -> int:
        """Get the number of pending updates."""
        with self._lock:
            return len(self._queue)

    @property
    def is_processing(self) -> bool:
        """Check if the queue is currently being processed."""
        with self._lock:
            return self._processing


_memory_queue: MemoryUpdateQueue | None = None
_queue_lock = threading.Lock()


def get_memory_queue() -> MemoryUpdateQueue:
    """yyds: 全局单例 — 懒初始化，整个进程共享一个队列。"""
    global _memory_queue
    with _queue_lock:
        if _memory_queue is None:
            _memory_queue = MemoryUpdateQueue()
        return _memory_queue


def reset_memory_queue() -> None:
    """yyds: 重置队列 — 测试用，清空队列并销毁单例。"""
    global _memory_queue
    with _queue_lock:
        if _memory_queue is not None:
            _memory_queue.clear()
        _memory_queue = None
