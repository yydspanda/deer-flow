"""yyds: Guardrail 接口协议 + 数据结构 — 工具调用授权的"合同"。

【大白话讲清楚】
  这个文件定义了 Guardrails 系统的接口和数据结构。
  任何想做"工具调用授权"的提供者，只要实现 GuardrailProvider 协议就行。

  三个数据类：
    GuardrailRequest：问"这个工具调用允许吗？"时附带的上下文
      - tool_name: 想调什么工具
      - tool_input: 工具参数是什么
      - agent_id: 谁在调（Lead Agent 还是 sub-agent）
      - is_subagent: 是不是 sub-agent 调的

    GuardrailDecision：回答"允许"还是"拒绝"
      - allow: True/False
      - reasons: 为什么允许/拒绝（结构化原因）
      - policy_id: 哪条策略触发的（审计用）

    GuardrailReason：结构化原因
      - code: 错误码（如 "oap.tool_not_allowed"）
      - message: 人类可读的描述

  一个协议：
    GuardrailProvider：提供者必须实现 evaluate() 和 aevaluate()
      - evaluate(): 同步评估
      - aevaluate(): 异步评估

  设计对齐 OAP（Open Agent Protocol）标准：
    数据结构和 OAP 的 Decision/Reason 对象一致，
    方便未来对接其他 OAP 兼容的授权服务。

---
GuardrailProvider protocol and data structures for pre-tool-call authorization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class GuardrailRequest:
    """yyds: 护栏评估请求 — "这个工具调用允许吗？"时附带的上下文。"""

    tool_name: str
    tool_input: dict[str, Any]
    agent_id: str | None = None
    thread_id: str | None = None
    is_subagent: bool = False  # yyds: sub-agent 的调用可能需要更严格的限制
    timestamp: str = ""


@dataclass
class GuardrailReason:
    """yyds: 结构化原因 — 错误码 + 描述，审计可追溯。"""

    code: str  # yyds: 如 "oap.tool_not_allowed"、"oap.denied"
    message: str = ""


@dataclass
class GuardrailDecision:
    """yyds: 护栏评估结果 — 允许/拒绝 + 原因列表 + 策略 ID。"""

    allow: bool
    reasons: list[GuardrailReason] = field(default_factory=list)
    policy_id: str | None = None  # yyds: 审计用，知道是哪条策略触发的
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class GuardrailProvider(Protocol):
    """yyds: 护栏提供者协议 — 任何类只要实现 evaluate + aevaluate 就行。

    加载方式：通过 resolve_variable() 按类路径动态加载，
    和 models/tools/sandbox 用的是同一套机制。
    config.yaml 里写 guardrails.provider: "my_package.MyProvider" 即可。
    """

    name: str

    def evaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        """Evaluate whether a tool call should proceed."""
        ...

    async def aevaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        """Async variant."""
        ...
