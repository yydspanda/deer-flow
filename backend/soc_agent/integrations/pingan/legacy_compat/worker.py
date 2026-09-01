"""Lease-owned worker that joins legacy ingress to the canonical SOC Runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from threading import Event, Lock, Thread
from typing import Any, Protocol

from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AnalysisRun,
    AnalysisRunStatus,
    EntrySurface,
    ProcessingJobStatus,
    ServiceRequestContext,
    SocActionExecutionRecord,
    SocCallbackOutboxSubmission,
    SocDecisionTransitionRecord,
    SocProcessingJob,
)
from soc_agent.integrations.pingan.legacy_compat.contracts import (
    PINGAN_LEGACY_QUEUE_NAME,
    PingAnAlertLifecycleState,
)
from soc_agent.integrations.pingan.legacy_compat.result_mapper import (
    PingAnLegacyResultMapper,
)
from soc_agent.integrations.pingan.legacy_compat.zeus_lifecycle import (
    PingAnAlertLifecycleService,
)
from soc_agent.protocols import ProcessingJobRepository


class PingAnAnalysisServicePort(Protocol):
    def analyze(
        self,
        payload: Mapping[str, Any],
        *,
        context: ServiceRequestContext,
    ) -> AnalysisRun: ...


class PingAnDecisionLineagePort(Protocol):
    def list_decision_transitions(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        limit: int = 100,
    ) -> list[SocDecisionTransitionRecord]: ...

    def list_action_executions(
        self,
        *,
        run_id: str | None = None,
        authorization_id: str | None = None,
        limit: int = 100,
    ) -> list[SocActionExecutionRecord]: ...


class PingAnLegacyJobWorker:
    def __init__(
        self,
        *,
        repository: ProcessingJobRepository,
        lifecycle_service: PingAnAlertLifecycleService,
        analysis_service: PingAnAnalysisServicePort,
        unknown_lifecycle_analysis_service: PingAnAnalysisServicePort | None = None,
        result_mapper: PingAnLegacyResultMapper,
        worker_id: str,
        lineage_repository: PingAnDecisionLineagePort | None = None,
        queue_name: str = PINGAN_LEGACY_QUEUE_NAME,
        lease_seconds: int = 900,
        max_attempts: int = 3,
        retry_backoff_seconds: int = 30,
        heartbeat_interval_seconds: float | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if lease_seconds < 1 or max_attempts < 1 or retry_backoff_seconds < 0:
            raise ValueError("worker lease/retry settings are invalid")
        self._repository = repository
        self._lifecycle = lifecycle_service
        self._analysis = analysis_service
        self._unknown_lifecycle_analysis = unknown_lifecycle_analysis_service or analysis_service
        self._mapper = result_mapper
        self._worker_id = worker_id
        self._lineage = lineage_repository
        self._queue_name = queue_name
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        resolved_heartbeat = min(60.0, max(0.25, lease_seconds / 3)) if heartbeat_interval_seconds is None else heartbeat_interval_seconds
        if resolved_heartbeat <= 0 or resolved_heartbeat >= lease_seconds:
            raise ValueError("worker heartbeat interval must be positive and shorter than the lease")
        self._heartbeat_interval_seconds = resolved_heartbeat
        self._now = now or (lambda: datetime.now(UTC))

    def run_once(self) -> SocProcessingJob | None:
        now = self._now()
        self._repository.recover_expired_leases(now=now)
        job = self._repository.claim_next(
            queue_name=self._queue_name,
            worker_id=self._worker_id,
            lease_seconds=self._lease_seconds,
            now=now,
        )
        if job is None:
            return None
        try:
            return self._process(job)
        except Exception as exc:
            return self._handle_failure(job.job_id, exc)

    def _process(self, job: SocProcessingJob) -> SocProcessingJob:
        now = self._now()
        if job.expires_at is not None and job.expires_at <= now:
            result = self._mapper.project_queue_expired(
                alert_id=job.alert_id or "",
                elapsed_seconds=(now - job.created_at).total_seconds(),
                model_name=job.model_name,
            )
            expired, _callback = self._repository.complete_with_callback(
                job.job_id,
                worker_id=self._worker_id,
                expected_status=ProcessingJobStatus.CLAIMED,
                target_status=ProcessingJobStatus.EXPIRED_BEFORE_ANALYSIS,
                event_type="queue_deadline_expired",
                result_payload=result,
                callback=_success_callback(job.job_id, result),
                now=now,
                details={
                    "expired_before_precheck": True,
                    "execute_type": job.execution_type,
                },
            )
            return expired
        current = self._repository.transition(
            job.job_id,
            worker_id=self._worker_id,
            expected_status=ProcessingJobStatus.CLAIMED,
            target_status=ProcessingJobStatus.PRECHECKING,
            event_type="external_lifecycle_precheck_started",
            now=now,
        )
        lifecycle = self._lifecycle.check(job.alert_id or "")
        if lifecycle.state is PingAnAlertLifecycleState.HANDLED:
            result = self._mapper.project_external_handled(
                alert_id=job.alert_id or "",
                provider_status=lifecycle.provider_status,
                reason=lifecycle.reason or "ZEUS alert was already handled",
            )
            completed, _callback = self._repository.complete_with_callback(
                current.job_id,
                worker_id=self._worker_id,
                expected_status=ProcessingJobStatus.PRECHECKING,
                target_status=ProcessingJobStatus.SKIPPED_EXTERNAL_HANDLED,
                event_type="external_lifecycle_handled",
                result_payload=result,
                callback=_success_callback(current.job_id, result),
                now=self._now(),
                details={"lifecycle": lifecycle.model_dump(mode="json")},
            )
            return completed

        current = self._repository.transition(
            current.job_id,
            worker_id=self._worker_id,
            expected_status=ProcessingJobStatus.PRECHECKING,
            target_status=ProcessingJobStatus.ANALYZING,
            event_type="analysis_started",
            now=self._now(),
            details={"lifecycle": lifecycle.model_dump(mode="json")},
        )
        analysis_service = self._analysis if lifecycle.state is PingAnAlertLifecycleState.PENDING else self._unknown_lifecycle_analysis
        heartbeat = _ProcessingLeaseHeartbeat(
            repository=self._repository,
            job_id=current.job_id,
            worker_id=self._worker_id,
            lease_seconds=self._lease_seconds,
            interval_seconds=self._heartbeat_interval_seconds,
            now=self._now,
        )
        heartbeat.start()
        try:
            run = analysis_service.analyze(
                current.input_payload,
                context=ServiceRequestContext(
                    request_id=f"REQ-{current.job_id}",
                    trace_id=current.job_id,
                    idempotency_key=f"processing-job:{current.job_id}:analysis",
                    actor=ActorContext(
                        actor_id="soc-pingan-worker",
                        actor_type=ActorType.SERVICE,
                        surface=EntrySurface.DAEMON,
                        roles=["soc_runtime_worker"],
                    ),
                ),
            )
        finally:
            heartbeat.stop()
        heartbeat.raise_if_failed()
        current = self._repository.transition(
            current.job_id,
            worker_id=self._worker_id,
            expected_status=ProcessingJobStatus.ANALYZING,
            target_status=ProcessingJobStatus.PROJECTING,
            event_type="legacy_projection_started",
            run_id=run.run_id,
            now=self._now(),
        )
        if run.status is AnalysisRunStatus.FAILED or run.decision is None:
            failure_code = run.failure.kind.value if run.failure is not None else "analysis_failed"
            failure_message = run.failure.message if run.failure is not None else "SOC Runtime did not produce a decision"
            result = {
                "errorCode": failure_code,
                "errorMessage": failure_message,
            }
            failed, _callback = self._repository.complete_with_callback(
                current.job_id,
                worker_id=self._worker_id,
                expected_status=ProcessingJobStatus.PROJECTING,
                target_status=ProcessingJobStatus.FAILED,
                event_type="analysis_failed",
                result_payload=result,
                callback=_failure_callback(
                    current.job_id,
                    failure_code,
                    failure_message,
                ),
                error_code=failure_code,
                error_message=failure_message,
                now=self._now(),
            )
            return failed

        transitions = (
            self._lineage.list_decision_transitions(
                run_id=run.run_id,
                limit=100,
            )
            if self._lineage is not None
            else []
        )
        executions = (
            self._lineage.list_action_executions(
                run_id=run.run_id,
                limit=100,
            )
            if self._lineage is not None
            else []
        )
        result = self._mapper.project(
            run,
            decision_transitions=transitions,
            action_executions=executions,
        )
        result["soc_lineage"]["external_lifecycle_state"] = lifecycle.state.value
        completed, _callback = self._repository.complete_with_callback(
            current.job_id,
            worker_id=self._worker_id,
            expected_status=ProcessingJobStatus.PROJECTING,
            target_status=ProcessingJobStatus.COMPLETED,
            event_type="completed",
            result_payload=result,
            callback=_success_callback(current.job_id, result),
            now=self._now(),
        )
        return completed

    def _handle_failure(
        self,
        job_id: str,
        exc: Exception,
    ) -> SocProcessingJob:
        current = self._repository.get(job_id)
        if current is None:
            raise exc
        if current.status.is_terminal:
            return current
        if current.lease_owner != self._worker_id:
            raise exc
        error_code = type(exc).__name__
        error_message = "processing failed at a guarded execution boundary"
        if current.attempt_count < self._max_attempts:
            return self._repository.transition(
                current.job_id,
                worker_id=self._worker_id,
                expected_status=current.status,
                target_status=ProcessingJobStatus.QUEUED,
                event_type="retry_scheduled",
                error_code=error_code,
                error_message=error_message,
                available_at=self._now() + timedelta(seconds=self._retry_backoff_seconds),
                now=self._now(),
            )
        result = {
            "errorCode": error_code,
            "errorMessage": error_message,
        }
        failed, _callback = self._repository.complete_with_callback(
            current.job_id,
            worker_id=self._worker_id,
            expected_status=current.status,
            target_status=ProcessingJobStatus.FAILED,
            event_type="retry_budget_exhausted",
            result_payload=result,
            callback=_failure_callback(
                current.job_id,
                error_code,
                error_message,
            ),
            error_code=error_code,
            error_message=error_message,
            now=self._now(),
        )
        return failed


class _ProcessingLeaseHeartbeat:
    """Keep one analysis lease alive while the synchronous model call runs."""

    def __init__(
        self,
        *,
        repository: ProcessingJobRepository,
        job_id: str,
        worker_id: str,
        lease_seconds: int,
        interval_seconds: float,
        now: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._job_id = job_id
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._interval_seconds = interval_seconds
        self._now = now
        self._stop = Event()
        self._lock = Lock()
        self._failure: Exception | None = None
        self._thread = Thread(
            target=self._run,
            name=f"soc-job-heartbeat-{job_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def raise_if_failed(self) -> None:
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise failure

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self._repository.renew_lease(
                    self._job_id,
                    worker_id=self._worker_id,
                    expected_status=ProcessingJobStatus.ANALYZING,
                    lease_seconds=self._lease_seconds,
                    now=self._now(),
                )
            except Exception as exc:
                with self._lock:
                    self._failure = exc
                self._stop.set()
                return


def _success_callback(
    job_id: str,
    result: dict[str, Any],
) -> SocCallbackOutboxSubmission:
    return SocCallbackOutboxSubmission(
        destination="pingan.zeus.alert_callback",
        idempotency_key=f"pingan:alert-callback:{job_id}",
        payload={"taskId": job_id, "status": "SUCCESS", "result": result},
    )


def _failure_callback(
    job_id: str,
    error_code: str,
    error_message: str,
) -> SocCallbackOutboxSubmission:
    return SocCallbackOutboxSubmission(
        destination="pingan.zeus.alert_callback",
        idempotency_key=f"pingan:alert-callback:{job_id}",
        payload={
            "taskId": job_id,
            "status": "FAILURE",
            "errorCode": error_code,
            "errorMessage": error_message,
        },
    )


__all__: Sequence[str] = (
    "PingAnAnalysisServicePort",
    "PingAnDecisionLineagePort",
    "PingAnLegacyJobWorker",
)
