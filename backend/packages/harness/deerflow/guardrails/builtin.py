# yyds: 内置护栏提供者，实现基于白名单/黑名单的简单工具调用授权，无外部依赖
"""Built-in guardrail providers that ship with DeerFlow."""

from deerflow.guardrails.provider import GuardrailDecision, GuardrailReason, GuardrailRequest


# yyds: 白名单/黑名单护栏提供者，通过配置允许和拒绝的工具名列表来控制工具调用权限
class AllowlistProvider:
    """Simple allowlist/denylist provider. No external dependencies."""

    name = "allowlist"

    def __init__(self, *, allowed_tools: list[str] | None = None, denied_tools: list[str] | None = None):
        self._allowed = set(allowed_tools) if allowed_tools else None
        self._denied = set(denied_tools) if denied_tools else set()

    # yyds: 同步评估工具调用是否被允许，优先检查白名单再检查黑名单
    def evaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        if self._allowed is not None and request.tool_name not in self._allowed:
            return GuardrailDecision(allow=False, reasons=[GuardrailReason(code="oap.tool_not_allowed", message=f"tool '{request.tool_name}' not in allowlist")])
        if request.tool_name in self._denied:
            return GuardrailDecision(allow=False, reasons=[GuardrailReason(code="oap.tool_not_allowed", message=f"tool '{request.tool_name}' is denied")])
        return GuardrailDecision(allow=True, reasons=[GuardrailReason(code="oap.allowed")])

    # yyds: 异步评估，直接委托给同步方法
    async def aevaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        return self.evaluate(request)
