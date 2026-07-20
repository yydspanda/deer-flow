from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from soc_agent.contracts import (
    ActorContext,
    AnalysisRequestJournalStatus,
    AnalysisRunRecoveryCommand,
    AnalysisRunStatus,
    AuditAction,
    CorrectionCommand,
    CorrelationQuery,
    DecisionConfidenceSource,
    InvestigationEvidence,
    ReviewQueueStatus,
    RuntimeFailureKind,
    ServiceRequestContext,
    SimilarAlertQuery,
    SocAgentApprovalRequest,
    SocAgentApprovalRequestStatus,
    SocAgentApprovedActionCommand,
    SocAgentRiskLevel,
    SocExternalDispositionApplyStatus,
    SocExternalDispositionCanonicalStatus,
    SocExternalDispositionEvent,
    SocExternalDispositionRecord,
    SocMemoryCandidateCreateCommand,
    SocMemoryCandidateReviewCommand,
    SocMemoryCandidateReviewDecision,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateStatus,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryDecisionImpact,
    SocMemoryRecordStatus,
    SocMemoryRetrievalActivationAction,
    SocMemoryRetrievalActivationCommand,
    SocMemoryTargetArtifact,
    SocMutationOperation,
    Verdict,
)
from soc_agent.core import (
    DeterministicAnalysisRuntime,
    SocAgentApprovalService,
    SocAnalysisService,
    SocCorrelationService,
    SocMemoryService,
    SocServiceConflictError,
)
from soc_agent.core.service import SocReviewService
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


def _sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def _analyst_context() -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(actor_id="analyst-1", roles=["soc_analyst"]),
    )


def _memory_governor_context() -> ServiceRequestContext:
    return ServiceRequestContext(
        idempotency_key="memory-repository:retrieval-enable",
        actor=ActorContext(
            actor_id="memory-governor-1",
            roles=["soc_memory_reviewer"],
        ),
    )


def _repository() -> SqlAlchemyAlertRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return SqlAlchemyAlertRepository(session_factory)


def _repository_memory_candidate_command(
    *,
    run_id: str = "RUN-MEM-1",
    alert_id: str = "ALT-MEM-1",
    queue_id: str = "REV-MEM-1",
    idempotency_key: str = "memory:repo:run-1",
) -> SocMemoryCandidateCreateCommand:
    return SocMemoryCandidateCreateCommand(
        candidate_type=SocMemoryCandidateType.BENIGN_PATTERN,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        summary="Repository memory candidate",
        content="Authorized maintenance activity should remain a pending candidate until reviewed.",
        tenant_scope="pingan",
        tenant_id="pingan",
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.MANUAL_NOTE,
            source_id="memory-repo-test",
            run_id=run_id,
            alert_id=alert_id,
            queue_id=queue_id,
        ),
        evidence_refs=["manual:repo-test"],
        validity=SocMemoryCandidateValidity(notes="Repository persistence test candidate."),
        idempotency_key=idempotency_key,
        confidence=0.6,
        facets={"tenant": ["pingan"], "domain": ["hids"]},
        decision_impact=SocMemoryDecisionImpact.REVIEW_HINT,
        review_owner="soc_analyst",
        labels=["candidate-only"],
    )


def test_sqlalchemy_alert_repository_saves_and_gets_run() -> None:
    repository = _repository()
    run = SocAnalysisService(repository=repository, summary_repository=repository).analyze(_sample("approved_scanner.json"))

    saved = repository.get_run(run.run_id)
    summary = repository.get_alert_summary(run.run_id)

    assert saved == run
    assert saved is not None
    assert saved.input_payload == run.input_payload
    assert saved.input_hash == run.input_hash
    assert summary is not None
    assert summary.alert_id == run.alert_id
    assert summary.verdict == Verdict.FALSE_POSITIVE
    assert summary.rule_code == "EDR-SCAN-001"
    assert "host:scanner-01" in summary.entity_keys


