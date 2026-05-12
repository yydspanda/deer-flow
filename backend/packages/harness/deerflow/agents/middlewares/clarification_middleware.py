"""Middleware for intercepting clarification requests and presenting them to the user.

yyds: 确认中间件——Agent 的"提问机制"。

为什么这个中间件必须放在最后？
  因为它拦截的是 ask_clarification 工具调用。
  LLM 觉得信息不够时会调用 ask_clarification()，这个中间件：
  1. 拦截这个工具调用（不让它真的执行）
  2. 格式化成用户友好的消息（带图标、选项列表）
  3. 返回 Command(goto=END) 中断执行，等用户回复

  如果它不在最后，其他中间件可能会先处理这个工具调用，导致提问被吞掉。

工作流程：
  用户："部署应用" → LLM 想：缺环境信息 → 调用 ask_clarification
  → 这个中间件拦截 → 返回 Command(goto=END) + 格式化消息
  → 前端展示 "❓ 部署到哪个环境？1. 开发 2. 测试 3. 生产"
  → 等用户选择后继续执行

这是 prompt.py 里 <clarification_system> 段落的对应实现：
  prompt 告诉 LLM "什么时候该问、怎么问"，这个中间件负责"拦截并中断"。
"""

import json
import logging
from collections.abc import Callable
from hashlib import sha256
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.graph import END
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)


class ClarificationMiddlewareState(AgentState):
    """yyds: Clarification 中间件状态（无额外字段，类型兼容）。"""


class ClarificationMiddleware(AgentMiddleware[ClarificationMiddlewareState]):
    """yyds: 确认中间件 — 必须放在最后！拦截 ask_clarification 工具调用并中断执行。

    执行时机：wrap_tool_call（包裹工具调用）
    核心机制：
      - 工具名 == "ask_clarification" → 拦截，格式化问题，返回 Command(goto=END)
      - 工具名 != "ask_clarification" → 正常执行，透传给下一个中间件
    Command(goto=END) 是 LangGraph 的中断机制，停止整个 StateGraph，等用户回复后继续。
    """

    state_schema = ClarificationMiddlewareState

    def _stable_message_id(self, tool_call_id: str, formatted_message: str) -> str:
        """yyds: 生成确定性的消息 ID — 确保重试时替换（不是追加）同一条消息。"""
        if tool_call_id:
            return f"clarification:{tool_call_id}"
        digest = sha256(formatted_message.encode("utf-8")).hexdigest()[:16]
        return f"clarification:{digest}"

    def _is_chinese(self, text: str) -> bool:
        """yyds: 检测文本是否包含中文字符（Unicode 范围 \u4e00-\u9fff）。"""
        return any("\u4e00" <= char <= "\u9fff" for char in text)

    def _format_clarification_message(self, args: dict) -> str:
        """Format the clarification arguments into a user-friendly message.

        yyds: 把 LLM 的工具调用参数（question, clarification_type, options）
              格式化成用户看得懂的消息。5 种类型有不同的图标：
                missing_info       → ❓ 缺少信息
                ambiguous_requirement → 🤔 需求模糊
                approach_choice    → 🔀 方案选择
                risk_confirmation  → ⚠️ 风险确认
                suggestion         → 💡 建议
        """
        """Format the clarification arguments into a user-friendly message.

        Args:
            args: The tool call arguments containing clarification details

        Returns:
            Formatted message string
        """
        question = args.get("question", "")
        clarification_type = args.get("clarification_type", "missing_info")
        context = args.get("context")
        options = args.get("options", [])

        # Some models (e.g. Qwen3-Max) serialize array parameters as JSON strings
        # instead of native arrays. Deserialize and normalize so `options`
        # is always a list for the rendering logic below.
        if isinstance(options, str):
            try:
                options = json.loads(options)
            except (json.JSONDecodeError, TypeError):
                options = [options]

        if options is None:
            options = []
        elif not isinstance(options, list):
            options = [options]

        # Type-specific icons
        type_icons = {
            "missing_info": "❓",
            "ambiguous_requirement": "🤔",
            "approach_choice": "🔀",
            "risk_confirmation": "⚠️",
            "suggestion": "💡",
        }

        icon = type_icons.get(clarification_type, "❓")

        # Build the message naturally
        message_parts = []

        # Add icon and question together for a more natural flow
        if context:
            # If there's context, present it first as background
            message_parts.append(f"{icon} {context}")
            message_parts.append(f"\n{question}")
        else:
            # Just the question with icon
            message_parts.append(f"{icon} {question}")

        # Add options in a cleaner format
        if options and len(options) > 0:
            message_parts.append("")  # blank line for spacing
            for i, option in enumerate(options, 1):
                message_parts.append(f"  {i}. {option}")

        return "\n".join(message_parts)

    def _handle_clarification(self, request: ToolCallRequest) -> Command:
        """Handle clarification request and return command to interrupt execution.

        yyds: 关键！返回 Command(goto=END) 中断 Agent 执行。
              这是 LangGraph 的中断机制：返回 Command(goto=END) 会让
              整个 StateGraph 停下来，把控制权交还给前端。
              用户回答后，前端发新请求，Agent 从断点继续。
        """
        """Handle clarification request and return command to interrupt execution.

        Args:
            request: Tool call request

        Returns:
            Command that interrupts execution with the formatted clarification message
        """
        # Extract clarification arguments
        args = request.tool_call.get("args", {})
        question = args.get("question", "")

        logger.info("Intercepted clarification request")
        logger.debug("Clarification question: %s", question)

        # Format the clarification message
        formatted_message = self._format_clarification_message(args)

        # Get the tool call ID
        tool_call_id = request.tool_call.get("id", "")

        # Create a ToolMessage with the formatted question
        # This will be added to the message history
        tool_message = ToolMessage(
            id=self._stable_message_id(tool_call_id, formatted_message),
            content=formatted_message,
            tool_call_id=tool_call_id,
            name="ask_clarification",
        )

        # Return a Command that:
        # 1. Adds the formatted tool message
        # 2. Interrupts execution by going to __end__
        # Note: We don't add an extra AIMessage here - the frontend will detect
        # and display ask_clarification tool messages directly
        return Command(
            update={"messages": [tool_message]},
            goto=END,
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """yyds: 同步版 — 拦截 ask_clarification，其他工具透传。"""
        # Check if this is an ask_clarification tool call
        if request.tool_call.get("name") != "ask_clarification":
            # Not a clarification call, execute normally
            return handler(request)

        return self._handle_clarification(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """yyds: 异步版 — 同上逻辑。"""
        # Check if this is an ask_clarification tool call
        if request.tool_call.get("name") != "ask_clarification":
            # Not a clarification call, execute normally
            return await handler(request)

        return self._handle_clarification(request)
