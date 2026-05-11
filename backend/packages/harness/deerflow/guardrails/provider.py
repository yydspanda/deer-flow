# yyds: 护栏提供者协议和数据结构定义，定义工具调用前授权检查的接口契约，与OAP标准对齐
"""GuardrailProvider protocol and data structures for pre-tool-call authorization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# yyds: 护栏评估请求，包含工具名、输入参数、Agent身份和线程信息等上下文
@dataclass
class GuardrailRequest:
    """Context passed to the provider for each tool call."""

    tool_name: str
    tool_input: dict[str, Any]
    agent_id: str | None = None
    thread_id: str | None = None
    is_subagent: bool = False
    timestamp: str = ""


# yyds: 结构化拒绝/允许原因，包含错误码和描述消息，遵循OAP原因对象规范
@dataclass
class GuardrailReason:
    """Structured reason for an allow/deny decision (OAP reason object)."""

    code: str
    message: str = ""


# yyds: 护栏评估结果，包含允许/拒绝判定、原因列表和策略ID，与OAP Decision对象对齐
@dataclass
class GuardrailDecision:
    """Provider's allow/deny verdict (aligned with OAP Decision object)."""

    allow: bool
    reasons: list[GuardrailReason] = field(default_factory=list)
    policy_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# yyds: 可插拔工具调用授权协议，通过resolve_variable按类路径动态加载，与模型/工具/沙箱使用相同机制
@runtime_checkable
class GuardrailProvider(Protocol):
    """Contract for pluggable tool-call authorization.

    Any class with these methods works - no base class required.
    Providers are loaded by class path via resolve_variable(),
    the same mechanism DeerFlow uses for models, tools, and sandbox.
    """

    name: str

    def evaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        """Evaluate whether a tool call should proceed."""
        ...

    async def aevaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        """Async variant."""
        ...
