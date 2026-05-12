"""yyds: 记忆中间件 — 在每次对话交互后，将对话内容异步持久化到记忆系统。

【做什么】Agent 执行完成后，将本轮对话中的用户输入和最终助手回复过滤出来，放入记忆队列进行异步更新。
【为什么存在】为了实现跨会话的长期记忆。Agent 需要记住用户偏好、历史决策等上下文信息，此中间件负责
   将对话内容提交给记忆系统进行 LLM 摘要和存储，与 summarization_middleware（摘要中间件）配合工作。
【在链中的位置】after_agent 阶段执行，即整个 Agent 执行完毕后运行。
【关键设计】
   - 只保留用户消息（human）和最终助手回复（ai），过滤掉工具调用等中间步骤。
   - 使用队列（MemoryQueue）进行异步批量更新，内置防抖（debounce）机制避免频繁更新。
   - 支持检测"纠正"（correction）和"强化"（reinforcement）语义，帮助记忆系统更准确地更新。
   - agent_name 参数支持按 Agent 名称隔离记忆（多 Agent 场景）。
   - 在入队时捕获 user_id，因为后续 Timer 线程中 ContextVar 不会传播。
"""

import logging
from typing import TYPE_CHECKING, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.config import get_config
from langgraph.runtime import Runtime

from deerflow.agents.memory.message_processing import detect_correction, detect_reinforcement, filter_messages_for_memory
from deerflow.agents.memory.queue import get_memory_queue
from deerflow.config.memory_config import get_memory_config
from deerflow.runtime.user_context import get_effective_user_id

if TYPE_CHECKING:
    from deerflow.config.memory_config import MemoryConfig

logger = logging.getLogger(__name__)


class MemoryMiddlewareState(AgentState):
    """yyds: Memory 中间件的状态扩展（无额外字段，只是为了类型兼容）。"""

    pass


class MemoryMiddleware(AgentMiddleware[MemoryMiddlewareState]):
    """yyds: 记忆中间件 — Agent 执行完毕后，将对话内容异步入队到记忆系统。

    执行时机：after_agent（整个 Agent 执行完毕后）
    做的事：过滤出用户消息+最终助手回复 → 检测纠正/强化语义 → 放入 MemoryQueue
    MemoryQueue 有防抖机制，会批量调用 LLM 提取关键信息 → 持久化到 memory.json
    """

    state_schema = MemoryMiddlewareState

    def __init__(self, agent_name: str | None = None, *, memory_config: "MemoryConfig | None" = None):
        """yyds: 初始化 — agent_name 支持按 Agent 名称隔离记忆，memory_config 支持注入配置。"""
        super().__init__()
        self._agent_name = agent_name
        self._memory_config = memory_config

    @override
    def after_agent(self, state: MemoryMiddlewareState, runtime: Runtime) -> dict | None:
        """yyds: 主入口 — Agent 执行完毕后，过滤对话内容并放入记忆队列。

        执行流程：
          1. 检查 memory 是否启用
          2. 从 runtime.context 取 thread_id
          3. 过滤消息（只保留用户消息 + 最终助手回复）
          4. 检测纠正/强化语义
          5. 在入队时捕获 user_id（Timer 线程中 ContextVar 不传播）
          6. 放入 MemoryQueue（异步批量更新）
        """
        config = self._memory_config or get_memory_config()
        if not config.enabled:
            return None

        # Get thread ID from runtime context first, then fall back to LangGraph's configurable metadata
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id is None:
            config_data = get_config()
            thread_id = config_data.get("configurable", {}).get("thread_id")
        if not thread_id:
            logger.debug("No thread_id in context, skipping memory update")
            return None

        # Get messages from state
        messages = state.get("messages", [])
        if not messages:
            logger.debug("No messages in state, skipping memory update")
            return None

        # Filter to only keep user inputs and final assistant responses
        filtered_messages = filter_messages_for_memory(messages)

        # Only queue if there's meaningful conversation
        # At minimum need one user message and one assistant response
        user_messages = [m for m in filtered_messages if getattr(m, "type", None) == "human"]
        assistant_messages = [m for m in filtered_messages if getattr(m, "type", None) == "ai"]

        if not user_messages or not assistant_messages:
            return None

        # Queue the filtered conversation for memory update
        correction_detected = detect_correction(filtered_messages)
        reinforcement_detected = not correction_detected and detect_reinforcement(filtered_messages)
        # Capture user_id at enqueue time while the request context is still alive.
        # threading.Timer fires on a different thread where ContextVar values are not
        # propagated, so we must store user_id explicitly in ConversationContext.
        user_id = get_effective_user_id()
        queue = get_memory_queue()
        queue.add(
            thread_id=thread_id,
            messages=filtered_messages,
            agent_name=self._agent_name,
            user_id=user_id,
            correction_detected=correction_detected,
            reinforcement_detected=reinforcement_detected,
        )

        return None
