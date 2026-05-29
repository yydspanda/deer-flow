"""yyds: 待办事项中间件 — 让 AI 按计划干活，不许偷懒也不许忘事。

【大白话讲清楚】
  Pro/Ultra 模式下，AI 会拆分复杂任务成待办列表，逐个执行。
  这个中间件解决两个实际问题：

  问题 A — AI 忘了待办列表：
    对话太长时，摘要中间件会压缩历史消息。如果"创建待办列表"的那条消息被删了，
    AI 就"看不见"待办列表了（虽然数据还在 state 里）。
    → before_model 检测到这个情况，重新给 AI 看一遍待办列表。

  问题 B — AI 偷懒提前交差：
    AI 有时做了 3 个任务中的 2 个，就直接输出最终答案了。
    → after_model 检测到"还有没做完的任务 + AI 想结束"，强制拉回来继续干活。

【具体例子】
  用户："调研 LangGraph 和 CrewAI 的架构对比"
  AI 创建待办：
    [pending] 搜索 LangGraph
    [pending] 搜索 CrewAI
    [pending] 对比差异
    [pending] 写报告

  正常流程：逐个完成 → 全部 completed → 输出最终答案 ✅

  异常流程 A（忘了）：
    搜索了 10 轮后，摘要中间件把早期消息删了 → AI 看不见待办列表了
    → 本中间件注入提醒："你的待办列表还在！当前状态：..."
    → AI 继续跟踪待办

  异常流程 B（偷懒）：
    AI 完成了搜索和对比，但还没写报告，就直接输出答案了
    → 本中间件拉回 AI："你还有 1 个任务没做完！继续！"
    → 最多拉回 2 次，超过就放行（防止真的卡死）

  【加载条件】
  只在 Pro/Ultra 模式加载（plan_mode=True）。
  Flash/Thinking 模式不需要待办列表，所以不加这个中间件。

  【基类 vs 本类的分工】
  LangChain 自带的 TodoListMiddleware（基类）提供：
    - write_todos 工具：让 AI 能创建/更新待办列表
    - system prompt 注入：教 AI 什么时候用、怎么用
    - 并行调用检测：如果 AI 同时调用 2 次 write_todos，报错拦截
      （因为 write_todos 每次调用会替换整个列表，并行调用会冲突）

  DeerFlow 的 TodoMiddleware（本文件）在基类基础上增加：
    - before_model：上下文丢失检测（问题 A）
    - after_model：过早退出预防（问题 B）

Additionally, this middleware prevents the agent from exiting the loop while
there are still incomplete todo items. When the model produces a final response
(no tool calls) but todos are not yet complete, the middleware queues a reminder
for the next model request and jumps back to the model node to force continued
engagement. The completion reminder is injected via ``wrap_model_call`` instead
of being persisted into graph state as a normal user-visible message.
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents.middleware import TodoListMiddleware
from langchain.agents.middleware.todo import Todo
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse, hook_config
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime

from deerflow.agents.thread_state import ThreadState


def _todos_in_messages(messages: list[Any]) -> bool:
    """yyds: 检查对话历史里还能不能找到 write_todos 的调用记录。

    找得到 → AI 还能"看见"待办列表，不需要提醒。
    找不到 → 被摘要中间件删了，需要注入提醒。

    怎么找的：遍历所有 AI 消息，看 tool_calls 里有没有 name="write_todos" 的。
    """
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.get("name") == "write_todos":
                    return True
    return False


def _reminder_in_messages(messages: list[Any]) -> bool:
    """yyds: 检查是否已经注入过"待办列表还在"的提醒。

    防止重复注入：上一轮已经提醒过了，AI 还没来得及处理，不能再塞一条。

    怎么判断的：找 name="todo_reminder" 的 HumanMessage。
    """
    for msg in messages:
        if isinstance(msg, HumanMessage) and getattr(msg, "name", None) == "todo_reminder":
            return True
    return False



    """yyds: 数一数已经催了 AI 几次"你还有任务没做完"。
    催了 >= 2 次 → 放行，不再催了（AI 可能真的做不完，不能死循环）。
    催了 < 2 次 → 继续催。
    怎么数的：数 name="todo_completion_reminder" 的 HumanMessage 有几条。


    """yyds: 统计已完成提醒的次数（用于限制最大提醒次数，防止无限循环）。"""

def _format_todos(todos: list[Todo]) -> str:
    """yyds: 把待办列表转成人能读的文本。

    输入：[{"status": "completed", "content": "搜索"}, {"status": "pending", "content": "写报告"}]
    输出：
      - [completed] 搜索
      - [pending] 写报告
    """
    lines: list[str] = []
    for todo in todos:
        status = todo.get("status", "pending")
        content = todo.get("content", "")
        lines.append(f"- [{status}] {content}")
    return "\n".join(lines)