def test_sqlalchemy_pre_provider_journal_survives_process_loss_and_recovers() -> None:
    class SimulatedProcessLoss(BaseException):
        pass

    class ProcessLossAnalyzer:
        step_name = "analyze_llm"
        model_name = "process-loss-model"
        prompt_version = "process-loss-prompt-v1"

        def analyze(self, request):
            raise SimulatedProcessLoss

    repository = _repository()
    crashing_service = SocAnalysisService(
        runtime=DeterministicAnalysisRuntime(analyzer=ProcessLossAnalyzer()),
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        analysis_persistence=repository,
    )
    context = ServiceRequestContext(
        request_id="REQ-PROCESS-LOSS-1",
        trace_id="TRACE-PROCESS-LOSS-1",
        idempotency_key="kafka:soc.alerts.raw.v1:0:process-loss",
    )

    with pytest.raises(SimulatedProcessLoss):
        crashing_service.analyze(_sample("approved_scanner.json"), context=context)

    interrupted_candidate = repository.list_runs(limit=1)[0]
    assert interrupted_candidate.status is AnalysisRunStatus.RUNNING
    assert interrupted_candidate.ended_at is None
    assert interrupted_candidate.request_journal is not None
    assert interrupted_candidate.request_journal.status is AnalysisRequestJournalStatus.RUNNING
    assert interrupted_candidate.request_journal.request_id == "REQ-PROCESS-LOSS-1"
    assert interrupted_candidate.request_journal.model_name == "process-loss-model"
    assert interrupted_candidate.request_journal.provider_step_name == "analyze_llm"
    assert interrupted_candidate.request_journal.request_hash
    assert "kafka:soc.alerts.raw.v1:0:process-loss" not in interrupted_candidate.request_journal.model_dump_json()

    recovery_service = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        analysis_persistence=repository,
    )
    with pytest.raises(SocServiceConflictError, match="use recover"):
        recovery_service.replay(interrupted_candidate.run_id)
    with pytest.raises(SocServiceConflictError, match="recovery window"):
        recovery_service.recover(
            AnalysisRunRecoveryCommand(
                run_id=interrupted_candidate.run_id,
                reason="worker health check is still inside the lease window",
                stale_after_seconds=300,
            )
        )

    recovered = recovery_service.recover(
        AnalysisRunRecoveryCommand(
            run_id=interrupted_candidate.run_id,
            reason="worker process exited during provider call",
            stale_after_seconds=0,
        ),
        context=ServiceRequestContext(idempotency_key="recover:process-loss-1"),
    )

    original = repository.get_run(interrupted_candidate.run_id)
    assert original is not None
    assert original.status is AnalysisRunStatus.INTERRUPTED
    assert original.request_journal is not None
    assert original.request_journal.status is AnalysisRequestJournalStatus.INTERRUPTED
    assert original.request_journal.recovery_reason == "worker process exited during provider call"
    assert original.request_journal.recovery_run_id == recovered.run_id
    assert repository.claim_run_recovery(original, expected_status=AnalysisRunStatus.RUNNING) is False
    assert recovered.replay_of_run_id == original.run_id
    assert recovered.request_journal is not None
    assert recovered.request_journal.action is AuditAction.REPLAY
    assert recovered.request_journal.status is AnalysisRequestJournalStatus.COMPLETED

    idempotent = recovery_service.recover(
        AnalysisRunRecoveryCommand(
            run_id=interrupted_candidate.run_id,
            reason="worker process exited during provider call",
            stale_after_seconds=0,
        ),
        context=ServiceRequestContext(idempotency_key="recover:process-loss-1"),
    )
    assert idempotent.run_id == recovered.run_id


def test_sqlalchemy_pre_provider_journal_finalizes_retryable_timeout() -> None:
    class TimeoutAnalyzer:
        step_name = "analyze_llm"
        model_name = "timeout-model"
        prompt_version = "timeout-prompt-v1"

        def analyze(self, request):
            raise TimeoutError("provider token must not be persisted")

    repository = _repository()
    run = SocAnalysisService(
        runtime=DeterministicAnalysisRuntime(analyzer=TimeoutAnalyzer()),
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        analysis_persistence=repository,
    ).analyze(_sample("approved_scanner.json"))

    assert run.status is AnalysisRunStatus.FAILED
    assert run.failure is not None
    assert run.failure.kind is RuntimeFailureKind.ANALYZER_TIMEOUT
    assert run.failure.retryable is True
    assert run.request_journal is not None
    assert run.request_journal.status is AnalysisRequestJournalStatus.FAILED
    assert run.request_journal.failure_kind is RuntimeFailureKind.ANALYZER_TIMEOUT
    assert run.request_journal.failure_retryable is True
    assert run.request_journal.finalized_at is not None
    assert "provider token" not in run.request_journal.model_dump_json()
    assert repository.get_run(run.run_id) == run


