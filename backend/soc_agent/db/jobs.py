"""SQL persistence for durable, vendor-neutral SOC processing jobs."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from soc_agent.contracts import (
    ACTIVE_PROCESSING_JOB_STATUSES,
    CallbackAttemptOutcome,
    CallbackOutboxStatus,
    ProcessingJobStatus,
    SocCallbackAttemptRecord,
    SocCallbackOutboxRecord,
    SocCallbackOutboxSubmission,
    SocProcessingJob,
    SocProcessingJobEvent,
    SocProcessingJobSubmission,
    stable_processing_payload_sha256,
    stable_processing_submission_sha256,
)
from soc_agent.db.models import (
    SocCallbackAttemptRow,
    SocCallbackOutboxRow,
    SocProcessingJobEventRow,
    SocProcessingJobRow,
)


class ProcessingJobError(RuntimeError):
    """Base error for durable processing-job operations."""


class ProcessingJobConflictError(ProcessingJobError):
    """Raised when an idempotency, state, version, or lease guard fails."""


class ProcessingJobNotFoundError(ProcessingJobError):
    """Raised when a requested job does not exist."""


_ALLOWED_TRANSITIONS: dict[ProcessingJobStatus, frozenset[ProcessingJobStatus]] = {
    ProcessingJobStatus.CLAIMED: frozenset(
        {
            ProcessingJobStatus.PRECHECKING,
            ProcessingJobStatus.QUEUED,
            ProcessingJobStatus.FAILED,
            ProcessingJobStatus.EXPIRED_BEFORE_ANALYSIS,
        }
    ),
    ProcessingJobStatus.PRECHECKING: frozenset(
        {
            ProcessingJobStatus.ANALYZING,
            ProcessingJobStatus.SKIPPED_EXTERNAL_HANDLED,
            ProcessingJobStatus.QUEUED,
            ProcessingJobStatus.FAILED,
        }
    ),
    ProcessingJobStatus.ANALYZING: frozenset(
        {
            ProcessingJobStatus.PROJECTING,
            ProcessingJobStatus.QUEUED,
            ProcessingJobStatus.FAILED,
        }
    ),
    ProcessingJobStatus.PROJECTING: frozenset(
        {
            ProcessingJobStatus.COMPLETED,
            ProcessingJobStatus.QUEUED,
            ProcessingJobStatus.FAILED,
        }
    ),
}


class SqlAlchemyProcessingJobRepository:
    """Transactional queue implemented on the existing SOC database."""

    def __init__(
        self,
        session_factory: Callable[[], AbstractContextManager[Session]],
    ) -> None:
        self._session_factory = session_factory

    def submit(
        self,
        submission: SocProcessingJobSubmission,
        *,
        now: datetime | None = None,
    ) -> tuple[SocProcessingJob, bool]:
        observed_at = _as_utc(now or datetime.now(UTC))
        submission_hash = stable_processing_submission_sha256(submission)
        with self._session_factory() as session:
            existing = session.execute(select(SocProcessingJobRow).where(SocProcessingJobRow.idempotency_key == submission.idempotency_key).limit(1)).scalar_one_or_none()
            if existing is not None:
                return self._validate_idempotent_replay(
                    existing,
                    submission_hash=submission_hash,
                ), False

            row = _new_job_row(submission, observed_at)
            session.add(row)
            self._append_event(
                session,
                row,
                event_type="submitted",
                from_status=None,
                to_status=ProcessingJobStatus.QUEUED,
                worker_id=None,
                occurred_at=observed_at,
                details={"created": True},
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                concurrent = session.execute(select(SocProcessingJobRow).where(SocProcessingJobRow.idempotency_key == submission.idempotency_key).limit(1)).scalar_one_or_none()
                if concurrent is None:
                    raise
                return self._validate_idempotent_replay(
                    concurrent,
                    submission_hash=submission_hash,
                ), False
            return _job_from_row(row), True

    def submit_many(self, submissions: Sequence[SocProcessingJobSubmission], *, now: datetime | None = None) -> list[tuple[SocProcessingJob, bool]]:
        """Atomic bounded insert; callers retry the whole transaction on a race."""
        if not 1 <= len(submissions) <= 500 or len({item.idempotency_key for item in submissions}) != len(submissions):
            raise ValueError("submit_many requires 1..500 distinct idempotency keys")
        observed_at = _as_utc(now or datetime.now(UTC))
        with self._session_factory() as session:
            existing = {row.idempotency_key: row for row in session.scalars(select(SocProcessingJobRow).where(SocProcessingJobRow.idempotency_key.in_([item.idempotency_key for item in submissions])))}
            rows = []
            for item in submissions:
                row = existing.get(item.idempotency_key)
                if row is not None:
                    self._validate_idempotent_replay(row, submission_hash=stable_processing_submission_sha256(item))
                else:
                    row = _new_job_row(item, observed_at)
                    session.add(row)
                    self._append_event(session, row, event_type="submitted", from_status=None, to_status=ProcessingJobStatus.QUEUED, worker_id=None, occurred_at=observed_at, details={"created": True})
                rows.append((row, item.idempotency_key not in existing))
            session.commit()
            return [(_job_from_row(row), created) for row, created in rows]

    def get(self, job_id: str) -> SocProcessingJob | None:
        with self._session_factory() as session:
            row = session.get(SocProcessingJobRow, job_id)
            return _job_from_row(row) if row is not None else None

    def active_workload_count(self, workloads: Sequence[str]) -> int:
        """Call under the shared claim lock when applying a multi-workload budget."""
        with self._session_factory() as session:
            return (
                session.scalar(
                    select(func.count())
                    .select_from(SocProcessingJobRow)
                    .where(
                        SocProcessingJobRow.workload_kind.in_(workloads),
                        SocProcessingJobRow.status.in_([s.value for s in ACTIVE_PROCESSING_JOB_STATUSES]),
                    )
                )
                or 0
            )

    def list_workload_jobs(self, workload_kind: str, *, external_ref: str | None = None, statuses: Sequence[ProcessingJobStatus] | None = None, limit: int = 50, offset: int = 0) -> list[SocProcessingJob]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("job page requires limit 1..100 and nonnegative offset")
        query = select(SocProcessingJobRow).where(SocProcessingJobRow.workload_kind == workload_kind)
        if external_ref is not None:
            query = query.where(SocProcessingJobRow.external_ref == external_ref)
        if statuses is not None:
            query = query.where(SocProcessingJobRow.status.in_([s.value for s in statuses]))
        with self._session_factory() as session:
            return [_job_from_row(row) for row in session.scalars(query.order_by(SocProcessingJobRow.created_at.desc(), SocProcessingJobRow.job_id).offset(offset).limit(limit))]

    def claim_next(
        self,
        *,
        queue_name: str,
        worker_id: str,
        lease_seconds: int,
        workload_kind: str | None = None,
        job_ids: Sequence[str] | None = None,
        now: datetime | None = None,
    ) -> SocProcessingJob | None:
        if not queue_name.strip() or not worker_id.strip():
            raise ValueError("queue_name and worker_id are required")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be >= 1")
        if job_ids is not None and not job_ids:
            return None
        observed_at = _as_utc(now or datetime.now(UTC))

        with self._session_factory() as session:
            # SQLite has no SELECT FOR UPDATE; serialize the short claim transaction.
            if session.get_bind().dialect.name == "sqlite" and not session.in_transaction():
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            while True:
                other = aliased(SocProcessingJobRow)
                active_scope = select(other.job_id).where(other.concurrency_key == SocProcessingJobRow.concurrency_key, other.status.in_([s.value for s in ACTIVE_PROCESSING_JOB_STATUSES])).exists()
                query = (
                    select(SocProcessingJobRow)
                    .where(
                        SocProcessingJobRow.queue_name == queue_name,
                        SocProcessingJobRow.status == ProcessingJobStatus.QUEUED.value,
                        SocProcessingJobRow.available_at <= observed_at,
                        or_(SocProcessingJobRow.concurrency_key.is_(None), ~active_scope),
                    )
                    .order_by(
                        SocProcessingJobRow.priority.asc(),
                        SocProcessingJobRow.created_at.asc(),
                        SocProcessingJobRow.job_id.asc(),
                    )
                    .limit(1)
                )
                if workload_kind is not None:
                    query = query.where(SocProcessingJobRow.workload_kind == workload_kind)
                if job_ids is not None:
                    query = query.where(SocProcessingJobRow.job_id.in_(job_ids))
                if session.get_bind().dialect.name == "postgresql":
                    query = query.with_for_update(skip_locked=True)
                row = session.execute(query).scalar_one_or_none()
                if row is None:
                    session.commit()
                    return None

                previous = ProcessingJobStatus(row.status)
                row.status = ProcessingJobStatus.CLAIMED.value
                row.lease_owner = worker_id
                row.lease_expires_at = observed_at + timedelta(seconds=lease_seconds)
                row.attempt_count += 1
                row.started_at = row.started_at or observed_at
                row.updated_at = observed_at
                row.version += 1
                self._append_event(
                    session,
                    row,
                    event_type="claimed",
                    from_status=previous,
                    to_status=ProcessingJobStatus.CLAIMED,
                    worker_id=worker_id,
                    occurred_at=observed_at,
                    details={"lease_seconds": lease_seconds},
                )
                scope = row.concurrency_key
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    if (
                        scope is not None
                        and session.execute(select(SocProcessingJobRow.job_id).where(SocProcessingJobRow.concurrency_key == scope, SocProcessingJobRow.status.in_([s.value for s in ACTIVE_PROCESSING_JOB_STATUSES])).limit(1)).first()
                    ):
                        return None
                    raise
                return _job_from_row(row)

    def recover_expired_leases(
        self,
        *,
        queue_name: str | None = None,
        workload_kind: str | None = None,
        now: datetime | None = None,
    ) -> list[str]:
        observed_at = _as_utc(now or datetime.now(UTC))
        active_values = [status.value for status in ACTIVE_PROCESSING_JOB_STATUSES]
        with self._session_factory() as session:
            query = (
                select(SocProcessingJobRow)
                .where(
                    SocProcessingJobRow.status.in_(active_values),
                    SocProcessingJobRow.lease_expires_at.is_not(None),
                    SocProcessingJobRow.lease_expires_at <= observed_at,
                )
                .order_by(
                    SocProcessingJobRow.lease_expires_at.asc(),
                    SocProcessingJobRow.job_id.asc(),
                )
            )
            if queue_name is not None:
                query = query.where(SocProcessingJobRow.queue_name == queue_name)
            if workload_kind is not None:
                query = query.where(SocProcessingJobRow.workload_kind == workload_kind)
            if session.get_bind().dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            rows = list(session.execute(query).scalars())
            recovered_ids: list[str] = []
            for row in rows:
                previous = ProcessingJobStatus(row.status)
                previous_owner = row.lease_owner
                row.status = ProcessingJobStatus.QUEUED.value
                row.available_at = observed_at
                row.lease_owner = None
                row.lease_expires_at = None
                row.updated_at = observed_at
                row.version += 1
                self._append_event(
                    session,
                    row,
                    event_type="lease_expired_requeued",
                    from_status=previous,
                    to_status=ProcessingJobStatus.QUEUED,
                    worker_id=previous_owner,
                    occurred_at=observed_at,
                    details={},
                )
                recovered_ids.append(row.job_id)
            session.commit()
            return recovered_ids

    def retry_failed(self, job_id: str, *, expected_version: int, actor_id: str, max_attempts: int = 3) -> SocProcessingJob:
        """Explicit bounded retry; retain Run and attempt events for recovery/audit."""
        with self._session_factory() as session:
            row = session.get(SocProcessingJobRow, job_id)
            if row is None:
                raise ProcessingJobNotFoundError(job_id)
            if row.version != expected_version or row.status != ProcessingJobStatus.FAILED.value:
                raise ProcessingJobConflictError("only the current failed job can be retried")
            if not (row.result_payload or {}).get("retryable") or row.attempt_count >= max_attempts:
                raise ProcessingJobConflictError("job is not retryable or its attempt budget is exhausted")
            now = datetime.now(UTC)
            row.status = ProcessingJobStatus.QUEUED.value
            row.available_at = now
            row.completed_at = None
            row.updated_at = now
            row.version += 1
            self._append_event(
                session,
                row,
                event_type="operator_retry",
                from_status=ProcessingJobStatus.FAILED,
                to_status=ProcessingJobStatus.QUEUED,
                worker_id=actor_id,
                occurred_at=now,
                details={"previous_error_code": row.error_code, "previous_error_message": row.error_message, "run_id": row.run_id},
            )
            row.error_code = None
            row.error_message = None
            session.commit()
            return _job_from_row(row)

    def renew_lease(
        self,
        job_id: str,
        *,
        worker_id: str,
        expected_status: ProcessingJobStatus,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> SocProcessingJob:
        """Extend one active worker lease without changing business state."""

        if expected_status not in ACTIVE_PROCESSING_JOB_STATUSES:
            raise ValueError("lease renewal requires an active processing status")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be >= 1")
        observed_at = _as_utc(now or datetime.now(UTC))
        with self._session_factory() as session:
            query = select(SocProcessingJobRow).where(SocProcessingJobRow.job_id == job_id)
            if session.get_bind().dialect.name == "postgresql":
                query = query.with_for_update()
            row = session.execute(query).scalar_one_or_none()
            if row is None:
                raise ProcessingJobNotFoundError(f"processing job {job_id} not found")
            self._guard_owned_active_job(
                row,
                worker_id=worker_id,
                expected_status=expected_status,
                now=observed_at,
            )
            row.lease_expires_at = observed_at + timedelta(seconds=lease_seconds)
            row.updated_at = observed_at
            row.version += 1
            self._append_event(
                session,
                row,
                event_type="lease_renewed",
                from_status=expected_status,
                to_status=expected_status,
                worker_id=worker_id,
                occurred_at=observed_at,
                details={"lease_seconds": lease_seconds},
            )
            session.commit()
            return _job_from_row(row)

    def transition(
        self,
        job_id: str,
        *,
        worker_id: str,
        expected_status: ProcessingJobStatus,
        target_status: ProcessingJobStatus,
        event_type: str,
        now: datetime | None = None,
        run_id: str | None = None,
        result_payload: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        available_at: datetime | None = None,
        details: dict[str, Any] | None = None,
    ) -> SocProcessingJob:
        if target_status not in _ALLOWED_TRANSITIONS.get(
            expected_status,
            frozenset(),
        ):
            raise ProcessingJobConflictError(f"unsupported processing-job transition {expected_status.value} -> {target_status.value}")
        observed_at = _as_utc(now or datetime.now(UTC))
        with self._session_factory() as session:
            query = select(SocProcessingJobRow).where(SocProcessingJobRow.job_id == job_id)
            if session.get_bind().dialect.name == "postgresql":
                query = query.with_for_update()
            row = session.execute(query).scalar_one_or_none()
            if row is None:
                raise ProcessingJobNotFoundError(f"processing job {job_id} not found")
            if row.status != expected_status.value:
                raise ProcessingJobConflictError(f"processing job {job_id} is {row.status}, expected {expected_status.value}")
            if row.lease_owner != worker_id:
                raise ProcessingJobConflictError(f"processing job {job_id} lease owner is not {worker_id}")
            lease_expires_at = _as_utc(row.lease_expires_at)
            if lease_expires_at is None or lease_expires_at <= observed_at:
                raise ProcessingJobConflictError(f"processing job {job_id} lease has expired")

            row.status = target_status.value
            row.updated_at = observed_at
            row.version += 1
            if run_id is not None:
                row.run_id = run_id
            if result_payload is not None:
                row.result_payload = result_payload
            if error_code is not None:
                row.error_code = error_code
            if error_message is not None:
                row.error_message = error_message
            if target_status is ProcessingJobStatus.QUEUED:
                row.available_at = _as_utc(available_at or observed_at)
                row.lease_owner = None
                row.lease_expires_at = None
            elif target_status.is_terminal:
                row.completed_at = observed_at
                row.lease_owner = None
                row.lease_expires_at = None
            self._append_event(
                session,
                row,
                event_type=event_type,
                from_status=expected_status,
                to_status=target_status,
                worker_id=worker_id,
                occurred_at=observed_at,
                details=details or {},
            )
            session.commit()
            return _job_from_row(row)

    def complete_with_callback(
        self,
        job_id: str,
        *,
        worker_id: str,
        expected_status: ProcessingJobStatus,
        target_status: ProcessingJobStatus,
        event_type: str,
        result_payload: dict[str, Any],
        callback: SocCallbackOutboxSubmission,
        now: datetime | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> tuple[SocProcessingJob, SocCallbackOutboxRecord]:
        if not target_status.is_terminal:
            raise ProcessingJobConflictError("complete_with_callback requires a terminal target status")
        if target_status not in _ALLOWED_TRANSITIONS.get(
            expected_status,
            frozenset(),
        ):
            raise ProcessingJobConflictError(f"unsupported processing-job transition {expected_status.value} -> {target_status.value}")
        observed_at = _as_utc(now or datetime.now(UTC))
        with self._session_factory() as session:
            query = select(SocProcessingJobRow).where(SocProcessingJobRow.job_id == job_id)
            if session.get_bind().dialect.name == "postgresql":
                query = query.with_for_update()
            row = session.execute(query).scalar_one_or_none()
            if row is None:
                raise ProcessingJobNotFoundError(f"processing job {job_id} not found")
            self._guard_owned_active_job(
                row,
                worker_id=worker_id,
                expected_status=expected_status,
                now=observed_at,
            )
            row.status = target_status.value
            row.result_payload = result_payload
            row.error_code = error_code
            row.error_message = error_message
            row.completed_at = observed_at
            row.updated_at = observed_at
            row.lease_owner = None
            row.lease_expires_at = None
            row.version += 1
            self._append_event(
                session,
                row,
                event_type=event_type,
                from_status=expected_status,
                to_status=target_status,
                worker_id=worker_id,
                occurred_at=observed_at,
                details=details or {},
            )
            callback_row = SocCallbackOutboxRow(
                outbox_id=f"OUT-{uuid4().hex[:16].upper()}",
                job_id=job_id,
                destination=callback.destination,
                idempotency_key=callback.idempotency_key,
                status=CallbackOutboxStatus.PENDING.value,
                payload=callback.payload,
                attempt_count=0,
                available_at=_as_utc(callback.available_at or observed_at),
                lease_owner=None,
                lease_expires_at=None,
                last_error_code=None,
                last_error_message=None,
                response_metadata=None,
                created_at=observed_at,
                updated_at=observed_at,
                delivered_at=None,
            )
            session.add(callback_row)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ProcessingJobConflictError("callback outbox idempotency conflict") from exc
            return _job_from_row(row), _callback_from_row(callback_row)

    def get_callback(
        self,
        outbox_id: str,
    ) -> SocCallbackOutboxRecord | None:
        with self._session_factory() as session:
            row = session.get(SocCallbackOutboxRow, outbox_id)
            return _callback_from_row(row) if row is not None else None

    def list_callbacks(self, job_id: str) -> list[SocCallbackOutboxRecord]:
        with self._session_factory() as session:
            rows = session.execute(
                select(SocCallbackOutboxRow)
                .where(SocCallbackOutboxRow.job_id == job_id)
                .order_by(
                    SocCallbackOutboxRow.created_at.asc(),
                    SocCallbackOutboxRow.outbox_id.asc(),
                )
            ).scalars()
            return [_callback_from_row(row) for row in rows]

    def claim_next_callback(
        self,
        *,
        destination: str,
        dispatcher_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> SocCallbackOutboxRecord | None:
        if not destination.strip() or not dispatcher_id.strip():
            raise ValueError("destination and dispatcher_id are required")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be >= 1")
        observed_at = _as_utc(now or datetime.now(UTC))
        with self._session_factory() as session:
            stale_query = select(SocCallbackOutboxRow).where(
                SocCallbackOutboxRow.status == CallbackOutboxStatus.SENDING.value,
                SocCallbackOutboxRow.lease_expires_at.is_not(None),
                SocCallbackOutboxRow.lease_expires_at <= observed_at,
            )
            if session.get_bind().dialect.name == "postgresql":
                stale_query = stale_query.with_for_update(skip_locked=True)
            for stale in session.execute(stale_query).scalars():
                self._append_callback_attempt(
                    session,
                    stale,
                    dispatcher_id=stale.lease_owner or "unknown-dispatcher",
                    outcome=CallbackAttemptOutcome.LEASE_EXPIRED,
                    completed_at=observed_at,
                    error_code="dispatcher_lease_expired",
                    error_message="callback dispatcher lease expired",
                )
                stale.status = CallbackOutboxStatus.RETRY_WAIT.value
                stale.available_at = observed_at
                stale.lease_owner = None
                stale.lease_expires_at = None
                stale.last_error_code = "dispatcher_lease_expired"
                stale.last_error_message = "callback dispatcher lease expired"
                stale.updated_at = observed_at

            query = (
                select(SocCallbackOutboxRow)
                .where(
                    SocCallbackOutboxRow.destination == destination,
                    SocCallbackOutboxRow.status.in_(
                        [
                            CallbackOutboxStatus.PENDING.value,
                            CallbackOutboxStatus.RETRY_WAIT.value,
                        ]
                    ),
                    SocCallbackOutboxRow.available_at <= observed_at,
                )
                .order_by(
                    SocCallbackOutboxRow.available_at.asc(),
                    SocCallbackOutboxRow.created_at.asc(),
                    SocCallbackOutboxRow.outbox_id.asc(),
                )
                .limit(1)
            )
            if session.get_bind().dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            row = session.execute(query).scalar_one_or_none()
            if row is None:
                session.commit()
                return None
            row.status = CallbackOutboxStatus.SENDING.value
            row.lease_owner = dispatcher_id
            row.lease_expires_at = observed_at + timedelta(seconds=lease_seconds)
            row.attempt_count += 1
            row.updated_at = observed_at
            session.commit()
            return _callback_from_row(row)

    def mark_callback_retry(
        self,
        outbox_id: str,
        *,
        dispatcher_id: str,
        error_code: str,
        error_message: str,
        response_metadata: dict[str, Any] | None = None,
        available_at: datetime,
        now: datetime | None = None,
        dead_letter: bool = False,
    ) -> SocCallbackOutboxRecord:
        observed_at = _as_utc(now or datetime.now(UTC))
        with self._session_factory() as session:
            row = self._owned_callback_row(
                session,
                outbox_id,
                dispatcher_id=dispatcher_id,
                now=observed_at,
            )
            outcome = CallbackAttemptOutcome.DEAD_LETTER if dead_letter else CallbackAttemptOutcome.RETRY_SCHEDULED
            self._append_callback_attempt(
                session,
                row,
                dispatcher_id=dispatcher_id,
                outcome=outcome,
                completed_at=observed_at,
                error_code=error_code,
                error_message=error_message,
                response_metadata=response_metadata,
            )
            row.status = CallbackOutboxStatus.DEAD_LETTER.value if dead_letter else CallbackOutboxStatus.RETRY_WAIT.value
            row.available_at = _as_utc(available_at)
            row.lease_owner = None
            row.lease_expires_at = None
            row.last_error_code = error_code
            row.last_error_message = error_message
            if response_metadata is not None:
                row.response_metadata = response_metadata
            row.updated_at = observed_at
            session.commit()
            return _callback_from_row(row)

    def mark_callback_delivered(
        self,
        outbox_id: str,
        *,
        dispatcher_id: str,
        response_metadata: dict[str, Any],
        now: datetime | None = None,
    ) -> SocCallbackOutboxRecord:
        observed_at = _as_utc(now or datetime.now(UTC))
        with self._session_factory() as session:
            row = self._owned_callback_row(
                session,
                outbox_id,
                dispatcher_id=dispatcher_id,
                now=observed_at,
            )
            self._append_callback_attempt(
                session,
                row,
                dispatcher_id=dispatcher_id,
                outcome=CallbackAttemptOutcome.DELIVERED,
                completed_at=observed_at,
                response_metadata=response_metadata,
            )
            row.status = CallbackOutboxStatus.DELIVERED.value
            row.lease_owner = None
            row.lease_expires_at = None
            row.last_error_code = None
            row.last_error_message = None
            row.response_metadata = response_metadata
            row.updated_at = observed_at
            row.delivered_at = observed_at
            session.commit()
            return _callback_from_row(row)

    def list_events(self, job_id: str) -> list[SocProcessingJobEvent]:
        with self._session_factory() as session:
            rows = session.execute(select(SocProcessingJobEventRow).where(SocProcessingJobEventRow.job_id == job_id).order_by(SocProcessingJobEventRow.sequence.asc())).scalars()
            return [_event_from_row(row) for row in rows]

    def list_callback_attempts(
        self,
        outbox_id: str,
    ) -> list[SocCallbackAttemptRecord]:
        with self._session_factory() as session:
            rows = session.execute(select(SocCallbackAttemptRow).where(SocCallbackAttemptRow.outbox_id == outbox_id).order_by(SocCallbackAttemptRow.attempt_number.asc())).scalars()
            return [_callback_attempt_from_row(row) for row in rows]

    @staticmethod
    def _guard_owned_active_job(
        row: SocProcessingJobRow,
        *,
        worker_id: str,
        expected_status: ProcessingJobStatus,
        now: datetime,
    ) -> None:
        if row.status != expected_status.value:
            raise ProcessingJobConflictError(f"processing job {row.job_id} is {row.status}, expected {expected_status.value}")
        if row.lease_owner != worker_id:
            raise ProcessingJobConflictError(f"processing job {row.job_id} lease owner is not {worker_id}")
        lease_expires_at = _as_utc(row.lease_expires_at)
        if lease_expires_at is None or lease_expires_at <= now:
            raise ProcessingJobConflictError(f"processing job {row.job_id} lease has expired")

    @staticmethod
    def _owned_callback_row(
        session: Session,
        outbox_id: str,
        *,
        dispatcher_id: str,
        now: datetime,
    ) -> SocCallbackOutboxRow:
        query = select(SocCallbackOutboxRow).where(SocCallbackOutboxRow.outbox_id == outbox_id)
        if session.get_bind().dialect.name == "postgresql":
            query = query.with_for_update()
        row = session.execute(query).scalar_one_or_none()
        if row is None:
            raise ProcessingJobNotFoundError(f"callback outbox {outbox_id} not found")
        if row.status != CallbackOutboxStatus.SENDING.value:
            raise ProcessingJobConflictError(f"callback outbox {outbox_id} is not sending")
        if row.lease_owner != dispatcher_id:
            raise ProcessingJobConflictError(f"callback outbox {outbox_id} lease owner is not {dispatcher_id}")
        lease_expires_at = _as_utc(row.lease_expires_at)
        if lease_expires_at is None or lease_expires_at <= now:
            raise ProcessingJobConflictError(f"callback outbox {outbox_id} lease has expired")
        return row

    @staticmethod
    def _validate_idempotent_replay(
        row: SocProcessingJobRow,
        *,
        submission_hash: str,
    ) -> SocProcessingJob:
        if row.submission_sha256 != submission_hash:
            raise ProcessingJobConflictError("processing-job idempotency key was reused with a different payload")
        return _job_from_row(row)

    @staticmethod
    def _append_event(
        session: Session,
        row: SocProcessingJobRow,
        *,
        event_type: str,
        from_status: ProcessingJobStatus | None,
        to_status: ProcessingJobStatus,
        worker_id: str | None,
        occurred_at: datetime,
        details: dict[str, Any],
    ) -> None:
        session.add(
            SocProcessingJobEventRow(
                event_id=f"JEV-{uuid4().hex[:16].upper()}",
                job_id=row.job_id,
                event_type=event_type,
                sequence=row.version,
                from_status=from_status.value if from_status is not None else None,
                to_status=to_status.value,
                worker_id=worker_id,
                attempt=row.attempt_count,
                occurred_at=occurred_at,
                details_payload=details,
            )
        )

    @staticmethod
    def _append_callback_attempt(
        session: Session,
        row: SocCallbackOutboxRow,
        *,
        dispatcher_id: str,
        outcome: CallbackAttemptOutcome,
        completed_at: datetime,
        error_code: str | None = None,
        error_message: str | None = None,
        response_metadata: dict[str, Any] | None = None,
    ) -> None:
        session.add(
            SocCallbackAttemptRow(
                attempt_id=f"CAT-{uuid4().hex[:16].upper()}",
                outbox_id=row.outbox_id,
                job_id=row.job_id,
                destination=row.destination,
                attempt_number=row.attempt_count,
                dispatcher_id=dispatcher_id,
                outcome=outcome.value,
                started_at=_as_utc(row.updated_at),
                completed_at=completed_at,
                error_code=error_code,
                error_message=error_message,
                response_metadata=response_metadata,
            )
        )


def _new_job_row(submission: SocProcessingJobSubmission, now: datetime) -> SocProcessingJobRow:
    return SocProcessingJobRow(
        **submission.model_dump(exclude={"metadata", "available_at", "expires_at"}),
        job_id=f"JOB-{uuid4().hex[:16].upper()}",
        status=ProcessingJobStatus.QUEUED.value,
        submission_sha256=stable_processing_submission_sha256(submission),
        payload_sha256=stable_processing_payload_sha256(submission.input_payload),
        metadata_payload=submission.metadata,
        run_id=None,
        result_payload=None,
        error_code=None,
        error_message=None,
        attempt_count=0,
        version=1,
        available_at=_as_utc(submission.available_at or now),
        expires_at=_as_utc(submission.expires_at),
        lease_owner=None,
        lease_expires_at=None,
        created_at=now,
        updated_at=now,
        started_at=None,
        completed_at=None,
    )


def _job_from_row(row: SocProcessingJobRow) -> SocProcessingJob:
    return SocProcessingJob(
        job_id=row.job_id,
        tenant_id=row.tenant_id,
        workload_kind=row.workload_kind,
        queue_name=row.queue_name,
        concurrency_key=row.concurrency_key,
        status=ProcessingJobStatus(row.status),
        idempotency_key=row.idempotency_key,
        external_ref=row.external_ref,
        alert_id=row.alert_id,
        detection_key=row.detection_key,
        execution_type=row.execution_type,
        model_name=row.model_name,
        priority=row.priority,
        payload_sha256=row.payload_sha256,
        input_payload=row.input_payload,
        metadata=row.metadata_payload,
        run_id=row.run_id,
        result_payload=row.result_payload,
        error_code=row.error_code,
        error_message=row.error_message,
        attempt_count=row.attempt_count,
        version=row.version,
        available_at=_as_utc(row.available_at),
        expires_at=_as_utc(row.expires_at),
        lease_owner=row.lease_owner,
        lease_expires_at=_as_utc(row.lease_expires_at),
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        started_at=_as_utc(row.started_at),
        completed_at=_as_utc(row.completed_at),
    )


def _event_from_row(row: SocProcessingJobEventRow) -> SocProcessingJobEvent:
    return SocProcessingJobEvent(
        event_id=row.event_id,
        job_id=row.job_id,
        event_type=row.event_type,
        sequence=row.sequence,
        from_status=(ProcessingJobStatus(row.from_status) if row.from_status is not None else None),
        to_status=ProcessingJobStatus(row.to_status),
        worker_id=row.worker_id,
        attempt=row.attempt,
        occurred_at=_as_utc(row.occurred_at),
        details=row.details_payload,
    )


def _callback_from_row(row: SocCallbackOutboxRow) -> SocCallbackOutboxRecord:
    return SocCallbackOutboxRecord(
        outbox_id=row.outbox_id,
        job_id=row.job_id,
        destination=row.destination,
        idempotency_key=row.idempotency_key,
        status=CallbackOutboxStatus(row.status),
        payload=row.payload,
        attempt_count=row.attempt_count,
        available_at=_as_utc(row.available_at),
        lease_owner=row.lease_owner,
        lease_expires_at=_as_utc(row.lease_expires_at),
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        response_metadata=row.response_metadata,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        delivered_at=_as_utc(row.delivered_at),
    )


def _callback_attempt_from_row(
    row: SocCallbackAttemptRow,
) -> SocCallbackAttemptRecord:
    return SocCallbackAttemptRecord(
        attempt_id=row.attempt_id,
        outbox_id=row.outbox_id,
        job_id=row.job_id,
        destination=row.destination,
        attempt_number=row.attempt_number,
        dispatcher_id=row.dispatcher_id,
        outcome=CallbackAttemptOutcome(row.outcome),
        started_at=_as_utc(row.started_at),
        completed_at=_as_utc(row.completed_at),
        error_code=row.error_code,
        error_message=row.error_message,
        response_metadata=row.response_metadata,
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__: Sequence[str] = (
    "ProcessingJobConflictError",
    "ProcessingJobError",
    "ProcessingJobNotFoundError",
    "SqlAlchemyProcessingJobRepository",
)