def _format_completion_reminder(todos: list[Todo]) -> str:
    """Format a completion reminder for incomplete todo items."""
    incomplete = [t for t in todos if t.get("status") != "completed"]
    incomplete_text = "\n".join(f"- [{t.get('status', 'pending')}] {t.get('content', '')}" for t in incomplete)
    return (
        "<system_reminder>\n"
        "You have incomplete todo items that must be finished before giving your final response:\n\n"
        f"{incomplete_text}\n\n"
        "Please continue working on these tasks. Call `write_todos` to mark items as completed "
        "as you finish them, and only respond when all items are done.\n"
        "</system_reminder>"
    )


_TOOL_CALL_FINISH_REASONS = {"tool_calls", "function_call"}


def _has_tool_call_intent_or_error(message: AIMessage) -> bool:
    """Return True when an AIMessage is not a clean final answer.

    Todo completion reminders should only fire when the model has produced a
    plain final response. Provider/tool parsing details have moved across
    LangChain versions and integrations, so keep all tool-intent/error signals
    behind this helper instead of checking one concrete field at the call site.
    """
    if message.tool_calls:
        return True

    if getattr(message, "invalid_tool_calls", None):
        return True

    # Backward/provider compatibility: some integrations preserve raw or legacy
    # tool-call intent in additional_kwargs even when structured tool_calls is
    # empty. If this helper changes, update the matching sentinel test
    # `TestToolCallIntentOrError.test_langchain_ai_message_tool_fields_are_explicitly_handled`;
    # if that test fails after a LangChain upgrade, review this helper so new
    # tool-call/error fields are not silently treated as clean final answers.
    additional_kwargs = getattr(message, "additional_kwargs", {}) or {}
    if additional_kwargs.get("tool_calls") or additional_kwargs.get("function_call"):
        return True

    response_metadata = getattr(message, "response_metadata", {}) or {}
    return response_metadata.get("finish_reason") in _TOOL_CALL_FINISH_REASONS


