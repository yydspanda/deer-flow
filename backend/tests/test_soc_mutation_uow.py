from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.gateway.routers import soc_review
from soc_agent.contracts import (
    ActorAuthSource,
    ActorContext,
    ActorType,
    AlertSourceType,
    AlertSummary,
    AnalysisRun,
    AnalysisRunStatus,
    CorrectionCommand,
    Decision,
    EntrySurface,
    ReviewNoteCommand,
    ReviewQueueCloseCommand,
    ReviewQueueItem,
    ReviewQueueStatus,
    ServiceRequestContext,
    SocAgentApprovalRequest,
    SocAgentApprovalRequestStatus,
    SocAgentApprovedActionCommand,
    SocAgentRiskLevel,
    SocExternalDispositionCanonicalStatus,
    SocExternalDispositionEvent,
    SocExternalDispositionMappingConfig,
    SocExternalDispositionStatusMapping,
    SocMemoryCandidateReviewCommand,
    SocMemoryCandidateReviewDecision,
    SocMemoryRecord,
    SocMemoryRetrievalActivationAction,
    SocMemoryRetrievalActivationCommand,
    SocMutationOperation,
    Verdict,
)
from soc_agent.core import (
    SocAgentApprovalService,
    SocExternalDispositionService,
    SocMemoryService,
    SocReviewService,
    SocServiceConflictError,
)
from soc_agent.db import SocBase, SqlAlchemyAlertRepository
from soc_agent.tui.app import SocReviewTUI


class InjectedMutationFailure(RuntimeError):
    pass


class RecordingEventSink:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)


def _repository(
    path: Path,
    *,
    fail_after_write: int | None = None,
    write_counts: list[int] | None = None,
) -> SqlAlchemyAlertRepository:
    engine = create_engine(f"sqlite:///{path}")
    SocBase.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def hook(write_count: int) -> None:
        if write_counts is not None:
            write_counts.append(write_count)
        if write_count == fail_after_write:
            raise InjectedMutationFailure(f"injected failure after write {write_count}")

    return SqlAlchemyAlertRepository(
        session_factory,
        mutation_write_hook=hook if fail_after_write is not None or write_counts is not None else None,
    )


def _seed_review(repository: SqlAlchemyAlertRepository) -> tuple[AnalysisRun, ReviewQueueItem]:
    run = AnalysisRun(
        run_id="RUN-MUTATION-001",
        alert_id="ALERT-MUTATION-001",
        status=AnalysisRunStatus.NEEDS_REVIEW,
        decision=Decision(
            verdict=Verdict.SUSPICIOUS,
            confidence=0.62,
            suggested_action="review",
            needs_review=True,
            reason="seed decision",
        ),
    )
    summary = AlertSummary(
        run_id=run.run_id,
        alert_id=run.alert_id,
        source_type=AlertSourceType.NDR,
        status=run.status,
        verdict=Verdict.SUSPICIOUS,
        confidence=0.62,
        needs_review=True,
        summary="seed summary",
    )
    queue_item = ReviewQueueItem(
        queue_id="REV-MUTATION-001",
        run_id=run.run_id,
        alert_id=run.alert_id,
        reason="seed review",
        source_type=AlertSourceType.NDR,
        verdict=Verdict.SUSPICIOUS,
        confidence=0.62,
    )
    repository.save_run(run)
    repository.save_alert_summary(summary)
    repository.save_review_item(queue_item)
    return run, queue_item


def _analyst_context(idempotency_key: str) -> ServiceRequestContext:
    return ServiceRequestContext(
        request_id=f"REQ-{idempotency_key}",
        idempotency_key=idempotency_key,
        actor=ActorContext(
            actor_id="analyst-1",
            actor_type=ActorType.USER,
            surface=EntrySurface.TUI,
            roles=["soc_analyst"],
            auth_source=ActorAuthSource.LOCAL_TUI,
        ),
    )


def _external_context() -> ServiceRequestContext:
    return ServiceRequestContext(
        request_id="REQ-EXTERNAL-MUTATION",
        actor=ActorContext(
            actor_id="external-adapter-1",
            actor_type=ActorType.SERVICE,
            surface=EntrySurface.DAEMON,
            roles=["external_disposition_adapter"],
            auth_source=ActorAuthSource.EXTERNAL_ADAPTER,
        ),
    )


