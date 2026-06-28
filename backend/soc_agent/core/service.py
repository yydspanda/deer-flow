"""Stable public service entry points for SOC Agent use cases."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from soc_agent.contracts import (
    AnalysisRun,
    AnalysisRunStatus,
    ServiceRequestContext,
    SocEvent,
    SocEventType,
)
from soc_agent.core.runtime import analyze_alert
from soc_agent.protocols import AlertRepository, AnalysisRuntime, SocEventSink


class SocServiceError(RuntimeError):
    """Base error for service-layer failures."""


class SocServiceNotImplementedError(SocServiceError):
    """Raised when a planned service operation has no Phase 1 implementation."""


class SocServiceNotFoundError(SocServiceError):
    """Raised when a requested SOC resource does not exist."""


class DeterministicAnalysisRuntime:
    """Adapter that exposes the current deterministic runtime as a protocol."""

    def analyze(self, payload: Mapping[str, Any]) -> AnalysisRun:
        return analyze_alert(payload)


class NoopEventSink:
    """Default event sink used until an entry adapter attaches subscribers."""

    def emit(self, event: SocEvent) -> None:
        return None


class SocAnalysisService:
    """Application service used by DeerFlow-aligned SOC entry adapters.

    TUI/headless CLI, Gateway API, Web UI, IM channels, and background ingestion
    call this service instead of directly assembling pipeline steps or touching
    repositories/adapters.
    """

    def __init__(
        self,
        *,
        runtime: AnalysisRuntime | None = None,
        repository: AlertRepository | None = None,
        event_sink: SocEventSink | None = None,
    ) -> None:
        self._runtime = runtime or DeterministicAnalysisRuntime()
        self._repository = repository
        self._event_sink = event_sink or NoopEventSink()

    def analyze(
        self,
        payload: Mapping[str, Any],
        *,
        context: ServiceRequestContext | None = None,
    ) -> AnalysisRun:
        request_context = context or ServiceRequestContext()
        return self._analyze(payload, context=request_context)

    def get_run(self, run_id: str) -> AnalysisRun | None:
        if self._repository is None:
            raise SocServiceNotImplementedError("get_run requires an AlertRepository")
        return self._repository.get_run(run_id)

    def replay(
        self,
        run_id: str,
        *,
        context: ServiceRequestContext | None = None,
    ) -> AnalysisRun:
        if self._repository is None:
            raise SocServiceNotImplementedError("replay requires an AlertRepository")
        previous = self._repository.get_run(run_id)
        if previous is None:
            raise SocServiceNotFoundError(f"run {run_id} not found")
        if previous.input_payload is None:
            raise SocServiceNotImplementedError(f"run {run_id} has no replayable input payload")

        request_context = context or ServiceRequestContext()
        return self._analyze(previous.input_payload, context=request_context, replay_of_run_id=run_id)

    def _analyze(
        self,
        payload: Mapping[str, Any],
        *,
        context: ServiceRequestContext,
        replay_of_run_id: str | None = None,
    ) -> AnalysisRun:
        self._emit(
            SocEvent(
                event_type=SocEventType.ANALYSIS_REQUESTED,
                request_id=context.request_id,
                actor=context.actor,
                payload={
                    "surface": context.actor.surface.value,
                    "replay_of_run_id": replay_of_run_id,
                },
            )
        )

        run = self._runtime.analyze(payload)
        run.replay_of_run_id = replay_of_run_id
        if self._repository is not None:
            self._repository.save_run(run)

        self._emit(
            SocEvent(
                event_type=_completion_event_type(run),
                request_id=context.request_id,
                run_id=run.run_id,
                alert_id=run.alert_id,
                actor=context.actor,
                payload={
                    "status": run.status.value,
                    "trace_id": context.trace_id,
                    "idempotency_key": context.idempotency_key,
                    "replay_of_run_id": replay_of_run_id,
                },
            )
        )
        return run

    def _emit(self, event: SocEvent) -> None:
        self._event_sink.emit(event)


class SocReviewService:
    """Review queue and correction service placeholder."""

    def correct(self, *args: Any, **kwargs: Any) -> None:
        raise SocServiceNotImplementedError("correction workflow is planned after persistence is implemented")


class SocMemoryService:
    """Facts and lessons service placeholder."""

    def list_facts(self) -> list[Any]:
        raise SocServiceNotImplementedError("memory store is planned after PostgreSQL persistence is implemented")


class SocDaemonService:
    """Kafka worker orchestration service placeholder."""

    def start(self) -> None:
        raise SocServiceNotImplementedError("daemon mode is planned for Phase 4")


class SocAgentChatService:
    """Interactive investigation service placeholder for TUI/Web UI."""

    def send_message(self, *args: Any, **kwargs: Any) -> None:
        raise SocServiceNotImplementedError("agent chat is planned after review/replay primitives stabilize")


def _completion_event_type(run: AnalysisRun) -> SocEventType:
    if run.status is AnalysisRunStatus.FAILED:
        return SocEventType.ANALYSIS_FAILED
    return SocEventType.ANALYSIS_COMPLETED
