from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.contracts import (
    ActorContext,
    AuditAction,
    CorrectionCommand,
    InvestigationEvidence,
    ReviewQueueStatus,
    ServiceRequestContext,
    SimilarAlertQuery,
    SocAgentApprovalRequest,
    SocAgentApprovedActionCommand,
    SocAgentRiskLevel,
    Verdict,
)
from soc_agent.core import SocAgentApprovalService, SocAnalysisService
from soc_agent.core.service import SocReviewService
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


def _sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def _repository() -> SqlAlchemyAlertRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return SqlAlchemyAlertRepository(session_factory)


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
        )
    )

    saved = repository.get_run(run.run_id)
    assert saved == corrected
    assert saved is not None
    assert saved.decision is not None
    assert saved.decision.verdict == Verdict.TRUE_POSITIVE
    assert saved.corrections[0].previous_verdict == Verdict.FALSE_POSITIVE

    records = repository.list_audit_records(run.run_id)
    assert [record.action for record in records] == [AuditAction.ANALYSIS, AuditAction.CORRECTION]
    assert records[1].previous_verdict == Verdict.FALSE_POSITIVE
    assert records[1].final_verdict == Verdict.TRUE_POSITIVE

    summary = repository.get_alert_summary(run.run_id)
    assert summary is not None
    assert summary.verdict == Verdict.TRUE_POSITIVE
    assert summary.confidence == 1.0
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
    assert item.reason == "summary.needs_review"
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
        )
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
    service = SocAgentApprovalService(grant_repository=repository)
    approval_request = SocAgentApprovalRequest(
        approval_request_id="APR-REPO-001",
        permission_decision_id="PERM-REPO-001",
        route="response.block_ip",
        action="response.block_ip",
        risk_level=SocAgentRiskLevel.HIGH_RISK,
        reason="requires approval",
        requested_by=ActorContext(actor_id="analyst-1", roles=["analyst"]),
    )
    grant = service.approve(
        approval_request,
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
    approval_request = SocAgentApprovalRequest(
        approval_request_id="APR-REPO-REQUEST-001",
        permission_decision_id="PERM-REPO-REQUEST-001",
        route="response.block_ip",
        action="response.block_ip",
        risk_level=SocAgentRiskLevel.HIGH_RISK,
        reason="requires approval",
        requested_by=ActorContext(actor_id="analyst-1", roles=["analyst"]),
    )

    repository.save_approval_request(approval_request)

    assert repository.get_approval_request("APR-REPO-REQUEST-001") == approval_request
    assert repository.list_approval_requests(status="pending") == [approval_request]
    assert repository.list_approval_requests(status=None) == [approval_request]
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

    grant = service.approve(
        approval_request,
        context=ServiceRequestContext(actor=ActorContext(actor_id="admin-1", roles=["soc_admin"])),
        reason="approved containment scope",
    )

    assert repository.get_approval_request(approval_request.approval_request_id) == approval_request
    assert repository.get_approval_grant(grant.approval_grant_id) == grant
