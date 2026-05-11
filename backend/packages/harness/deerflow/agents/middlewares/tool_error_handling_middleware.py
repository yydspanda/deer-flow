"""Tool error handling middleware and shared runtime middleware builders.

yyds: 这个文件做两件事：

1. ToolErrorHandlingMiddleware — 工具错误降级中间件
   Agent 调用工具时，如果工具抛异常（网络错误、API 限流等），
   这个中间件把异常"降级"为 ToolMessage(status="error")，
   而不是让整个 Agent 崩溃。

   这就是你之前遇到的 summarization bug 的"对偶"——
   summarization 错误地截断了 ToolMessage，而这个中间件的职责是
   在工具出错时生成 ToolMessage。

   原理：它包裹了每个工具调用的 handler，用 try/except 捕获异常，
   把异常信息变成一条"错误 ToolMessage"，LLM 看到后可以换一个工具重试。

2. build_lead_runtime_middlewares() / build_subagent_runtime_middlewares()
   基础中间件链的构建函数，被 agent.py 的 _build_middlewares() 调用。
   组装顺序：
     ① ThreadDataMiddleware  → 设置 thread_id、工作目录
     ② UploadsMiddleware     → 处理上传文件（仅 lead agent）
     ③ SandboxMiddleware     → 沙箱生命周期管理
     ④ DanglingToolCallMiddleware → 修补缺失的 ToolMessage
     ⑤ LLMErrorHandlingMiddleware → LLM 调用错误处理
     ⑥ GuardrailMiddleware   → 安全护栏（如果配置了）
     ⑦ SandboxAuditMiddleware → 沙箱审计日志
     ⑧ ToolErrorHandlingMiddleware → 工具错误降级
"""

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from deerflow.config.app_config import AppConfig
from deerflow.subagents.status_contract import (
    extract_subagent_status,
    make_subagent_additional_kwargs,
)

if TYPE_CHECKING:
    from deerflow.tools.builtins.tool_search import DeferredToolSetup

logger = logging.getLogger(__name__)

_MISSING_TOOL_CALL_ID = "missing_tool_call_id"
_TASK_TOOL_NAME = "task"


def _stamp_task_subagent_status(message: ToolMessage, *, tool_name: str, error: str | None = None) -> ToolMessage:
    """Centralised stamping of ``additional_kwargs.subagent_status``.

    Bytedance/deer-flow issue #3146: the frontend now reads the subagent
    status from a structured field instead of parsing the leading text of
    the task tool's return string. That contract is enforced here, in the
    one place every task tool result flows through, rather than at the 5
    normal-return + 3 ``Error:`` pre-execution branches inside
    ``task_tool.py``. Centralisation prevents the "added a new return
    path, forgot the stamp" drift mode.

    For non-``task`` tools this is a no-op so other tools' additional_kwargs
    conventions are untouched.
    """
    if tool_name != _TASK_TOOL_NAME:
        return message
    content = message.content if isinstance(message.content, str) else ""
    status = extract_subagent_status(content)
    if status is None:
        # Non-terminal streaming chunks or unrecognised shapes leave the
        # field unset so the frontend can keep the card on its in-progress
        # placeholder until a real terminal frame arrives.
        return message
    stamp = make_subagent_additional_kwargs(status, error=error)
    existing = dict(message.additional_kwargs or {})
    existing.update(stamp)
    message.additional_kwargs = existing
    return message


