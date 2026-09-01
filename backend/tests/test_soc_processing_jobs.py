from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from soc_agent.contracts import (
    CallbackAttemptOutcome,
    CallbackOutboxStatus,
    ProcessingJobStatus,
    SocCallbackOutboxSubmission,
    SocProcessingJobSubmission,
)
from soc_agent.db import (
    ProcessingJobConflictError,
    SqlAlchemyProcessingJobRepository,
    create_soc_tables,
)


def _repository() -> SqlAlchemyProcessingJobRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    return SqlAlchemyProcessingJobRepository(sessionmaker(bind=engine, expire_on_commit=False))


def _submission(
    *,
    idempotency_key: str,
    alert_id: str,
    priority: int = 5,
    payload: dict | None = None,
    expires_at: datetime | None = None,
) -> SocProcessingJobSubmission:
    return SocProcessingJobSubmission(
        tenant_id="pingan",
        workload_kind="alert_analysis",
        queue_name="deepseek-v4-flash",
        idempotency_key=idempotency_key,
        external_ref=f"zeus:{alert_id}",
        alert_id=alert_id,
        detection_key="RULE-001",
        execution_type="3",
        model_name="deepseek-v4-flash-0731",
        priority=priority,
        input_payload=payload or {"alert": {"id": alert_id}},
        expires_at=expires_at,
    )


def test_submit_is_idempotent_but_rejects_key_reuse_with_changed_payload() -> None:
    repository = _repository()
    now = datetime(2026, 9, 1, 1, 0, tzinfo=UTC)

    first, created = repository.submit(
        _submission(idempotency_key="zeus:submission:1", alert_id="A-1"),
        now=now,
    )
    replay, replay_created = repository.submit(
        _submission(idempotency_key="zeus:submission:1", alert_id="A-1"),
        now=now + timedelta(seconds=1),
    )

    assert created is True
    assert replay_created is False
    assert replay == first
    assert first.status is ProcessingJobStatus.QUEUED
    assert first.payload_sha256

    with pytest.raises(ProcessingJobConflictError, match="different payload"):
        repository.submit(
            _submission(
                idempotency_key="zeus:submission:1",
                alert_id="A-1",
                payload={"alert": {"id": "A-1", "changed": True}},
            ),
            now=now + timedelta(seconds=2),
        )


def test_claim_uses_priority_then_fifo_and_records_queryable_fields() -> None:
    repository = _repository()
    now = datetime(2026, 9, 1, 1, 0, tzinfo=UTC)
    low, _ = repository.submit(
        _submission(idempotency_key="low", alert_id="A-low", priority=9),
        now=now,
    )
    high, _ = repository.submit(
        _submission(idempotency_key="high", alert_id="A-high", priority=0),
        now=now + timedelta(seconds=1),
    )

    claimed = repository.claim_next(
        queue_name="deepseek-v4-flash",
        worker_id="worker-1",
        lease_seconds=120,
        now=now + timedelta(seconds=2),
    )

    assert claimed is not None
    assert claimed.job_id == high.job_id
    assert claimed.status is ProcessingJobStatus.CLAIMED
    assert claimed.lease_owner == "worker-1"
    assert claimed.lease_expires_at == now + timedelta(seconds=122)
    assert claimed.attempt_count == 1
    assert claimed.alert_id == "A-high"
    assert claimed.detection_key == "RULE-001"
    assert claimed.execution_type == "3"
    assert claimed.model_name == "deepseek-v4-flash-0731"
    assert repository.get(low.job_id) is not None

    event_types = [event.event_type for event in repository.list_events(high.job_id)]
    assert event_types == ["submitted", "claimed"]


