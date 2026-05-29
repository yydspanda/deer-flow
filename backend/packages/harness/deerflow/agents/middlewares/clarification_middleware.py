"""yyds: 确认中间件 — Agent 不确定时向用户提问，然后停下来等回复。

【做什么】Agent 遇到不确定的事情（信息不够、有风险、要用户选方案），
   会调一个叫 ask_clarification 的"提问工具"。这个中间件拦截这个工具调用，
   把问题格式化成用户看得懂的消息，然后让 Agent 停下来等用户回复。
【为什么存在】用户说"部署应用"，Agent 不知道部署到哪个环境。
   与其瞎猜部署到生产环境出事故，不如先问用户。
   这个中间件就是"提问+等待"的机制。
【在链中的位置】wrap_tool_call 阶段，中间件链**最后一个**。
   必须放最后：中间件的 wrap 是洋葱模型（最后注册 = 最外层 = 最先执行）。
   放最后保证它是第一个处理工具调用的，其他中间件不会先吞掉提问。
【关键设计】
   - 只拦截工具名=="ask_clarification"的调用，其他工具正常透传
   - 5 种提问类型各有图标：❓缺少信息 🤔需求模糊 🔀方案选择 ⚠️风险确认 💡建议
   - Command(goto=END)：LangGraph 的中断机制，让整个 Agent 停下来
   - 确定性 ID：用 "clarification:" + tool_call_id 生成固定 ID，
     重试时 LangGraph 替换旧消息（不是追加重复的）
   - ask_clarification 工具本身只返回占位字符串，真正的拦截在这里

实际效果：
  用户："帮我部署一下"
    ↓
  Agent 想了想：不确定部署到哪，先问一下
    ↓
  Agent 调用 ask_clarification(question="部署到哪个环境？", options=["开发","测试","生产"])
    ↓
  这个中间件拦截 → 格式化成：
    ❓ 部署到哪个环境？
      1. 开发
      2. 测试
      3. 生产
    ↓
  返回 Command(goto=END) → Agent 停下来 → 前端展示问题
    ↓
  用户选择"生产" → 发新消息 → Agent 从断点继续执行

两个文件配合：
  clarification_tool.py — 定义工具（给 LLM 看的 schema，告诉它"你可以调这个工具提问"）
  clarification_middleware.py（本文件）— 拦截工具调用（真正的逻辑）
  prompt.py <clarification_system> — 告诉 LLM "什么时候该问、怎么问"
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

_TYPE_ICONS = {  # yyds: 5 种提问类型的图标映射
    "missing_info": "❓",  # 缺少信息
    "ambiguous_requirement": "🤔",  # 需求模糊
    "approach_choice": "🔀",  # 方案选择
    "risk_confirmation": "⚠️",  # 风险确认
    "suggestion": "💡",  # 建议
}


class ClarificationMiddlewareState(AgentState):
    """yyds: Clarification 中间件状态（无额外字段，类型兼容用）。"""


class ClarificationMiddleware(AgentMiddleware[ClarificationMiddlewareState]):
    """yyds: 确认中间件 — 拦截 ask_clarification 工具调用，中断执行等用户回复。

    执行时机：wrap_tool_call（包裹工具调用），中间件链最后一个。
    核心机制：
      工具名 == "ask_clarification" → 拦截，格式化问题，返回 Command(goto=END)
      工具名 != "ask_clarification" → 正常执行，透传给下一个中间件

    为什么必须放最后？
      如果其他中间件先处理了这个工具调用（比如 ToolErrorHandling 捕获了异常），
      提问就被吞掉了，用户永远看不到 LLM 想问什么。放最后保证它是第一个
      处理 wrap_tool_call 的中间件（中间件的 wrap 是洋葱模型：最外层先执行）。

    数据流：
      wrap_tool_call(request)
        ├─ name ≠ "ask_clarification" → handler(request)（正常执行工具）
        └─ name == "ask_clarification" → _handle_clarification(request)
              ├─ _format_clarification_message(args) → "❓ 部署到哪个环境？\n  1. 开发..."
              ├─ _stable_message_id() → "clarification:toolu_abc123"
              ├─ 构建 ToolMessage（格式化消息 + 确定性 ID）
              └─ 返回 Command(update={messages: [ToolMessage]}, goto=END)
                    → LangGraph 停下来，前端展示问题
                    → 用户回复 → 新请求 → Agent 继续

    Command(goto=END) 是什么？
      LangGraph 的控制流机制。返回 Command(goto=END) 会让 StateGraph
      跳转到 __end__ 节点（终止节点），停止当前执行。
      前端收到 ToolMessage 后展示问题，等用户回复。
      用户回复后前端发新请求，Agent 从断点继续（因为历史里有 ToolMessage）。
    """

    state_schema = ClarificationMiddlewareState

    def _stable_message_id(self, tool_call_id: str, formatted_message: str) -> str:
        """yyds: 生成固定的消息 ID — 同一个提问重试时只保留一条，不重复。

        为什么需要这个？
          LangGraph 的 add_messages 规则：相同 id → 替换，不同 id → 追加。
          如果 LLM 重试同一个提问（tool_call_id 相同），用固定 ID 可以
          让新消息替换旧的，而不是在对话里出现两条一模一样的提问。

          不用固定 ID：
            [ToolMessage id="random_1" "❓ 部署到哪？"]  ← 第一次
            [ToolMessage id="random_2" "❓ 部署到哪？"]  ← 重试，重复了！
          用固定 ID：
            [ToolMessage id="clarification:toolu_abc" "❓ 部署到哪？"]  ← 第一次
            [ToolMessage id="clarification:toolu_abc" "❓ 部署到哪？"]  ← 重试，替换旧的

        yyds 执行顺序：
          ① 有 tool_call_id → "clarification:{tool_call_id}"
          ② 没有 → 用消息内容 sha256 前 16 位（兜底）
        """
        if tool_call_id:
            return f"clarification:{tool_call_id}"
        digest = sha256(formatted_message.encode("utf-8")).hexdigest()[:16]
        return f"clarification:{digest}"

    def _is_chinese(self, text: str) -> bool:
        """yyds: 检测文本是否包含中文字符（Unicode CJK 基本区 \u4e00-\u9fff）。"""
        return any("\u4e00" <= char <= "\u9fff" for char in text)

    def _format_clarification_message(self, args: dict) -> str:
        """yyds: 把 LLM 的工具调用参数格式化成用户看得懂的消息。

        yyds 执行顺序：
          ① 从 args 提取 question、clarification_type、context、options
          ② options 反序列化兼容：
             有些模型（Qwen3-Max）把数组参数序列化成 JSON 字符串 '["a","b"]'
             而不是原生数组 ["a","b"]。这里统一处理成 list。
          ③ 根据 clarification_type 选图标（❓🤔🔀⚠️💡）
          ④ 拼装消息：
             有 context → "❓ 背景信息\n问题内容"
             无 context → "❓ 问题内容"
          ⑤ 追加选项列表（如有）："  1. 开发\n  2. 测试\n  3. 生产"

        LLM 传入的 args 长什么样？
          {
            "question": "部署到哪个环境？",
            "clarification_type": "missing_info",
            "context": "检测到应用配置文件",
            "options": ["开发环境", "测试环境", "生产环境"]
          }

        格式化后的消息：
          ❓ 检测到应用配置文件

          部署到哪个环境？

            1. 开发环境
            2. 测试环境
            3. 生产环境
        """
        # yyds: ① 提取参数
        question = args.get("question", "")
        clarification_type = args.get("clarification_type", "missing_info")
        context = args.get("context")
        options = args.get("options", [])

        # yyds: ② options 反序列化兼容
        #   有些模型把数组序列化成 JSON 字符串 '{"options": "[\"a\",\"b\"]"}'
        #   而不是原生数组 '{"options": ["a", "b"]}'
        #   这里统一处理：字符串 → json.loads → list
        if isinstance(options, str):
            try:
                options = json.loads(options)
            except (json.JSONDecodeError, TypeError):
                options = [options]
        if options is None:
            options = []
        elif not isinstance(options, list):
            options = [options]

        # yyds: ③ 选图标
        icon = _TYPE_ICONS.get(clarification_type, "❓")

        # yyds: ④⑤ 拼装消息
        message_parts = []
        if context:
            message_parts.append(f"{icon} {context}")
            message_parts.append(f"\n{question}")
        else:
            message_parts.append(f"{icon} {question}")

        if options and len(options) > 0:
            message_parts.append("")  # yyds: 空行分隔问题和选项
            for i, option in enumerate(options, 1):
                message_parts.append(f"  {i}. {option}")

        return "\n".join(message_parts)

    def _handle_clarification(self, request: ToolCallRequest) -> Command:
        """yyds: 核心处理 — 拦截 ask_clarification，返回 Command(goto=END) 中断 Agent。

        yyds 执行顺序：
          ① 从 request.tool_call["args"] 提取参数
          ② _format_clarification_message() 格式化成用户友好的消息
          ③ _stable_message_id() 生成确定性 ID（重试时替换而非追加）
          ④ 构建 ToolMessage（包含格式化消息）
          ⑤ 返回 Command(update={messages: [ToolMessage]}, goto=END)
             → LangGraph 停下来，前端展示问题，等用户回复

        为什么返回 Command 而不是 ToolMessage？
          普通中间件返回 ToolMessage（告诉 LLM "工具执行完了"），
          但这里需要**中断整个 Agent 执行**（停下来等用户回复），
          所以返回 Command(goto=END)，让 LangGraph 跳到终止节点。
        """
        # yyds: ①② 提取参数 + 格式化
        args = request.tool_call.get("args", {})
        question = args.get("question", "")
        logger.info("Intercepted clarification request")
        logger.debug("Clarification question: %s", question)
        formatted_message = self._format_clarification_message(args)

        # yyds: ③④ 构建 ToolMessage（确定性 ID + 格式化消息）
        tool_call_id = request.tool_call.get("id", "")
        tool_message = ToolMessage(
            id=self._stable_message_id(tool_call_id, formatted_message),
            content=formatted_message,
            tool_call_id=tool_call_id,
            name="ask_clarification",
        )

        # yyds: ⑤ 返回 Command(goto=END) — 中断执行，前端展示问题
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
        """yyds: 同步版 — 拦截 ask_clarification，其他工具透传。

        yyds 执行顺序：
          ① 工具名 ≠ "ask_clarification" → 正常执行 handler（透传）
          ② 工具名 == "ask_clarification" → _handle_clarification()（拦截+中断）
        """
        if request.tool_call.get("name") != "ask_clarification":
            return handler(request)
        return self._handle_clarification(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """yyds: 异步版 — 逻辑和同步版完全相同。"""
        if request.tool_call.get("name") != "ask_clarification":
            return await handler(request)
        return self._handle_clarification(request)
