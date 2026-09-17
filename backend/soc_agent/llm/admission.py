"""Process-local admission control for bounded SOC model calls."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import ContextVar
from itertools import count
from math import isfinite
from threading import Condition, Lock
from time import monotonic
from typing import Literal

_model_workload: ContextVar[Literal["interactive", "background"]] = ContextVar("soc_model_workload", default="interactive")


@contextmanager
def soc_model_workload(kind: Literal["interactive", "background"]) -> Iterator[None]:
    """Set by the server dispatcher, not from model output or request priority."""
    if kind not in {"interactive", "background"}:
        raise ValueError("unknown SOC model workload")
    token = _model_workload.set(kind)
    try:
        yield
    finally:
        _model_workload.reset(token)


class SocLLMAdmissionError(RuntimeError):
    """Raised when a model call cannot enter the configured local budget."""


class _AdmissionSlot:
    def __init__(self, controller: SocLLMAdmissionController):
        self._controller = controller
        self._future: Future | None = None
        self._closed = False
        self._released = False

    def release_after(self, future: Future) -> None:
        """An uncancellable timed-out invocation must keep its capacity slot."""
        with self._controller._condition:
            if self._closed or self._future is not None:
                raise RuntimeError("admission slot already bound or closed")
            self._future = future
        future.add_done_callback(lambda _: self._try_release())

    def close(self) -> None:
        with self._controller._condition:
            self._closed = True
            self._try_release()

    def _try_release(self) -> None:
        with self._controller._condition:
            if self._released or not self._closed or (self._future is not None and not self._future.done()):
                return
            self._released = True
            self._controller._active -= 1
            self._controller._condition.notify_all()


class SocLLMAdmissionController:
    """Bound concurrent calls and optional requests-per-minute per process."""

    def __init__(
        self,
        *,
        max_concurrency: int = 1,
        requests_per_minute: int = 0,
        acquire_timeout_seconds: float = 5.0,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if requests_per_minute < 0:
            raise ValueError("requests_per_minute must be >= 0")
        if not isfinite(acquire_timeout_seconds) or acquire_timeout_seconds < 0:
            raise ValueError("acquire_timeout_seconds must be a finite number >= 0")
        self._condition = Condition()
        self._max_concurrency = max_concurrency
        self._active = 0
        self._waiting: list[tuple[int, int]] = []
        self._tickets = count()
        self._requests_per_minute = requests_per_minute
        self._acquire_timeout_seconds = acquire_timeout_seconds
        self._request_times: deque[float] = deque()
        self._rate_lock = Lock()

    @contextmanager
    def admit(self) -> Iterator[_AdmissionSlot]:
        deadline = monotonic() + self._acquire_timeout_seconds
        with self._condition:
            ticket = (0 if _model_workload.get() == "interactive" else 1, next(self._tickets))
            self._waiting.append(ticket)
            try:
                while self._active >= self._max_concurrency or ticket != min(self._waiting):
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise SocLLMAdmissionError("SOC LLM concurrency limit is saturated")
                    self._condition.wait(remaining)
                self._active += 1
            finally:
                self._waiting.remove(ticket)
                self._condition.notify_all()
        slot = _AdmissionSlot(self)
        try:
            self._reserve_rate_slot()
            yield slot
        finally:
            slot.close()

    def _reserve_rate_slot(self) -> None:
        if self._requests_per_minute == 0:
            return
        now = monotonic()
        cutoff = now - 60.0
        with self._rate_lock:
            while self._request_times and self._request_times[0] <= cutoff:
                self._request_times.popleft()
            if len(self._request_times) >= self._requests_per_minute:
                raise SocLLMAdmissionError("SOC LLM requests-per-minute limit is exhausted")
            self._request_times.append(now)


__all__ = ["SocLLMAdmissionController", "SocLLMAdmissionError", "soc_model_workload"]
