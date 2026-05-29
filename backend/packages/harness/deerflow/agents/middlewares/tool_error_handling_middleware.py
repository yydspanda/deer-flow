"""yyds: 工具错误降级中间件 + Agent 中间件链构建器（零件工厂）。

【做什么】两件事：
   1. ToolErrorHandlingMiddleware — 工具执行异常的降级处理。
      工具抛异常时，捕获并生成 status="error" 的 ToolMessage，
      让 Agent 继续（可以换工具重试），而不是整个运行崩溃。
   2. build_lead/subagent_runtime_middlewares() — 组装 Agent 的基础中间件链。
      被 agent.py 和 executor.py 调用，是中间件链的"零件工厂"。

【为什么中间件类和构建函数放在同一个文件】
   DeerFlow 的中间件分两类：
   - 独立中间件：每个文件一个类（dangling_tool_call_middleware.py、loop_detection_middleware.py 等）
   - 构建器：负责把所有中间件按正确顺序组装成链

   更合理的做法是把构建函数拆到单独的 middleware_chain.py，
   因为构建器单向 import 所有中间件文件，不存在循环依赖问题。
   但 upstream 没这么做——可能是历史原因：ToolErrorHandlingMiddleware 只有 30 行，
   是所有中间件里最简单的，构建逻辑顺手写在了这个文件的末尾，
   后续没人重构拆出去。我们沿用了这个结构。

【调用关系】（从上到下）
   ┌─────────────────────────────────────────────────────────┐
   │ agent.py: _build_middlewares()                          │
   │   ├─ build_lead_runtime_middlewares()      ← 本文件     │
   │   │     └─ _build_runtime_middlewares()    ← 本文件     │
   │   │           ├─ ThreadDataMiddleware                    │
   │   │           ├─ UploadsMiddleware                       │
   │   │           ├─ SandboxMiddleware                       │
   │   │           ├─ DanglingToolCallMiddleware              │
   │   │           ├─ LLMErrorHandlingMiddleware              │
   │   │           ├─ GuardrailMiddleware（可选）             │
   │   │           ├─ SandboxAuditMiddleware                  │
   │   │           └─ ToolErrorHandlingMiddleware ← 本文件    │
   │   ├─ DynamicContextMiddleware                            │
   │   ├─ SummarizationMiddleware（可选）                     │
   │   ├─ ...（Title、Memory、LoopDetection 等 lead 专属）   │
   │   └─ ClarificationMiddleware（永远最后）                │
   │                                                          │
   │ executor.py: _create_agent()                             │
   │   └─ build_subagent_runtime_middlewares()  ← 本文件     │
   │         └─ _build_runtime_middlewares()    ← 本文件     │
   │               ├─（同上，但没有 UploadsMiddleware）       │
   │               └─ ToolErrorHandlingMiddleware ← 本文件    │
   │         └─ ViewImageMiddleware（视觉模型时追加）        │
   └─────────────────────────────────────────────────────────┘

   agent.py 调 build_lead（lead agent 有 17+ 个中间件），
   executor.py 调 build_subagent（subagent 只有 8-9 个，更轻量）。
   两者共用 _build_runtime_middlewares() 组装基础 7-8 层。

【关键设计】
   - 异常降级：try/except 包裹 handler，非 GraphBubbleUp 异常都转为错误 ToolMessage。
   - GraphBubbleUp 透传：LangGraph 的中断/暂停/恢复信号，必须原封不动往上抛。
   - 错误信息截断：500 字符上限，防止超长错误信息吃掉上下文窗口。
   - Guardrail 条件加载：安全护栏只在配置了 provider 时才加入中间件链。
   - lead vs subagent 差异：用参数控制（include_uploads、include_dangling_tool_call_patch）。
"""

import logging
from collections.abc import Awaitable, Callable
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

_MISSING_TOOL_CALL_ID = "missing_tool_call_id"  # yyds: tool_call 缺失 id 时的兜底值


