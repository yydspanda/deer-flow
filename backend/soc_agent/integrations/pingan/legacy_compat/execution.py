"""Small process supervisor for durable analysis workers and callbacks."""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence

from soc_agent.integrations.pingan.legacy_compat.callback import (
    PingAnLegacyCallbackDispatcher,
)
from soc_agent.integrations.pingan.legacy_compat.worker import (
    PingAnLegacyJobWorker,
)

logger = logging.getLogger(__name__)


class PingAnLegacyExecutionSupervisor:
    """Run bounded workers and the callback outbox as independent consumers."""

    def __init__(
        self,
        *,
        workers: Sequence[PingAnLegacyJobWorker],
        callback_dispatcher: PingAnLegacyCallbackDispatcher,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        if not workers:
            raise ValueError("at least one processing worker is required")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._workers = tuple(workers)
        self._callback = callback_dispatcher
        self._poll_interval_seconds = poll_interval_seconds

    def run_forever(self, *, stop_event: threading.Event) -> None:
        threads = [
            threading.Thread(
                target=self._run_worker,
                args=(worker, stop_event),
                name=f"soc-pingan-worker-{index + 1}",
                daemon=True,
            )
            for index, worker in enumerate(self._workers)
        ]
        threads.append(
            threading.Thread(
                target=self._run_callback,
                args=(stop_event,),
                name="soc-pingan-callback",
                daemon=True,
            )
        )
        for thread in threads:
            thread.start()
        try:
            while not stop_event.wait(0.5):
                continue
        finally:
            stop_event.set()
            for thread in threads:
                thread.join()

    def drain_until_idle(self, *, idle_rounds: int = 2, max_rounds: int = 100) -> int:
        """Deterministically drain fake/acceptance work without daemon threads."""

        if idle_rounds < 1 or max_rounds < 1:
            raise ValueError("drain limits must be positive")
        idle = 0
        processed = 0
        for _ in range(max_rounds):
            progressed = False
            for worker in self._workers:
                if worker.run_once() is not None:
                    progressed = True
                    processed += 1
            if self._callback.run_once() is not None:
                progressed = True
                processed += 1
            if progressed:
                idle = 0
            else:
                idle += 1
                if idle >= idle_rounds:
                    return processed
        raise RuntimeError("legacy execution plane did not become idle")

    def _run_worker(
        self,
        worker: PingAnLegacyJobWorker,
        stop_event: threading.Event,
    ) -> None:
        while not stop_event.is_set():
            try:
                result = worker.run_once()
            except Exception:
                logger.exception("PingAn processing worker iteration failed")
                result = None
            if result is None:
                stop_event.wait(self._poll_interval_seconds)

    def _run_callback(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                result = self._callback.run_once()
            except Exception:
                logger.exception("PingAn callback dispatcher iteration failed")
                result = None
            if result is None:
                stop_event.wait(self._poll_interval_seconds)


__all__ = ["PingAnLegacyExecutionSupervisor"]