def test_sqlalchemy_analysis_bundle_rolls_back_every_read_model_on_failure() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))

    class CapturingRuntime:
        def __init__(self) -> None:
            self.run = None
            self.runtime = DeterministicAnalysisRuntime()

        def analyze(self, payload):
            self.run = self.runtime.analyze(payload)
            return self.run

        def analyze_journaled(self, payload, *, before_provider):
            self.run = self.runtime.analyze_journaled(
                payload,
                before_provider=before_provider,
            )
            return self.run

    runtime = CapturingRuntime()

    def fail_audit_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT") and "soc_decision_audit_log" in statement:
            raise RuntimeError("forced audit persistence failure")

    event.listen(engine, "before_cursor_execute", fail_audit_insert)
    try:
        with pytest.raises(RuntimeError, match="forced audit persistence failure"):
            SocAnalysisService(
                runtime=runtime,
                repository=repository,
                summary_repository=repository,
                audit_repository=repository,
                review_queue_repository=repository,
                analysis_persistence=repository,
            ).analyze(_sample("approved_scanner.json"))
    finally:
        event.remove(engine, "before_cursor_execute", fail_audit_insert)

    assert runtime.run is not None
    persisted = repository.get_run(runtime.run.run_id)
    assert persisted is not None
    assert persisted.status is AnalysisRunStatus.RUNNING
    assert persisted.request_journal is not None
    assert persisted.request_journal.status is AnalysisRequestJournalStatus.RUNNING
    assert repository.get_alert_summary(runtime.run.run_id) is None
    assert repository.list_review_items(limit=10) == []
    assert repository.list_audit_records(runtime.run.run_id) == []


def test_sqlalchemy_alert_repository_updates_existing_run() -> None:
    repository = _repository()
    run = SocAnalysisService(repository=repository, summary_repository=repository).analyze(_sample("approved_scanner.json"))
    run.model_name = "updated-model"

    repository.save_run(run)

    saved = repository.get_run(run.run_id)
    assert saved is not None
    assert saved.model_name == "updated-model"


def test_sqlalchemy_alert_repository_supports_service_replay() -> None:
    repository = _repository()
    service = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
    )
    original = service.analyze(_sample("approved_scanner.json"))

    replayed = service.replay(original.run_id)

    assert replayed.run_id != original.run_id
    assert replayed.replay_of_run_id == original.run_id
    assert repository.get_run(original.run_id) == original
    assert repository.get_run(replayed.run_id) == replayed

    original_records = repository.list_audit_records(original.run_id)
    replay_records = repository.list_audit_records(replayed.run_id)
    assert original_records[0].action == AuditAction.ANALYSIS
    assert replay_records[0].action == AuditAction.REPLAY
    assert replay_records[0].replay_of_run_id == original.run_id

    replay_summary = repository.get_alert_summary(replayed.run_id)
    assert replay_summary is not None
    assert replay_summary.replay_of_run_id == original.run_id
    assert replayed.run_id in {summary.run_id for summary in repository.list_alert_summaries(limit=2)}


def test_sqlalchemy_alert_repository_finds_audit_by_idempotency_key() -> None:
    repository = _repository()
    service = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
    )

    run = service.analyze(
        _sample("approved_scanner.json"),
        context=ServiceRequestContext(idempotency_key="kafka:soc.alerts.raw.v1:0:99"),
    )

    audit_record = repository.find_audit_record_by_idempotency_key(
        "kafka:soc.alerts.raw.v1:0:99",
        action=AuditAction.ANALYSIS.value,
    )

    assert audit_record is not None
    assert audit_record.run_id == run.run_id
    assert audit_record.payload["idempotency_key"] == "kafka:soc.alerts.raw.v1:0:99"
    assert repository.find_audit_record_by_idempotency_key("missing") is None


def test_sqlalchemy_alert_repository_lists_recent_runs() -> None:
    repository = _repository()
    service = SocAnalysisService(repository=repository, summary_repository=repository)
    first = service.analyze(_sample("approved_scanner.json"))
    second = service.analyze(_sample("missing_fields.json"))

    recent = repository.list_runs(limit=1)

    assert [run.run_id for run in recent] == [second.run_id]
    assert first.run_id not in {run.run_id for run in recent}


