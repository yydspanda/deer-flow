"""yyds: 图片查看中间件 — 在 LLM 调用前，将 view_image 工具获取的图片详情（含 base64 数据）注入上下文。

【做什么】当上一轮助手消息中包含 view_image 工具调用，且所有工具调用都已完成（收到了 ToolMessage）时，
   从 state.viewed_images 中提取图片的 base64 数据，构造一个包含图片的多模态 HumanMessage 注入到消息列表中，
   使 LLM 在下一次调用时能"看到"并分析这些图片。
【为什么存在】LLM 本身不能直接访问文件系统中的图片。view_image 工具读取图片并存储到 state 中，
   但工具返回的是文本描述。要让 LLM 真正"看到"图片内容，需要将 base64 编码的图片数据以多模态
   image_url 的形式注入消息。此中间件负责在适当时机完成这个注入。
【在链中的位置】before_model 阶段执行，在模型调用前、工具执行后介入。
【关键设计】
   - 仅在所有 view_image 工具调用都完成后才注入（避免部分图片未就绪）。
   - 使用幂等性检查：如果已经注入过图片详情消息则不再重复注入。
   - 注入的消息格式为多模态 content blocks（text + image_url），支持多种图片格式。
   - 同时支持同步（before_model）和异步（abefore_model）两个入口。
"""

import logging
from typing import override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime

from deerflow.agents.thread_state import ThreadState

logger = logging.getLogger(__name__)


class ViewImageMiddlewareState(ThreadState):
    """yyds: 复用 ThreadState（含 viewed_images reducer 字段）。"""


class ViewImageMiddleware(AgentMiddleware[ViewImageMiddlewareState]):
    """yyds: 图片查看中间件 — 在 LLM 调用前注入 view_image 工具获取的图片 base64 数据。

    执行时机：before_model（模型调用前、工具执行后）
    触发条件：上一轮助手消息包含 view_image 工具调用，且所有调用都已完成
    注入方式：构造多模态 HumanMessage（text + image_url blocks）
    幂等性：检查是否已注入过，防止重复
    """

    state_schema = ViewImageMiddlewareState

    def _get_last_assistant_message(self, messages: list) -> AIMessage | None:
        """yyds: 从消息列表尾部找最后一条 AIMessage。"""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                return msg
        return None

    def _has_view_image_tool(self, message: AIMessage) -> bool:
        """yyds: 检查助手消息是否包含 view_image 工具调用。"""
        if not hasattr(message, "tool_calls") or not message.tool_calls:
            return False

        return any(tool_call.get("name") == "view_image" for tool_call in message.tool_calls)

    def _all_tools_completed(self, messages: list, assistant_msg: AIMessage) -> bool:
        """yyds: 检查助手消息中的所有工具调用是否都收到了 ToolMessage 回复。"""
        if not hasattr(assistant_msg, "tool_calls") or not assistant_msg.tool_calls:
            return False

        # Get all tool call IDs from the assistant message
        tool_call_ids = {tool_call.get("id") for tool_call in assistant_msg.tool_calls if tool_call.get("id")}

        # Find the index of the assistant message
        try:
            assistant_idx = messages.index(assistant_msg)
        except ValueError:
            return False

        # Get all ToolMessages after the assistant message
        completed_tool_ids = set()
        for msg in messages[assistant_idx + 1 :]:
            if isinstance(msg, ToolMessage) and msg.tool_call_id:
                completed_tool_ids.add(msg.tool_call_id)

        # Check if all tool calls have been completed
        return tool_call_ids.issubset(completed_tool_ids)

    def _create_image_details_message(self, state: ViewImageMiddlewareState) -> list[str | dict]:
        """yyds: 构建多模态消息内容 — 文本描述 + image_url（base64 数据）。
        LLM 收到 image_url 后能"看到"并分析图片。
        """
        viewed_images = state.get("viewed_images", {})
        if not viewed_images:
            # Return a properly formatted text block, not a plain string array
            return [{"type": "text", "text": "No images have been viewed."}]

        # Build the message with image information
        content_blocks: list[str | dict] = [{"type": "text", "text": "Here are the images you've viewed:"}]

        for image_path, image_data in viewed_images.items():
            mime_type = image_data.get("mime_type", "unknown")
            base64_data = image_data.get("base64", "")

            # Add text description
            content_blocks.append({"type": "text", "text": f"\n- **{image_path}** ({mime_type})"})

            # Add the actual image data so LLM can "see" it
            if base64_data:
                content_blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{base64_data}"},
                    }
                )

        return content_blocks

    def _should_inject_image_message(self, state: ViewImageMiddlewareState) -> bool:
        """yyds: 判断是否应该注入图片消息 — 四个条件都满足才行：
        1. 有消息
        2. 最后一条 AIMessage 包含 view_image 调用
        3. 所有工具调用都已完成
        4. 还没注入过图片详情消息（幂等性检查）
        """
        messages = state.get("messages", [])
        if not messages:
            return False

        # Get the last assistant message
        last_assistant_msg = self._get_last_assistant_message(messages)
        if not last_assistant_msg:
            return False

        # Check if it has view_image tool calls
        if not self._has_view_image_tool(last_assistant_msg):
            return False

        # Check if all tools have been completed
        if not self._all_tools_completed(messages, last_assistant_msg):
            return False

        # Check if we've already added an image details message
        # Look for a human message after the last assistant message that contains image details
        assistant_idx = messages.index(last_assistant_msg)
        for msg in messages[assistant_idx + 1 :]:
            if isinstance(msg, HumanMessage):
                content_str = str(msg.content)
                if "Here are the images you've viewed" in content_str or "Here are the details of the images you've viewed" in content_str:
                    # Already added, don't add again
                    return False

        return True

    def _inject_image_message(self, state: ViewImageMiddlewareState) -> dict | None:
        """Internal helper to inject image details message.

        Args:
            state: Current state

        Returns:
            State update with additional human message, or None if no update needed
        """
        if not self._should_inject_image_message(state):
            return None

        # Create the image details message with text and image content
        image_content = self._create_image_details_message(state)

        # Create a new human message with mixed content (text + images)
        human_msg = HumanMessage(content=image_content)

        logger.debug("Injecting image details message with images before LLM call")

        # Return state update with the new message
        return {"messages": [human_msg]}

    @override
    def before_model(self, state: ViewImageMiddlewareState, runtime: Runtime) -> dict | None:
        """yyds: 同步版 — 在 LLM 调用前注入图片消息。"""
        return self._inject_image_message(state)

    @override
    async def abefore_model(self, state: ViewImageMiddlewareState, runtime: Runtime) -> dict | None:
        """yyds: 异步版 — 同上逻辑。"""
        return self._inject_image_message(state)
