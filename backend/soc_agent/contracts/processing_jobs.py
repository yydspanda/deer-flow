"""Vendor-neutral contracts for durable SOC background processing."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProcessingJobStatus(StrEnum):
    """Internal state; external adapters own any legacy status projection."""

    QUEUED = "queued"
    CLAIMED = "claimed"
    PRECHECKING = "prechecking"
    ANALYZING = "analyzing"
    PROJECTING = "projecting"
    COMPLETED = "completed"
    SKIPPED_EXTERNAL_HANDLED = "skipped_external_handled"
    EXPIRED_BEFORE_ANALYSIS = "expired_before_analysis"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in {
            ProcessingJobStatus.COMPLETED,
            ProcessingJobStatus.SKIPPED_EXTERNAL_HANDLED,
            ProcessingJobStatus.EXPIRED_BEFORE_ANALYSIS,
            ProcessingJobStatus.FAILED,
        }


ACTIVE_PROCESSING_JOB_STATUSES = frozenset(
    {
        ProcessingJobStatus.CLAIMED,
        ProcessingJobStatus.PRECHECKING,
        ProcessingJobStatus.ANALYZING,
        ProcessingJobStatus.PROJECTING,
    }
)


class CallbackOutboxStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    RETRY_WAIT = "retry_wait"
    DELIVERED = "delivered"
    DEAD_LETTER = "dead_letter"


class CallbackAttemptOutcome(StrEnum):
    """Append-only outcome of one external callback delivery attempt."""

    DELIVERED = "delivered"
    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTER = "dead_letter"
    LEASE_EXPIRED = "lease_expired"


class SocProcessingJobSubmission(BaseModel):
    """One durable job request after an entry adapter has translated its source."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str | None = Field(default=None, max_length=128)
    workload_kind: str = Field(min_length=1, max_length=64)
    queue_name: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=512)
    external_ref: str | None = Field(default=None, max_length=256)
    alert_id: str | None = Field(default=None, max_length=128)
    detection_key: str | None = Field(default=None, max_length=256)
    execution_type: str | None = Field(default=None, max_length=64)
    model_name: str | None = Field(default=None, max_length=128)
    priority: int = Field(default=5, ge=0, le=9)
    input_payload: dict[str, Any]
    available_at: datetime | None = None
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "tenant_id",
        "workload_kind",
        "queue_name",
        "idempotency_key",
        "external_ref",
        "alert_id",
        "detection_key",
        "execution_type",
        "model_name",
        mode="before",
    )
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class SocProcessingJob(BaseModel):
    """Current durable state of one background processing job."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "soc.processing_job.v1"
    job_id: str
    tenant_id: str | None = None
    workload_kind: str
    queue_name: str
    status: ProcessingJobStatus
    idempotency_key: str
    external_ref: str | None = None
    alert_id: str | None = None
    detection_key: str | None = None
    execution_type: str | None = None
    model_name: str | None = None
    priority: int = Field(ge=0, le=9)
    payload_sha256: str = Field(min_length=64, max_length=64)
    input_payload: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    run_id: str | None = None
    result_payload: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    attempt_count: int = Field(default=0, ge=0)
    version: int = Field(default=1, ge=1)
    available_at: datetime
    expires_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class SocProcessingJobEvent(BaseModel):
    """Append-only state and recovery event for one job."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "soc.processing_job_event.v1"
    event_id: str
    job_id: str
    event_type: str
    sequence: int = Field(ge=1)
    from_status: ProcessingJobStatus | None = None
    to_status: ProcessingJobStatus
    worker_id: str | None = None
    attempt: int = Field(ge=0)
    occurred_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)


class SocCallbackOutboxSubmission(BaseModel):
    """Secret-free callback intent committed with a terminal job result."""

    model_config = ConfigDict(extra="forbid")

    destination: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=512)
    payload: dict[str, Any]
    available_at: datetime | None = None


class SocCallbackOutboxRecord(BaseModel):
    """Current delivery state; callback retries never own analysis execution."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "soc.callback_outbox.v1"
    outbox_id: str
    job_id: str
    destination: str
    idempotency_key: str
    status: CallbackOutboxStatus
    payload: dict[str, Any]
    attempt_count: int = Field(default=0, ge=0)
    available_at: datetime
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    response_metadata: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    delivered_at: datetime | None = None


class SocCallbackAttemptRecord(BaseModel):
    """Immutable callback-attempt audit separate from current Outbox state."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "soc.callback_attempt.v1"
    attempt_id: str
    outbox_id: str
    job_id: str
    destination: str
    attempt_number: int = Field(ge=1)
    dispatcher_id: str
    outcome: CallbackAttemptOutcome
    started_at: datetime
    completed_at: datetime
    error_code: str | None = None
    error_message: str | None = None
    response_metadata: dict[str, Any] | None = None


def stable_processing_payload_sha256(payload: dict[str, Any]) -> str:
    """Hash a JSON payload without depending on source key ordering."""

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_processing_submission_sha256(submission: SocProcessingJobSubmission) -> str:
    """Fingerprint every immutable field protected by an idempotency key."""

    protected = submission.model_dump(
        mode="json",
        exclude={"available_at", "expires_at"},
    )
    return stable_processing_payload_sha256(protected)


__all__ = [
    "ACTIVE_PROCESSING_JOB_STATUSES",
    "CallbackAttemptOutcome",
    "CallbackOutboxStatus",
    "ProcessingJobStatus",
    "SocCallbackAttemptRecord",
    "SocProcessingJob",
    "SocCallbackOutboxRecord",
    "SocCallbackOutboxSubmission",
    "SocProcessingJobEvent",
    "SocProcessingJobSubmission",
    "stable_processing_payload_sha256",
    "stable_processing_submission_sha256",
]