def test_sqlalchemy_alert_repository_persists_corrections() -> None:
    repository = _repository()
    run = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
    ).analyze(_sample("approved_scanner.json"))

    corrected = SocReviewService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
    ).correct(
        CorrectionCommand(
            run_id=run.run_id,
            corrected_verdict=Verdict.TRUE_POSITIVE,
            reason="Confirmed malicious behavior after host review.",
        ),
        context=_analyst_context(),
    )

    saved = repository.get_run(run.run_id)
    assert saved == corrected
    assert saved is not None
    assert saved.decision is not None
    assert saved.decision.verdict == Verdict.TRUE_POSITIVE
    assert saved.decision.confidence_source is DecisionConfidenceSource.HUMAN_CONFIRMATION
    assert saved.decision.confidence_is_calibrated is False
    assert saved.decision.policy_version == "soc.correction_policy.v1"
    assert saved.corrections[0].previous_verdict == Verdict.FALSE_POSITIVE

    records = repository.list_audit_records(run.run_id)
    assert [record.action for record in records] == [AuditAction.ANALYSIS, AuditAction.CORRECTION]
    assert records[1].previous_verdict == Verdict.FALSE_POSITIVE
    assert records[1].final_verdict == Verdict.TRUE_POSITIVE
    assert records[1].payload["confidence_source"] == "human_confirmation"
    assert records[1].payload["confidence_is_calibrated"] is False

    summary = repository.get_alert_summary(run.run_id)
    assert summary is not None
    assert summary.verdict == Verdict.TRUE_POSITIVE
    assert summary.confidence == 1.0
    assert summary.confidence_source is DecisionConfidenceSource.HUMAN_CONFIRMATION
    assert summary.needs_review is False


def test_sqlalchemy_alert_repository_finds_similar_alert_summaries() -> None:
    repository = _repository()
    service = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
    )
    similar = service.analyze(_sample("pingan_legacy_apt.json"))
    current = service.analyze(_sample("pingan_legacy_apt.json"))
    current_summary = repository.get_alert_summary(current.run_id)
    assert current_summary is not None

    matches = repository.find_similar_alert_summaries(
        SimilarAlertQuery(
            run_id=current_summary.run_id,
            detection_key=current_summary.detection_key,
            rule_code=current_summary.rule_code,
            source_type=current_summary.source_type,
            category=current_summary.category,
            entity_keys=current_summary.entity_keys,
            limit=5,
        )
    )

    assert len(matches) == 1
    match = matches[0]
    assert match.summary.run_id == similar.run_id
    assert match.score >= 90
    assert "detection_key:sec_guard_apt:rule_code:rpaadm_002635" in match.matched_reasons
    assert "rule_code:RPAADM_002635" in match.matched_reasons
    assert "entity_key:ip:30.180.248.178" in match.matched_reasons


def test_sqlalchemy_correlation_service_returns_reusable_evidence() -> None:
    repository = _repository()
    service = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
    )
    similar = service.analyze(_sample("pingan_legacy_apt.json"))
    current = service.analyze(_sample("pingan_legacy_apt.json"))
    repository.save_evidence(
        InvestigationEvidence(
            evidence_id="EVI-CORRELATION-1",
            route="asset.locate",
            action="asset.locate",
            status="success",
            message="Asset location completed.",
            result_payload={"owner": "mock-owner", "source": "unit-test"},
            run_id=similar.run_id,
            alert_id=similar.alert_id,
        )
    )

    result = SocCorrelationService(
        summary_repository=repository,
        evidence_repository=repository,
    ).correlate(CorrelationQuery(run_id=current.run_id, limit=5))

    assert result.subject_summary.run_id == current.run_id
    assert len(result.matches) == 1
    assert result.matches[0].summary.run_id == similar.run_id
    assert result.reusable_evidence_count == 1
    assert result.matches[0].reusable_evidence[0].evidence_id == "EVI-CORRELATION-1"
    assert result.matches[0].reusable_evidence[0].result_payload["owner"] == "mock-owner"


