"""yyds: 悬空工具调用修复中间件 — 在 LLM 调用前修补消息历史中缺失的 ToolMessage。

【做什么】扫描消息历史，找到那些发出了工具调用（AIMessage.tool_calls）但没有对应 ToolMessage 回复的
   "悬空"调用，为它们插入合成的错误 ToolMessage（内容为"[Tool call was interrupted...]"）。
【为什么存在】当用户中断对话或请求取消时，AIMessage 中的 tool_calls 可能没有对应的 ToolMessage。
   这会导致 LLM API 报错（OpenAI 等提供商要求每个 tool_call 都必须有对应的 ToolMessage 回复），
   此中间件确保消息格式始终正确。
【在链中的位置】wrap_model_call 阶段执行，即拦截模型调用，在实际发送给 LLM 之前修补消息列表。
   - 使用 wrap_model_call 而非 before_model 是因为需要精确插入位置（紧接在悬空 AIMessage 之后），
     而 before_model + add_messages reducer 只能追加到消息列表末尾。
【关键设计】
   - 合成 ToolMessage 的 status 设为 "error"，明确标记为异常。
   - 支持从结构化字段（tool_calls）和原始 provider 负载（additional_kwargs.tool_calls）两种来源提取工具调用。
   - 使用 patched_ids 集合避免重复插入。
"""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage

"""Middleware to fix dangling tool calls in message history.

A dangling tool call occurs when an AIMessage contains tool_calls but there are
no corresponding ToolMessages in the history (e.g., due to user interruption or
request cancellation). This causes LLM errors due to incomplete message format.

This middleware intercepts the model call to detect and patch such gaps by
inserting synthetic ToolMessages with an error indicator immediately after the
AIMessage that made the tool calls, ensuring correct message ordering.

Note: Uses wrap_model_call instead of before_model to ensure patches are inserted
at the correct positions (immediately after each dangling AIMessage), not appended
to the end of the message list as before_model + add_messages reducer would do.
"""

import json
import logging
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)