class ToolErrorHandlingMiddleware(AgentMiddleware[AgentState]):
    """Convert tool exceptions into error ToolMessages so the run can continue.

    yyds: 核心设计——"一个工具挂了，不应该让整个 Agent 崩溃"。

    工作流程：
      LLM 发出 tool_call → 中间件链层层包裹 → 最终到达实际工具 handler
      如果 handler 抛异常 → 这个中间件捕获 → 生成错误 ToolMessage
      LLM 看到错误 ToolMessage → 可以选择换一个工具或告知用户

    两种异常不捕获：
      GraphBubbleUp → LangGraph 的控制流信号（中断/暂停/恢复），必须透传
    """

    def _build_error_message(self, request: ToolCallRequest, exc: Exception) -> ToolMessage:
        """yyds: 把工具异常转换为错误 ToolMessage。
        错误信息截断到 500 字符（避免超长错误吃掉上下文）。
        status="error" 告诉 LLM 这个工具调用失败了，可以用别的方式继续。
        """
        tool_name = str(request.tool_call.get("name") or "unknown_tool")
        tool_call_id = str(request.tool_call.get("id") or _MISSING_TOOL_CALL_ID)
        detail = str(exc).strip() or exc.__class__.__name__
        if len(detail) > 500:
            detail = detail[:497] + "..."

        content = f"Error: Tool '{tool_name}' failed with {exc.__class__.__name__}: {detail}. Continue with available context, or choose an alternative tool."
        message = ToolMessage(
            content=content,
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",
        )
        # Stamp the structured subagent status on the wrapper too: the
        # frontend would otherwise have to fall back to prefix-matching
        # ``Error: Tool 'task' failed ...`` on the wire. The ``subagent_error``
        # carries the same ``ExcClass: detail`` shape the wrapper string
        # uses so debugging artifacts stay aligned.
        structured_error = f"{exc.__class__.__name__}: {detail}"
        return _stamp_task_subagent_status(message, tool_name=tool_name, error=structured_error)

    @staticmethod
    def _maybe_stamp(result: ToolMessage | Command, request: ToolCallRequest) -> ToolMessage | Command:
        """Apply the subagent stamp to successful task tool returns.

        ``Command`` results bypass the stamp — they encode LangGraph
        control flow rather than user-facing tool output.
        """
        if not isinstance(result, ToolMessage):
            return result
        tool_name = str(request.tool_call.get("name") or "")
        return _stamp_task_subagent_status(result, tool_name=tool_name)

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        try:
            result = handler(request)
        except GraphBubbleUp:
            # Preserve LangGraph control-flow signals (interrupt/pause/resume).
            raise
        except Exception as exc:
            logger.exception("Tool execution failed (sync): name=%s id=%s", request.tool_call.get("name"), request.tool_call.get("id"))
            return self._build_error_message(request, exc)
        return self._maybe_stamp(result, request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        try:
            result = await handler(request)
        except GraphBubbleUp:
            # Preserve LangGraph control-flow signals (interrupt/pause/resume).
            raise
        except Exception as exc:
            logger.exception("Tool execution failed (async): name=%s id=%s", request.tool_call.get("name"), request.tool_call.get("id"))
            return self._build_error_message(request, exc)
        return self._maybe_stamp(result, request)


def _build_runtime_middlewares(
    *,
    app_config: AppConfig,
    include_uploads: bool,
    include_dangling_tool_call_patch: bool,
    lazy_init: bool = True,
) -> list[AgentMiddleware]:
    """Build shared base middlewares for agent execution.

    yyds: 基础中间件链构建器。lead agent 和 subagent 共用大部分中间件，
          只有少数不同（比如 subagent 不需要 UploadsMiddleware）。
          组装顺序在 agent.py 的注释里已经画过了，这里补充每个的作用：
            ThreadDataMiddleware  → 设置 thread_id + 工作目录路径
            UploadsMiddleware     → 处理用户上传文件（insert 到第 2 位）
            SandboxMiddleware     → 沙箱容器生命周期（获取/释放）
            DanglingToolCallMiddleware → 修复 LLM history 里缺失的 ToolMessage
            LLMErrorHandlingMiddleware → LLM API 调用失败时的处理
            GuardrailMiddleware   → 安全护栏（可选，配置了才加）
            SandboxAuditMiddleware → 记录沙箱操作审计日志
            ToolErrorHandlingMiddleware → 工具执行异常降级
    """
    from deerflow.agents.middlewares.llm_error_handling_middleware import LLMErrorHandlingMiddleware
    from deerflow.agents.middlewares.thread_data_middleware import ThreadDataMiddleware
    from deerflow.agents.middlewares.tool_output_budget_middleware import ToolOutputBudgetMiddleware
    from deerflow.sandbox.middleware import SandboxMiddleware

    middlewares: list[AgentMiddleware] = [
        ToolOutputBudgetMiddleware.from_app_config(app_config),
        ThreadDataMiddleware(lazy_init=lazy_init),
        SandboxMiddleware(lazy_init=lazy_init),
    ]

    if include_uploads:
        from deerflow.agents.middlewares.uploads_middleware import UploadsMiddleware

        middlewares.insert(2, UploadsMiddleware())

    if include_dangling_tool_call_patch:
        from deerflow.agents.middlewares.dangling_tool_call_middleware import DanglingToolCallMiddleware

        middlewares.append(DanglingToolCallMiddleware())

    middlewares.append(LLMErrorHandlingMiddleware(app_config=app_config))

    # Guardrail middleware (if configured)
    # yyds: 安全护栏中间件——可以在 LLM 调用前后执行安全检查。
    #       比如：输入过滤（检测恶意 prompt）、输出过滤（检测敏感信息泄露）。
    #       这和你的安全预警系统设计直接相关！
    #       fail_closed=True 时，检查失败会阻止响应返回（安全优先）。
    guardrails_config = app_config.guardrails
    if guardrails_config.enabled and guardrails_config.provider:
        import inspect

        from deerflow.guardrails.middleware import GuardrailMiddleware
        from deerflow.reflection import resolve_variable

        provider_cls = resolve_variable(guardrails_config.provider.use)
        provider_kwargs = dict(guardrails_config.provider.config) if guardrails_config.provider.config else {}
        # Pass framework hint if the provider accepts it (e.g. for config discovery).
        # Built-in providers like AllowlistProvider don't need it, so only inject
        # when the constructor accepts 'framework' or '**kwargs'.
        if "framework" not in provider_kwargs:
            try:
                sig = inspect.signature(provider_cls.__init__)
                if "framework" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                    provider_kwargs["framework"] = "deerflow"
            except (ValueError, TypeError):
                pass
        provider = provider_cls(**provider_kwargs)
        middlewares.append(GuardrailMiddleware(provider, fail_closed=guardrails_config.fail_closed, passport=guardrails_config.passport))

    from deerflow.agents.middlewares.sandbox_audit_middleware import SandboxAuditMiddleware

    middlewares.append(SandboxAuditMiddleware())
    middlewares.append(ToolErrorHandlingMiddleware())
    return middlewares


def build_lead_runtime_middlewares(*, app_config: AppConfig, lazy_init: bool = True) -> list[AgentMiddleware]:
    """Middlewares shared by lead agent runtime before lead-only middlewares.

    yyds: Lead Agent 的基础中间件链。被 agent.py 的 _build_middlewares() 第一步调用。
          include_uploads=True → 有上传文件处理
          include_dangling_tool_call_patch=True → 有 ToolMessage 修补
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
    deferred_setup: "DeferredToolSetup | None" = None,
) -> list[AgentMiddleware]:
    """Middlewares shared by subagent runtime before subagent-only middlewares.

    yyds: SubAgent（子 Agent）的基础中间件链。和 Lead Agent 的区别：
          - include_uploads=False → 子 Agent 不处理用户上传
          - 额外加了 ViewImageMiddleware（如果模型支持视觉）
          子 Agent 是 Lead Agent 用 task() 工具派出去的，架构更轻量。
    """
    if app_config is None:
        from deerflow.config import get_app_config

        app_config = get_app_config()

    middlewares = _build_runtime_middlewares(
        app_config=app_config,
        include_uploads=False,
        include_dangling_tool_call_patch=True,
        lazy_init=lazy_init,
    )

    if model_name is None and app_config.models:
        model_name = app_config.models[0].name

    model_config = app_config.get_model_config(model_name) if model_name else None
    if model_config is not None and model_config.supports_vision:
        from deerflow.agents.middlewares.view_image_middleware import ViewImageMiddleware

        middlewares.append(ViewImageMiddleware())

    # Hide deferred (MCP) tool schemas from the subagent's model binding until
    # tool_search promotes them. This is the same wiring the lead agent gets. The deferred
    # set + catalog hash come from the build-time setup (assembled after
    # tool-policy filtering); promotion is read from graph state. Empty/None
    # setup (deferral disabled or no MCP tool survived) is a pure no-op.
    if deferred_setup is not None and deferred_setup.deferred_names:
        from deerflow.agents.middlewares.deferred_tool_filter_middleware import DeferredToolFilterMiddleware

        middlewares.append(DeferredToolFilterMiddleware(deferred_setup.deferred_names, deferred_setup.catalog_hash))

    # Same provider safety-termination guard the lead agent uses — subagents
    # are equally exposed to truncated tool_calls returned with
    # finish_reason=content_filter (and friends), and the bad call would then
    # propagate back to the lead agent via the task tool result.
    safety_config = app_config.safety_finish_reason
    if safety_config.enabled:
        from deerflow.agents.middlewares.safety_finish_reason_middleware import SafetyFinishReasonMiddleware

        middlewares.append(SafetyFinishReasonMiddleware.from_config(safety_config))

    return middlewares