def test_sqlalchemy_alert_repository_persists_review_queue_items() -> None:
    repository = _repository()
    run = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
    ).analyze(_sample("pingan_legacy_apt.json"))

    items = repository.list_review_items(status=ReviewQueueStatus.OPEN)

    assert len(items) == 1
    item = items[0]
    assert item.run_id == run.run_id
    assert item.alert_id == "2026494"
    assert item.status == ReviewQueueStatus.OPEN
    assert item.reason == "uncertain_verdict"
    assert any(reason.value == "confidence_not_calibrated" for reason in item.review_reasons)
    assert item.priority.value == "high"
    assert item.rule_code == "RPAADM_002635"
    assert "ip:30.180.248.178" in item.entity_keys
    assert repository.get_open_review_item_by_run(run.run_id) == item
    assert repository.get_review_item(item.queue_id) == item


def test_sqlalchemy_alert_repository_persists_investigation_evidence() -> None:
    repository = _repository()
    older = InvestigationEvidence(
        evidence_id="EVI-OLDER",
        route="asset.lookup",
        action="asset.lookup",
        status="success",
        message="Asset lookup completed.",
        result_payload={"asset_found": True, "asset_record": {"asset_id": "asset-001"}},
        queue_id="REV-1",
        run_id="RUN-1",
        alert_id="ALT-1",
        thread_id="SOC-THREAD-1",
        source_proposal_id="PROP-1",
        actor=ActorContext(actor_id="analyst-1"),
        created_at=datetime(2026, 7, 5, 10, 0, tzinfo=UTC),
    )
    newer = InvestigationEvidence(
        evidence_id="EVI-NEWER",
        route="asset.locate",
        action="asset.locate",
        status="success",
        message="Asset location completed.",
        result_payload={"mcp_result": {"company_code": "PA011", "mocked": True}},
        mocked=True,
        queue_id="REV-1",
        run_id="RUN-1",
        alert_id="ALT-1",
        thread_id="SOC-THREAD-2",
        created_at=older.created_at + timedelta(minutes=1),
    )
    unrelated = InvestigationEvidence(
        evidence_id="EVI-UNRELATED",
        route="asset.locate",
        action="asset.locate",
        status="success",
        message="Unrelated asset location completed.",
        result_payload={"mcp_result": {"company_code": "PA999"}},
        queue_id="REV-2",
        run_id="RUN-2",
        alert_id="ALT-2",
        thread_id="SOC-THREAD-3",
        created_at=older.created_at + timedelta(minutes=2),
    )

    repository.save_evidence(older)
    repository.save_evidence(newer)
    repository.save_evidence(unrelated)

    by_queue = repository.list_evidence(queue_id="REV-1")
    assert [item.evidence_id for item in by_queue] == ["EVI-NEWER", "EVI-OLDER"]
    assert by_queue[0].mocked is True
    assert by_queue[0].result_payload["mcp_result"]["company_code"] == "PA011"
    assert by_queue[1].actor is not None
    assert by_queue[1].actor.actor_id == "analyst-1"
    assert repository.list_evidence(run_id="RUN-1", limit=1)[0].evidence_id == "EVI-NEWER"
    assert repository.list_evidence(alert_id="ALT-2")[0].evidence_id == "EVI-UNRELATED"
    assert repository.list_evidence(thread_id="SOC-THREAD-1")[0].evidence_id == "EVI-OLDER"
    assert repository.list_evidence(queue_id="REV-MISSING") == []


def test_review_service_context_loads_sqlalchemy_investigation_evidence() -> None:
    repository = _repository()
    run = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
    ).analyze(_sample("pingan_legacy_apt.json"))
    item = repository.get_open_review_item_by_run(run.run_id)
    assert item is not None
    repository.save_evidence(
        InvestigationEvidence(
            route="asset.locate",
            action="asset.locate",
            status="success",
            message="Asset location completed.",
            result_payload={"mcp_result": {"company_code": "PA011", "mocked": True}},
            queue_id=item.queue_id,
            run_id=run.run_id,
            alert_id=run.alert_id,
        )
    )

    context = SocReviewService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        evidence_repository=repository,
    ).get_investigation_context(item.queue_id)

    assert len(context.action_evidence) == 1
    assert context.action_evidence[0].action == "asset.locate"
    assert context.action_evidence[0].result_payload["mcp_result"]["company_code"] == "PA011"