def _memory_governor_context() -> ServiceRequestContext:
    return ServiceRequestContext(
        request_id="REQ-MEMORY-ACTIVATION",
        idempotency_key="memory-activation-001",
        actor=ActorContext(
            actor_id="memory-governor-1",
            actor_type=ActorType.USER,
            surface=EntrySurface.CLI,
            roles=["soc_memory_reviewer"],
        ),
    )


def _seed_confirmed_memory(repository: SqlAlchemyAlertRepository) -> SocMemoryRecord:
    _, queue_item = _seed_review(repository)
    note = _review_service(repository, RecordingEventSink()).add_note(
        ReviewNoteCommand(
            queue_id=queue_item.queue_id,
            note="Reusable governed retrieval test lesson.",
        ),
        context=_analyst_context("memory-note-seed"),
    )
    reviewed = SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
    ).review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=note.memory_candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="Memory reviewer confirmed the seeded lesson.",
        ),
        context=_analyst_context("memory-confirm-seed"),
    )
    assert reviewed.memory_record is not None
    return reviewed.memory_record


def _external_event() -> SocExternalDispositionEvent:
    return SocExternalDispositionEvent(
        external_system="test-itsm",
        external_case_id="CASE-MUTATION-001",
        source_event_id="EVENT-MUTATION-001",
        soc_run_id="RUN-MUTATION-001",
        soc_queue_id="REV-MUTATION-001",
        external_status="false-positive-closed",
        external_reason="analyst confirmed an authorized test",
        updated_at=datetime(2026, 7, 18, 8, 0, tzinfo=UTC),
        raw_payload_hash="sha256:test-external-event",
    )


def _external_mapping() -> SocExternalDispositionMappingConfig:
    return SocExternalDispositionMappingConfig(
        status_mappings=[
            SocExternalDispositionStatusMapping(
                external_system="test-itsm",
                external_status="false-positive-closed",
                canonical_status=SocExternalDispositionCanonicalStatus.CLOSED_FALSE_POSITIVE,
                trust_level="high",
            )
        ]
    )


def _review_service(repository: SqlAlchemyAlertRepository, sink: RecordingEventSink) -> SocReviewService:
    return SocReviewService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        memory_candidate_repository=repository,
        event_sink=sink,
    )


def _external_service(
    repository: SqlAlchemyAlertRepository,
    sink: RecordingEventSink,
) -> SocExternalDispositionService:
    return SocExternalDispositionService(
        repository=repository,
        mapping_config=_external_mapping(),
        alert_repository=repository,
        summary_repository=repository,
        review_queue_repository=repository,
        audit_repository=repository,
        event_sink=sink,
        memory_service=SocMemoryService(candidate_repository=repository, event_sink=sink),
    )


def test_correction_fault_after_each_write_rolls_back_entire_command(tmp_path: Path) -> None:
    baseline_path = tmp_path / "correction-success.db"
    seed_repository = _repository(baseline_path)
    _seed_review(seed_repository)
    writes: list[int] = []
    repository = _repository(baseline_path, write_counts=writes)
    _review_service(repository, RecordingEventSink()).correct(
        CorrectionCommand(
            run_id="RUN-MUTATION-001",
            corrected_verdict=Verdict.FALSE_POSITIVE,
            reason="analyst correction",
        ),
        context=_analyst_context("correct-001"),
    )
    assert writes == list(range(1, max(writes) + 1))
    assert max(writes) >= 7

    for fail_after_write in writes:
        path = tmp_path / f"correction-failure-{fail_after_write}.db"
        seed_repository = _repository(path)
        _seed_review(seed_repository)
        sink = RecordingEventSink()
        failing_repository = _repository(path, fail_after_write=fail_after_write)

        with pytest.raises(InjectedMutationFailure, match=f"write {fail_after_write}"):
            _review_service(failing_repository, sink).correct(
                CorrectionCommand(
                    run_id="RUN-MUTATION-001",
                    corrected_verdict=Verdict.FALSE_POSITIVE,
                    reason="analyst correction",
                ),
                context=_analyst_context("correct-001"),
            )

        persisted_run = seed_repository.get_run("RUN-MUTATION-001")
        persisted_summary = seed_repository.get_alert_summary("RUN-MUTATION-001")
        persisted_queue = seed_repository.get_review_item("REV-MUTATION-001")
        assert persisted_run is not None and persisted_run.corrections == []
        assert persisted_run.decision is not None and persisted_run.decision.verdict is Verdict.SUSPICIOUS
        assert persisted_summary is not None and persisted_summary.verdict is Verdict.SUSPICIOUS
        assert persisted_queue is not None and persisted_queue.status is ReviewQueueStatus.OPEN
        assert seed_repository.list_memory_candidates() == []
        assert seed_repository.list_audit_records("RUN-MUTATION-001") == []
        assert seed_repository.list_mutation_audits() == []
        assert sink.events == []