class DanglingToolCallMiddleware(AgentMiddleware[AgentState]):
    """yyds: 悬空工具调用修复中间件 — 确保 LLM 看到的消息格式始终合法。

    执行时机：wrap_model_call（拦截模型调用，在发送给 LLM 之前修补）
    为什么不用 before_model？因为 before_model + add_messages reducer 只能追加到列表末尾，
    而 ToolMessage 必须插在对应 AIMessage 的正后方，只有 wrap_model_call 能精确控制位置。

    典型场景：用户中断对话 → AIMessage(tool_calls=[...]) 没有对应 ToolMessage → LLM 报错
    修复方式：插入合成的 ToolMessage(content="[Tool call was interrupted...]", status="error")
    """

    @staticmethod
    def _message_tool_calls(msg) -> list[dict]:
        """Return normalized tool calls from structured fields or raw provider payloads.

        LangChain stores malformed provider function calls in ``invalid_tool_calls``.
        They do not execute, but provider adapters may still serialize enough of
        the call id/name back into the next request that strict OpenAI-compatible
        validators expect a matching ToolMessage. Treat them as dangling calls so
        the next model request stays well-formed and the model sees a recoverable
        tool error instead of another provider 400.

        yyds 执行顺序：
          ① 先尝试 msg.tool_calls（LangChain 标准格式，大部分情况走这里）
          ② 如果没有，尝试 msg.additional_kwargs["tool_calls"]（Provider 原始格式）
          ③ 把原始格式解析成统一格式 [{"id", "name", "args"}]
          ④ 最后检查 msg.invalid_tool_calls（Provider 返回的格式错误调用，如参数不合法）
        """
        normalized: list[dict] = []

        # yyds: ① 先走标准格式，大部分情况在这里就返回了
        tool_calls = getattr(msg, "tool_calls", None) or []
        normalized.extend(list(tool_calls))

        # yyds: ② 标准格式为空，尝试从 Provider 原始格式解析
        raw_tool_calls = (getattr(msg, "additional_kwargs", None) or {}).get("tool_calls") or []
        if not tool_calls:
            for raw_tc in raw_tool_calls:
                if not isinstance(raw_tc, dict):
                    continue

                # yyds: ③a 提取 name — 先看顶层 name，没有就从 function.name 取
                #   OpenAI 格式: {"type":"function", "function":{"name":"bash", ...}}
                function = raw_tc.get("function")
                name = raw_tc.get("name")
                if not name and isinstance(function, dict):
                    name = function.get("name")

                # yyds: ③b 提取 args — 先看顶层 args，没有就从 function.arguments 解析 JSON 字符串
                #   OpenAI 格式里 arguments 是 JSON 字符串 '{"command":"ls"}'，需要 json.loads 反序列化
                args = raw_tc.get("args", {})
                if not args and isinstance(function, dict):
                    raw_args = function.get("arguments")
                    if isinstance(raw_args, str):
                        try:
                            parsed_args = json.loads(raw_args)
                        except (TypeError, ValueError, json.JSONDecodeError):
                            parsed_args = {}
                        args = parsed_args if isinstance(parsed_args, dict) else {}

                # yyds: ③c 组装统一格式，追加到结果列表
                normalized.append(
                    {
                        "id": raw_tc.get("id"),
                        "name": name or "unknown",
                        "args": args if isinstance(args, dict) else {},
                    }
                )

        # yyds: ④ 检查 invalid_tool_calls（Provider 返回的格式错误调用，如参数不合法）
        for invalid_tc in getattr(msg, "invalid_tool_calls", None) or []:
            if not isinstance(invalid_tc, dict):
                continue
            normalized.append(
                {
                    "id": invalid_tc.get("id"),
                    "name": invalid_tc.get("name") or "unknown",
                    "args": {},
                    "invalid": True,
                    "error": invalid_tc.get("error"),
                }
            )

        return normalized

    @staticmethod
    def _synthetic_tool_message_content(tool_call: dict) -> str:
        if tool_call.get("invalid"):
            error = tool_call.get("error")
            if isinstance(error, str) and error:
                return f"[Tool call could not be executed because its arguments were invalid: {error}]"
            return "[Tool call could not be executed because its arguments were invalid.]"
        return "[Tool call was interrupted and did not return a result.]"

    def _build_patched_messages(self, messages: list) -> list | None:
        """Return messages with tool results grouped after their tool-call AIMessage.

        This normalizes model-bound causal order before provider serialization while
        preserving already-valid transcripts unchanged.

        yyds: 扫描消息列表，为每个悬空的 tool_call 插入合成的错误 ToolMessage。

        整体执行顺序：
          第一遍扫描（第①步）：收集所有已有 ToolMessage → tool_messages_by_id
          第二遍扫描（第②步）：收集所有 tool_call 的 ID → tool_call_ids
          第三遍扫描（第③步）：重建消息列表，按 AIMessage 分组插入 ToolMessage

        什么叫"悬空"？
          AIMessage 里有 tool_calls=[{id:"call_1"}, {id:"call_2"}]
          但消息列表里只有 ToolMessage(tool_call_id="call_1")
          call_2 没有 ToolMessage 回复 → 悬空 → 需要补一个假的

        consumed_tool_msg_ids 的作用：防止同一个 tool_call_id 被重复补丁
          （理论上同一个 id 不应该出现在多条 AIMessage 中，但防御性编程）
        """
        tool_messages_by_id: dict[str, deque[ToolMessage]] = defaultdict(deque)
        for msg in messages:
            if isinstance(msg, ToolMessage):
                tool_messages_by_id[msg.tool_call_id].append(msg)

        # yyds: 第②步 — 收集所有 AIMessage 中的 tool_call ID
        tool_call_ids: set[str] = set()
        for msg in messages:
            if getattr(msg, "type", None) != "ai":
                continue
            for tc in self._message_tool_calls(msg):
                tc_id = tc.get("id")
                if tc_id:
                    tool_call_ids.add(tc_id)

        # yyds: 第③步 — 重建消息列表，按 AIMessage 分组插入对应的 ToolMessage
        patched: list = []
        patch_count = 0
        for msg in messages:
            if isinstance(msg, ToolMessage) and msg.tool_call_id in tool_call_ids:
                continue

            patched.append(msg)
            if getattr(msg, "type", None) != "ai":
                continue

            # yyds: 检查这条 AIMessage 的每个 tool_call
            #   条件：id 在 tool_call_ids 里（这个 AIMessage 有工具调用）
            #   → 在这条 AIMessage 后面立即插入对应的 ToolMessage（有回复用真的，没回复用假的）
            for tc in self._message_tool_calls(msg):
                tc_id = tc.get("id")
                if not tc_id:
                    continue

                tool_msg_queue = tool_messages_by_id.get(tc_id)
                existing_tool_msg = tool_msg_queue.popleft() if tool_msg_queue else None
                if existing_tool_msg is not None:
                    patched.append(existing_tool_msg)
                else:
                    patched.append(
                        ToolMessage(
                            content=self._synthetic_tool_message_content(tc),
                            tool_call_id=tc_id,
                            name=tc.get("name", "unknown"),
                            status="error",  # yyds: 标记为错误，LLM 知道这个工具没执行成功
                        )
                    )
                    patch_count += 1

        if patched == messages:
            return None

        if patch_count:
            logger.warning(f"Injecting {patch_count} placeholder ToolMessage(s) for dangling tool calls")
        return patched

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        """yyds: 同步版 — 修补消息后传给 LLM。"""
        patched = self._build_patched_messages(request.messages)
        if patched is not None:
            request = request.override(messages=patched)
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        """yyds: 异步版 — 同上逻辑。"""
        patched = self._build_patched_messages(request.messages)
        if patched is not None:
            request = request.override(messages=patched)
        return await handler(request)
