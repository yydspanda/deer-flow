"""yyds: 图片查看中间件 — Agent 调用 view_image 工具后，把图片"喂"给 LLM 看。

【做什么】Agent 调用 view_image 工具读取了图片后，这个中间件把图片的 base64 数据
   构造成一条多模态 HumanMessage（文本 + 图片），注入到对话里，让 LLM 能"看到"图片。
【为什么存在】view_image 工具读取图片后只返回一句"Successfully read image"，
   LLM 看到这句话根本不知道图片长什么样。这个中间件在 LLM 下一次调用前，
   把图片的 base64 数据以 image_url 的形式塞进对话，LLM 才能真正"看到"并分析图片。
【在链中的位置】before_model 阶段（LLM 调用前、工具执行后）。
   时机很重要：必须在 view_image 工具执行完之后（图片数据已经在 state 里了），
   且在 LLM 下一次调用之前（把图片塞进对话，LLM 才能看到）。
【关键设计】
   - 触发条件：上一条 AI 消息包含 view_image 工具调用 + 所有工具调用都已完成
   - 幂等性：检查对话里是否已经有 "Here are the images you've viewed" 的消息，有则跳过
   - 多模态格式：content blocks（text + image_url），OpenAI/Anthropic 都支持
   - 仅给视觉模型用（supports_vision=True 的模型才加这个中间件）

两个文件配合：
  view_image_tool.py — 读取图片文件 → base64 编码 → 存到 state["viewed_images"]
  view_image_middleware.py（本文件）— 从 state 取图片 → 构造多模态消息 → 注入对话

数据流：
  用户："帮我看看 logo.png 长什么样"
    → Agent 调用 view_image("logo.png")
    → view_image_tool 读取文件 → base64 → 存入 state["viewed_images"]
    → 返回 ToolMessage "Successfully read image"
    ↓
  本中间件在 before_model 触发：
    → 检测到上一条 AI 消息有 view_image 调用 + 工具已完成
    → 从 state["viewed_images"] 取出 base64 数据
    → 构造 HumanMessage([
         {"type": "text", "text": "Here are the images you've viewed:"},
         {"type": "text", "text": "- **logo.png** (image/png)"},
         {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBOR..."}},
       ])
    → 注入到对话 → LLM 下一次调用时能"看到"图片
"""

import logging
from typing import override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime

from deerflow.agents.thread_state import ThreadState

logger = logging.getLogger(__name__)

_INJECT_MARKER = "Here are the images you've viewed"  # yyds: 幂等性标记，检测是否已注入过


class ViewImageMiddlewareState(ThreadState):
    """yyds: 复用 ThreadState（含 viewed_images reducer 字段）。

    viewed_images 是一个 dict：{图片路径: {"base64": "...", "mime_type": "image/png"}}
    由 view_image_tool 写入，由本中间件读取。
    """