def test_external_feedback_fault_after_each_write_rolls_back_entire_command(tmp_path: Path) -> None:
    baseline_path = tmp_path / "external-success.db"
    seed_repository = _repository(baseline_path)
    _seed_review(seed_repository)
    writes: list[int] = []
    repository = _repository(baseline_path, write_counts=writes)
    _external_service(repository, RecordingEventSink()).apply_event(
        _external_event(),
        context=_external_context(),
    )
    assert writes == list(range(1, max(writes) + 1))
    assert max(writes) >= 8

    for fail_after_write in writes:
        path = tmp_path / f"external-failure-{fail_after_write}.db"
        seed_repository = _repository(path)
        _seed_review(seed_repository)
        sink = RecordingEventSink()
        failing_repository = _repository(path, fail_after_write=fail_after_write)

        with pytest.raises(InjectedMutationFailure, match=f"write {fail_after_write}"):
            _external_service(failing_repository, sink).apply_event(
                _external_event(),
                context=_external_context(),
            )

        persisted_run = seed_repository.get_run("RUN-MUTATION-001")
        persisted_summary = seed_repository.get_alert_summary("RUN-MUTATION-001")
        persisted_queue = seed_repository.get_review_item("REV-MUTATION-001")
        assert persisted_run is not None and persisted_run.corrections == []
        assert persisted_run.decision is not None and persisted_run.decision.verdict is Verdict.SUSPICIOUS
        assert persisted_summary is not None and persisted_summary.verdict is Verdict.SUSPICIOUS
        assert persisted_queue is not None and persisted_queue.status is ReviewQueueStatus.OPEN
        assert seed_repository.list_external_dispositions() == []
        assert seed_repository.list_memory_candidates() == []
        assert seed_repository.list_audit_records("RUN-MUTATION-001") == []
        assert seed_repository.list_mutation_audits() == []
        assert sink.events == []


def test_memory_retrieval_activation_fault_rolls_back_record_and_audit(
    tmp_path: Path,
) -> None:
    success_path = tmp_path / "memory-activation-success.db"
    seed_repository = _repository(success_path)
    seeded = _seed_confirmed_memory(seed_repository)
    writes: list[int] = []
    repository = _repository(success_path, write_counts=writes)
    command = SocMemoryRetrievalActivationCommand(
        memory_id=seeded.memory_id,
        action=SocMemoryRetrievalActivationAction.ENABLE,
        expected_record_version=seeded.version,
        reason="Memory governor approved bounded retrieval.",
        activation_valid_until=datetime.now(UTC) + timedelta(days=90),
        review_after_days=30,
    )
    SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
    ).set_retrieval_activation(command, context=_memory_governor_context())
    assert writes == [1, 2]

    for fail_after_write in writes:
        path = tmp_path / f"memory-activation-failure-{fail_after_write}.db"
        seed_repository = _repository(path)
        seeded = _seed_confirmed_memory(seed_repository)
        baseline_audit_count = len(seed_repository.list_mutation_audits())
        sink = RecordingEventSink()
        failing_repository = _repository(path, fail_after_write=fail_after_write)
        failing_command = command.model_copy(
            update={
                "memory_id": seeded.memory_id,
                "expected_record_version": seeded.version,
            }
        )

        with pytest.raises(InjectedMutationFailure, match=f"write {fail_after_write}"):
            SocMemoryService(
                candidate_repository=failing_repository,
                record_repository=failing_repository,
                event_sink=sink,
            ).set_retrieval_activation(
                failing_command,
                context=_memory_governor_context(),
            )

        persisted = seed_repository.get_memory_record(seeded.memory_id)
        assert persisted is not None
        assert persisted.version == seeded.version
        assert persisted.retrieval_enabled is False
        assert len(seed_repository.list_mutation_audits()) == baseline_audit_count
        assert sink.events == []


