"""yyds: Guardrail 中间件 — 拦截每次工具调用，先问护栏"允许吗？"再决定执行。

【大白话讲清楚】
  这个中间件挂在 Agent 的中间件链上，每次 Agent 想调工具时自动触发。

  核心流程：
    Agent 想调 bash("rm -rf /")
      → GuardrailMiddleware.wrap_tool_call() 拦截
      → 构建 GuardrailRequest(tool_name="bash", tool_input={"command": "rm -rf /"})
      → 调 provider.evaluate(request)
      → provider 返回 GuardrailDecision(allow=False)
      → 不执行 bash，而是返回 ToolMessage("Guardrail denied: ...")
      → Agent 看到"被拦了"，自己换方式

  关键设计 — fail_closed：
    provider 抛异常时怎么办？
    fail_closed=True（默认）→ 当作拒绝（安全优先）
    fail_closed=False → 当作允许（可用性优先）

    为什么默认 fail_closed？
    护栏的意义就是安全。如果 provider 挂了还放行，
    等于护栏失效，不安全。宁可误拦也不能漏放。

  关键设计 — GraphBubbleUp 不拦截：
    LangGraph 的 interrupt/pause/resume 信号通过异常传播。
    如果拦截了这些异常，LangGraph 的中断机制就坏了。
    所以 except GraphBubbleUp 时直接 raise，不处理。

【具体例子】
  场景 1：正常拦截
    Agent 调 write_file("/etc/passwd", "hacked")
    → AllowlistProvider: denied_tools=["write_file"] → allow=False
    → ToolMessage("Guardrail denied: tool 'write_file' is denied")
    → Agent 换成 read_file("/etc/passwd")

  场景 2：provider 异常
    自定义 provider 连接远程授权服务，网络断了
    → evaluate() 抛 ConnectionError
    → fail_closed=True → decision=allow=False
    → 返回 ToolMessage("guardrail provider error (fail-closed)")

  场景 3：GraphBubbleUp
    Agent 调工具，但触发了 LangGraph interrupt（比如需要用户确认）
    → GraphBubbleUp 异常抛出
    → 直接 raise，不拦截（让 LangGraph 正常处理中断）

---
GuardrailMiddleware - evaluates tool calls against a GuardrailProvider before execution.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from deerflow.guardrails.provider import GuardrailDecision, GuardrailProvider, GuardrailReason, GuardrailRequest

logger = logging.getLogger(__name__)


class GuardrailMiddleware(AgentMiddleware[AgentState]):
    """yyds: 护栏中间件 — 每次工具调用前拦截，评估通过才执行。

    决策树：
      工具调用进来
      → 构建 GuardrailRequest
      → provider.evaluate()
        ├─ 正常返回 decision
        │   ├─ decision.allow=True → 执行原始 handler
        │   └─ decision.allow=False → 返回错误 ToolMessage
        ├─ GraphBubbleUp → 直接 raise（LangGraph 控制流信号）
        └─ 其他异常
            ├─ fail_closed=True → 拒绝调用
            └─ fail_closed=False → 放行调用
    """

    def __init__(self, provider: GuardrailProvider, *, fail_closed: bool = True, passport: str | None = None):
        self.provider = provider
        self.fail_closed = fail_closed  # yyds: 默认 True，provider 挂了就当拒绝
        self.passport = passport  # yyds: agent_id，用于 GuardrailRequest 的 agent_id 字段

    def _build_request(self, request: ToolCallRequest) -> GuardrailRequest:
        """yyds: 把 LangGraph 的 ToolCallRequest 转成护栏的 GuardrailRequest。"""
        return GuardrailRequest(
            tool_name=str(request.tool_call.get("name", "")),
            tool_input=request.tool_call.get("args", {}),
            agent_id=self.passport,
            timestamp=datetime.now(UTC).isoformat(),
        )

    def _build_denied_message(self, request: ToolCallRequest, decision: GuardrailDecision) -> ToolMessage:
        """yyds: 构建拒绝消息 — 告诉 Agent 被拦了、为什么被拦、建议换方式。"""
        tool_name = str(request.tool_call.get("name", "unknown_tool"))
        tool_call_id = str(request.tool_call.get("id", "missing_id"))
        reason_text = decision.reasons[0].message if decision.reasons else "blocked by guardrail policy"
        reason_code = decision.reasons[0].code if decision.reasons else "oap.denied"
        return ToolMessage(
            content=f"Guardrail denied: tool '{tool_name}' was blocked ({reason_code}). Reason: {reason_text}. Choose an alternative approach.",
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",  # yyds: status="error" 让 Agent 知道这次调用失败了
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """yyds: 同步拦截 — 评估通过才执行 handler，拒绝则返回错误消息。"""
        gr = self._build_request(request)
        try:
            decision = self.provider.evaluate(gr)
        except GraphBubbleUp:
            raise  # yyds: 不拦截 LangGraph 控制流信号（interrupt/pause/resume）
        except Exception:
            logger.exception("Guardrail provider error (sync)")
            if self.fail_closed:
                decision = GuardrailDecision(allow=False, reasons=[GuardrailReason(code="oap.evaluator_error", message="guardrail provider error (fail-closed)")])
            else:
                return handler(request)  # yyds: fail_closed=False → provider 挂了也放行
        if not decision.allow:
            logger.warning("Guardrail denied: tool=%s policy=%s code=%s", gr.tool_name, decision.policy_id, decision.reasons[0].code if decision.reasons else "unknown")
            return self._build_denied_message(request, decision)
        return handler(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        """yyds: 异步拦截 — 和同步版本逻辑一致，用 await provider.aevaluate()。"""
        gr = self._build_request(request)
        try:
            decision = await self.provider.aevaluate(gr)
        except GraphBubbleUp:
            raise
        except Exception:
            logger.exception("Guardrail provider error (async)")
            if self.fail_closed:
                decision = GuardrailDecision(allow=False, reasons=[GuardrailReason(code="oap.evaluator_error", message="guardrail provider error (fail-closed)")])
            else:
                return await handler(request)
        if not decision.allow:
            logger.warning("Guardrail denied: tool=%s policy=%s code=%s", gr.tool_name, decision.policy_id, decision.reasons[0].code if decision.reasons else "unknown")
            return self._build_denied_message(request, decision)
        return await handler(request)
