# yyds: middleware.py — 沙箱生命周期中间件（SandboxMiddleware）
#
# ═══ 这是唯一一个"对称中间件"—— before_agent + after_agent 都有实质逻辑 ═══
# （其他中间件要么只有 before，要么只有 after，要么都是 no-op）
#
# ┌─────────────────────────────────────────────────────────────────────┐
# │ 沙箱生命周期（一次用户消息的完整流程）                              │
# │                                                                     │
# │  用户发消息 → before_agent（acquire 沙箱）→ LLM → 工具执行          │
# │                                              → after_agent（release）│
# │                                                                     │
# │  lazy_init=True（默认）：before_agent 跳过 acquire，               │
# │     由工具函数里的 ensure_sandbox_initialized() 按需创建            │
# │  lazy_init=False：before_agent 立即 acquire                        │
# │                                                                     │
# │  无论哪种模式，after_agent 都会 release                             │
# └─────────────────────────────────────────────────────────────────────┘
#
# 关键依赖：ThreadDataMiddleware 必须在本中间件之前注册，
#           否则 workspace/uploads/outputs 路径没准备好，沙箱目录挂不上
#
# 沙箱 Provider 由 config.yaml 的 sandbox.use 决定（动态加载）：
#   - local → 直接在宿主机执行（开发用）
#   - aio_sandbox → Docker 容器隔离（生产用）
import asyncio
import logging
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from deerflow.agents.thread_state import SandboxState, ThreadDataState
from deerflow.sandbox import get_sandbox_provider

logger = logging.getLogger(__name__)


class SandboxMiddlewareState(AgentState):
    """yyds: 最小状态声明 — 只碰 sandbox 和 thread_data 两个字段

    跟 ThreadDataMiddlewareState 一样的设计原则：
    中间件只声明自己需要读/写的字段，不需要完整的 ThreadState。
    LangGraph 会自动合并多个中间件返回的部分状态。
    """

    sandbox: NotRequired[SandboxState | None]
    thread_data: NotRequired[ThreadDataState | None]


