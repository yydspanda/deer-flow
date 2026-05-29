"""yyds: Sub-Agent 执行引擎 — 在独立线程里跑子 Agent，不阻塞主对话。

【大白话讲清楚】
  这个文件是整个 Sub-Agent 子系统的核心。它解决一个问题：
  "怎么让一个独立的 Agent 在后台线程里跑，同时不阻塞主对话？"

  核心挑战：
    Lead Agent 运行在 LangGraph 的 async event loop 里。
    如果直接在同一个 loop 里跑 sub-agent，会阻塞主对话（用户等不到回复）。

  解决方案：
    在独立线程里创建一个持久化的 event loop（_isolated_subagent_loop），
    把 sub-agent 的 async 执行提交到这个独立 loop 里。
    主对话的 event loop 不受影响，可以继续处理 SSE 推送。

【具体例子】
  用户："帮我重构这个项目的所有测试文件"
    Lead Agent 调用 task_tool(description="重构测试", subagent_type="general-purpose")
      │
      ① task_tool 创建 SubagentExecutor(config, tools, sandbox_state)
      ② executor.execute_async(task) → 返回 task_id="a1b2c3"
      ③ task_tool 每 5 秒轮询 get_background_task_result("a1b2c3")
         → 轮询期间主对话不阻塞，前端通过 SSE 看到 "task_running" 进度
      ④ sub-agent 在独立线程里跑：
         加载 skills → 构建 messages → create_agent → agent.astream()
         逐 chunk 收集 AI 消息 → token 统计 → 15 分钟超时
      ⑤ 跑完后 status=COMPLETED → task_tool 读到结果 → 返回给 Lead Agent

  超时场景：
    sub-agent 跑了 15 分钟还没完 → future.result(timeout=900) 抛 TimeoutError
    → cancel_event.set() → _aexecute 在 astream 迭代边界检测到 → 提前返回 TIMED_OUT

  取消场景：
    用户说"别跑了" → request_cancel_background_task(task_id)
    → cancel_event.set() → 同上，协作式取消

【执行路径的两条分支】
  execute() 同步执行：
    已在 event loop 中？→ _execute_in_isolated_loop（提交到持久化 loop）
    不在 event loop？→ asyncio.run()（创建临时 loop，单次使用）

  execute_async() 后台执行（task_tool 用这个）：
    创建 SubagentResult → 存入 _background_tasks → 提交到 _scheduler_pool
    → _scheduler_pool 线程里 → copy_context → 提交到 _isolated_subagent_loop
    → 返回 task_id，task_tool 轮询获取结果

【关键设计】
  - _isolated_subagent_loop：持久化 event loop，daemon 线程，进程生命周期内复用
  - _scheduler_pool(max_workers=3)：最多 3 个 sub-agent 并发调度
  - copy_context()：保留父线程的 ContextVar（trace_id 等）
  - cancel_event：协作式取消，astream 每个 chunk 检查一次
  - disallowed_tools=["task"]：递归防护
  - build_subagent_runtime_middlewares：精简版中间件（ThreadData/Sandbox/Guardrail/ToolError）

---
Subagent execution engine.
"""

import asyncio
import atexit
import logging
import threading
import uuid
from collections.abc import Callable, Coroutine
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from contextvars import Context, copy_context
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from langchain.agents import create_agent
from langchain.tools import BaseTool
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from deerflow.agents.thread_state import SandboxState, ThreadDataState, ThreadState
from deerflow.config import get_app_config
from deerflow.config.app_config import AppConfig
from deerflow.models import create_chat_model
from deerflow.skills.tool_policy import filter_tools_by_skill_allowed_tools
from deerflow.skills.types import Skill
from deerflow.subagents.config import SubagentConfig, resolve_subagent_model_name
from deerflow.subagents.token_collector import SubagentTokenCollector

logger = logging.getLogger(__name__)


# yyds: 模块热重载时的清理 — 如果旧的 isolated loop 还在，先关掉再重建
_previous_shutdown_isolated_subagent_loop = globals().get("_shutdown_isolated_subagent_loop")
if callable(_previous_shutdown_isolated_subagent_loop):
    atexit.unregister(_previous_shutdown_isolated_subagent_loop)
    _previous_shutdown_isolated_subagent_loop()