def test_correction_and_external_feedback_exact_retries_create_one_logical_result(
    tmp_path: Path,
) -> None:
    correction_repository = _repository(tmp_path / "correction-idempotency.db")
    _seed_review(correction_repository)
    correction_service = _review_service(correction_repository, RecordingEventSink())
    correction = CorrectionCommand(
        run_id="RUN-MUTATION-001",
        corrected_verdict=Verdict.FALSE_POSITIVE,
        reason="analyst correction",
    )
    context = _analyst_context("correct-idempotent-001")

    first = correction_service.correct(correction, context=context)
    second = correction_service.correct(correction, context=context)

    assert second.corrections[-1].correction_id == first.corrections[-1].correction_id
    assert len(second.corrections) == 1
    assert len(correction_repository.list_memory_candidates()) == 1
    assert len(correction_repository.list_audit_records(first.run_id)) == 1
    assert len(correction_repository.list_mutation_audits(operation=SocMutationOperation.REVIEW_CORRECT)) == 1
    with pytest.raises(SocServiceConflictError, match="different content"):
        correction_service.correct(
            correction.model_copy(update={"reason": "changed retry"}),
            context=context,
        )

    external_repository = _repository(tmp_path / "external-idempotency.db")
    _seed_review(external_repository)
    external_service = _external_service(external_repository, RecordingEventSink())
    first_external = external_service.apply_event(
        _external_event(),
        context=_external_context(),
    )
    second_external = external_service.apply_event(
        _external_event(),
        context=_external_context(),
    )

    assert second_external.idempotent is True
    assert second_external.record.disposition_id == first_external.record.disposition_id
    assert len(external_repository.list_external_dispositions()) == 1
    assert len(external_repository.list_memory_candidates()) == 1
    assert len(external_repository.list_audit_records("RUN-MUTATION-001")) == 2
    assert len(external_repository.list_mutation_audits()) == 2


def test_review_memory_and_approval_mutations_share_secret_safe_audit_chain(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "audit-coverage.db")
    _, queue_item = _seed_review(repository)
    review_service = _review_service(repository, RecordingEventSink())

    review_service.close_queue_item(
        ReviewQueueCloseCommand(queue_id=queue_item.queue_id, reason="review completed"),
        context=_analyst_context("close-001"),
    )
    note_result = review_service.add_note(
        ReviewNoteCommand(
            queue_id=queue_item.queue_id,
            note="confirmed reusable analyst lesson",
            scenario_key="network.authorized_test",
        ),
        context=_analyst_context("note-001"),
    )
    memory_service = SocMemoryService(candidate_repository=repository, record_repository=repository)
    memory_service.review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=note_result.memory_candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="memory reviewer confirmed the lesson",
        ),
        context=_analyst_context("memory-review-001"),
    )

    submitter = _analyst_context("approval-submit-001")
    approval_request = SocAgentApprovalRequest(
        approval_request_id="APR-MUTATION-001",
        permission_decision_id="PERM-MUTATION-001",
        route="response.block_ip",
        action="response.block_ip",
        risk_level=SocAgentRiskLevel.HIGH_RISK,
        reason="token=super-secret requires controlled response",
        requested_by=submitter.actor,
        action_payload={"ip": "203.0.113.10", "password": "p@ss"},
    )
    approval_service = SocAgentApprovalService(
        request_repository=repository,
        grant_repository=repository,
    )
    approval_service.submit_request(approval_request, context=submitter)
    approver_context = ServiceRequestContext(
        request_id="REQ-APPROVE-001",
        idempotency_key="approval-approve-001",
        actor=ActorContext(
            actor_id="approver-1",
            surface=EntrySurface.WEB,
            roles=["soc_admin"],
            auth_source=ActorAuthSource.SESSION,
        ),
    )
    grant = approval_service.approve(
        approval_request.approval_request_id,
        context=approver_context,
        reason="approved after independent review",
    )
    operator = ActorContext(
        actor_id="operator-1",
        surface=EntrySurface.TUI,
        roles=["soc_analyst"],
        auth_source=ActorAuthSource.LOCAL_TUI,
    )
    approval_service.dry_run_approved_action(
        SocAgentApprovedActionCommand(
            route=grant.route,
            action=grant.action,
            execution_token_id=grant.execution_token_id,
            dry_run=True,
            payload={"password": "p@ss", "ip": "203.0.113.10"},
        ),
        context=ServiceRequestContext(
            request_id="REQ-DRY-RUN-001",
            idempotency_key="approval-dry-run-001",
            actor=operator,
        ),
    )
    approval_service.execute_approved_action(
        SocAgentApprovedActionCommand(
            route=grant.route,
            action=grant.action,
            execution_token_id=grant.execution_token_id,
            dry_run=False,
            payload={"password": "p@ss", "ip": "203.0.113.10"},
        ),
        context=ServiceRequestContext(
            request_id="REQ-EXECUTE-001",
            idempotency_key="approval-execute-001",
            actor=operator,
        ),
    )

    records = repository.list_mutation_audits(limit=100)
    operations = {record.operation for record in records}
    assert {
        SocMutationOperation.REVIEW_CLOSE,
        SocMutationOperation.REVIEW_NOTE,
        SocMutationOperation.MEMORY_REVIEW,
        SocMutationOperation.APPROVAL_REQUEST_SUBMIT,
        SocMutationOperation.APPROVAL_REQUEST_APPROVE,
        SocMutationOperation.APPROVAL_ACTION_DRY_RUN,
        SocMutationOperation.APPROVAL_ACTION_EXECUTE,
    }.issubset(operations)
    serialized = json.dumps([record.model_dump(mode="json") for record in records])
    assert "super-secret" not in serialized
    assert "p@ss" not in serialized
    assert "execution_token_id" not in serialized
    submit_audit = next(record for record in records if record.operation is SocMutationOperation.APPROVAL_REQUEST_SUBMIT)
    assert "[REDACTED]" in submit_audit.reason
    assert submit_audit.actor.auth_source is ActorAuthSource.LOCAL_TUI
    assert repository.get_approval_request(approval_request.approval_request_id).status is SocAgentApprovalRequestStatus.APPROVED


