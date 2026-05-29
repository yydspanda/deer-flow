"""yyds: 记忆中间件 — Agent 干完活后，把对话内容丢给记忆系统，让它记住用户是谁。

【大白话讲清楚】
  你跟 AI 聊了很多轮，说了"我是做安全的"、"我主要用 Python"。
  你希望 AI 下次对话还记得这些。

  这个中间件就是干这个的：Agent 执行完毕后，把本轮对话过滤一下
  （只留用户消息和最终 AI 回复，去掉工具调用等中间步骤），
  然后扔进一个队列。队列攒够一批后，调 LLM 从对话里提取关键信息
  （用户偏好、事实等），存到 memory.json。下次对话时注入回去。

  整个链路：
    本中间件(after_agent) → MemoryQueue(30s 去抖动) → MemoryUpdater(LLM 提取) → memory.json

【具体例子】
  第 1 轮对话：
    用户："我是做 AI 安全的，主要用 Python"
    AI："了解了！你做什么类型的安全研究？"
    → 本中间件过滤后：[用户消息, AI回复]
    → 检测信号：无纠正，无强化
    → 放入队列 → 30s 后 LLM 提取 → memory.json 多了一条：
      {fact: "用户是 AI 安全工程师，主要用 Python", confidence: 0.9}

  第 2 轮对话：
    用户："不对，我其实也用 Go"
    AI："好的，了解了！"
    → 检测信号：correction_detected=True（"不对"触发了纠正模式）
    → 放入队列 → LLM 会以更高置信度更新记忆

  第 3 轮对话（上传文件）：
    用户：（只上传了一个 PDF，没说话）
    AI："我已经读取了文件..."
    → 过滤后：空列表（纯上传消息被过滤掉了）
    → 不入队（不需要记住"用户上传了一个文件"这种临时操作）

【加载条件】
  所有模式都加载（Flash/Thinking/Pro/Ultra），但只有 config.yaml 里 memory.enabled=True 时才工作。
  在中间件链里排第 9 号，after_agent 阶段执行（Agent 全部执行完毕后）。

---

Memory middleware that queues conversation content for async memory updates after agent execution.
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
    """yyds: Memory 中间件的状态扩展 — 直接用基类，没加额外字段。"""

    pass


class MemoryMiddleware(AgentMiddleware[MemoryMiddlewareState]):
    """yyds: 记忆中间件 — Agent 干完活后，把对话过滤一下丢给记忆队列。

    整个流程一句话：after_agent → 过滤消息 → 检测信号 → 入队 → 30s 后 LLM 提取 → memory.json

    决策树：
      Agent 执行完了，要不要把对话交给记忆系统？
      ├─ memory 没开（config.enabled=False）→ 跳过
      ├─ 没有 thread_id → 跳过（不知道存哪）
      ├─ 没有消息 → 跳过
      ├─ 过滤后没有用户消息 or 没有 AI 回复 → 跳过
      └─ 有有效对话 → 过滤 + 检测信号 + 入队

    入队之后发生什么（不在本文件，在 queue.py + updater.py）：
      queue.add() → 重置 30s 定时器
      → 30s 内没新消息 → 取出所有队列项
      → 逐个调 MemoryUpdater.update_memory()
      → LLM 从对话中提取 facts → 存到 memory.json
      → 下次对话时 DynamicContextMiddleware 会把记忆注入到上下文里

    为什么要 30s 去抖动：
      用户可能连续发 3 条消息（"帮我搜索" → "不对换一个" → "继续"），
      如果每条都触发 LLM 提取就太贵了。攒 30s 一起处理，省钱。
    """

    state_schema = MemoryMiddlewareState

    def __init__(self, agent_name: str | None = None, *, memory_config: "MemoryConfig | None" = None):
        """yyds: 初始化。

        agent_name：多 Agent 场景下按名字隔离记忆（比如 "research-agent" 和 "code-agent" 各存各的）。
        memory_config：支持注入配置（测试用），默认从全局配置读。
        """
        super().__init__()
        self._agent_name = agent_name
        self._memory_config = memory_config

    @override
    def after_agent(self, state: MemoryMiddlewareState, runtime: Runtime) -> dict | None:
        """yyds: 主入口 — Agent 执行完毕后，过滤对话内容并放入记忆队列。

        例子：
          对话历史有 20 条消息（用户消息、AI 回复、工具调用、工具结果...）
          过滤后只剩 4 条：
            [HumanMessage("我是做安全的"), AIMessage("了解了"), HumanMessage("帮搜 LangGraph"), AIMessage("搜到了")]
          检测信号：用户说了"不对" → correction_detected=True
          入队：queue.add(thread_id="t1", messages=过滤后消息, correction_detected=True)
          → 30s 后 LLM 提取 → memory.json 更新

        yyds 执行顺序：
          ① 检查 memory 是否启用 → 没开就跳过
          ② 取 thread_id → 没有 thread_id 就跳过（不知道存哪）
          ③ 取消息 → 没有消息就跳过
          ④ 过滤消息：只留用户消息 + 最终 AI 回复（去掉工具调用、工具结果、上传通知等中间步骤）
          ⑤ 检查过滤结果：至少要有一条用户消息和一条 AI 回复
          ⑥ 检测信号：用户有没有说"不对"（纠正）或"完全正确"（强化）
          ⑦ 捕获 user_id：必须在入队时捕获，因为 30s 后 Timer 线程里 ContextVar 就失效了
          ⑧ 入队：queue.add()，返回 None（不修改 state）
        """
        # yyds: ① memory 没开 → 跳过
        config = self._memory_config or get_memory_config()
        if not config.enabled:
            return None

        # yyds: ② 取 thread_id（先从 runtime.context，取不到再从 LangGraph config 取）
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id is None:
            config_data = get_config()
            thread_id = config_data.get("configurable", {}).get("thread_id")
        if not thread_id:
            logger.debug("No thread_id in context, skipping memory update")
            return None

        # yyds: ③ 取消息 → 没有就跳过
        messages = state.get("messages", [])
        if not messages:
            logger.debug("No messages in state, skipping memory update")
            return None

        # yyds: ④ 过滤消息
        #   只留：用户消息（HumanMessage）和最终 AI 回复（AIMessage，没有 tool_calls 的）
        #   去掉：工具调用（AIMessage 有 tool_calls）、工具结果（ToolMessage）、纯上传消息
        #   为什么要去掉：工具调用是中间步骤，给 LLM 提取记忆只会浪费 token
        filtered_messages = filter_messages_for_memory(messages)

        # yyds: ⑤ 至少要有一条用户消息和一条 AI 回复
        #   如果用户只是上传了个文件没说话，过滤后可能只剩 AI 回复没有用户消息，这种情况不入队
        user_messages = [m for m in filtered_messages if getattr(m, "type", None) == "human"]
        assistant_messages = [m for m in filtered_messages if getattr(m, "type", None) == "ai"]

        if not user_messages or not assistant_messages:
            return None

        # yyds: ⑥ 检测信号
        #   correction：用户说了"不对"、"你理解错了"、"try again"等 → LLM 会以更高置信度覆盖旧记忆
        #   reinforcement：用户说了"完全正确"、"exactly"、"keep doing that"等 → LLM 会确认偏好
        #   只看最近 6 条消息（更早的不算），且 correction 优先（同一轮只算一种）
        correction_detected = detect_correction(filtered_messages)
        reinforcement_detected = not correction_detected and detect_reinforcement(filtered_messages)

        # TODO yyds: ⑦ 在入队时捕获 user_id  这里需要反复看！
        #   为什么：queue.add() 会启动 30s 的 threading.Timer，Timer 回调在另一个线程执行。
        #   Python 的 ContextVar 只在当前协程/线程有效，跨线程就丢了。
        #   所以必须在这里（请求上下文还活着的时候）把 user_id 存下来，塞进 ConversationContext。
        user_id = get_effective_user_id()

        # yyds: ⑧ 入队。queue.add() 会重置 30s 定时器，到期后批量处理。
        #   返回 None — 不修改 state，对 Agent 的执行结果没有任何影响。
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
