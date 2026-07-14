"""Process-local admission control for bounded SOC model calls."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from math import isfinite
from threading import BoundedSemaphore, Lock
from time import monotonic


class SocLLMAdmissionError(RuntimeError):
    """Raised when a model call cannot enter the configured local budget."""


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
        self._semaphore = BoundedSemaphore(max_concurrency)
        self._requests_per_minute = requests_per_minute
        self._acquire_timeout_seconds = acquire_timeout_seconds
        self._request_times: deque[float] = deque()
        self._rate_lock = Lock()

    @contextmanager
    def admit(self) -> Iterator[None]:
        acquired = self._semaphore.acquire(timeout=self._acquire_timeout_seconds)
        if not acquired:
            raise SocLLMAdmissionError("SOC LLM concurrency limit is saturated")
        try:
            self._reserve_rate_slot()
            yield
        finally:
            self._semaphore.release()

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


__all__ = ["SocLLMAdmissionController", "SocLLMAdmissionError"]