def test_review_service_context_loads_sqlalchemy_external_dispositions() -> None:
    repository = _repository()
    run = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
    ).analyze(_sample("pingan_legacy_apt.json"))
    item = repository.get_open_review_item_by_run(run.run_id)
    assert item is not None
    record = SocExternalDispositionRecord(
        event=SocExternalDispositionEvent(
            external_system="zeus",
            external_case_id="ZEUS-CASE-1",
            soc_alert_id=run.alert_id,
            soc_run_id=run.run_id,
            soc_queue_id=item.queue_id,
            external_status="误报关闭",
            external_reason="同事在老 ZEUS 工单中确认是授权测试。",
            updated_at=datetime.now(UTC),
            raw_payload_hash="hash-zeus-case-1",
        ),
        canonical_status=SocExternalDispositionCanonicalStatus.CLOSED_FALSE_POSITIVE,
        apply_status=SocExternalDispositionApplyStatus.MAPPED,
        idempotency_key="external_disposition:zeus:case-1:event-1",
        target_run_id=run.run_id,
        target_alert_id=run.alert_id,
        target_queue_id=item.queue_id,
        matched_by="soc_queue_id",
        apply_reason="external status mapped to a unique local target",
        correction_id="CORR-ZEUS-1",
        memory_candidate_id="MEM-ZEUS-1",
    )
    repository.save_external_disposition(record)

    assert repository.find_external_disposition_by_idempotency_key(record.idempotency_key) == record
    assert repository.list_external_dispositions(queue_id=item.queue_id)[0] == record
    assert repository.list_external_dispositions(run_id=run.run_id)[0] == record
    assert repository.list_external_dispositions(alert_id=run.alert_id)[0] == record
    assert repository.list_external_dispositions(external_system="zeus", external_case_id="ZEUS-CASE-1")[0] == record

    context = SocReviewService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        evidence_repository=repository,
        external_disposition_repository=repository,
    ).get_investigation_context(item.queue_id)

    assert len(context.external_dispositions) == 1
    assert context.external_dispositions[0].event.external_system == "zeus"
    assert context.external_dispositions[0].canonical_status is SocExternalDispositionCanonicalStatus.CLOSED_FALSE_POSITIVE


def test_sqlalchemy_memory_candidate_repository_persists_and_filters_candidates() -> None:
    repository = _repository()
    service = SocMemoryService(candidate_repository=repository, record_repository=repository)

    candidate = service.propose_candidate(_repository_memory_candidate_command())
    duplicate = service.propose_candidate(_repository_memory_candidate_command())

    assert duplicate == candidate
    assert repository.get_memory_candidate(candidate.candidate_id) == candidate
    assert repository.find_memory_candidate_by_idempotency_key("memory:repo:run-1") == candidate
    assert repository.list_memory_candidates(status=SocMemoryCandidateStatus.PENDING_REVIEW) == [candidate]
    assert repository.list_memory_candidates(tenant_scope="pingan") == [candidate]
    assert repository.list_memory_candidates(tenant_id="pingan") == [candidate]
    assert repository.list_memory_candidates(run_id="RUN-MEM-1") == [candidate]
    assert repository.list_memory_candidates(alert_id="ALT-MEM-1") == [candidate]
    assert repository.list_memory_candidates(queue_id="REV-MEM-1") == [candidate]
    assert repository.list_memory_candidates(queue_id="REV-MISSING") == []

    review = service.review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="Repository test analyst confirmation.",
        ),
        context=_analyst_context(),
    )

    assert review.candidate.status is SocMemoryCandidateStatus.CONFIRMED
    assert repository.get_memory_candidate(candidate.candidate_id) == review.candidate
    assert review.memory_record is not None
    assert review.memory_record.status is SocMemoryRecordStatus.CONFIRMED
    assert review.memory_record.retrieval_enabled is False
    assert repository.get_memory_record(review.memory_record.memory_id) == review.memory_record
    assert repository.get_memory_record_by_candidate_id(candidate.candidate_id) == review.memory_record
    assert repository.list_memory_records(status=SocMemoryRecordStatus.CONFIRMED) == [review.memory_record]
    assert repository.list_memory_records(tenant_scope="pingan") == [review.memory_record]
    assert repository.list_memory_records(source_candidate_id=candidate.candidate_id) == [review.memory_record]
    assert repository.list_memory_records(retrieval_enabled=True) == []
    activation = service.set_retrieval_activation(
        SocMemoryRetrievalActivationCommand(
            memory_id=review.memory_record.memory_id,
            action=SocMemoryRetrievalActivationAction.ENABLE,
            expected_record_version=review.memory_record.version,
            reason="Repository test memory governor approved retrieval.",
            activation_valid_until=datetime.now(UTC) + timedelta(days=90),
            review_after_days=30,
        ),
        context=_memory_governor_context(),
    )
    assert repository.list_memory_records(retrieval_enabled=True) == [activation.record]
    assert repository.list_mutation_audits(operation=SocMutationOperation.MEMORY_RETRIEVAL_ACTIVATION)[0].audit_id == activation.audit_id
    stale_update = activation.record.model_copy(update={"version": activation.record.version + 1})
    assert (
        repository.compare_and_set_memory_record(
            stale_update,
            expected_version=review.memory_record.version,
        )
        is False
    )