def test_approval_reject_and_expire_are_audited(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "approval-terminal-audit.db")
    service = SocAgentApprovalService(request_repository=repository, grant_repository=repository)
    submitter = _analyst_context("approval-submit-terminal")

    for suffix, operation in (
        ("REJECT", SocMutationOperation.APPROVAL_REQUEST_REJECT),
        ("EXPIRE", SocMutationOperation.APPROVAL_REQUEST_EXPIRE),
    ):
        request = SocAgentApprovalRequest(
            approval_request_id=f"APR-MUTATION-{suffix}",
            permission_decision_id=f"PERM-MUTATION-{suffix}",
            route="response.isolate_host",
            action="response.isolate_host",
            risk_level=SocAgentRiskLevel.HIGH_RISK,
            reason="high-risk response",
            requested_by=submitter.actor,
        )
        service.submit_request(
            request,
            context=submitter.model_copy(update={"idempotency_key": f"submit-{suffix.lower()}"}),
        )
        resolution_context = ServiceRequestContext(
            request_id=f"REQ-{suffix}",
            idempotency_key=f"resolve-{suffix.lower()}",
            actor=ActorContext(
                actor_id="approver-1",
                surface=EntrySurface.WEB,
                roles=["soc_admin"],
                auth_source=ActorAuthSource.SESSION,
            ),
        )
        if suffix == "REJECT":
            service.reject(request.approval_request_id, context=resolution_context, reason="rejected")
        else:
            service.expire(request.approval_request_id, context=resolution_context, reason="expired")
        assert repository.list_mutation_audits(operation=operation)[0].target_id == request.approval_request_id


def test_review_api_persists_authenticated_mutation_audit(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "api-audit.db")
    _, queue_item = _seed_review(repository)
    service = _review_service(repository, RecordingEventSink())
    request = SimpleNamespace(
        headers={"idempotency-key": "api-close-001", "x-soc-surface": "web"},
        state=SimpleNamespace(
            auth_source="session",
            user=SimpleNamespace(id="api-analyst-1", system_role="user"),
        ),
    )

    soc_review.close_review_item(
        queue_item.queue_id,
        soc_review.ReviewQueueCloseRequest(reason="API analyst completed review"),
        request,
        service=service,
    )

    audit = repository.list_mutation_audits(operation=SocMutationOperation.REVIEW_CLOSE)[0]
    assert audit.actor.actor_id == "api-analyst-1"
    assert audit.actor.surface is EntrySurface.WEB
    assert audit.actor.auth_source is ActorAuthSource.SESSION
    assert audit.idempotency_key == "api-close-001"


def test_review_tui_command_persists_mutation_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path / "tui-audit.db")
    _, queue_item = _seed_review(repository)
    app = SocReviewTUI(_review_service(repository, RecordingEventSink()))
    monkeypatch.setattr(app, "_notice", lambda *args, **kwargs: None)
    monkeypatch.setattr(app, "_load_queue", lambda: None)

    app._close_item(f"{queue_item.queue_id} TUI analyst completed review")

    audit = repository.list_mutation_audits(operation=SocMutationOperation.REVIEW_CLOSE)[0]
    assert audit.actor.surface is EntrySurface.TUI
    assert audit.actor.auth_source is ActorAuthSource.LOCAL_TUI
    assert audit.reason == "TUI analyst completed review"