class SandboxMiddleware(AgentMiddleware[SandboxMiddlewareState]):
    """Create a sandbox environment and assign it to an agent.

    Lifecycle Management:
    - With lazy_init=True (default): Sandbox is acquired on first tool call
    - With lazy_init=False: Sandbox is acquired on first agent invocation (before_agent)
    - Sandbox is reused across multiple turns within the same thread
    - Sandbox is NOT released after each agent call to avoid wasteful recreation
    - Cleanup happens at application shutdown via SandboxProvider.shutdown()
    """

    state_schema = SandboxMiddlewareState

    def __init__(self, lazy_init: bool = True):
        """Initialize sandbox middleware.

        Args:
            lazy_init: If True, defer sandbox acquisition until first tool call.
                      If False, acquire sandbox eagerly in before_agent().
                      Default is True for optimal performance.
        """
        super().__init__()
        self._lazy_init = lazy_init

    def _acquire_sandbox(self, thread_id: str) -> str:
        # yyds: acquire 三步曲
        #   1. get_sandbox_provider() → 拿到全局单例 Provider
        #   2. provider.acquire(thread_id) → 创建/获取沙箱，返回 sandbox_id
        #      acquire 内部会检查是否已有该 thread_id 的沙箱（复用机制）
        #   3. 返回 sandbox_id，后续存入 state["sandbox"]
        provider = get_sandbox_provider()
        sandbox_id = provider.acquire(thread_id)
        logger.info(f"Acquiring sandbox {sandbox_id}")
        return sandbox_id

    async def _acquire_sandbox_async(self, thread_id: str) -> str:
        provider = get_sandbox_provider()
        sandbox_id = await provider.acquire_async(thread_id)
        logger.info(f"Acquiring sandbox {sandbox_id}")
        return sandbox_id

    async def _release_sandbox_async(self, sandbox_id: str) -> None:
        await asyncio.to_thread(get_sandbox_provider().release, sandbox_id)

    @override
    def before_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        # yyds: before_agent — 沙箱获取（acquire）
        #
        # 两种模式：
        #   lazy_init=True（默认）→ 直接 return None，什么都不做
        #     沙箱在第一次调用工具时由 ensure_sandbox_initialized() 创建
        #     好处：如果用户只聊天不写代码，就不浪费 Docker 容器资源
        #
        #   lazy_init=False → 立即 acquire
        #     用于测试或需要确保沙箱在 LLM 调用前就准备好的场景
        #
        # 为什么需要 lazy_init？
        #   Docker 容器的 acquire 很慢（几秒），大部分对话不需要沙箱。
        #   延迟到工具调用时才创建，用户体验好很多（首条消息秒回）。

        # Skip acquisition if lazy_init is enabled
        if self._lazy_init:
            return super().before_agent(state, runtime)

        # Eager initialization (original behavior)
        if "sandbox" not in state or state["sandbox"] is None:
            thread_id = (runtime.context or {}).get("thread_id")
            if thread_id is None:
                return super().before_agent(state, runtime)
            sandbox_id = self._acquire_sandbox(thread_id)
            logger.info(f"Assigned sandbox {sandbox_id} to thread {thread_id}")
            # yyds: 返回部分状态 → LangGraph 自动合并到完整 state 中
            #   sandbox_id 是后续所有工具函数找到沙箱的关键
            return {"sandbox": {"sandbox_id": sandbox_id}}
        return super().before_agent(state, runtime)

    @override
    async def abefore_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        # Skip acquisition if lazy_init is enabled
        if self._lazy_init:
            return await super().abefore_agent(state, runtime)

        # Eager initialization (original behavior), but use the async provider
        # hook so blocking sandbox startup/polling runs outside the event loop.
        if "sandbox" not in state or state["sandbox"] is None:
            thread_id = (runtime.context or {}).get("thread_id")
            if thread_id is None:
                return await super().abefore_agent(state, runtime)
            sandbox_id = await self._acquire_sandbox_async(thread_id)
            logger.info(f"Assigned sandbox {sandbox_id} to thread {thread_id}")
            return {"sandbox": {"sandbox_id": sandbox_id}}
        return await super().abefore_agent(state, runtime)

    @override
    def after_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        # yyds: after_agent — 沙箱释放（release）
        #
        # 无论 lazy_init 是 True 还是 False，after_agent 都执行。
        # 这是"对称中间件"的核心：before 负责 acquire，after 负责 release。
        #
        # 沙箱可能在两个地方：
        #   1. state["sandbox"]["sandbox_id"] — 正常路径（before 或工具函数设置的）
        #   2. runtime.context["sandbox_id"] — 兜底路径（ensure_sandbox_initialized 设置的）
        #
        # release 做什么？
        #   - local 模式：什么都不做（宿主机不需要清理）
        #   - Docker 模式：停止并删除容器，回收资源
        #
        # return None 表示不修改 state（释放沙箱不影响对话状态）
        sandbox = state.get("sandbox")
        if sandbox is not None:
            sandbox_id = sandbox["sandbox_id"]
            logger.info(f"Releasing sandbox {sandbox_id}")
            get_sandbox_provider().release(sandbox_id)
            return None

        if (runtime.context or {}).get("sandbox_id") is not None:
            sandbox_id = runtime.context.get("sandbox_id")
            logger.info(f"Releasing sandbox {sandbox_id} from context")
            get_sandbox_provider().release(sandbox_id)
            return None

        # No sandbox to release
        return super().after_agent(state, runtime)

    @override
    async def aafter_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        sandbox = state.get("sandbox")
        if sandbox is not None:
            sandbox_id = sandbox["sandbox_id"]
            logger.info(f"Releasing sandbox {sandbox_id}")
            await self._release_sandbox_async(sandbox_id)
            return None

        if (runtime.context or {}).get("sandbox_id") is not None:
            sandbox_id = runtime.context.get("sandbox_id")
            logger.info(f"Releasing sandbox {sandbox_id} from context")
            await self._release_sandbox_async(sandbox_id)
            return None

        # No sandbox to release
        return await super().aafter_agent(state, runtime)