def test_review_service_context_loads_sqlalchemy_memory_candidates() -> None:
    repository = _repository()
    run = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
    ).analyze(_sample("pingan_legacy_apt.json"))
    item = repository.get_open_review_item_by_run(run.run_id)
    assert item is not None
    candidate = SocMemoryService(candidate_repository=repository).propose_candidate(
        _repository_memory_candidate_command(
            run_id=run.run_id,
            alert_id=run.alert_id,
            queue_id=item.queue_id,
            idempotency_key=f"memory:repo:{run.run_id}",
        )
    )

    context = SocReviewService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        evidence_repository=repository,
        external_disposition_repository=repository,
        memory_candidate_repository=repository,
    ).get_investigation_context(item.queue_id)

    assert context.memory_candidates == [candidate]
    assert context.memory_candidates[0].runtime_decision_allowed is False


def test_sqlalchemy_alert_repository_closes_review_queue_after_correction() -> None:
    repository = _repository()
    run = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
    ).analyze(_sample("pingan_legacy_edr.json"))
    open_item = repository.get_open_review_item_by_run(run.run_id)
    assert open_item is not None

    SocReviewService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
    ).correct(
        CorrectionCommand(
            run_id=run.run_id,
            corrected_verdict=Verdict.FALSE_POSITIVE,
            reason="Analyst confirmed authorized lateral movement test.",
        ),
        context=_analyst_context(),
    )

    assert repository.get_open_review_item_by_run(run.run_id) is None
    closed = repository.get_review_item(open_item.queue_id)
    assert closed is not None
    assert closed.status == ReviewQueueStatus.CLOSED
    assert closed.close_reason == "manual correction: Analyst confirmed authorized lateral movement test."
    assert closed.closed_by is not None
    assert repository.list_review_items(status=ReviewQueueStatus.OPEN) == []
    assert repository.list_review_items(status=ReviewQueueStatus.CLOSED) == [closed]


def test_sqlalchemy_alert_repository_persists_approval_grant_consume_state() -> None:
    repository = _repository()
    service = SocAgentApprovalService(grant_repository=repository, request_repository=repository)
    approval_request = SocAgentApprovalRequest(
        approval_request_id="APR-REPO-001",
        permission_decision_id="PERM-REPO-001",
        route="response.block_ip",
        action="response.block_ip",
        risk_level=SocAgentRiskLevel.HIGH_RISK,
        reason="requires approval",
        requested_by=ActorContext(actor_id="analyst-1", roles=["analyst"]),
    )
    service.submit_request(
        approval_request,
        context=ServiceRequestContext(actor=approval_request.requested_by),
    )
    grant = service.approve(
        approval_request.approval_request_id,
        context=ServiceRequestContext(
            actor=ActorContext(actor_id="approver-1", roles=["soc_approver"]),
            idempotency_key="idem-approve-1",
        ),
        reason="approved containment scope",
    )

    saved = repository.get_approval_grant(grant.approval_grant_id)
    saved_by_token = repository.get_approval_grant_by_token(grant.execution_token_id)

    assert saved == grant
    assert saved_by_token == grant
    assert saved.status == "approved"

    result = service.execute_approved_action(
        SocAgentApprovedActionCommand(
            execution_token_id=grant.execution_token_id,
            route="response.block_ip",
            action="response.block_ip",
            dry_run=False,
            payload={"ip": "203.0.113.8"},
        ),
        context=ServiceRequestContext(
            actor=ActorContext(actor_id="analyst-2", roles=["analyst"]),
            idempotency_key="idem-execute-1",
        ),
    )
    consumed = repository.get_approval_grant(grant.approval_grant_id)

    assert consumed.status == "consumed"
    assert consumed.consumed_by.actor_id == "analyst-2"
    assert consumed.consume_idempotency_key == "idem-execute-1"
    assert consumed.execution_result_payload == result.model_dump(mode="json")


