"""yyds: 内置护栏实现 — 白名单/黑名单，最简单的授权策略。

【大白话讲清楚】
  AllowlistProvider 是开箱即用的护栏实现，只做一件事：
  检查工具名在不在允许/拒绝列表里。

  检查顺序：先白名单，再黑名单。
    白名单设了 + 工具不在白名单里 → 拒绝
    黑名单设了 + 工具在黑名单里 → 拒绝
    都没命中 → 允许

【具体例子】
  config.yaml:
    guardrails:
      allowed_tools: ["read_file", "ls", "bash"]
      denied_tools: ["write_file"]

  read_file → 白名单里有 → 允许 ✓
  send_email → 白名单里没有 → 拒绝 ✗（"not in allowlist"）
  write_file → 白名单里有，但黑名单里也有 → 不走到黑名单检查（白名单先过了）
  grep → 白名单里没有 → 拒绝 ✗

  注意：白名单和黑名单同时生效时，白名单先检查。
  如果想完全禁止某个工具，只用黑名单就行（不设白名单）。

---
Built-in guardrail providers that ship with DeerFlow.
"""

from deerflow.guardrails.provider import GuardrailDecision, GuardrailReason, GuardrailRequest


class AllowlistProvider:
    """yyds: 白名单/黑名单护栏 — 按工具名控制权限，无外部依赖。"""

    name = "allowlist"

    def __init__(self, *, allowed_tools: list[str] | None = None, denied_tools: list[str] | None = None):
        self._allowed = set(allowed_tools) if allowed_tools else None  # yyds: None = 不限白名单
        self._denied = set(denied_tools) if denied_tools else set()  # yyds: 空 set = 不限黑名单

    def evaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        """yyds: 同步评估 — 先查白名单，再查黑名单。"""
        if self._allowed is not None and request.tool_name not in self._allowed:  # yyds: 白名单设了但不在里面 → 拒绝
            return GuardrailDecision(allow=False, reasons=[GuardrailReason(code="oap.tool_not_allowed", message=f"tool '{request.tool_name}' not in allowlist")])
        if request.tool_name in self._denied:  # yyds: 在黑名单里 → 拒绝
            return GuardrailDecision(allow=False, reasons=[GuardrailReason(code="oap.tool_not_allowed", message=f"tool '{request.tool_name}' is denied")])
        return GuardrailDecision(allow=True, reasons=[GuardrailReason(code="oap.allowed")])

    async def aevaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        """yyds: 异步评估 — 直接委托给同步方法（纯内存判断，没有 IO）。"""
        return self.evaluate(request)
