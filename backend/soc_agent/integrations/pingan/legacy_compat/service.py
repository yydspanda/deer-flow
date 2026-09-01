"""Application service for the legacy ZEUS asynchronous-task surface."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from soc_agent.contracts import (
    ProcessingJobStatus,
    SocProcessingJob,
    SocProcessingJobSubmission,
)
from soc_agent.integrations.pingan.legacy_compat.contracts import (
    PingAnLegacyTaskRequest,
    PingAnLegacyTaskResponse,
    extract_pingan_legacy_task_metadata,
    project_legacy_task_status,
)
from soc_agent.protocols import ProcessingJobRepository


class PingAnLegacyTaskNotFoundError(LookupError):
    pass


class PingAnLegacyTaskService:
    """Translate old task requests into durable generic processing jobs."""

    def __init__(
        self,
        *,
        repository: ProcessingJobRepository,
        queue_ttl_seconds: int = 1800,
    ) -> None:
        if queue_ttl_seconds < 1:
            raise ValueError("queue_ttl_seconds must be >= 1")
        self._repository = repository
        self._queue_ttl_seconds = queue_ttl_seconds

    def submit(
        self,
        request: PingAnLegacyTaskRequest,
        *,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> PingAnLegacyTaskResponse:
        observed_at = now or datetime.now(UTC)
        metadata = extract_pingan_legacy_task_metadata(request)
        stable_key = _submission_idempotency_key(
            request,
            explicit=idempotency_key,
        )
        expires_at = observed_at + timedelta(seconds=self._queue_ttl_seconds) if metadata.execute_type in {"1", "3"} else None
        job, _created = self._repository.submit(
            SocProcessingJobSubmission(
                tenant_id="pingan",
                workload_kind="alert_analysis",
                queue_name=metadata.queue_name,
                idempotency_key=stable_key,
                external_ref=f"zeus:{request.alert_id}",
                alert_id=request.alert_id,
                detection_key=metadata.detection_key,
                execution_type=metadata.execute_type,
                model_name=metadata.model_name,
                priority=metadata.priority,
                input_payload=request.alert_data or {},
                expires_at=expires_at,
                metadata=metadata.model_dump(mode="json"),
            ),
            now=observed_at,
        )
        return _response_from_job(job)

    def get_status(self, task_id: str) -> PingAnLegacyTaskResponse:
        return _response_from_job(self.get_job(task_id))

    def get_job(self, task_id: str) -> SocProcessingJob:
        job = self._repository.get(task_id)
        if job is None:
            raise PingAnLegacyTaskNotFoundError(f"legacy task {task_id} not found")
        return job


def _response_from_job(job: SocProcessingJob) -> PingAnLegacyTaskResponse:
    legacy_status = project_legacy_task_status(job.status)
    result = job.result_payload
    if legacy_status == "FAILURE" and result is None:
        result = {
            "errorCode": job.error_code or "SOC_PROCESSING_FAILED",
            "errorMessage": job.error_message or "SOC processing failed",
        }
    elif job.status is ProcessingJobStatus.EXPIRED_BEFORE_ANALYSIS and result is None:
        result = {
            "alert_action": "过期",
            "alert_rationale": "任务在开始研判前超过排队时限。",
        }
    return PingAnLegacyTaskResponse(
        id=job.job_id,
        status=legacy_status,
        result=result,
    )


def _submission_idempotency_key(
    request: PingAnLegacyTaskRequest,
    *,
    explicit: str | None,
) -> str:
    if explicit is not None and explicit.strip():
        normalized = explicit.strip()
        if len(normalized) > 400:
            raise ValueError("idempotency key must not exceed 400 characters")
        return f"pingan:{request.app_code}:caller:{normalized}"
    payload = request.model_dump(mode="json")
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"pingan:{request.app_code}:derived:{hashlib.sha256(encoded).hexdigest()}"


__all__ = [
    "PingAnLegacyTaskNotFoundError",
    "PingAnLegacyTaskService",
]