class ViewImageMiddleware(AgentMiddleware[ViewImageMiddlewareState]):
    """yyds: 图片查看中间件 — 把 view_image 工具读取的图片喂给 LLM 看。

    执行时机：before_model（LLM 调用前、工具执行后）。
    触发条件：上一条 AI 消息包含 view_image 调用 + 所有工具都已完成 + 还没注入过。

    为什么不在 after_tool 阶段注入？
      因为可能有多个工具调用（view_image + 其他工具），需要等所有工具都执行完，
      再一起注入图片。如果在 after_tool 阶段，可能图片工具完成了但其他工具还没完成。

    数据流：
      before_model(state)
        └─ _should_inject_image_message(state)
             ├─ 最后一条 AI 消息有 view_image 调用？
             ├─ 所有工具调用都收到 ToolMessage 了？
             └─ 还没注入过图片消息？（幂等性）
                  → 全部满足 → _inject_image_message()
                       └─ _create_image_details_message()
                            → 从 state["viewed_images"] 取 base64 数据
                            → 构造 [text block, image_url block, ...]
                            → 包装成 HumanMessage
                            → 返回 {"messages": [HumanMessage]}
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
        """yyds: 检查助手消息中的所有工具调用是否都收到了 ToolMessage 回复。

        为什么需要这个检查？
          Agent 可能同时调用 view_image + search 两个工具。
          如果 view_image 先完成但 search 还没完成，此时注入图片，
          后面 search 完成后 LLM 会再调一次，可能导致图片被注入两次。
          等所有工具都完成后才注入，保证只注入一次。

        yyds 执行顺序：
          ① 收集 assistant_msg 中所有 tool_call 的 id
          ② 扫描 assistant_msg 之后的 ToolMessage，收集已完成的 id
          ③ 所有 tool_call_id 都在已完成集合里 → True
        """
        if not hasattr(assistant_msg, "tool_calls") or not assistant_msg.tool_calls:
            return False

        # yyds: ① 收集所有 tool_call_id
        tool_call_ids = {tool_call.get("id") for tool_call in assistant_msg.tool_calls if tool_call.get("id")}

        # yyds: ② 扫描 assistant_msg 之后的 ToolMessage
        try:
            assistant_idx = messages.index(assistant_msg)
        except ValueError:
            return False

        completed_tool_ids = set()
        for msg in messages[assistant_idx + 1 :]:
            if isinstance(msg, ToolMessage) and msg.tool_call_id:
                completed_tool_ids.add(msg.tool_call_id)

        # yyds: ③ 检查是否全部完成
        return tool_call_ids.issubset(completed_tool_ids)

    def _create_image_details_message(self, state: ViewImageMiddlewareState) -> list[str | dict]:
        """yyds: 构造多模态消息内容 — 文本描述 + image_url（base64 数据）。

        yyds 执行顺序：
          ① 从 state["viewed_images"] 取出所有已查看的图片
          ② 无图片 → 返回 "No images have been viewed."
          ③ 有图片 → 遍历，为每张图片构造：
             - text block："- **logo.png** (image/png)"
             - image_url block：{"url": "data:image/png;base64,iVBOR..."}

        LLM 收到 image_url block 后能"看到"图片内容并分析。
        data URI 格式（data:mime;base64,xxx）是 OpenAI/Anthropic 都支持的标准格式。
        """
        viewed_images = state.get("viewed_images", {})
        if not viewed_images:
            return [{"type": "text", "text": "No images have been viewed."}]

        # yyds: ③ 遍历图片，构造 content blocks
        content_blocks: list[str | dict] = [{"type": "text", "text": f"{_INJECT_MARKER}:"}]

        for image_path, image_data in viewed_images.items():
            mime_type = image_data.get("mime_type", "unknown")
            base64_data = image_data.get("base64", "")

            # yyds: 文本描述
            content_blocks.append({"type": "text", "text": f"\n- **{image_path}** ({mime_type})"})

            # yyds: 图片数据（LLM 看到这个 block 就能分析图片）
            if base64_data:
                content_blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{base64_data}"},
                    }
                )

        return content_blocks

    def _should_inject_image_message(self, state: ViewImageMiddlewareState) -> bool:
        """yyds: 判断是否应该注入图片消息 — 四个条件全满足才注入。

        yyds 执行顺序：
          ① 消息列表为空 → 不注入
          ② 最后一条 AIMessage 不含 view_image 调用 → 不注入
          ③ 不是所有工具都完成了 → 不注入
          ④ 已经注入过图片消息（幂等性）→ 不注入
          ⑤ 全部通过 → 返回 True

        幂等性检查怎么做的？
          在 assistant_msg 之后的所有 HumanMessage 里搜索是否包含
          "Here are the images you've viewed" 文本。
          有 → 说明之前已经注入过了，不再重复注入。
        """
        # yyds: ① 检查消息列表
        messages = state.get("messages", [])
        if not messages:
            return False

        # yyds: ② 最后一条 AI 消息有 view_image 吗？
        last_assistant_msg = self._get_last_assistant_message(messages)
        if not last_assistant_msg:
            return False

        if not self._has_view_image_tool(last_assistant_msg):
            return False

        # yyds: ③ 所有工具都完成了吗？
        if not self._all_tools_completed(messages, last_assistant_msg):
            return False

        # yyds: ④ 幂等性 — 是否已经注入过？
        assistant_idx = messages.index(last_assistant_msg)
        for msg in messages[assistant_idx + 1 :]:
            if isinstance(msg, HumanMessage):
                content_str = str(msg.content)
                if _INJECT_MARKER in content_str or "Here are the details of the images you've viewed" in content_str:
                    return False

        # yyds: ⑤ 全部通过
        return True

    def _inject_image_message(self, state: ViewImageMiddlewareState) -> dict | None:
        """yyds: 注入图片消息 — 构造多模态 HumanMessage 写入 state。

        yyds 执行顺序：
          ① _should_inject_image_message() 检查是否需要注入
          ② _create_image_details_message() 构造多模态内容
          ③ 包装成 HumanMessage 写入 state["messages"]
        """
        # yyds: ① 检查
        if not self._should_inject_image_message(state):
            return None

        # yyds: ② 构造内容
        image_content = self._create_image_details_message(state)

        # yyds: ③ 写入 state
        human_msg = HumanMessage(content=image_content)
        logger.debug("Injecting image details message with images before LLM call")
        return {"messages": [human_msg]}

    @override
    def before_model(self, state: ViewImageMiddlewareState, runtime: Runtime) -> dict | None:
        """yyds: 同步版 — 在 LLM 调用前注入图片消息。"""
        return self._inject_image_message(state)

    @override
    async def abefore_model(self, state: ViewImageMiddlewareState, runtime: Runtime) -> dict | None:
        """yyds: 异步版 — 逻辑和同步版完全相同（_inject_image_message 内部无异步操作）。"""
        return self._inject_image_message(state)
