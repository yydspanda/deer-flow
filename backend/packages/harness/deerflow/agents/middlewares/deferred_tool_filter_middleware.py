"""yyds: 延迟工具过滤中间件 — 隐藏延迟注册的 MCP 工具 schema，减少模型绑定时的 token 消耗（tool_search 功能）。

【做什么】当 tool_search（工具搜索）功能启用时，MCP 工具注册到 DeferredToolRegistry 但不发送给 LLM。
   此中间件在模型调用前从 request.tools 中移除延迟工具的 schema，并在工具执行时阻止直接调用
   未 promoted 的延迟工具（要求先通过 tool_search 激活）。
【为什么存在】MCP 服务器可能注册大量工具（数十甚至上百个），如果全部发送给 LLM 会消耗大量 context token。
   延迟注册机制允许工具"注册但不绑定"——只在 Agent 需要时通过 tool_search 工具动态发现和加载。
【在链中的位置】
   - wrap_model_call 阶段：在模型绑定工具前过滤，只保留活跃工具的 schema。
   - wrap_tool_call 阶段：在工具执行前检查，阻止直接调用未被 promoted 的延迟工具。
【关键设计】
   - 双重拦截：模型侧过滤 schema + 工具侧拦截未授权调用。
   - 延迟工具仍注册在 ToolNode 中（可执行），但 LLM 看不到其 schema。
   - Agent 通过 tool_search 工具发现并 promote 延迟工具后，后续调用才会被放行。
   - 被拦截的调用返回错误 ToolMessage，引导模型先调用 tool_search。
"""

import logging
from collections.abc import Awaitable, Callable
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

"""Middleware to filter deferred tool schemas from model binding.

When tool_search is enabled, MCP tools are registered in the DeferredToolRegistry
and passed to ToolNode for execution, but their schemas should NOT be sent to the
LLM via bind_tools (that's the whole point of deferral — saving context tokens).

This middleware intercepts wrap_model_call and removes deferred tools from
request.tools so that model.bind_tools only receives active tool schemas.
The agent discovers deferred tools at runtime via the tool_search tool.
"""

logger = logging.getLogger(__name__)


class DeferredToolFilterMiddleware(AgentMiddleware[AgentState]):
    """yyds: 延迟工具过滤中间件 — 双重拦截：模型侧隐藏 schema + 工具侧阻止未授权调用。

    两个拦截点：
      wrap_model_call: 从 request.tools 中移除延迟工具 schema（LLM 看不到）
      wrap_tool_call: 拦截直接调用延迟工具，返回错误提示"先调用 tool_search"
    """

    def _filter_tools(self, request: ModelRequest) -> ModelRequest:
        """yyds: 从 model request 中移除延迟工具的 schema — LLM 只能看到活跃工具。"""
        from deerflow.tools.builtins.tool_search import get_deferred_registry

        registry = get_deferred_registry()
        if not registry:
            return request

        deferred_names = registry.deferred_names
        active_tools = [t for t in request.tools if getattr(t, "name", None) not in deferred_names]

        if len(active_tools) < len(request.tools):
            logger.debug(f"Filtered {len(request.tools) - len(active_tools)} deferred tool schema(s) from model binding")

        return request.override(tools=active_tools)

    def _blocked_tool_message(self, request: ToolCallRequest) -> ToolMessage | None:
        """yyds: 检查工具是否是延迟的且未被 promote — 是则返回错误 ToolMessage。
        错误消息引导模型先调用 tool_search 来发现和激活该工具。
        """
        from deerflow.tools.builtins.tool_search import get_deferred_registry

        registry = get_deferred_registry()
        if not registry:
            return None

        tool_name = str(request.tool_call.get("name") or "")
        if not tool_name:
            return None

        if not registry.contains(tool_name):
            return None

        tool_call_id = str(request.tool_call.get("id") or "missing_tool_call_id")
        return ToolMessage(
            content=(f"Error: Tool '{tool_name}' is deferred and has not been promoted yet. Call tool_search first to expose and promote this tool's schema, then retry."),
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",
        )

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        return handler(self._filter_tools(request))

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        blocked = self._blocked_tool_message(request)
        if blocked is not None:
            return blocked
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        return await handler(self._filter_tools(request))

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        blocked = self._blocked_tool_message(request)
        if blocked is not None:
            return blocked
        return await handler(request)
