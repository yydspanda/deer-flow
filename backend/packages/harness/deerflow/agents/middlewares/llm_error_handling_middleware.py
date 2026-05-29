"""yyds: LLM 错误处理中间件 — API 挂了不要紧，自动重试，挂太久就拉闸。

【大白话讲清楚】
  LLM API 调用不稳定是常态。DeepSeek "服务繁忙"、网络超时、API Key 过期、配额用完...
  这个中间件包裹每次 LLM 调用，确保 Agent 不会因为一次 API 故障就崩溃。

  它解决三个问题：
  问题 A — 暂时性故障（超时、限速、503）：
    → 自动重试，最多 3 次，指数退避（1s→2s→4s→8s）
    → 尊重服务端的 Retry-After 头（它说等 5 秒就等 5 秒）
    → 前端展示重试进度（llm_retry 事件）

  问题 B — 不可恢复错误（配额不足、认证失败）：
    → 不重试（重试也不会成功）
    → 返回友好的 AIMessage 给用户（而不是堆栈溢出到前端）

  问题 C — 持续故障雪崩（服务商大面积宕机）：
    → 熔断器：连续失败 5 次后"跳闸"，后续请求直接返回"暂时不可用"
    → 不浪费 API 调用（省 token 省钱）
    → 60 秒后放一个探测请求，成功了就恢复

【具体例子】
  用户发消息，Agent 调 LLM...

  正常流程：API 调用成功 → 返回结果 ✅

  异常流程 A（暂时性故障）：
    第 1 次调用：DeepSeek 返回 503 "服务繁忙"
    → 等待 1 秒，重试
    第 2 次调用：成功 → 返回结果 ✅
    → 用户完全无感知，前端可能闪一下"正在重试"

  异常流程 B（不可恢复）：
    API Key 过期 → 返回 401 "unauthorized"
    → 不重试，直接返回 AIMessage("认证失败，请检查 API Key")

  异常流程 C（熔断）：
    连续 5 个用户请求都失败了 → 熔断器跳闸
    第 6 个请求 → 直接返回"暂时不可用"（不调 API，省 token 省钱）
    ... 60 秒后 ...
    第 7 个请求 → 探测一下 → 成功了 → 熔断器恢复

【在链中的位置】
  wrap_model_call 钩子，包裹整个 LLM 调用。#5 号中间件。
  因为 wrap_model_call 是洋葱模型（外层先执行），所以它最先捕获所有异常。

【关键设计】
  - 错误分类优先级：quota > auth > transient > busy > generic
    （429 可能是限速也可能是配额不足，优先检查配额）
  - GraphBubbleUp 必须透传：LangGraph 控制流信号，不能被 try/except 吞掉
  - 非可重试错误不计入熔断器：配额/认证是业务错误，不是服务故障
  - 模式匹配表中英文双语：覆盖 OpenAI/Claude/DeepSeek/智谱/通义

---

LLM error handling middleware with retry/backoff and user-facing fallbacks.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from email.utils import parsedate_to_datetime
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    ModelCallResult,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage
from langgraph.errors import GraphBubbleUp

from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

# ─── yyds: 错误分类用的模式匹配表 ───
# 四组模式：可重试的状态码 / 服务繁忙 / 配额不足 / 认证失败
# 每组都包含中英文关键词，覆盖国内外 LLM Provider（OpenAI/Claude/DeepSeek/智谱/通义等）

_RETRIABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
# yyds: 可重试的 HTTP 状态码
# 408 Request Timeout     — 请求超时，服务器没收到完整请求
# 409 Conflict             — 资源冲突（罕见，但可重试）
# 425 Too Early            — 服务器不愿冒重放攻击风险（RFC 8470）
# 429 Too Many Requests    — 限速，最常见！DeepSeek/智谱高频触发
# 500 Internal Server Error— 服务端内部错误
# 502 Bad Gateway          — 网关/代理错误
# 503 Service Unavailable  — 服务过载/维护，DeepSeek"服务繁忙"常见
# 504 Gateway Timeout      — 网关超时

_BUSY_PATTERNS = (  # yyds: 服务繁忙模式 → 分类为 busy（可重试）
    "server busy",
    "temporarily unavailable",
    "try again later",
    "please retry",
    "please try again",
    "overloaded",
    "high demand",
    "rate limit",
    "负载较高",  # yyds: DeepSeek 的中文错误消息
    "服务繁忙",
    "稍后重试",
    "请稍后重试",
)
_QUOTA_PATTERNS = (  # yyds: 配额不足模式 → 分类为 quota（不重试，重试也不会成功）
    "insufficient_quota",
    "quota",
    "billing",
    "credit",
    "payment",
    "余额不足",
    "超出限额",
    "额度不足",
    "欠费",
)
_AUTH_PATTERNS = (  # yyds: 认证失败模式 → 分类为 auth（不重试，重试也不会成功）
    "authentication",
    "unauthorized",
    "invalid api key",
    "invalid_api_key",
    "permission",
    "forbidden",
    "access denied",
    "无权",
    "未授权",
)

# Per-exception retry budget overrides.
#
# Some transient errors are retriable in principle but expensive to retry at
# the default budget. StreamChunkTimeoutError in particular fires after the
# upstream provider has already stalled for `stream_chunk_timeout` seconds
# (typically 120-240s); a full 3-attempt loop can therefore stack 6-12 minutes
# of dead air before surfacing the failure to the user. We keep exactly one
# retry (cheap reconnect that catches genuine transient TCP blips) and then
# fail fast — the same buffered payload is overwhelmingly likely to fail
# again at the upstream provider for the same reason.
#
# Keys are exception class *names* (not classes) so we don't introduce
# import-time coupling on optional dependencies like langchain-openai. The
# value is the absolute max attempt count, NOT additional retries — so a
# value of 2 means "1 first attempt + 1 retry" (the CR-requested
# "keep one retry" behavior).
_RETRY_BUDGET_OVERRIDES: dict[str, int] = {
    "StreamChunkTimeoutError": 2,
}

# Exception class names that indicate the upstream stream-chunk watchdog
# fired because the model stalled mid-flight. These deserve a more specific
# user-facing message than the generic "temporarily unavailable" copy,
# because the typical root cause is a long tool-call serialization stalling
# the upstream stream — and the most actionable advice we can give the user
# is "ask for a shorter / split output" rather than "wait and retry".
# Generic connection drops (httpx RemoteProtocolError / ReadError) are
# intentionally excluded: they routinely fire on transient network blips
# with normal payloads, where the "split the work" guidance is misleading.
_STREAM_DROP_EXCEPTIONS: frozenset[str] = frozenset(
    {
        "StreamChunkTimeoutError",
    }
)


class LLMErrorHandlingMiddleware(AgentMiddleware[AgentState]):
    """yyds: LLM 错误处理中间件 — API 挂了自动重试，挂太久拉闸省 token。

    一次完整的 LLM 调用经历什么？

      用户请求进来 → wrap_model_call 被调用
        │
        │  ── 熔断器检查 ──
        │  现在能调 API 吗？
        │  ├─ 跳闸中（open）→ 不调了，直接返回"暂时不可用"
        │  ├─ 跳闸超时了（half_open）→ 放一个探测请求试试
        │  └─ 正常（closed）→ 继续
        │
        │  ── 重试循环（最多 3 次）──
        │  调用 handler(request)（真正的 LLM API 调用）
        │  ├─ 成功了 → 重置熔断器 → 返回结果
        │  ├─ GraphBubbleUp → 必须透传（LangGraph 控制流信号）
        │  └─ 其他异常 → 这个错误能重试吗？
        │     ├─ 能重试 + 还有次数 → 算退避时间 → sleep → 继续循环
        │     └─ 不能重试 / 次数耗尽 → 返回友好错误消息
        │        ├─ quota → "配额不足，请检查账户"
        │        ├─ auth → "认证失败，请检查 API Key"
        │        ├─ busy/transient → "暂时不可用，请稍后继续"
        │        └─ generic → "请求失败: {detail}"
        │
        └─ 结束

    熔断器三态（像家里的电闸）：

      正常用电(CLOSED) → 连续跳闸 5 次 → 断电保护(OPEN) → 60秒后 → 试试来电(HALF_OPEN)
        ├─ 来电了 → 恢复正常(CLOSED)
        └─ 还是没电 → 继续断电(OPEN)，重新计时 60 秒

    Demo（时序举例）：

      假设 threshold=3, recovery_timeout=10s

      请求 1: 502 错误 → 重试 3 次都失败 → failure_count=1 → 返回"暂时不可用"
      请求 2: 502 错误 → 重试 3 次都失败 → failure_count=2 → 返回"暂时不可用"
      请求 3: 502 错误 → 重试 3 次都失败 → failure_count=3 ≥ threshold → 熔断器跳闸(OPEN)
      请求 4: 熔断中 → 直接返回"熔断器跳闸"（不调 API，省 token 省钱）
      请求 5: 熔断中 → 同上
      ... 10 秒后 ...
      请求 6: 超时到期 → 转入 HALF_OPEN → 放一个探测请求
        ├─ 如果成功 → 熔断器恢复(CLOSED)，failure_count 重置为 0
        └─ 如果失败 → 熔断器重新跳闸(OPEN)，重新计时 10s
    """

    retry_max_attempts: int = 3  # yyds: 最多重试 3 次（含首次调用，所以最多 sleep 2 次）
    retry_base_delay_ms: int = 1000  # yyds: 初始退避 1 秒
    retry_cap_delay_ms: int = 8000  # yyds: 退避上限 8 秒（实际公式: min(base*2^(attempt-1), cap)）

    def __init__(self, *, app_config: AppConfig, **kwargs: Any) -> None:
        # yyds: 从 config.yaml 的 circuit_breaker 段读取参数
        # failure_threshold: 连续失败多少次跳闸（默认 5）
        # recovery_timeout_sec: 跳闸后多久放一个探测请求（默认 60s）
        super().__init__(**kwargs)

        self.circuit_failure_threshold = app_config.circuit_breaker.failure_threshold
        self.circuit_recovery_timeout_sec = app_config.circuit_breaker.recovery_timeout_sec

        # yyds: 熔断器内部状态（线程安全，用 threading.Lock 保护）
        self._circuit_lock = threading.Lock()
        self._circuit_failure_count = 0  # yyds: 连续失败计数（成功时重置为 0）
        self._circuit_open_until = 0.0  # yyds: 跳闸截止时间戳（time.time() + recovery_timeout）
        self._circuit_state = "closed"  # yyds: 三态: "closed" | "open" | "half_open"
        self._circuit_probe_in_flight = False  # yyds: 半开状态下，是否已有探测请求在飞行中

    def _max_attempts_for(self, exc: BaseException) -> int:
        """Return the effective max attempt count for this exception.

        Falls back to `self.retry_max_attempts` unless the exception class name
        appears in the per-exception override table.
        """
        override = _RETRY_BUDGET_OVERRIDES.get(type(exc).__name__)
        if override is None:
            return self.retry_max_attempts

        return min(override, self.retry_max_attempts)

    def _check_circuit(self) -> bool:
        """yyds: 熔断器能调 API 吗？返回 True = 不行（跳闸中），False = 可以。

        例子：
          正常状态 → False（放行）
          跳闸中，还剩 30 秒 → True（快速失败）
          跳闸超时了 → False（放一个探测请求试试）

        五种情况：
          ① closed → 放行
          ② open + 未超时 → 快速失败
          ③ open + 已超时 → 转入 half_open
          ④ half_open + 探测中 → 快速失败（只允许一个探测）
          ⑤ half_open + 无探测 → 放行探测
        """
        with self._circuit_lock:
            now = time.time()

            # ② open + 未超时 → 快速失败
            if self._circuit_state == "open":
                if now < self._circuit_open_until:
                    return True  # yyds: 还在跳闸期，直接拒绝
                # ③ open + 已超时 → 转入 half_open，准备放探测
                self._circuit_state = "half_open"
                self._circuit_probe_in_flight = False

            # ④ half_open + 已有探测在飞 → 拒绝（只能放一个探测）
            if self._circuit_state == "half_open":
                if self._circuit_probe_in_flight:
                    return True
                # ⑤ 放行这个探测请求
                self._circuit_probe_in_flight = True
                return False

            # ① closed → 正常放行
            return False

    def _record_success(self) -> None:
        """yyds: 记录成功 — 熔断器完全恢复（一次成功就清零）。

        无论是 closed 还是 half_open，成功就意味着服务好了。
        这是最乐观的情况。
        """
        with self._circuit_lock:
            if self._circuit_state != "closed" or self._circuit_failure_count > 0:
                logger.info("Circuit breaker reset (Closed). LLM service recovered.")
            self._circuit_failure_count = 0
            self._circuit_open_until = 0.0
            self._circuit_state = "closed"
            self._circuit_probe_in_flight = False

    def _record_failure(self) -> None:
        """yyds: 记录失败 — 累计到阈值就跳闸。

        两种情况：
          ① half_open 探测失败了 → 直接回 open（服务还没好）
          ② closed 状态下失败了 → 累加计数，到 threshold 跳闸

        注意：只有可重试错误才调这里。配额/认证这种业务错误不计入，
        因为它们不是服务故障，重试也不会成功。
        """
        with self._circuit_lock:
            # ① half_open 探测失败 → 直接回 open
            if self._circuit_state == "half_open":
                self._circuit_open_until = time.time() + self.circuit_recovery_timeout_sec
                self._circuit_state = "open"
                self._circuit_probe_in_flight = False
                logger.error(
                    "Circuit breaker probe failed (Open). Will probe again after %ds.",
                    self.circuit_recovery_timeout_sec,
                )
                return

            # ② closed 状态 → 累加计数
            self._circuit_failure_count += 1
            if self._circuit_failure_count >= self.circuit_failure_threshold:
                self._circuit_open_until = time.time() + self.circuit_recovery_timeout_sec
                if self._circuit_state != "open":
                    self._circuit_state = "open"
                    self._circuit_probe_in_flight = False
                    logger.error(
                        "Circuit breaker tripped (Open). Threshold reached (%d). Will probe after %ds.",
                        self.circuit_failure_threshold,
                        self.circuit_recovery_timeout_sec,
                    )

    def _classify_error(self, exc: BaseException) -> tuple[bool, str]:
        """yyds: 这个错误能重试吗？— 核心决策函数。

        返回 (能不能重试, 错误类别)：
          (True,  "transient") — 超时/网络/5xx，能重试
          (True,  "busy")      — 服务繁忙/限速，能重试
          (False, "quota")     — 配额不足，别重试了（重试也不成功）
          (False, "auth")      — 认证失败，别重试了
          (False, "generic")   — 未知错误，保守起见不重试

        分类优先级（先匹配先生效）：
          ① quota（最高优先级！因为 429 可能是限速也可能是配额）
          ② auth
          ③ transient（已知异常类型 + 可重试状态码）
          ④ busy（繁忙关键词）
          ⑤ generic（兜底）

        例子：
          "insufficient_quota: ..." + status_code=429
            → ① 匹配 quota → (False, "quota")
            → 虽然 429 是可重试状态码，但配额不足重试也没用

          "server busy" + status_code=503
            → ①②不匹配 → ③ 503 是可重试状态码 → (True, "transient")

          "当前服务集群负载较高"
            → ①②③不匹配 → ④ 匹配"负载较高" → (True, "busy")
        """
        detail = _extract_error_detail(exc)
        lowered = detail.lower()
        error_code = _extract_error_code(exc)
        status_code = _extract_status_code(exc)

        # ① quota — 配额相关，优先级最高
        if _matches_any(lowered, _QUOTA_PATTERNS) or _matches_any(str(error_code).lower(), _QUOTA_PATTERNS):
            return False, "quota"
        # ② auth — 认证相关
        if _matches_any(lowered, _AUTH_PATTERNS):
            return False, "auth"

        # ③ transient — 已知异常类型名（按类名字符串匹配，不用 import httpx）
        exc_name = exc.__class__.__name__
        if exc_name in {

            "APITimeoutError",
            "APIConnectionError",
            "InternalServerError",
            "ReadError",  # httpx.ReadError: connection dropped mid-stream
            "RemoteProtocolError",  # httpx: server closed connection unexpectedly
            "StreamChunkTimeoutError",  # langchain-openai: chunk gap exceeded stream_chunk_timeout
            "APITimeoutError",  # yyds: OpenAI SDK 超时
            "APIConnectionError",  # yyds: OpenAI SDK 网络错误
            "ReadError",  # yyds: httpx 连接中断（流式响应时常见）
            "RemoteProtocolError",  # yyds: httpx 服务端关闭连接

        }:
            return True, "transient"
        # ③ transient — 可重试状态码
        if status_code in _RETRIABLE_STATUS_CODES:
            return True, "transient"
        # ④ busy — 繁忙关键词
        if _matches_any(lowered, _BUSY_PATTERNS):
            return True, "busy"

        # ⑤ generic — 兜底，保守起见不重试
        return False, "generic"

    def _build_retry_delay_ms(self, attempt: int, exc: BaseException) -> int:
        """yyds: 重试等多久？— 优先听服务端的，否则自己算指数退避。

        退避公式：min(base * 2^(attempt-1), cap)
        实际值：attempt=1 → 1s, attempt=2 → 2s, attempt=3 → 4s（封顶 8s）

        例子：
          服务端说 Retry-After: 2 → 等 2 秒（听它的）
          没说 → attempt=1 等 1 秒, attempt=2 等 2 秒
        """
        retry_after = _extract_retry_after_ms(exc)  # yyds: 优先听服务端的
        if retry_after is not None:
            return retry_after
        backoff = self.retry_base_delay_ms * (2 ** max(0, attempt - 1))
        return min(backoff, self.retry_cap_delay_ms)

    def _build_retry_message(self, attempt: int, wait_ms: int, reason: str) -> str:
        seconds = max(1, round(wait_ms / 1000))
        reason_text = "provider is busy" if reason == "busy" else "provider request failed temporarily"
        return f"LLM request retry {attempt}/{self.retry_max_attempts}: {reason_text}. Retrying in {seconds}s."

    def _build_circuit_breaker_message(self) -> str:
        return "The configured LLM provider is currently unavailable due to continuous failures. Circuit breaker is engaged to protect the system. Please wait a moment before trying again."

    def _build_error_fallback_message(
        self,
        content: str,
        *,
        error_type: str,
        reason: str,
        detail: str,
    ) -> AIMessage:
        return AIMessage(
            content=content,
            additional_kwargs={
                "deerflow_error_fallback": True,
                "error_type": error_type,
                "error_reason": reason,
                "error_detail": detail,
            },
        )

    def _build_user_message(self, exc: BaseException, reason: str) -> str:
        """yyds: 给用户看的错误消息 — 不暴露状态码、堆栈等技术细节。

        四种消息：
          quota → "配额不足，请检查账户"
          auth → "认证失败，请检查 API Key"
          busy/transient → "暂时不可用，请稍后继续"
          generic → "请求失败: {detail}"
        """
        detail = _extract_error_detail(exc)
        if reason == "quota":
            return "The configured LLM provider rejected the request because the account is out of quota, billing is unavailable, or usage is restricted. Please fix the provider account and try again."
        if reason == "auth":
            return "The configured LLM provider rejected the request because authentication or access is invalid. Please check the provider credentials and try again."
        if reason in {"busy", "transient"}:
            # Stream-drop failures (chunk-gap timeout, peer-closed connection,
            # raw read error) almost always point at a single oversized
            # tool-call payload — the model spent so long serializing JSON
            # arguments that the upstream provider buffered and the stream
            # gap exceeded `stream_chunk_timeout`. Surfacing this distinct
            # cause lets the user split or shorten their next request
            # instead of helplessly retrying the same prompt.
            if type(exc).__name__ in _STREAM_DROP_EXCEPTIONS:
                return (
                    "The model's streaming response was interrupted before it could "
                    "finish. This usually happens when a single response or tool call "
                    "is very large — please ask the assistant to split the work into "
                    "smaller steps, or shorten the requested output, and try again."
                )
            return "The configured LLM provider is temporarily unavailable after multiple retries. Please wait a moment and continue the conversation."
        return f"LLM request failed: {detail}"

    def _build_user_fallback_message(self, exc: BaseException, reason: str) -> AIMessage:
        return self._build_error_fallback_message(
            self._build_user_message(exc, reason),
            error_type=type(exc).__name__,
            reason=reason,
            detail=_extract_error_detail(exc),
        )

    def _emit_retry_event(self, attempt: int, wait_ms: int, reason: str) -> None:
        """yyds: 告诉前端"正在重试" — 通过 LangGraph 的 stream_writer 发事件。

        事件格式：{"type": "llm_retry", "attempt": 1, "max_attempts": 3, "wait_ms": 1000}
        如果不在 LangGraph 上下文中（比如单元测试），get_stream_writer() 会抛异常，静默忽略就行。
        """
        try:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()
            writer(
                {
                    "type": "llm_retry",
                    "attempt": attempt,
                    "max_attempts": self.retry_max_attempts,
                    "wait_ms": wait_ms,
                    "reason": reason,
                    "message": self._build_retry_message(attempt, wait_ms, reason),
                }
            )
        except Exception:
            logger.debug("Failed to emit llm_retry event", exc_info=True)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        """yyds: 同步版主入口 — 先看熔断器，再重试，最后降级。

        例子：
          正常：handler 成功 → _record_success() → 返回结果
          超时：handler 抛 APITimeoutError → 可重试 → 等 1s → 重试 → 成功
          配额用完：handler 抛 "insufficient_quota" → 不可重试 → 返回"配额不足"
          熔断中：_check_circuit() 返回 True → 直接返回"暂时不可用"

        特殊处理：
          GraphBubbleUp（LangGraph 控制流信号）→ 必须 raise 透传
          但如果是 half_open 探测中收到信号弹 → 重置 probe_in_flight
          否则下次请求会被"探测中"挡住，熔断器永远恢复不了
        """
        # ① 熔断器检查：跳闸中就不调 API 了
        if self._check_circuit():
            return self._build_error_fallback_message(
                self._build_circuit_breaker_message(),
                error_type="CircuitBreakerOpen",
                reason="circuit_open",
                detail="LLM circuit breaker is open",
            )

        # ② 重试循环
        attempt = 1
        while True:
            try:
                response = handler(request)  # yyds: 真正的 LLM API 调用
                self._record_success()  # yyds: 成功 → 熔断器恢复
                return response
            except GraphBubbleUp:
                # yyds: LangGraph 控制流信号，必须透传
                # 但要重置 probe_in_flight（探测请求被信号弹取消了，不算失败）
                with self._circuit_lock:
                    if self._circuit_state == "half_open":
                        self._circuit_probe_in_flight = False
                raise
            except Exception as exc:
                retriable, reason = self._classify_error(exc)

                max_attempts = self._max_attempts_for(exc)
                if retriable and attempt < max_attempts:
                    # yyds: 能重试 + 还有次数 → 等一会再来

                    wait_ms = self._build_retry_delay_ms(attempt, exc)
                    logger.warning(
                        "Transient LLM error on attempt %d/%d; retrying in %dms: %s",
                        attempt,
                        self.retry_max_attempts,
                        wait_ms,
                        _extract_error_detail(exc),
                    )
                    self._emit_retry_event(attempt, wait_ms, reason)  # yyds: 通知前端
                    time.sleep(wait_ms / 1000)
                    attempt += 1
                    continue
                # yyds: 不能重试 或 次数耗尽 → 记录失败 + 返回友好消息
                logger.warning(
                    "LLM call failed after %d attempt(s): %s",
                    attempt,
                    _extract_error_detail(exc),
                    exc_info=exc,
                )
                if retriable:

                    self._record_failure()
                return self._build_user_fallback_message(exc, reason)
                    self._record_failure()  # yyds: 只有可重试错误才计入熔断器


    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        """yyds: 异步版主入口 — 同上逻辑，asyncio.sleep 替代 time.sleep。"""
        # ① 熔断器检查
        if self._check_circuit():
            return self._build_error_fallback_message(
                self._build_circuit_breaker_message(),
                error_type="CircuitBreakerOpen",
                reason="circuit_open",
                detail="LLM circuit breaker is open",
            )

        # ② 重试循环
        attempt = 1
        while True:
            try:
                response = await handler(request)
                self._record_success()
                return response
            except GraphBubbleUp:
                with self._circuit_lock:
                    if self._circuit_state == "half_open":
                        self._circuit_probe_in_flight = False
                raise
            except Exception as exc:
                retriable, reason = self._classify_error(exc)
                max_attempts = self._max_attempts_for(exc)
                if retriable and attempt < max_attempts:
                    wait_ms = self._build_retry_delay_ms(attempt, exc)
                    logger.warning(
                        "Transient LLM error on attempt %d/%d; retrying in %dms: %s",
                        attempt,
                        self.retry_max_attempts,
                        wait_ms,
                        _extract_error_detail(exc),
                    )
                    self._emit_retry_event(attempt, wait_ms, reason)
                    await asyncio.sleep(wait_ms / 1000)  # yyds: 异步 sleep，不阻塞事件循环
                    attempt += 1
                    continue
                logger.warning(
                    "LLM call failed after %d attempt(s): %s",
                    attempt,
                    _extract_error_detail(exc),
                    exc_info=exc,
                )
                if retriable:
                    self._record_failure()
                return self._build_user_fallback_message(exc, reason)


# ─── yyds: 辅助函数 — 从各种 Provider 的异常结构里提取错误信息 ───
# 这些函数处理多种 Provider 的异常结构差异：
# OpenAI:   exc.status_code=429, exc.code="insufficient_quota"
# Anthropic: exc.status_code=429, exc.body={"error": {"type": "rate_limit_error"}}
# DeepSeek:  exc.message="服务繁忙", exc.status_code=503
# 智谱:      exc.message="余额不足"
# httpx:     exc.response.status_code=502, exc.response.headers={"Retry-After": "2"}


def _matches_any(detail: str, patterns: tuple[str, ...]) -> bool:
    """yyds: 子串匹配 — detail 里有没有 patterns 中的任一关键词（调用前已 lower()）。"""
    return any(pattern in detail for pattern in patterns)


def _extract_error_code(exc: BaseException) -> Any:
    """yyds: 从异常里挖错误码 — 试三个地方：exc.code / exc.error_code / exc.body.error。

    不同 Provider 放的位置不一样：
      ① exc.code（OpenAI SDK 直接属性）
      ② exc.error_code（部分 SDK 用这个名字）
      ③ exc.body.error.code / exc.body.error.type（嵌套结构）
    """
    for attr in ("code", "error_code"):
        value = getattr(exc, attr, None)
        if value not in (None, ""):
            return value

    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            for key in ("code", "type"):
                value = error.get(key)
                if value not in (None, ""):
                    return value
    return None


def _extract_status_code(exc: BaseException) -> int | None:
    """yyds: 从异常里挖 HTTP 状态码 — 试两个地方。

    ① exc.status_code（OpenAI SDK：直接属性）
    ② exc.response.status_code（httpx：嵌套在 response 里）
    """
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _extract_retry_after_ms(exc: BaseException) -> int | None:
    """yyds: 从响应头里挖 Retry-After — 三种格式都支持。

    ① 秒数：Retry-After: 2 → 2000ms
    ② 毫秒：retry-after-ms: 1500 → 1500ms
    ③ HTTP 日期：Retry-After: Fri, 14 May 2026 12:00:00 GMT → 算差值转毫秒
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None

    raw = None
    header_name = ""
    for key in ("retry-after-ms", "Retry-After-Ms", "retry-after", "Retry-After"):
        header_name = key
        if hasattr(headers, "get"):
            raw = headers.get(key)
        if raw:
            break
    if not raw:
        return None

    # ①② 数值格式
    try:
        multiplier = 1 if "ms" in header_name.lower() else 1000
        return max(0, int(float(raw) * multiplier))
    except (TypeError, ValueError):
        # ③ HTTP 日期格式
        try:
            target = parsedate_to_datetime(str(raw))
            delta = target.timestamp() - time.time()
            return max(0, int(delta * 1000))
        except (TypeError, ValueError, OverflowError):
            return None


def _extract_error_detail(exc: BaseException) -> str:
    """yyds: 从异常里挖可读描述 — 试三个地方。

    ① str(exc) — 大多数异常这就够了
    ② exc.message — 有些 SDK（如智谱）错误消息在这里，str() 反而是空的
    ③ exc.__class__.__name__ — 兜底，至少知道异常类型（如 "ReadError"）
    """
    detail = str(exc).strip()
    if detail:
        return detail
    message = getattr(exc, "message", None)
    if isinstance(message, str) and message.strip():
        return message.strip()
    return exc.__class__.__name__