class SubagentStatus(Enum):
    """yyds: Sub-agent 执行状态 — PENDING → RUNNING → 终态（COMPLETED/FAILED/CANCELLED/TIMED_OUT）。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    @property
    def is_terminal(self) -> bool:
        return self in {
            type(self).COMPLETED,
            type(self).FAILED,
            type(self).CANCELLED,
            type(self).TIMED_OUT,
        }


@dataclass
class SubagentResult:
    """yyds: Sub-agent 执行结果 — 贯穿整个生命周期的数据容器。

    生命周期：
      execute_async() 创建(PENDING) → 存入 _background_tasks
      → run_task() 改为 RUNNING → _aexecute() 跑完改为终态
      → task_tool 轮询读取 → cleanup 删除

    关键字段：
      ai_messages: sub-agent 过程中产生的所有 AI 消息（前端展示用）
      token_usage_records: LLM token 用量（传给父 Agent 的 RunJournal）
      cancel_event: 协作式取消信号（父 Agent 或超时设置）
    """

    task_id: str
    trace_id: str
    status: SubagentStatus
    result: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    ai_messages: list[dict[str, Any]] | None = None
    token_usage_records: list[dict[str, int | str]] = field(default_factory=list)
    usage_reported: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)  # yyds: 协作式取消信号，astream 每个 chunk 检查
    _state_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self):
        if self.ai_messages is None:
            self.ai_messages = []

    def try_set_terminal(
        self,
        status: SubagentStatus,
        *,
        result: str | None = None,
        error: str | None = None,
        completed_at: datetime | None = None,
        ai_messages: list[dict[str, Any]] | None = None,
        token_usage_records: list[dict[str, int | str]] | None = None,
    ) -> bool:
        """Set a terminal status exactly once.

        Background timeout/cancellation and the execution worker can race on the
        same result holder.  The first terminal transition wins; late terminal
        writes must not change status or payload fields.
        """
        if not status.is_terminal:
            raise ValueError(f"Status {status} is not terminal")

        with self._state_lock:
            if self.status.is_terminal:
                return False

            if result is not None:
                self.result = result
            if error is not None:
                self.error = error
            if ai_messages is not None:
                self.ai_messages = ai_messages
            if token_usage_records is not None:
                self.token_usage_records = token_usage_records
            self.completed_at = completed_at or datetime.now()
            self.status = status
            return True


# yyds: ──── 全局存储 ────
# yyds: _background_tasks: 所有后台任务的 result 容器，task_id → SubagentResult
# yyds: _scheduler_pool: 调度线程池，max_workers=3，控制并发 sub-agent 数量
# yyds: _isolated_subagent_loop: 持久化 event loop，避免每次执行创建新 loop

_background_tasks: dict[str, SubagentResult] = {}
_background_tasks_lock = threading.Lock()  # yyds: 多线程安全（task_tool 线程 + scheduler 线程 + 主线程都会访问）

_scheduler_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="subagent-scheduler-")  # yyds: 最多 3 个 sub-agent 并发

_isolated_subagent_loop: asyncio.AbstractEventLoop | None = None
_isolated_subagent_loop_thread: threading.Thread | None = None
_isolated_subagent_loop_started: threading.Event | None = None
_isolated_subagent_loop_lock = threading.Lock()


def _run_isolated_subagent_loop(
    loop: asyncio.AbstractEventLoop,
    started_event: threading.Event,
) -> None:
    """yyds: 在守护线程中跑持久化 event loop — 进程生命周期内一直跑，sub-agent 循环复用。"""
    asyncio.set_event_loop(loop)
    loop.call_soon(started_event.set)  # yyds: 通知 _get_isolated_subagent_loop "loop 已启动"
    try:
        loop.run_forever()
    finally:
        started_event.clear()


def _shutdown_isolated_subagent_loop() -> None:
    """yyds: 进程退出时清理 — 停 loop → join 线程 → 关 loop。"""
    global _isolated_subagent_loop, _isolated_subagent_loop_thread, _isolated_subagent_loop_started

    with _isolated_subagent_loop_lock:
        loop = _isolated_subagent_loop
        thread = _isolated_subagent_loop_thread
        _isolated_subagent_loop = None
        _isolated_subagent_loop_thread = None
        _isolated_subagent_loop_started = None

    if loop is None:
        return

    if loop.is_running():
        loop.call_soon_threadsafe(loop.stop)

    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=1)

    thread_stopped = thread is None or not thread.is_alive()
    loop_stopped = not loop.is_running()

    if not loop.is_closed():
        if thread_stopped and loop_stopped:
            loop.close()
        else:
            logger.warning(
                "Skipping close of isolated subagent loop because shutdown did not complete within timeout (thread_alive=%s, loop_running=%s)",
                thread is not None and thread.is_alive(),
                loop.is_running(),
            )


atexit.register(_shutdown_isolated_subagent_loop)


def _get_isolated_subagent_loop() -> asyncio.AbstractEventLoop:
    """yyds: 获取或创建持久化 isolated event loop（懒初始化，线程安全）。

    懒初始化：第一次调用时才创建 loop + daemon 线程。
    复用：后续调用直接返回同一个 loop。
    重建：如果 loop 不可用（关闭/线程死掉），自动重建。

    为什么用持久化 loop 而不是每次 asyncio.new_event_loop()？
      每个 loop 创建时分配资源（连接池等），销毁时释放。
      高频创建销毁 → 资源抖动 + 潜在泄漏。持久化 loop 避免这个问题。
    """
    global _isolated_subagent_loop, _isolated_subagent_loop_thread, _isolated_subagent_loop_started
    with _isolated_subagent_loop_lock:
        thread_is_alive = _isolated_subagent_loop_thread is not None and _isolated_subagent_loop_thread.is_alive()
        loop_is_usable = _isolated_subagent_loop is not None and not _isolated_subagent_loop.is_closed() and _isolated_subagent_loop.is_running() and thread_is_alive

        if not loop_is_usable:
            loop = asyncio.new_event_loop()
            started_event = threading.Event()
            thread = threading.Thread(
                target=_run_isolated_subagent_loop,
                args=(loop, started_event),
                name="subagent-persistent-loop",
                daemon=True,  # yyds: daemon 线程，进程退出时自动终止
            )
            thread.start()
            if not started_event.wait(timeout=5):  # yyds: 等 5 秒，loop 应该启动了
                loop.call_soon_threadsafe(loop.stop)
                thread.join(timeout=1)
                loop.close()
                raise RuntimeError("Timed out starting isolated subagent event loop")
            _isolated_subagent_loop = loop
            _isolated_subagent_loop_thread = thread
            _isolated_subagent_loop_started = started_event

        if _isolated_subagent_loop is None:
            raise RuntimeError("Isolated subagent event loop is not initialized")
        return _isolated_subagent_loop


def _submit_to_isolated_loop_in_context(
    context: Context,
    coro_factory: Callable[[], Coroutine[Any, Any, SubagentResult]],
) -> Future[SubagentResult]:
    """yyds: 把协程提交到 isolated loop，同时保留 ContextVar 状态。

    为什么用 context.run() 包裹？
      ContextVar（如 trace_id）是线程局部的。
      新线程默认看不到父线程的 ContextVar。
      context.run() 让 lambda 在父线程的 context 里执行，
      asyncio.run_coroutine_threadsafe 提交的协程就能继承这些 ContextVar 了。
    """
    return context.run(
        lambda: asyncio.run_coroutine_threadsafe(
            coro_factory(),
            _get_isolated_subagent_loop(),
        )
    )


def _filter_tools(
    all_tools: list[BaseTool],
    allowed: list[str] | None,
    disallowed: list[str] | None,
) -> list[BaseTool]:
    """yyds: 工具过滤 — 先白名单，再黑名单。

    白名单（tools=["bash", "ls"]）：只保留指定的工具
    黑名单（disallowed_tools=["task"]）：从结果中移除指定的工具
    两者可以同时生效：先按白名单筛，再按黑名单排除。
    """
    filtered = all_tools

    if allowed is not None:
        allowed_set = set(allowed)
        filtered = [t for t in filtered if t.name in allowed_set]

    if disallowed is not None:
        disallowed_set = set(disallowed)
        filtered = [t for t in filtered if t.name not in disallowed_set]

    return filtered


class SubagentExecutor:
    """yyds: Sub-Agent 执行器 — 每次 task_tool 委派任务时创建一个实例。

    完整生命周期（以 execute_async 为例）：

    task_tool 调用 executor.execute_async(task)
      │
      ① 创建 SubagentResult(PENDING) → 存入 _background_tasks
      ② 提交到 _scheduler_pool 线程池
      │
      └─→ _scheduler_pool 线程里：
            │
            ③ copy_context() 保留父线程 ContextVar
            ④ 提交到 _isolated_subagent_loop
            │
            └─→ isolated loop 上跑 _aexecute(task)：
                  │
                  ⑤ _build_initial_state(task)
                     → 加载 skills（白名单过滤）
                     → 过滤工具（skill allowed_tools）
                     → 构建消息：[SystemMessage(prompt+skills), HumanMessage(task)]
                     → 注入父 Agent 的 sandbox_state + thread_data
                  │
                  ⑥ _create_agent(filtered_tools)
                     → 解析模型（inherit → 父模型）
                     → 构建精简版中间件链（ThreadData/Sandbox/Guardrail/ToolError）
                     → create_agent(model, tools, middlewares)
                  │
                  ⑦ agent.astream(state)
                     → 逐 chunk 迭代
                     → 每个 chunk 检查 cancel_event
                     → 收集 AI 消息 + token 用量
                  │
                  ⑧ 提取最后一个 AIMessage → result.result
                     → status = COMPLETED

    两种执行模式的区别：
      execute()：同步等待结果，适合单次脚本调用
      execute_async()：后台执行返回 task_id，适合 task_tool 轮询模式
    """

    def __init__(
        self,
        config: SubagentConfig,
        tools: list[BaseTool],
        app_config: AppConfig | None = None,
        parent_model: str | None = None,
        sandbox_state: SandboxState | None = None,
        thread_data: ThreadDataState | None = None,
        thread_id: str | None = None,
        trace_id: str | None = None,
    ):
        """yyds: 初始化执行器 — 过滤工具 + 解析模型 + 保存父 Agent 状态。"""
        self.config = config
        self.app_config = app_config
        self.parent_model = parent_model
        if config.model != "inherit" or parent_model is not None or app_config is not None:
            self.model_name: str | None = resolve_subagent_model_name(config, parent_model, app_config=app_config)
        else:
            self.model_name = None  # yyds: 延迟到 _create_agent 时再解析（测试场景可能没有 config.yaml）
        self.sandbox_state = sandbox_state  # yyds: 传递父 Agent 的 sandbox，sub-agent 能访问同一个沙箱
        self.thread_data = thread_data  # yyds: 传递父 Agent 的 thread_data（workspace/uploads 路径）
        self.thread_id = thread_id
        self.trace_id = trace_id or str(uuid.uuid4())[:8]  # yyds: 没给就生成一个，用于日志关联

        self._base_tools = _filter_tools(
            tools,
            config.tools,
            config.disallowed_tools,  # yyds: 过滤掉 task 等禁用工具
        )
        self.tools = self._base_tools

        logger.info(f"[trace={self.trace_id}] SubagentExecutor initialized: {config.name} with {len(self.tools)} tools")

    def _create_agent(self, tools: list[BaseTool] | None = None):
        """yyds: 创建 LangGraph Agent 实例 — 模型 + 中间件 + 工具。

        和 Lead Agent 的区别：
          - thinking_enabled=False（sub-agent 不启用 thinking，省 token）
          - system_prompt=None（prompt 在 _build_initial_state 里通过消息注入，不绑在 agent 上）
          - 中间件是精简版（只有 ThreadData/Sandbox/Guardrail/ToolError）
        """
        app_config = self.app_config or get_app_config()
        if self.model_name is None:
            self.model_name = resolve_subagent_model_name(self.config, self.parent_model, app_config=app_config)
        model = create_chat_model(name=self.model_name, thinking_enabled=False, app_config=app_config)

        from deerflow.agents.middlewares.tool_error_handling_middleware import build_subagent_runtime_middlewares

        middlewares = build_subagent_runtime_middlewares(app_config=app_config, model_name=self.model_name, lazy_init=True)

        return create_agent(
            model=model,
            tools=tools if tools is not None else self.tools,
            middleware=middlewares,
            system_prompt=None,
            state_schema=ThreadState,
        )

    async def _load_skills(self) -> list[Skill]:
        """yyds: 从磁盘加载 skill 元数据 — 支持白名单过滤。

        三种情况：
          config.skills = None → 加载所有 enabled skills
          config.skills = [] → 不加载任何 skill
          config.skills = ["skill-a"] → 只加载指定的
        """
        if self.config.skills is not None and len(self.config.skills) == 0:
            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} skills=[] — skipping skill loading")
            return []

        try:
            from deerflow.skills.storage import get_or_new_skill_storage

            storage_kwargs = {"app_config": self.app_config} if self.app_config is not None else {}
            storage = await asyncio.to_thread(get_or_new_skill_storage, **storage_kwargs)  # yyds: to_thread 避免阻塞 event loop
            all_skills = await asyncio.to_thread(storage.load_skills, enabled_only=True)
            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} loaded {len(all_skills)} enabled skills from disk")
        except Exception:
            logger.exception(f"[trace={self.trace_id}] Failed to load skills for subagent {self.config.name}")
            raise

        if not all_skills:
            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} no enabled skills found")
            return []

        if self.config.skills is not None:
            allowed = set(self.config.skills)
            return [s for s in all_skills if s.name in allowed]
        return all_skills

    def _apply_skill_allowed_tools(self, skills: list[Skill]) -> list[BaseTool]:
        """yyds: skill 可以进一步限制工具 — 比如某个 skill 只允许 bash 和 read_file。"""
        return filter_tools_by_skill_allowed_tools(self._base_tools, skills)

    async def _load_skill_messages(self, skills: list[Skill]) -> list[SystemMessage]:
        """yyds: 读取每个 skill 的 SKILL.md，包装为 SystemMessage — 注入到 sub-agent 对话中。

        注入方式（Codex 风格）：不写入 system_prompt，而是作为独立消息注入。
        这样 sub-agent 的 system_prompt 和 skill 内容分离，互不干扰。
        """
        if not skills:
            return []

        messages = []
        for skill in skills:
            try:
                content = await asyncio.to_thread(skill.skill_file.read_text, encoding="utf-8")
                content = content.strip()
                if content:
                    messages.append(SystemMessage(content=f'<skill name="{skill.name}">\n{content}\n</skill>'))
                    logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} loaded skill: {skill.name}")
            except Exception:
                logger.debug(f"[trace={self.trace_id}] Failed to read skill {skill.name}", exc_info=True)

        return messages

    async def _build_initial_state(self, task: str) -> tuple[dict[str, Any], list[BaseTool]]:
        """yyds: 构建 sub-agent 的初始状态 — 加载 skills + 过滤工具 + 构建消息 + 注入父状态。

        消息构建顺序：
          ① [SystemMessage] — system_prompt + skill 内容合并为一条
          ② [HumanMessage] — 任务描述

        为什么把 system_prompt 和 skills 合并为一条 SystemMessage？
          有些 LLM API 不支持多条 SystemMessage（报错 "System message must be at the beginning"）。
          所以合并成一条，避免兼容性问题。

        父状态传递：
          sandbox_state → sub-agent 能访问同一个沙箱环境
          thread_data → sub-agent 能看到 workspace/uploads 路径
        """
        skills = await self._load_skills()
        filtered_tools = self._apply_skill_allowed_tools(skills)
        skill_messages = await self._load_skill_messages(skills)

        system_parts: list[str] = []
        if self.config.system_prompt:
            system_parts.append(self.config.system_prompt)
        for skill_msg in skill_messages:
            system_parts.append(skill_msg.content)

        messages: list[Any] = []
        if system_parts:
            messages.append(SystemMessage(content="\n\n".join(system_parts)))  # yyds: 合并为一条，避免多条 SystemMessage 兼容性问题

        messages.append(HumanMessage(content=task))

        state: dict[str, Any] = {
            "messages": messages,
        }

        if self.sandbox_state is not None:
            state["sandbox"] = self.sandbox_state  # yyds: 共享父 Agent 的沙箱
        if self.thread_data is not None:
            state["thread_data"] = self.thread_data  # yyds: 共享父 Agent 的路径映射

        return state, filtered_tools

    async def _aexecute(self, task: str, result_holder: SubagentResult | None = None) -> SubagentResult:
        """yyds: 异步执行核心 — 在 isolated loop 上跑 agent.astream()。

        完整流程：
          ① 拿到 result_holder（后台执行时共享的，同步执行时新建的）
          ② _build_initial_state → _create_agent → astream
          ③ 每个 chunk：检查 cancel → 收集 AI 消息 → token 统计
          ④ 跑完：提取最后一个 AIMessage → result.result → status=COMPLETED
          ⑤ 异常：status=FAILED + error

        协作式取消的"协作"含义：
          cancel_event.set() 不会强制中断 astream。
          而是 _aexecute 在每个 chunk 迭代时主动检查。
          如果 sub-agent 正在跑一个很长的 bash 命令，
          要等那个命令跑完、下一个 chunk 产生时才能检测到取消。

        AI 消息去重：
          astream 的 stream_mode="values" 每次返回完整的 state snapshot。
          两次 chunk 可能包含相同的 AIMessage。
          通过 message id 或完整 dict 比较去重。
        """
        if result_holder is not None:
            result = result_holder
        else:
            task_id = str(uuid.uuid4())[:8]
            result = SubagentResult(
                task_id=task_id,
                trace_id=self.trace_id,
                status=SubagentStatus.RUNNING,
                started_at=datetime.now(),
            )
        ai_messages = result.ai_messages
        if ai_messages is None:
            ai_messages = []
            result.ai_messages = ai_messages

        collector: SubagentTokenCollector | None = None
        try:
            state, filtered_tools = await self._build_initial_state(task)
            agent = self._create_agent(filtered_tools)

            collector_caller = f"subagent:{self.config.name}"
            collector = SubagentTokenCollector(caller=collector_caller)

            run_config: RunnableConfig = {
                "recursion_limit": self.config.max_turns,
                "callbacks": [collector],  # yyds: token 收集器作为 callback 注入
                "tags": [collector_caller],
            }
            context: dict[str, Any] = {}
            if self.thread_id:
                run_config["configurable"] = {"thread_id": self.thread_id}
                context["thread_id"] = self.thread_id
            if self.app_config is not None:
                context["app_config"] = self.app_config

            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} starting async execution with max_turns={self.config.max_turns}")

            final_state = None

            if result.cancel_event.is_set():  # yyds: 还没开始就被取消了
                logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} cancelled before streaming")
                result.try_set_terminal(
                    SubagentStatus.CANCELLED,
                    error="Cancelled by user",
                    token_usage_records=collector.snapshot_records(),
                )
                return result

            async for chunk in agent.astream(state, config=run_config, context=context, stream_mode="values"):
                if result.cancel_event.is_set():  # yyds: 协作式取消 — 每个 chunk 检查一次
                    logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} cancelled by parent")
                    result.try_set_terminal(
                        SubagentStatus.CANCELLED,
                        error="Cancelled by user",
                        token_usage_records=collector.snapshot_records(),
                    )
                    return result

                final_state = chunk

                messages = chunk.get("messages", [])
                if messages:
                    last_message = messages[-1]
                    if isinstance(last_message, AIMessage):
                        message_dict = last_message.model_dump()
                        message_id = message_dict.get("id")
                        is_duplicate = False
                        if message_id:
                            is_duplicate = any(msg.get("id") == message_id for msg in ai_messages)  # yyds: 按 id 去重
                        else:
                            is_duplicate = message_dict in ai_messages  # yyds: 没有 id 就按完整 dict 比较

                        if not is_duplicate:
                            ai_messages.append(message_dict)
                            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} captured AI message #{len(ai_messages)}")

            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} completed async execution")
            token_usage_records = collector.snapshot_records()
            final_result: str | None = None

            if final_state is None:
                logger.warning(f"[trace={self.trace_id}] Subagent {self.config.name} no final state")
                final_result = "No response generated"
            else:
                messages = final_state.get("messages", [])
                logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} final messages count: {len(messages)}")

                last_ai_message = None
                for msg in reversed(messages):
                    if isinstance(msg, AIMessage):
                        last_ai_message = msg
                        break

                if last_ai_message is not None:
                    content = last_ai_message.content
                    if isinstance(content, str):
                        final_result = content
                    elif isinstance(content, list):
                        text_parts = []
                        pending_str_parts = []
                        for block in content:
                            if isinstance(block, str):
                                pending_str_parts.append(block)
                            elif isinstance(block, dict):
                                if pending_str_parts:
                                    text_parts.append("".join(pending_str_parts))
                                    pending_str_parts.clear()
                                text_val = block.get("text")
                                if isinstance(text_val, str):
                                    text_parts.append(text_val)
                        if pending_str_parts:
                            text_parts.append("".join(pending_str_parts))
                        final_result = "\n".join(text_parts) if text_parts else "No text content in response"
                    else:
                        final_result = str(content)
                elif messages:
                    last_message = messages[-1]
                    logger.warning(f"[trace={self.trace_id}] Subagent {self.config.name} no AIMessage found, using last message: {type(last_message)}")
                    raw_content = last_message.content if hasattr(last_message, "content") else str(last_message)
                    if isinstance(raw_content, str):
                        final_result = raw_content
                    elif isinstance(raw_content, list):
                        parts = []
                        pending_str_parts = []
                        for block in raw_content:
                            if isinstance(block, str):
                                pending_str_parts.append(block)
                            elif isinstance(block, dict):
                                if pending_str_parts:
                                    parts.append("".join(pending_str_parts))
                                    pending_str_parts.clear()
                                text_val = block.get("text")
                                if isinstance(text_val, str):
                                    parts.append(text_val)
                        if pending_str_parts:
                            parts.append("".join(pending_str_parts))
                        final_result = "\n".join(parts) if parts else "No text content in response"
                    else:
                        final_result = str(raw_content)
                else:
                    logger.warning(f"[trace={self.trace_id}] Subagent {self.config.name} no messages in final state")
                    final_result = "No response generated"

            if final_result is None:
                final_result = "No response generated"

            result.try_set_terminal(
                SubagentStatus.COMPLETED,
                result=final_result,
                token_usage_records=token_usage_records,
            )

        except Exception as e:
            logger.exception(f"[trace={self.trace_id}] Subagent {self.config.name} async execution failed")
            result.try_set_terminal(
                SubagentStatus.FAILED,
                error=str(e),
                token_usage_records=collector.snapshot_records() if collector is not None else None,
            )

        return result

    def _execute_in_isolated_loop(self, task: str, result_holder: SubagentResult | None = None) -> SubagentResult:
        """yyds: 在 isolated loop 上同步执行 — 带 ContextVar 传播 + 超时控制。

        超时处理：
          future.result(timeout=900) 抛 TimeoutError
          → cancel_event.set() 告诉 _aexecute "别跑了"
          → future.cancel() 取消 future
          → _aexecute 在下一个 chunk 检查到 cancel → 提前返回 TIMED_OUT
        """
        future: Future[SubagentResult] | None = None
        parent_context = copy_context()  # yyds: 捕获当前线程的 ContextVar（trace_id 等）
        try:
            future = _submit_to_isolated_loop_in_context(
                parent_context,
                lambda: self._aexecute(task, result_holder),
            )
            return future.result(timeout=self.config.timeout_seconds)
        except FuturesTimeoutError:
            if result_holder is not None:
                result_holder.cancel_event.set()  # yyds: 协作式取消 — 告诉 _aexecute 别跑了
            if future is not None:
                future.cancel()
            raise
        except Exception:
            if future is None:
                logger.debug(
                    f"[trace={self.trace_id}] Failed to submit subagent {self.config.name} to the isolated event loop",
                    exc_info=True,
                )
            else:
                logger.debug(
                    f"[trace={self.trace_id}] Subagent {self.config.name} failed while executing on the isolated event loop",
                    exc_info=True,
                )
            raise

    def execute(self, task: str, result_holder: SubagentResult | None = None) -> SubagentResult:
        """yyds: 同步执行入口 — 自动检测是否在 event loop 内。

        在 event loop 内（LangGraph async 节点）？
          → _execute_in_isolated_loop（提交到持久化 loop，不阻塞当前 loop）
        不在 event loop 内（脚本/测试）？
          → asyncio.run()（创建临时 loop，用完即销毁）
        """
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                logger.debug(f"[trace={self.trace_id}] Subagent {self.config.name} detected running event loop, using isolated loop")
                return self._execute_in_isolated_loop(task, result_holder)

            return asyncio.run(self._aexecute(task, result_holder))
        except Exception as e:
            logger.exception(f"[trace={self.trace_id}] Subagent {self.config.name} execution failed")
            if result_holder is not None:
                result = result_holder
            else:
                result = SubagentResult(
                    task_id=str(uuid.uuid4())[:8],
                    trace_id=self.trace_id,
                    status=SubagentStatus.RUNNING,
                )
            result.try_set_terminal(SubagentStatus.FAILED, error=str(e))
            return result

    def execute_async(self, task: str, task_id: str | None = None) -> str:
        """yyds: 后台异步执行 — task_tool 的调用入口，返回 task_id 供轮询。

        流程：
          ① 创建 SubagentResult(PENDING) → 存入 _background_tasks
          ② 提交 run_task() 到 _scheduler_pool
          ③ 返回 task_id

        run_task() 在 _scheduler_pool 线程里：
          → 状态改为 RUNNING
          → copy_context → 提交到 isolated loop
          → future.result(timeout=900) 等结果
          → 超时 → cancel_event + TIMED_OUT
          → 异常 → FAILED
        """
        if task_id is None:
            task_id = str(uuid.uuid4())[:8]

        result = SubagentResult(
            task_id=task_id,
            trace_id=self.trace_id,
            status=SubagentStatus.PENDING,
        )

        logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} starting async execution, task_id={task_id}, timeout={self.config.timeout_seconds}s")

        with _background_tasks_lock:
            _background_tasks[task_id] = result

        parent_context = copy_context()

        def run_task():
            with _background_tasks_lock:
                _background_tasks[task_id].status = SubagentStatus.RUNNING
                _background_tasks[task_id].started_at = datetime.now()
                result_holder = _background_tasks[task_id]

            try:
                execution_future = _submit_to_isolated_loop_in_context(
                    parent_context,
                    lambda: self._aexecute(task, result_holder),
                )
                try:
                    # Wait for execution with timeout
                    execution_future.result(timeout=self.config.timeout_seconds)
                except FuturesTimeoutError:
                    logger.error(f"[trace={self.trace_id}] Subagent {self.config.name} execution timed out after {self.config.timeout_seconds}s")
                    # Signal cooperative cancellation and cancel the future
                    result_holder.cancel_event.set()  # yyds: 协作式取消
                    result_holder.try_set_terminal(
                        SubagentStatus.TIMED_OUT,
                        error=f"Execution timed out after {self.config.timeout_seconds} seconds",
                    )
                    execution_future.cancel()
            except Exception as e:
                logger.exception(f"[trace={self.trace_id}] Subagent {self.config.name} async execution failed")
                with _background_tasks_lock:
                    task_result = _background_tasks[task_id]
                task_result.try_set_terminal(SubagentStatus.FAILED, error=str(e))

        _scheduler_pool.submit(run_task)
        return task_id


# yyds: ──── 后台任务管理函数 ────
# yyds: task_tool 通过这些函数轮询结果、请求取消、清理任务

MAX_CONCURRENT_SUBAGENTS = 3


def request_cancel_background_task(task_id: str) -> None:
    """yyds: 协作式取消 — 设置 cancel_event，_aexecute 在 astream 迭代边界检查。"""
    with _background_tasks_lock:
        result = _background_tasks.get(task_id)
        if result is not None:
            result.cancel_event.set()
            logger.info("Requested cancellation for background task %s", task_id)


def get_background_task_result(task_id: str) -> SubagentResult | None:
    """yyds: 按 task_id 获取结果 — task_tool 每 5 秒调这个轮询。"""
    with _background_tasks_lock:
        return _background_tasks.get(task_id)


def list_background_tasks() -> list[SubagentResult]:
    """yyds: 列出所有后台任务 — 调试用。"""
    with _background_tasks_lock:
        return list(_background_tasks.values())


def cleanup_background_task(task_id: str) -> None:
    """yyds: 清理已完成的任务 — 防止内存泄漏。只删终态任务。

    为什么只删终态？
      如果删了还在跑的任务，_background_tasks 里就找不到了，
      但 run_task() 线程还在跑，它更新 status 时会 KeyError。
      所以只删 COMPLETED/FAILED/CANCELLED/TIMED_OUT 的。
    """
    with _background_tasks_lock:
        result = _background_tasks.get(task_id)
        if result is None:
            logger.debug("Requested cleanup for unknown background task %s", task_id)
            return

        # Only clean up tasks that are in a terminal state to avoid races with
        # the background executor still updating the task entry.
        if result.status.is_terminal or result.completed_at is not None:
            del _background_tasks[task_id]
            logger.debug("Cleaned up background task: %s", task_id)
        else:
            logger.debug(
                "Skipping cleanup for non-terminal background task %s (status=%s)",
                task_id,
                result.status.value if hasattr(result.status, "value") else result.status,
            )