def test_sqlalchemy_alert_repository_persists_approval_requests() -> None:
    repository = _repository()
    service = SocAgentApprovalService(request_repository=repository)
    approval_request = SocAgentApprovalRequest(
        approval_request_id="APR-REPO-REQUEST-001",
        permission_decision_id="PERM-REPO-REQUEST-001",
        route="response.block_ip",
        action="response.block_ip",
        risk_level=SocAgentRiskLevel.HIGH_RISK,
        reason="requires approval",
        requested_by=ActorContext(actor_id="analyst-1", roles=["analyst"]),
    )

    submitted = service.submit_request(approval_request, context=_analyst_context())

    assert repository.get_approval_request("APR-REPO-REQUEST-001") == submitted
    assert repository.list_approval_requests(status="pending") == [submitted]
    assert repository.list_approval_requests(status=None) == [submitted]
    assert repository.list_approval_requests(status="closed") == []


def test_sqlalchemy_approval_service_approve_persists_request_and_grant() -> None:
    repository = _repository()
    service = SocAgentApprovalService(grant_repository=repository, request_repository=repository)
    approval_request = SocAgentApprovalRequest(
        approval_request_id="APR-REPO-APPROVE-001",
        permission_decision_id="PERM-REPO-APPROVE-001",
        route="response.block_ip",
        action="response.block_ip",
        risk_level=SocAgentRiskLevel.HIGH_RISK,
        reason="requires approval",
        requested_by=ActorContext(actor_id="analyst-1", roles=["analyst"]),
    )

    service.submit_request(
        approval_request,
        context=ServiceRequestContext(actor=approval_request.requested_by),
    )
    approval_context = ServiceRequestContext(
        actor=ActorContext(actor_id="admin-1", roles=["soc_admin"]),
        idempotency_key="approve:repo-001",
    )
    grant = service.approve(
        approval_request.approval_request_id,
        context=approval_context,
        reason="approved containment scope",
    )
    replayed = service.approve(
        approval_request.approval_request_id,
        context=approval_context,
        reason="approved containment scope",
    )

    resolved = repository.get_approval_request(approval_request.approval_request_id)
    assert resolved is not None
    assert resolved.status.value == "approved"
    assert resolved.approval_grant_id == grant.approval_grant_id
    assert replayed == grant
    assert repository.get_approval_grant(grant.approval_grant_id) == grant
    assert repository.get_approval_grant_by_request_id(approval_request.approval_request_id) == grant


def test_sqlalchemy_approval_service_persists_rejected_terminal_request() -> None:
    repository = _repository()
    service = SocAgentApprovalService(grant_repository=repository, request_repository=repository)
    approval_request = SocAgentApprovalRequest(
        approval_request_id="APR-REPO-REJECT-001",
        permission_decision_id="PERM-REPO-REJECT-001",
        route="endpoint.isolate_host",
        action="endpoint.isolate_host",
        risk_level=SocAgentRiskLevel.HIGH_RISK,
        reason="requires approval",
        requested_by=ActorContext(actor_id="analyst-1", roles=["soc_analyst"]),
    )
    service.submit_request(approval_request, context=_analyst_context())

    rejected = service.reject(
        approval_request.approval_request_id,
        context=ServiceRequestContext(
            actor=ActorContext(actor_id="admin-1", roles=["soc_admin"]),
            idempotency_key="reject:repo-001",
        ),
        reason="Requested scope was not authorized.",
    )

    persisted = repository.get_approval_request(approval_request.approval_request_id)
    assert persisted == rejected
    assert persisted.status is SocAgentApprovalRequestStatus.REJECTED
    assert persisted.resolution_idempotency_key == "reject:repo-001"
    assert repository.get_approval_grant_by_request_id(approval_request.approval_request_id) is None