class ToolErrorHandlingMiddleware(AgentMiddleware[AgentState]):
    """yyds: 工具错误降级中间件 — "一个工具挂了，不让整个 Agent 崩溃"。

    执行时机：wrap_tool_call（包裹每个工具的实际执行 handler）。
    在链中的位置：中间件链最后一层（SandboxAuditMiddleware 之后），
      异常从最内层 handler 抛出，到这里被捕获并降级。

    为什么这个类和构建函数放在同一个文件？
      更合理的做法是拆到单独的 middleware_chain.py（构建器单向 import 所有中间件，不存在循环依赖），
      但 upstream 没这么做——可能是历史原因，我们沿用了这个结构。

    数据流：
      工具调用请求 → handler(request) → 正常返回 ToolMessage/Command
                                    └─ 抛异常 → 本中间件捕获
                                                  ├─ GraphBubbleUp → 透传（控制流信号）
                                                  └─ 其他 Exception → _build_error_message()
                                                                          → 错误 ToolMessage
    两种异常的区别：
      GraphBubbleUp — LangGraph 的中断/暂停/恢复信号（interrupt()、ParentCommand），
                      不是"错误"而是"控制流"，必须 raise 让上层处理。
                      如果被吞掉：interrupt() 失效（人在回路无法暂停）、
                      ParentCommand 丢失（子图向父图发 Command 断裂）。
      其他 Exception — 工具真正的执行错误（网络超时、API 限流、文件不存在等），
                      降级为错误 ToolMessage，LLM 看到后可以换工具重试。
    """

    state_schema = AgentState

    def _build_error_message(self, request: ToolCallRequest, exc: Exception) -> ToolMessage:
        """yyds: 把工具异常转换为错误 ToolMessage — LLM 看到后可以换工具重试。

        yyds 执行顺序：
          ① 从 request 提取工具名和 tool_call_id（缺失时用兜底值）
          ② 格式化异常信息，截断到 500 字符（防止超长 stack trace 吃掉上下文）
          ③ 构建 ToolMessage（status="error"），内容含工具名、异常类型、异常详情
          ④ 错误信息末尾提示 LLM "Continue with available context, or choose an alternative tool"

        为什么 status="error"？
          ToolMessage 的 status 字段告诉 LLM 这个工具调用失败了。
          LLM 看到后会认为"这个工具不行，换个方法"，而不是认为命令执行成功了。
          这和 DanglingToolCall 的"生成缺失 ToolMessage"是对偶关系：
            Dangling 修补结构（AI 发了 tool_call 但没有对应 ToolMessage）
            ToolError 修补内容（工具抛异常，生成一条错误 ToolMessage）
        """
        # yyds: ① 提取工具名和 tool_call_id
        tool_name = str(request.tool_call.get("name") or "unknown_tool")
        tool_call_id = str(request.tool_call.get("id") or _MISSING_TOOL_CALL_ID)
        # yyds: ② 格式化异常详情（str(exception) 优先，为空则用类名）
        detail = str(exc).strip() or exc.__class__.__name__
        if len(detail) > 500:
            detail = detail[:497] + "..."
        # yyds: ③④ 构建错误 ToolMessage
        content = f"Error: Tool '{tool_name}' failed with {exc.__class__.__name__}: {detail}. Continue with available context, or choose an alternative tool."
        return ToolMessage(
            content=content,
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """yyds: 同步版 — try/except 包裹 handler，异常降级为错误 ToolMessage。

        yyds 执行顺序：
          ① 调用 handler 执行工具
          ② 成功 → 原样返回结果
          ③ GraphBubbleUp 异常 → 透传（LangGraph 控制流信号，不是错误）
          ④ 其他异常 → 记录日志 + 返回 _build_error_message 错误 ToolMessage
        """
        try:
            # yyds: ①② 正常执行
            return handler(request)
        except GraphBubbleUp:
            # yyds: ③ 透传控制流信号（interrupt/pause/resume/ParentCommand）
            raise
        except Exception as exc:
            # yyds: ④ 降级为错误 ToolMessage
            logger.exception("Tool execution failed (sync): name=%s id=%s", request.tool_call.get("name"), request.tool_call.get("id"))
            return self._build_error_message(request, exc)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        """yyds: 异步版 — 逻辑和 wrap_tool_call 完全相同，只是 handler 是 await 的。"""
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            logger.exception("Tool execution failed (async): name=%s id=%s", request.tool_call.get("name"), request.tool_call.get("id"))
            return self._build_error_message(request, exc)


# ---------------------------------------------------------------------------
# Middleware chain builders
# ---------------------------------------------------------------------------
# yyds: 下面三个函数是中间件链的"零件工厂"。
#
# 为什么需要构建器？
#   lead agent 和 subagent 共用 80% 的中间件（ThreadData、Sandbox、ToolError 等），
#   只有少数差异（lead 有 Uploads，subagent 没有；subagent 可能有 ViewImage）。
#   构建器把差异参数化（include_uploads、include_dangling_tool_call_patch），
#   改顺序只改这个文件。
#
# 更合理的做法是拆到单独的 middleware_chain.py（构建器单向 import 所有中间件，不存在循环依赖），
# 但 upstream 没这么做——可能是历史原因，我们沿用了这个结构。


def _build_runtime_middlewares(
    *,
    app_config: AppConfig,
    include_uploads: bool,
    include_dangling_tool_call_patch: bool,
    lazy_init: bool = True,
) -> list[AgentMiddleware]:
    """yyds: 基础中间件链构建器 — lead agent 和 subagent 共用的零件工厂。

    被 build_lead_runtime_middlewares() 和 build_subagent_runtime_middlewares() 调用，
    不直接被 agent.py / executor.py 调用。

    yyds 执行顺序：
      ① 初始化基础中间件：ThreadDataMiddleware + SandboxMiddleware（所有 Agent 都有）
      ② 如果 include_uploads=True → 在第 2 位插入 UploadsMiddleware（仅 lead agent）
      ③ 如果 include_dangling_tool_call_patch=True → 追加 DanglingToolCallMiddleware
      ④ 追加 LLMErrorHandlingMiddleware（LLM API 调用失败处理）
      ⑤ 如果配置了 guardrails → 动态加载 GuardrailMiddleware（安全护栏）
         - resolve_variable() 把配置里的类路径实例化
         - 检查 provider 构造函数是否接受 framework 参数（给内置 provider 传框架名）
      ⑥ 追加 SandboxAuditMiddleware（沙箱审计日志）
      ⑦ 追加 ToolErrorHandlingMiddleware（工具错误降级，本文件的主类）

    中间件最终顺序（lead agent 的基础链）：
      ThreadData → Uploads → Sandbox → DanglingToolCall → LLMError → Guardrail → SandboxAudit → ToolError

    中间件最终顺序（subagent 的基础链）：
      ThreadData → Sandbox → DanglingToolCall → LLMError → Guardrail → SandboxAudit → ToolError
      （没有 Uploads）
      （ViewImageMiddleware 由 build_subagent_runtime_middlewares 在返回后追加）

    参数说明：
      include_uploads: lead=True（处理用户上传文件），subagent=False
      include_dangling_tool_call_patch: 都是 True（修补缺失 ToolMessage）
      lazy_init: 都是 True（延迟初始化，避免 import 时就创建资源）
    """
    from deerflow.agents.middlewares.llm_error_handling_middleware import LLMErrorHandlingMiddleware
    from deerflow.agents.middlewares.thread_data_middleware import ThreadDataMiddleware
    from deerflow.sandbox.middleware import SandboxMiddleware

    # yyds: ① 基础中间件 — 所有 Agent 都有（thread_id 设置 + 沙箱生命周期）
    middlewares: list[AgentMiddleware] = [
        ThreadDataMiddleware(lazy_init=lazy_init),
        SandboxMiddleware(lazy_init=lazy_init),
    ]

    # yyds: ② UploadsMiddleware — 在第 2 位插入（仅 lead agent，处理用户上传文件）
    #   为什么 insert(1) 而不是 append？
    #   因为 UploadsMiddleware 需要 thread_id（由 ThreadDataMiddleware 设置），
    #   但必须在 SandboxMiddleware 之前（上传文件需要在沙箱初始化前就准备好路径）
    if include_uploads:
        from deerflow.agents.middlewares.uploads_middleware import UploadsMiddleware

        middlewares.insert(1, UploadsMiddleware())

    # yyds: ③ DanglingToolCallMiddleware — 修补 LLM history 中缺失的 ToolMessage
    #   为什么放在 Sandbox 之后？
    #   因为 DanglingToolCall 需要分析完整的历史消息（包括沙箱相关的 ToolMessage）
    if include_dangling_tool_call_patch:
        from deerflow.agents.middlewares.dangling_tool_call_middleware import DanglingToolCallMiddleware

        middlewares.append(DanglingToolCallMiddleware())

    # yyds: ④ LLMErrorHandlingMiddleware — LLM API 调用（invoke）失败时的处理
    #   包括断路器模式（circuit breaker）：连续失败 N 次后熔断，过一会再试
    middlewares.append(LLMErrorHandlingMiddleware(app_config=app_config))

    # yyds: ⑤ GuardrailMiddleware — 安全护栏（条件加载，配置了才加）
    #   安全护栏的作用：在 LLM 调用前后执行安全检查
    #   - 输入过滤：检测恶意 prompt 注入
    #   - 输出过滤：检测敏感信息泄露
    #   fail_closed=True 时，检查失败会阻止响应返回（安全优先）
    #   这和你的安全预警系统设计直接相关！
    guardrails_config = app_config.guardrails
    if guardrails_config.enabled and guardrails_config.provider:
        import inspect

        from deerflow.guardrails.middleware import GuardrailMiddleware
        from deerflow.reflection import resolve_variable

        # yyds: ⑤a resolve_variable 把配置里的类路径（如 "module.ClassName"）解析为实际类
        provider_cls = resolve_variable(guardrails_config.provider.use)
        provider_kwargs = dict(guardrails_config.provider.config) if guardrails_config.provider.config else {}

        # yyds: ⑤b 检查 provider 构造函数是否接受 framework 参数
        #   内置 provider（如 AllowlistProvider）不需要 framework，
        #   第三方 provider 可能需要知道当前框架名来做配置发现
        if "framework" not in provider_kwargs:
            try:
                sig = inspect.signature(provider_cls.__init__)
                if "framework" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                    provider_kwargs["framework"] = "deerflow"
            except (ValueError, TypeError):
                pass

        # yyds: ⑤c 实例化 provider + 创建 GuardrailMiddleware
        provider = provider_cls(**provider_kwargs)
        middlewares.append(GuardrailMiddleware(provider, fail_closed=guardrails_config.fail_closed, passport=guardrails_config.passport))

    # yyds: ⑥ SandboxAuditMiddleware — 记录沙箱操作的审计日志（bash 命令安全分类）
    from deerflow.agents.middlewares.sandbox_audit_middleware import SandboxAuditMiddleware

    middlewares.append(SandboxAuditMiddleware())

    # yyds: ⑦ ToolErrorHandlingMiddleware — 工具执行异常降级（本文件的主类，最后一层）
    middlewares.append(ToolErrorHandlingMiddleware())

    return middlewares


def build_lead_runtime_middlewares(*, app_config: AppConfig, lazy_init: bool = True) -> list[AgentMiddleware]:
    """yyds: Lead Agent 的基础中间件链。

    调用者：agent.py 的 _build_middlewares() 第一步调用。
    _build_middlewares() 拿到这个基础链后，再追加 lead 专属的中间件
    （DynamicContext、Summarization、Title、Memory、LoopDetection 等），
    最终形成 17+ 个中间件的完整链。

    和 subagent 的唯一区别：include_uploads=True（有上传文件处理）。
    """
    return _build_runtime_middlewares(
        app_config=app_config,
        include_uploads=True,
        include_dangling_tool_call_patch=True,
        lazy_init=lazy_init,
    )


def build_subagent_runtime_middlewares(
    *,
    app_config: AppConfig | None = None,
    model_name: str | None = None,
    lazy_init: bool = True,
) -> list[AgentMiddleware]:
    """yyds: SubAgent（子 Agent）的基础中间件链。

    调用者：executor.py 的 _create_agent() 调用。
    subagent 是 lead agent 通过 task() 工具派出去的轻量 Agent，
    只需要基础中间件链，不需要 lead 专属的（Title、Memory、Summarization 等）。

    和 lead agent 的差异：
      - include_uploads=False → 子 Agent 不处理用户上传（由 Lead Agent 负责）
      - 额外加 ViewImageMiddleware（如果模型支持视觉，让子 Agent 能看图）

    yyds 执行顺序：
      ① app_config 为 None 时从全局配置获取
      ② 调用 _build_runtime_middlewares（include_uploads=False）
      ③ model_name 为 None 时取配置的第一个模型
      ④ 如果模型支持视觉（supports_vision）→ 追加 ViewImageMiddleware
    """
    # yyds: ① 获取全局配置（subagent 可能不传 app_config）
    if app_config is None:
        from deerflow.config import get_app_config

        app_config = get_app_config()

    # yyds: ② 构建基础中间件链（没有 UploadsMiddleware）
    middlewares = _build_runtime_middlewares(
        app_config=app_config,
        include_uploads=False,
        include_dangling_tool_call_patch=True,
        lazy_init=lazy_init,
    )

    # yyds: ③ 取默认模型名（用于判断是否支持视觉）
    if model_name is None and app_config.models:
        model_name = app_config.models[0].name

    # yyds: ④ 视觉模型 → 追加 ViewImageMiddleware（让子 Agent 能处理图片）
    model_config = app_config.get_model_config(model_name) if model_name else None
    if model_config is not None and model_config.supports_vision:
        from deerflow.agents.middlewares.view_image_middleware import ViewImageMiddleware

        middlewares.append(ViewImageMiddleware())

    # Same provider safety-termination guard the lead agent uses — subagents
    # are equally exposed to truncated tool_calls returned with
    # finish_reason=content_filter (and friends), and the bad call would then
    # propagate back to the lead agent via the task tool result.
    safety_config = app_config.safety_finish_reason
    if safety_config.enabled:
        from deerflow.agents.middlewares.safety_finish_reason_middleware import SafetyFinishReasonMiddleware

        middlewares.append(SafetyFinishReasonMiddleware.from_config(safety_config))

    return middlewares