def test_postgresql_claim_query_uses_skip_locked_for_multi_worker_safety() -> None:
    session = MagicMock()
    session.get_bind.return_value.dialect.name = "postgresql"
    session.execute.return_value.scalar_one_or_none.return_value = None

    @contextmanager
    def session_factory():
        yield session

    repository = SqlAlchemyProcessingJobRepository(session_factory)

    assert (
        repository.claim_next(
            queue_name="deepseek-v4-flash",
            worker_id="worker-1",
            lease_seconds=60,
            now=datetime(2026, 9, 1, 1, 0, tzinfo=UTC),
        )
        is None
    )

    query = session.execute.call_args.args[0]
    compiled = str(query.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in compiled


def test_expired_lease_is_requeued_and_can_be_claimed_without_losing_lineage() -> None:
    repository = _repository()
    now = datetime(2026, 9, 1, 1, 0, tzinfo=UTC)
    submitted, _ = repository.submit(
        _submission(idempotency_key="recover", alert_id="A-recover"),
        now=now,
    )
    first_claim = repository.claim_next(
        queue_name="deepseek-v4-flash",
        worker_id="worker-dead",
        lease_seconds=30,
        now=now,
    )
    assert first_claim is not None

    recovered_ids = repository.recover_expired_leases(now=now + timedelta(seconds=31))
    second_claim = repository.claim_next(
        queue_name="deepseek-v4-flash",
        worker_id="worker-live",
        lease_seconds=30,
        now=now + timedelta(seconds=32),
    )

    assert recovered_ids == [submitted.job_id]
    assert second_claim is not None
    assert second_claim.job_id == submitted.job_id
    assert second_claim.attempt_count == 2
    assert second_claim.lease_owner == "worker-live"
    assert [event.event_type for event in repository.list_events(submitted.job_id)] == [
        "submitted",
        "claimed",
        "lease_expired_requeued",
        "claimed",
    ]


def test_worker_can_renew_an_active_lease_during_a_long_model_call() -> None:
    repository = _repository()
    now = datetime(2026, 9, 1, 1, 0, tzinfo=UTC)
    submitted, _ = repository.submit(
        _submission(idempotency_key="heartbeat", alert_id="A-heartbeat"),
        now=now,
    )
    claimed = repository.claim_next(
        queue_name="deepseek-v4-flash",
        worker_id="worker-1",
        lease_seconds=30,
        now=now,
    )
    assert claimed is not None
    analyzing = repository.transition(
        claimed.job_id,
        worker_id="worker-1",
        expected_status=ProcessingJobStatus.CLAIMED,
        target_status=ProcessingJobStatus.PRECHECKING,
        event_type="precheck_started",
        now=now + timedelta(seconds=1),
    )
    analyzing = repository.transition(
        analyzing.job_id,
        worker_id="worker-1",
        expected_status=ProcessingJobStatus.PRECHECKING,
        target_status=ProcessingJobStatus.ANALYZING,
        event_type="analysis_started",
        now=now + timedelta(seconds=2),
    )

    renewed = repository.renew_lease(
        analyzing.job_id,
        worker_id="worker-1",
        expected_status=ProcessingJobStatus.ANALYZING,
        lease_seconds=30,
        now=now + timedelta(seconds=20),
    )

    assert renewed.lease_expires_at == now + timedelta(seconds=50)
    assert repository.recover_expired_leases(now=now + timedelta(seconds=31)) == []
    assert repository.list_events(submitted.job_id)[-1].event_type == "lease_renewed"


def test_generic_queue_claims_expired_work_for_adapter_owned_completion() -> None:
    repository = _repository()
    now = datetime(2026, 9, 1, 1, 0, tzinfo=UTC)
    expired, _ = repository.submit(
        _submission(
            idempotency_key="expired",
            alert_id="A-expired",
            priority=0,
            expires_at=now + timedelta(seconds=5),
        ),
        now=now,
    )
    ready, _ = repository.submit(
        _submission(idempotency_key="ready", alert_id="A-ready", priority=1),
        now=now,
    )

    claimed = repository.claim_next(
        queue_name="deepseek-v4-flash",
        worker_id="worker-1",
        lease_seconds=60,
        now=now + timedelta(seconds=6),
    )

    assert claimed is not None and claimed.job_id == expired.job_id
    assert claimed.status is ProcessingJobStatus.CLAIMED
    expired_after = repository.get(expired.job_id)
    assert expired_after is not None
    assert expired_after.status is ProcessingJobStatus.CLAIMED
    assert expired_after.completed_at is None
    assert repository.get(ready.job_id) is not None


def test_worker_owned_transition_is_compare_and_swap_guarded() -> None:
    repository = _repository()
    now = datetime(2026, 9, 1, 1, 0, tzinfo=UTC)
    submitted, _ = repository.submit(
        _submission(idempotency_key="transition", alert_id="A-transition"),
        now=now,
    )
    claimed = repository.claim_next(
        queue_name="deepseek-v4-flash",
        worker_id="worker-1",
        lease_seconds=60,
        now=now,
    )
    assert claimed is not None

    prechecking = repository.transition(
        claimed.job_id,
        worker_id="worker-1",
        expected_status=ProcessingJobStatus.CLAIMED,
        target_status=ProcessingJobStatus.PRECHECKING,
        event_type="precheck_started",
        now=now + timedelta(seconds=1),
    )

    assert prechecking.status is ProcessingJobStatus.PRECHECKING
    with pytest.raises(ProcessingJobConflictError, match="lease owner"):
        repository.transition(
            submitted.job_id,
            worker_id="other-worker",
            expected_status=ProcessingJobStatus.PRECHECKING,
            target_status=ProcessingJobStatus.ANALYZING,
            event_type="analysis_started",
            now=now + timedelta(seconds=2),
        )


def test_terminal_result_and_callback_outbox_are_committed_together() -> None:
    repository = _repository()
    now = datetime(2026, 9, 1, 1, 0, tzinfo=UTC)
    submitted, _ = repository.submit(
        _submission(idempotency_key="outbox", alert_id="A-outbox"),
        now=now,
    )
    claimed = repository.claim_next(
        queue_name="deepseek-v4-flash",
        worker_id="worker-1",
        lease_seconds=300,
        now=now,
    )
    assert claimed is not None
    prechecking = repository.transition(
        claimed.job_id,
        worker_id="worker-1",
        expected_status=ProcessingJobStatus.CLAIMED,
        target_status=ProcessingJobStatus.PRECHECKING,
        event_type="precheck_started",
        now=now + timedelta(seconds=1),
    )
    analyzing = repository.transition(
        prechecking.job_id,
        worker_id="worker-1",
        expected_status=ProcessingJobStatus.PRECHECKING,
        target_status=ProcessingJobStatus.ANALYZING,
        event_type="analysis_started",
        now=now + timedelta(seconds=2),
    )
    projecting = repository.transition(
        analyzing.job_id,
        worker_id="worker-1",
        expected_status=ProcessingJobStatus.ANALYZING,
        target_status=ProcessingJobStatus.PROJECTING,
        event_type="projection_started",
        run_id="RUN-A-outbox",
        now=now + timedelta(seconds=3),
    )

    completed, callback = repository.complete_with_callback(
        projecting.job_id,
        worker_id="worker-1",
        expected_status=ProcessingJobStatus.PROJECTING,
        target_status=ProcessingJobStatus.COMPLETED,
        event_type="completed",
        result_payload={"alert_action": "转交"},
        callback=SocCallbackOutboxSubmission(
            destination="pingan.zeus.alert_callback",
            idempotency_key=f"pingan:callback:{projecting.job_id}",
            payload={
                "taskId": projecting.job_id,
                "status": "SUCCESS",
                "result": {"alert_action": "转交"},
            },
        ),
        now=now + timedelta(seconds=4),
    )

    assert completed.status is ProcessingJobStatus.COMPLETED
    assert completed.result_payload == {"alert_action": "转交"}
    assert callback.job_id == submitted.job_id
    assert callback.status is CallbackOutboxStatus.PENDING
    assert callback.attempt_count == 0
    assert repository.get_callback(callback.outbox_id) == callback
    assert repository.list_callbacks(submitted.job_id) == [callback]
    assert repository.list_callbacks("JOB-UNKNOWN") == []


def test_callback_retry_is_independent_from_completed_analysis() -> None:
    repository = _repository()
    now = datetime(2026, 9, 1, 1, 0, tzinfo=UTC)
    submitted, _ = repository.submit(
        _submission(idempotency_key="callback-retry", alert_id="A-callback"),
        now=now,
    )
    claimed_job = repository.claim_next(
        queue_name="deepseek-v4-flash",
        worker_id="worker-1",
        lease_seconds=300,
        now=now,
    )
    assert claimed_job is not None
    prechecking = repository.transition(
        claimed_job.job_id,
        worker_id="worker-1",
        expected_status=ProcessingJobStatus.CLAIMED,
        target_status=ProcessingJobStatus.PRECHECKING,
        event_type="precheck_started",
        now=now + timedelta(seconds=1),
    )
    _completed, callback = repository.complete_with_callback(
        prechecking.job_id,
        worker_id="worker-1",
        expected_status=ProcessingJobStatus.PRECHECKING,
        target_status=ProcessingJobStatus.SKIPPED_EXTERNAL_HANDLED,
        event_type="external_handled",
        result_payload={"alert_action": "已介入"},
        callback=SocCallbackOutboxSubmission(
            destination="pingan.zeus.alert_callback",
            idempotency_key=f"pingan:callback:{prechecking.job_id}",
            payload={"taskId": prechecking.job_id, "status": "SUCCESS"},
        ),
        now=now + timedelta(seconds=2),
    )

    sending = repository.claim_next_callback(
        destination="pingan.zeus.alert_callback",
        dispatcher_id="callback-1",
        lease_seconds=30,
        now=now + timedelta(seconds=3),
    )
    assert sending is not None and sending.outbox_id == callback.outbox_id
    retrying = repository.mark_callback_retry(
        sending.outbox_id,
        dispatcher_id="callback-1",
        error_code="timeout",
        error_message="callback timed out",
        available_at=now + timedelta(seconds=10),
        now=now + timedelta(seconds=4),
    )
    assert retrying.status is CallbackOutboxStatus.RETRY_WAIT
    retry_attempts = repository.list_callback_attempts(callback.outbox_id)
    assert len(retry_attempts) == 1
    assert retry_attempts[0].attempt_number == 1
    assert retry_attempts[0].outcome is CallbackAttemptOutcome.RETRY_SCHEDULED
    assert retry_attempts[0].dispatcher_id == "callback-1"
    assert retry_attempts[0].error_code == "timeout"
    assert (
        repository.claim_next_callback(
            destination="pingan.zeus.alert_callback",
            dispatcher_id="callback-2",
            lease_seconds=30,
            now=now + timedelta(seconds=9),
        )
        is None
    )
    sending_again = repository.claim_next_callback(
        destination="pingan.zeus.alert_callback",
        dispatcher_id="callback-2",
        lease_seconds=30,
        now=now + timedelta(seconds=10),
    )
    assert sending_again is not None and sending_again.attempt_count == 2
    delivered = repository.mark_callback_delivered(
        sending_again.outbox_id,
        dispatcher_id="callback-2",
        response_metadata={"http_status": 200},
        now=now + timedelta(seconds=11),
    )

    assert delivered.status is CallbackOutboxStatus.DELIVERED
    attempts = repository.list_callback_attempts(callback.outbox_id)
    assert [attempt.outcome for attempt in attempts] == [
        CallbackAttemptOutcome.RETRY_SCHEDULED,
        CallbackAttemptOutcome.DELIVERED,
    ]
    assert attempts[1].response_metadata == {"http_status": 200}
    persisted_job = repository.get(submitted.job_id)
    assert persisted_job is not None
    assert persisted_job.status is ProcessingJobStatus.SKIPPED_EXTERNAL_HANDLED
    assert persisted_job.attempt_count == 1
