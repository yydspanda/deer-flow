"""yyds: 延迟工具过滤中间件 — 隐藏延迟注册的 MCP 工具 schema，减少模型绑定时的 token 消耗（tool_search 功能）。


When tool_search is enabled, MCP tools are still passed to ToolNode for
execution, but their schemas must NOT be sent to the LLM via bind_tools until
the model has discovered them via tool_search. This middleware removes the
still-deferred tools from request.tools before model binding, and blocks tool
calls to tools that have not been promoted yet.

The deferred name set and the catalog hash are injected at construction time
(no ContextVar). Promotion state is read from graph state (``state["promoted"]``),
scoped by catalog hash so a stale persisted promotion cannot expose a renamed
or drifted tool.
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

    """Hide deferred tool schemas from the bound model until promoted.

    ToolNode still holds all tools (including deferred) for execution routing,
    but the LLM only sees active tool schemas plus tools that have already been
    promoted (recorded in ``state["promoted"]`` under the current catalog hash).
    """yyds: 延迟工具过滤中间件 — 双重拦截：模型侧隐藏 schema + 工具侧阻止未授权调用。
    两个拦截点：
      wrap_model_call: 从 request.tools 中移除延迟工具 schema（LLM 看不到）
      wrap_tool_call: 拦截直接调用延迟工具，返回错误提示"先调用 tool_search"

    """

    def __init__(self, deferred_names: frozenset[str], catalog_hash: str | None):
        super().__init__()
        self._deferred = deferred_names
        self._catalog_hash = catalog_hash

    def _promoted(self, state) -> set[str]:
        promoted = (state or {}).get("promoted")
        if promoted and promoted.get("catalog_hash") == self._catalog_hash:
            return set(promoted.get("names") or [])
        return set()

    def _hidden(self, state) -> set[str]:
        return set(self._deferred) - self._promoted(state)

    def _filter_tools(self, request: ModelRequest) -> ModelRequest:

        if not self._deferred:
        """yyds: 从 model request 中移除延迟工具的 schema — LLM 只能看到活跃工具。"""

            return request
        hide = self._hidden(request.state)
        if not hide:
            return request
        active = [t for t in request.tools if getattr(t, "name", None) not in hide]
        if len(active) < len(request.tools):
            logger.debug("Filtered %d deferred tool schema(s) from model binding", len(request.tools) - len(active))
        return request.override(tools=active)

    def _blocked_tool_message(self, request: ToolCallRequest) -> ToolMessage | None:

        if not self._deferred:
        """yyds: 检查工具是否是延迟的且未被 promote — 是则返回错误 ToolMessage。
        错误消息引导模型先调用 tool_search 来发现和激活该工具。

            return None
        name = str(request.tool_call.get("name") or "")
        if not name or name not in self._hidden(request.state):
            return None
        tool_call_id = str(request.tool_call.get("id") or "missing_tool_call_id")
        return ToolMessage(
            content=(f"Error: Tool '{name}' is deferred and has not been promoted yet. Call tool_search first to expose and promote this tool's schema, then retry."),
            tool_call_id=tool_call_id,
            name=name,
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