class TodoMiddleware(TodoListMiddleware):
    """yyds: 待办事项中间件 — 解决 AI "忘事"和"偷懒"两个问题。

    完整生命周期（Pro/Ultra 模式下）：

    用户："调研 LangGraph 和 CrewAI"
      │
      │  ── before_model（模型调用前）──
      │  AI 你还记得待办列表吗？
      │  ├─ state 里没有 todos → 没有待办列表，跳过
      │  ├─ 对话历史里有 write_todos → 还能看见，跳过
      │  ├─ 已经提醒过了 → 不重复提醒，跳过
      │  └─ 对话历史里找不到 write_todos → 注入提醒：
      │      "你的待办列表还在！当前状态：[列表]"
      │
      │  ── LLM 调用 ──
      │  模型生成响应
      │
      │  ── after_model（模型响应后）──
      │  AI 你做完了吗？
      │  ├─ 基类先检查：并行 write_todos？→ 是就报错拦截
      │  ├─ AI 还在调工具（有 tool_calls）→ 还在干活，不干预
      │  ├─ 所有待办都 completed → 真的做完了，允许退出
      │  ├─ 已经催了 >= 2 次 → 放行（别死循环了）
      │  └─ 还有没做完的 + AI 想结束 → 注入提醒 + 拉回继续干：
      │      "你还有 N 个任务没做完！继续！"
      │      jump_to="model" → 强制重新调用模型
      │
      └─ 循环，直到全部完成 or 催了 2 次后放行
    """

    state_schema = ThreadState

    @override
    def before_model(
        self,
        state: ThreadState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        """yyds: 解决问题 A — AI 忘了待办列表。

        例子：
          第 5 轮对话：AI 调用 write_todos 创建待办 [搜索, 分析, 报告]
          第 15 轮对话：摘要中间件把第 1-10 轮压缩了，write_todos 调用被删了
          → state["todos"] 还有数据：[{completed, "搜索"}, {in_progress, "分析"}, ...]
          → 但 AI 看不见 write_todos 那条消息了
          → 注入一条 HumanMessage："你的待办列表还在！[列表]"

        四个守卫条件（满足任何一个就不干预）：
          ① state 里没有 todos → 根本没有待办列表
          ② 对话历史里有 write_todos → AI 还能看见
          ③ 已经注入过提醒了 → 不重复注入
          ④ 以上都不满足 → 注入提醒
        """
        # ① 没有 todos → 没有待办列表，不用管
        todos: list[Todo] = state.get("todos") or []  # type: ignore[assignment]
        if not todos:
            return None

        messages = state.get("messages") or []
        # ② 对话历史里能找到 write_tools 调用 → AI 还看得见待办列表
        if _todos_in_messages(messages):
            return None

        # ③ 已经提醒过了 → 不重复注入
        if _reminder_in_messages(messages):
            return None

        # ④ 被摘要删了，AI 看不见了 → 注入一条"待办列表还在"的提醒
        formatted = _format_todos(todos)
        reminder = HumanMessage(
            name="todo_reminder",  # yyds: 用 name 标记这条消息是"待办提醒"，不是用户发的
            additional_kwargs={"hide_from_ui": True},
            content=(
                "<system_reminder>\n"
                "Your todo list from earlier is no longer visible in the current context window, "
                "but it is still active. Here is the current state:\n\n"
                f"{formatted}\n\n"
                "Continue tracking and updating this todo list as you work. "
                "Call `write_todos` whenever the status of any item changes.\n"
                "</system_reminder>"
            ),
        )
        return {"messages": [reminder]}

    @override
    async def abefore_model(
        self,
        state: ThreadState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        """Async version of before_model."""
        return self.before_model(state, runtime)

    _MAX_COMPLETION_REMINDERS = 2  # yyds: 最多催 2 次。超过就放行，防止 AI 真的做不完时死循环。
    _MAX_COMPLETION_REMINDER_KEYS = 4096

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lock = threading.Lock()
        self._pending_completion_reminders: dict[tuple[str, str], list[str]] = {}
        self._completion_reminder_counts: dict[tuple[str, str], int] = {}
        self._completion_reminder_touch_order: dict[tuple[str, str], int] = {}
        self._completion_reminder_next_order = 0

    @staticmethod
    def _get_thread_id(runtime: Runtime) -> str:
        context = getattr(runtime, "context", None)
        thread_id = context.get("thread_id") if context else None
        return str(thread_id) if thread_id else "default"

    @staticmethod
    def _get_run_id(runtime: Runtime) -> str:
        context = getattr(runtime, "context", None)
        run_id = context.get("run_id") if context else None
        return str(run_id) if run_id else "default"

    def _pending_key(self, runtime: Runtime) -> tuple[str, str]:
        return self._get_thread_id(runtime), self._get_run_id(runtime)

    def _touch_completion_reminder_key_locked(self, key: tuple[str, str]) -> None:
        self._completion_reminder_next_order += 1
        self._completion_reminder_touch_order[key] = self._completion_reminder_next_order

    def _completion_reminder_keys_locked(self) -> set[tuple[str, str]]:
        keys = set(self._pending_completion_reminders)
        keys.update(self._completion_reminder_counts)
        keys.update(self._completion_reminder_touch_order)
        return keys

    def _drop_completion_reminder_key_locked(self, key: tuple[str, str]) -> None:
        self._pending_completion_reminders.pop(key, None)
        self._completion_reminder_counts.pop(key, None)
        self._completion_reminder_touch_order.pop(key, None)

    def _prune_completion_reminder_state_locked(self, protected_key: tuple[str, str]) -> None:
        keys = self._completion_reminder_keys_locked()
        overflow = len(keys) - self._MAX_COMPLETION_REMINDER_KEYS
        if overflow <= 0:
            return

        candidates = [key for key in keys if key != protected_key]
        candidates.sort(key=lambda key: self._completion_reminder_touch_order.get(key, 0))
        for key in candidates[:overflow]:
            self._drop_completion_reminder_key_locked(key)

    def _queue_completion_reminder(self, runtime: Runtime, reminder: str) -> None:
        key = self._pending_key(runtime)
        with self._lock:
            self._pending_completion_reminders.setdefault(key, []).append(reminder)
            self._completion_reminder_counts[key] = self._completion_reminder_counts.get(key, 0) + 1
            self._touch_completion_reminder_key_locked(key)
            self._prune_completion_reminder_state_locked(protected_key=key)

    def _completion_reminder_count_for_runtime(self, runtime: Runtime) -> int:
        key = self._pending_key(runtime)
        with self._lock:
            return self._completion_reminder_counts.get(key, 0)

    def _drain_completion_reminders(self, runtime: Runtime) -> list[str]:
        key = self._pending_key(runtime)
        with self._lock:
            reminders = self._pending_completion_reminders.pop(key, [])
            if reminders or key in self._completion_reminder_counts:
                self._touch_completion_reminder_key_locked(key)
            return reminders

    def _clear_other_run_completion_reminders(self, runtime: Runtime) -> None:
        thread_id, current_run_id = self._pending_key(runtime)
        with self._lock:
            for key in self._completion_reminder_keys_locked():
                if key[0] == thread_id and key[1] != current_run_id:
                    self._drop_completion_reminder_key_locked(key)

    def _clear_current_run_completion_reminders(self, runtime: Runtime) -> None:
        key = self._pending_key(runtime)
        with self._lock:
            self._drop_completion_reminder_key_locked(key)

    @override
    def before_agent(self, state: ThreadState, runtime: Runtime) -> dict[str, Any] | None:
        self._clear_other_run_completion_reminders(runtime)
        return None

    @override
    async def abefore_agent(self, state: ThreadState, runtime: Runtime) -> dict[str, Any] | None:
        self._clear_other_run_completion_reminders(runtime)
        return None

    @hook_config(can_jump_to=["model"])
    @override
    def after_model(
        self,
        state: ThreadState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        """yyds: 解决问题 B — AI 偷懒提前交差。

        例子：
          待办列表：[completed 搜索, completed 分析, pending 写报告]
          AI 输出："好的，调研完成，总结如下..."（没有调用任何工具，想结束）
          → 检测到"还有 1 个 pending + AI 没调工具"→ 注入"你还没做完！"+ 拉回继续干
          → AI 被迫继续，写完报告后才输出最终答案

        jump_to="model" 的效果：
          正常情况下，after_model 返回 None → 进入下一个阶段（可能结束对话）
          返回 {"jump_to": "model"} → 跳回模型节点，重新调用 LLM
          相当于对 AI 说"回去重做"

        五层退出决策：
          ① 基类检测到并行 write_todos → 返回错误（基类逻辑优先）
          ② AI 还在调工具（有 tool_calls）→ 不干预，让它继续干活
          ③ 所有 todos 都 completed → 真的做完了，允许退出
          ④ 已经催了 >= 2 次 → 放行（别死循环了）
          ⑤ 还有没做完的 + AI 想结束 → 注入提醒 + jump_to="model" 拉回来
        """
        # ① 基类逻辑：如果 AI 同时调了 2 次 write_todos，返回错误 ToolMessage
        base_result = super().after_model(state, runtime)
        if base_result is not None:
            return base_result

        # ② AI 还有 tool_calls → 还在干活（调工具、搜索等），不想退出，不干预
        # Only intervene when the agent wants to exit cleanly. Tool-call
        # intent or tool-call parse errors should be handled by the tool path
        # instead of being masked by todo reminders.
        messages = state.get("messages") or []
        last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        if not last_ai or _has_tool_call_intent_or_error(last_ai):
            return None

        # ③ 全部 completed 或无待办 → 做完了，允许退出
        todos: list[Todo] = state.get("todos") or []  # type: ignore[assignment]
        if not todos or all(t.get("status") == "completed" for t in todos):
            return None

        # ④ 催了 >= 2 次 → 放行。可能 AI 真的卡住了，不能无限循环。
        if self._completion_reminder_count_for_runtime(runtime) >= self._MAX_COMPLETION_REMINDERS:
            return None

        # ⑤ 还有没做完的 + AI 想结束 → 注入提醒 + 拉回来继续干
        # We must not persist this control prompt as a normal HumanMessage, otherwise it
        # can leak into user-visible message streams and saved transcripts.
        self._queue_completion_reminder(runtime, _format_completion_reminder(todos))
        return {"jump_to": "model"}

    @override
    @hook_config(can_jump_to=["model"])
    async def aafter_model(
        self,
        state: ThreadState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        """Async version of after_model."""
        return self.after_model(state, runtime)

    @staticmethod
    def _format_pending_completion_reminders(reminders: list[str]) -> str:
        return "\n\n".join(dict.fromkeys(reminders))

    def _augment_request(self, request: ModelRequest) -> ModelRequest:
        reminders = self._drain_completion_reminders(request.runtime)
        if not reminders:
            return request
        new_messages = [
            *request.messages,
            HumanMessage(
                content=self._format_pending_completion_reminders(reminders),
                name="todo_completion_reminder",
                additional_kwargs={"hide_from_ui": True},
            ),
        ]
        return request.override(messages=new_messages)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        return handler(self._augment_request(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        return await handler(self._augment_request(request))

    @override
    def after_agent(self, state: ThreadState, runtime: Runtime) -> dict[str, Any] | None:
        self._clear_current_run_completion_reminders(runtime)
        return None

    @override
    async def aafter_agent(self, state: ThreadState, runtime: Runtime) -> dict[str, Any] | None:
        self._clear_current_run_completion_reminders(runtime)
        return None
