"""SQLAlchemy repository implementations for SOC Agent contracts."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from soc_agent.contracts import (
    AlertSummary,
    AnalysisRun,
    DecisionAuditRecord,
    InvestigationEvidence,
    NormalizationBaselineStatus,
    NormalizationMaintenanceIssue,
    NormalizationMaintenanceIssueStatus,
    NormalizationSchemaBaseline,
    ReviewQueueItem,
    ReviewQueueStatus,
    SimilarAlertMatch,
    SimilarAlertQuery,
    SocAgentApprovalGrant,
    SocAgentApprovalRequest,
    SocExternalDispositionRecord,
    SocMemoryCandidate,
    SocMemoryCandidateStatus,
    SocMemoryRecord,
    SocMemoryRecordStatus,
)
from soc_agent.db.models import (
    SocAlertSummaryRow,
    SocAnalysisRunRow,
    SocApprovalGrantRow,
    SocApprovalRequestRow,
    SocDecisionAuditLogRow,
    SocExternalDispositionRow,
    SocInvestigationEvidenceRow,
    SocMemoryCandidateRow,
    SocMemoryRecordRow,
    SocNormalizationMaintenanceIssueRow,
    SocNormalizationSchemaBaselineRow,
    SocReviewQueueRow,
)


class SqlAlchemyAlertRepository:
    """SQLAlchemy-backed implementation of ``AlertRepository``.

    The repository accepts a sync ``Session`` factory so Phase 1 headless CLI and
    service tests can use the same persistence boundary. Async Gateway adapters
    should call it off the event loop or get a dedicated async adapter later.
    """

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def save_run(self, run: AnalysisRun) -> None:
        payload = run.model_dump(mode="json")
        now = datetime.now(UTC)

        with self._session_factory() as session:
            row = session.get(SocAnalysisRunRow, run.run_id)
            if row is None:
                row = SocAnalysisRunRow(
                    run_id=run.run_id,
                    created_at=now,
                    **_row_values(run, payload, updated_at=now),
                )
                session.add(row)
            else:
                for key, value in _row_values(run, payload, updated_at=now).items():
                    setattr(row, key, value)
            session.commit()

    def get_run(self, run_id: str) -> AnalysisRun | None:
        with self._session_factory() as session:
            row = session.get(SocAnalysisRunRow, run_id)
            if row is None:
                return None
            return AnalysisRun.model_validate(row.run_payload)

    def list_runs(self, *, limit: int = 50) -> list[AnalysisRun]:
        with self._session_factory() as session:
            result = session.execute(select(SocAnalysisRunRow).order_by(SocAnalysisRunRow.updated_at.desc(), SocAnalysisRunRow.created_at.desc()).limit(limit))
            return [AnalysisRun.model_validate(row.run_payload) for row in result.scalars()]

    def save_audit_record(self, record: DecisionAuditRecord) -> None:
        payload = record.model_dump(mode="json")
        with self._session_factory() as session:
            row = session.get(SocDecisionAuditLogRow, record.audit_id)
            if row is None:
                session.add(SocDecisionAuditLogRow(audit_id=record.audit_id, **_audit_row_values(record, payload)))
            else:
                for key, value in _audit_row_values(record, payload).items():
                    setattr(row, key, value)
            session.commit()

    def list_audit_records(self, run_id: str) -> list[DecisionAuditRecord]:
        with self._session_factory() as session:
            result = session.execute(select(SocDecisionAuditLogRow).where(SocDecisionAuditLogRow.run_id == run_id).order_by(SocDecisionAuditLogRow.occurred_at.asc()))
            return [DecisionAuditRecord.model_validate(row.record_payload) for row in result.scalars()]

    def find_audit_record_by_idempotency_key(
        self,
        idempotency_key: str,
        *,
        action: str | None = None,
    ) -> DecisionAuditRecord | None:
        with self._session_factory() as session:
            query = select(SocDecisionAuditLogRow).where(SocDecisionAuditLogRow.idempotency_key == idempotency_key)
            if action is not None:
                query = query.where(SocDecisionAuditLogRow.action == action)
            result = session.execute(query.order_by(SocDecisionAuditLogRow.occurred_at.desc()).limit(1))
            row = result.scalar_one_or_none()
            return DecisionAuditRecord.model_validate(row.record_payload) if row is not None else None

    def save_alert_summary(self, summary: AlertSummary) -> None:
        payload = summary.model_dump(mode="json")
        with self._session_factory() as session:
            row = session.get(SocAlertSummaryRow, summary.run_id)
            if row is None:
                session.add(SocAlertSummaryRow(run_id=summary.run_id, **_summary_row_values(summary, payload)))
            else:
                for key, value in _summary_row_values(summary, payload).items():
                    setattr(row, key, value)
            session.commit()

    def get_alert_summary(self, run_id: str) -> AlertSummary | None:
        with self._session_factory() as session:
            row = session.get(SocAlertSummaryRow, run_id)
            if row is None:
                return None
            return AlertSummary.model_validate(row.summary_payload)

    def list_alert_summaries(self, *, limit: int = 50) -> list[AlertSummary]:
        with self._session_factory() as session:
            result = session.execute(select(SocAlertSummaryRow).order_by(SocAlertSummaryRow.updated_at.desc()).limit(limit))
            return [AlertSummary.model_validate(row.summary_payload) for row in result.scalars()]

    def find_similar_alert_summaries(self, query: SimilarAlertQuery) -> list[SimilarAlertMatch]:
        with self._session_factory() as session:
            result = session.execute(select(SocAlertSummaryRow).where(SocAlertSummaryRow.run_id != query.run_id).order_by(SocAlertSummaryRow.updated_at.desc()).limit(query.candidate_limit))
            summaries = [AlertSummary.model_validate(row.summary_payload) for row in result.scalars()]

        matches = [match for summary in summaries if (match := _score_similar_alert(query, summary)) is not None]
        return sorted(matches, key=lambda item: (item.score, item.summary.updated_at), reverse=True)[: query.limit]

    def save_review_item(self, item: ReviewQueueItem) -> None:
        payload = item.model_dump(mode="json")
        with self._session_factory() as session:
            row = session.get(SocReviewQueueRow, item.queue_id)
            if row is None:
                session.add(SocReviewQueueRow(queue_id=item.queue_id, **_review_queue_row_values(item, payload)))
            else:
                for key, value in _review_queue_row_values(item, payload).items():
                    setattr(row, key, value)
            session.commit()

    def get_review_item(self, queue_id: str) -> ReviewQueueItem | None:
        with self._session_factory() as session:
            row = session.get(SocReviewQueueRow, queue_id)
            if row is None:
                return None
            return ReviewQueueItem.model_validate(row.item_payload)

    def get_open_review_item_by_run(self, run_id: str) -> ReviewQueueItem | None:
        with self._session_factory() as session:
            result = session.execute(select(SocReviewQueueRow).where(SocReviewQueueRow.run_id == run_id, SocReviewQueueRow.status == ReviewQueueStatus.OPEN.value).order_by(SocReviewQueueRow.updated_at.desc()).limit(1))
            row = result.scalar_one_or_none()
            return ReviewQueueItem.model_validate(row.item_payload) if row is not None else None

    def list_review_items(
        self,
        *,
        status: ReviewQueueStatus | None = None,
        limit: int = 50,
    ) -> list[ReviewQueueItem]:
        with self._session_factory() as session:
            query = select(SocReviewQueueRow)
            if status is not None:
                query = query.where(SocReviewQueueRow.status == status.value)
            result = session.execute(query.order_by(SocReviewQueueRow.updated_at.desc()).limit(limit))
            return [ReviewQueueItem.model_validate(row.item_payload) for row in result.scalars()]

    def save_approval_grant(self, grant: SocAgentApprovalGrant) -> None:
        payload = grant.model_dump(mode="json")
        with self._session_factory() as session:
            row = session.get(SocApprovalGrantRow, grant.approval_grant_id)
            if row is None:
                session.add(SocApprovalGrantRow(approval_grant_id=grant.approval_grant_id, **_approval_grant_row_values(grant, payload)))
            else:
                for key, value in _approval_grant_row_values(grant, payload).items():
                    setattr(row, key, value)
            session.commit()

    def get_approval_grant(self, approval_grant_id: str) -> SocAgentApprovalGrant | None:
        with self._session_factory() as session:
            row = session.get(SocApprovalGrantRow, approval_grant_id)
            if row is None:
                return None
            return SocAgentApprovalGrant.model_validate(row.grant_payload)

    def get_approval_grant_by_token(self, execution_token_id: str) -> SocAgentApprovalGrant | None:
        with self._session_factory() as session:
            result = session.execute(select(SocApprovalGrantRow).where(SocApprovalGrantRow.execution_token_id == execution_token_id).limit(1))
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return SocAgentApprovalGrant.model_validate(row.grant_payload)

    def save_approval_request(self, approval_request: SocAgentApprovalRequest) -> None:
        payload = approval_request.model_dump(mode="json")
        with self._session_factory() as session:
            row = session.get(SocApprovalRequestRow, approval_request.approval_request_id)
            if row is None:
                session.add(SocApprovalRequestRow(approval_request_id=approval_request.approval_request_id, **_approval_request_row_values(approval_request, payload)))
            else:
                for key, value in _approval_request_row_values(approval_request, payload).items():
                    setattr(row, key, value)
            session.commit()

    def get_approval_request(self, approval_request_id: str) -> SocAgentApprovalRequest | None:
        with self._session_factory() as session:
            row = session.get(SocApprovalRequestRow, approval_request_id)
            if row is None:
                return None
            return SocAgentApprovalRequest.model_validate(row.request_payload)

    def list_approval_requests(
        self,
        *,
        status: str | None = "pending",
        limit: int = 50,
    ) -> list[SocAgentApprovalRequest]:
        with self._session_factory() as session:
            query = select(SocApprovalRequestRow)
            if status is not None:
                query = query.where(SocApprovalRequestRow.status == status)
            result = session.execute(query.order_by(SocApprovalRequestRow.created_at.desc()).limit(limit))
            return [SocAgentApprovalRequest.model_validate(row.request_payload) for row in result.scalars()]

    def save_evidence(self, evidence: InvestigationEvidence) -> None:
        payload = evidence.model_dump(mode="json")
        with self._session_factory() as session:
            row = session.get(SocInvestigationEvidenceRow, evidence.evidence_id)
            if row is None:
                session.add(SocInvestigationEvidenceRow(evidence_id=evidence.evidence_id, **_evidence_row_values(evidence, payload)))
            else:
                for key, value in _evidence_row_values(evidence, payload).items():
                    setattr(row, key, value)
            session.commit()

    def list_evidence(
        self,
        *,
        queue_id: str | None = None,
        run_id: str | None = None,
        alert_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 20,
    ) -> list[InvestigationEvidence]:
        filters = []
        if queue_id:
            filters.append(SocInvestigationEvidenceRow.queue_id == queue_id)
        if run_id:
            filters.append(SocInvestigationEvidenceRow.run_id == run_id)
        if alert_id:
            filters.append(SocInvestigationEvidenceRow.alert_id == alert_id)
        if thread_id:
            filters.append(SocInvestigationEvidenceRow.thread_id == thread_id)

        with self._session_factory() as session:
            query = select(SocInvestigationEvidenceRow)
            if filters:
                query = query.where(or_(*filters))
            result = session.execute(query.order_by(SocInvestigationEvidenceRow.created_at.desc()).limit(limit))
            return [InvestigationEvidence.model_validate(row.evidence_payload) for row in result.scalars()]

    def save_external_disposition(self, record: SocExternalDispositionRecord) -> None:
        payload = record.model_dump(mode="json")
        with self._session_factory() as session:
            row = session.get(SocExternalDispositionRow, record.disposition_id)
            if row is None:
                session.add(SocExternalDispositionRow(disposition_id=record.disposition_id, **_external_disposition_row_values(record, payload)))
            else:
                for key, value in _external_disposition_row_values(record, payload).items():
                    setattr(row, key, value)
            session.commit()

    def find_external_disposition_by_idempotency_key(self, idempotency_key: str) -> SocExternalDispositionRecord | None:
        with self._session_factory() as session:
            result = session.execute(select(SocExternalDispositionRow).where(SocExternalDispositionRow.idempotency_key == idempotency_key).limit(1))
            row = result.scalar_one_or_none()
            return SocExternalDispositionRecord.model_validate(row.disposition_payload) if row is not None else None

    def list_external_dispositions(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        external_system: str | None = None,
        external_case_id: str | None = None,
        limit: int = 50,
    ) -> list[SocExternalDispositionRecord]:
        target_filters = []
        if run_id:
            target_filters.append(SocExternalDispositionRow.target_run_id == run_id)
        if alert_id:
            target_filters.append(SocExternalDispositionRow.target_alert_id == alert_id)
        if queue_id:
            target_filters.append(SocExternalDispositionRow.target_queue_id == queue_id)

        with self._session_factory() as session:
            query = select(SocExternalDispositionRow)
            if target_filters:
                query = query.where(or_(*target_filters))
            if external_system:
                query = query.where(SocExternalDispositionRow.external_system == external_system)
            if external_case_id:
                query = query.where(SocExternalDispositionRow.external_case_id == external_case_id)
            result = session.execute(query.order_by(SocExternalDispositionRow.created_at.desc()).limit(limit))
            return [SocExternalDispositionRecord.model_validate(row.disposition_payload) for row in result.scalars()]

    def save_memory_candidate(self, candidate: SocMemoryCandidate) -> None:
        payload = candidate.model_dump(mode="json")
        with self._session_factory() as session:
            row = session.get(SocMemoryCandidateRow, candidate.candidate_id)
            if row is None:
                session.add(SocMemoryCandidateRow(candidate_id=candidate.candidate_id, **_memory_candidate_row_values(candidate, payload)))
            else:
                for key, value in _memory_candidate_row_values(candidate, payload).items():
                    setattr(row, key, value)
            session.commit()

    def get_memory_candidate(self, candidate_id: str) -> SocMemoryCandidate | None:
        with self._session_factory() as session:
            row = session.get(SocMemoryCandidateRow, candidate_id)
            return SocMemoryCandidate.model_validate(row.candidate_payload) if row is not None else None

    def find_memory_candidate_by_idempotency_key(self, idempotency_key: str) -> SocMemoryCandidate | None:
        with self._session_factory() as session:
            result = session.execute(select(SocMemoryCandidateRow).where(SocMemoryCandidateRow.idempotency_key == idempotency_key).limit(1))
            row = result.scalar_one_or_none()
            return SocMemoryCandidate.model_validate(row.candidate_payload) if row is not None else None

    def list_memory_candidates(
        self,
        *,
        status: SocMemoryCandidateStatus | None = None,
        tenant_scope: str | None = None,
        tenant_id: str | None = None,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        limit: int = 50,
    ) -> list[SocMemoryCandidate]:
        source_filters = []
        if run_id:
            source_filters.append(SocMemoryCandidateRow.source_run_id == run_id)
        if alert_id:
            source_filters.append(SocMemoryCandidateRow.source_alert_id == alert_id)
        if queue_id:
            source_filters.append(SocMemoryCandidateRow.source_queue_id == queue_id)

        with self._session_factory() as session:
            query = select(SocMemoryCandidateRow)
            if status is not None:
                query = query.where(SocMemoryCandidateRow.status == status.value)
            if tenant_scope is not None:
                query = query.where(SocMemoryCandidateRow.tenant_scope == tenant_scope)
            if tenant_id is not None:
                query = query.where(SocMemoryCandidateRow.tenant_id == tenant_id)
            if source_filters:
                query = query.where(or_(*source_filters))
            result = session.execute(query.order_by(SocMemoryCandidateRow.created_at.desc()).limit(limit))
            return [SocMemoryCandidate.model_validate(row.candidate_payload) for row in result.scalars()]

    def save_memory_record(self, record: SocMemoryRecord) -> None:
        payload = record.model_dump(mode="json")
        with self._session_factory() as session:
            row = session.get(SocMemoryRecordRow, record.memory_id)
            if row is None:
                session.add(SocMemoryRecordRow(memory_id=record.memory_id, **_memory_record_row_values(record, payload)))
            else:
                for key, value in _memory_record_row_values(record, payload).items():
                    setattr(row, key, value)
            session.commit()

    def get_memory_record(self, memory_id: str) -> SocMemoryRecord | None:
        with self._session_factory() as session:
            row = session.get(SocMemoryRecordRow, memory_id)
            return SocMemoryRecord.model_validate(row.record_payload) if row is not None else None

    def get_memory_record_by_candidate_id(self, candidate_id: str) -> SocMemoryRecord | None:
        with self._session_factory() as session:
            result = session.execute(select(SocMemoryRecordRow).where(SocMemoryRecordRow.source_candidate_id == candidate_id).limit(1))
            row = result.scalar_one_or_none()
            return SocMemoryRecord.model_validate(row.record_payload) if row is not None else None

    def list_memory_records(
        self,
        *,
        status: SocMemoryRecordStatus | None = None,
        tenant_scope: str | None = None,
        tenant_id: str | None = None,
        source_candidate_id: str | None = None,
        retrieval_enabled: bool | None = None,
        limit: int = 50,
    ) -> list[SocMemoryRecord]:
        with self._session_factory() as session:
            query = select(SocMemoryRecordRow)
            if status is not None:
                query = query.where(SocMemoryRecordRow.status == status.value)
            if tenant_scope is not None:
                query = query.where(SocMemoryRecordRow.tenant_scope == tenant_scope)
            if tenant_id is not None:
                query = query.where(SocMemoryRecordRow.tenant_id == tenant_id)
            if source_candidate_id is not None:
                query = query.where(SocMemoryRecordRow.source_candidate_id == source_candidate_id)
            if retrieval_enabled is not None:
                query = query.where(SocMemoryRecordRow.retrieval_enabled == retrieval_enabled)
            result = session.execute(query.order_by(SocMemoryRecordRow.updated_at.desc()).limit(limit))
            return [SocMemoryRecord.model_validate(row.record_payload) for row in result.scalars()]

    def save_normalization_baseline(self, baseline: NormalizationSchemaBaseline) -> None:
        payload = baseline.model_dump(mode="json")
        with self._session_factory() as session:
            row = session.get(SocNormalizationSchemaBaselineRow, baseline.baseline_id)
            if row is None:
                session.add(
                    SocNormalizationSchemaBaselineRow(
                        baseline_id=baseline.baseline_id,
                        **_normalization_baseline_row_values(baseline, payload),
                    )
                )
            else:
                for key, value in _normalization_baseline_row_values(baseline, payload).items():
                    setattr(row, key, value)
            session.commit()

    def get_normalization_baseline(self, baseline_id: str) -> NormalizationSchemaBaseline | None:
        with self._session_factory() as session:
            row = session.get(SocNormalizationSchemaBaselineRow, baseline_id)
            return NormalizationSchemaBaseline.model_validate(row.baseline_payload) if row is not None else None

    def list_normalization_baselines(
        self,
        *,
        status: NormalizationBaselineStatus | None = None,
        tenant_id: str | None = None,
        source_system: str | None = None,
        adapter: str | None = None,
        parser_name: str | None = None,
        parser_version: str | None = None,
        limit: int = 50,
    ) -> list[NormalizationSchemaBaseline]:
        with self._session_factory() as session:
            query = select(SocNormalizationSchemaBaselineRow)
            filters = {
                "status": status.value if status is not None else None,
                "tenant_id": tenant_id,
                "source_system": source_system,
                "adapter": adapter,
                "parser_name": parser_name,
                "parser_version": parser_version,
            }
            for name, value in filters.items():
                if value is not None:
                    query = query.where(getattr(SocNormalizationSchemaBaselineRow, name) == value)
            result = session.execute(query.order_by(SocNormalizationSchemaBaselineRow.updated_at.desc()).limit(limit))
            return [NormalizationSchemaBaseline.model_validate(row.baseline_payload) for row in result.scalars()]

    def save_normalization_issue(self, issue: NormalizationMaintenanceIssue) -> None:
        payload = issue.model_dump(mode="json")
        with self._session_factory() as session:
            row = session.get(SocNormalizationMaintenanceIssueRow, issue.issue_id)
            if row is None:
                session.add(
                    SocNormalizationMaintenanceIssueRow(
                        issue_id=issue.issue_id,
                        **_normalization_issue_row_values(issue, payload),
                    )
                )
            else:
                for key, value in _normalization_issue_row_values(issue, payload).items():
                    setattr(row, key, value)
            session.commit()

    def get_normalization_issue(self, issue_id: str) -> NormalizationMaintenanceIssue | None:
        with self._session_factory() as session:
            row = session.get(SocNormalizationMaintenanceIssueRow, issue_id)
            return NormalizationMaintenanceIssue.model_validate(row.issue_payload) if row is not None else None

    def find_normalization_issue_by_dedupe_key(self, dedupe_key: str) -> NormalizationMaintenanceIssue | None:
        with self._session_factory() as session:
            result = session.execute(select(SocNormalizationMaintenanceIssueRow).where(SocNormalizationMaintenanceIssueRow.dedupe_key == dedupe_key).limit(1))
            row = result.scalar_one_or_none()
            return NormalizationMaintenanceIssue.model_validate(row.issue_payload) if row is not None else None

    def list_normalization_issues(
        self,
        *,
        status: NormalizationMaintenanceIssueStatus | None = None,
        tenant_id: str | None = None,
        source_system: str | None = None,
        limit: int = 50,
    ) -> list[NormalizationMaintenanceIssue]:
        with self._session_factory() as session:
            query = select(SocNormalizationMaintenanceIssueRow)
            if status is not None:
                query = query.where(SocNormalizationMaintenanceIssueRow.status == status.value)
            if tenant_id is not None:
                query = query.where(SocNormalizationMaintenanceIssueRow.tenant_id == tenant_id)
            if source_system is not None:
                query = query.where(SocNormalizationMaintenanceIssueRow.source_system == source_system)
            result = session.execute(query.order_by(SocNormalizationMaintenanceIssueRow.last_seen_at.desc()).limit(limit))
            return [NormalizationMaintenanceIssue.model_validate(row.issue_payload) for row in result.scalars()]


def _row_values(run: AnalysisRun, payload: dict, *, updated_at: datetime) -> dict:
    return {
        "alert_id": run.alert_id,
        "status": run.status.value,
        "input_hash": run.input_hash,
        "replay_of_run_id": run.replay_of_run_id,
        "pipeline_version": run.pipeline_version,
        "model_name": run.model_name,
        "prompt_version": run.prompt_version,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "input_payload": run.input_payload,
        "run_payload": payload,
        "updated_at": updated_at,
    }


def _audit_row_values(record: DecisionAuditRecord, payload: dict) -> dict:
    return {
        "action": record.action.value,
        "run_id": record.run_id,
        "alert_id": record.alert_id,
        "actor_id": record.actor.actor_id,
        "actor_type": record.actor.actor_type.value,
        "actor_surface": record.actor.surface.value,
        "occurred_at": record.occurred_at,
        "input_hash": record.input_hash,
        "idempotency_key": _idempotency_key_from_audit_payload(record),
        "previous_verdict": record.previous_verdict.value if record.previous_verdict is not None else None,
        "final_verdict": record.final_verdict.value if record.final_verdict is not None else None,
        "confidence": record.confidence,
        "replay_of_run_id": record.replay_of_run_id,
        "correction_id": record.correction_id,
        "record_payload": payload,
    }


def _idempotency_key_from_audit_payload(record: DecisionAuditRecord) -> str | None:
    value = record.payload.get("idempotency_key")
    return value if isinstance(value, str) and value else None


def _summary_row_values(summary: AlertSummary, payload: dict) -> dict:
    return {
        "alert_id": summary.alert_id,
        "tenant_id": summary.tenant_id,
        "source_type": summary.source_type.value,
        "source_system": summary.source_system,
        "detection_key": summary.detection_key,
        "rule_code": summary.rule_code,
        "rule_name": summary.rule_name,
        "severity": summary.severity,
        "category": summary.category,
        "entity_keys": summary.entity_keys,
        "status": summary.status.value,
        "verdict": summary.verdict.value if summary.verdict is not None else None,
        "confidence": summary.confidence,
        "needs_review": summary.needs_review,
        "summary": summary.summary,
        "recommended_action": summary.recommended_action,
        "input_hash": summary.input_hash,
        "replay_of_run_id": summary.replay_of_run_id,
        "created_at": summary.created_at,
        "updated_at": summary.updated_at,
        "summary_payload": payload,
    }


def _score_similar_alert(query: SimilarAlertQuery, summary: AlertSummary) -> SimilarAlertMatch | None:
    score = 0.0
    reasons: list[str] = []

    if query.detection_key and summary.detection_key == query.detection_key:
        score += 50
        reasons.append(f"detection_key:{query.detection_key}")
    if query.rule_code and summary.rule_code == query.rule_code:
        score += 40
        reasons.append(f"rule_code:{query.rule_code}")
    if query.source_type is not None and summary.source_type == query.source_type:
        score += 8
        reasons.append(f"source_type:{query.source_type.value}")
    if query.category and summary.category == query.category:
        score += 6
        reasons.append(f"category:{query.category}")

    shared_entity_keys = sorted(set(query.entity_keys).intersection(summary.entity_keys))
    if shared_entity_keys:
        score += min(len(shared_entity_keys) * 15, 60)
        reasons.extend(f"entity_key:{value}" for value in shared_entity_keys[:10])

    if score == 0:
        return None
    return SimilarAlertMatch(summary=summary, score=score, matched_reasons=reasons)


def _review_queue_row_values(item: ReviewQueueItem, payload: dict) -> dict:
    return {
        "run_id": item.run_id,
        "alert_id": item.alert_id,
        "tenant_id": item.tenant_id,
        "status": item.status.value,
        "priority": item.priority.value,
        "reason": item.reason,
        "source_type": item.source_type.value,
        "source_system": item.source_system,
        "rule_code": item.rule_code,
        "rule_name": item.rule_name,
        "severity": item.severity,
        "category": item.category,
        "verdict": item.verdict.value if item.verdict is not None else None,
        "confidence": item.confidence,
        "entity_keys": item.entity_keys,
        "summary": item.summary,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "closed_at": item.closed_at,
        "closed_by_payload": item.closed_by.model_dump(mode="json") if item.closed_by is not None else None,
        "close_reason": item.close_reason,
        "item_payload": payload,
    }


def _approval_grant_row_values(grant: SocAgentApprovalGrant, payload: dict) -> dict:
    return {
        "execution_token_id": grant.execution_token_id,
        "approval_request_id": grant.approval_request_id,
        "permission_decision_id": grant.permission_decision_id,
        "route": grant.route,
        "action": grant.action,
        "risk_level": grant.risk_level.value,
        "status": grant.status,
        "approved_by_actor_id": grant.approved_by.actor_id,
        "requested_by_actor_id": grant.requested_by.actor_id,
        "approval_reason": grant.approval_reason,
        "idempotency_key": grant.idempotency_key,
        "consume_idempotency_key": grant.consume_idempotency_key,
        "execution_result_id": grant.execution_result_id,
        "approved_at": grant.approved_at,
        "expires_at": grant.expires_at,
        "consumed_at": grant.consumed_at,
        "grant_payload": payload,
    }


def _approval_request_row_values(approval_request: SocAgentApprovalRequest, payload: dict) -> dict:
    return {
        "permission_decision_id": approval_request.permission_decision_id,
        "route": approval_request.route,
        "action": approval_request.action,
        "risk_level": approval_request.risk_level.value,
        "status": approval_request.status,
        "requested_by_actor_id": approval_request.requested_by.actor_id,
        "reason": approval_request.reason,
        "created_at": approval_request.created_at,
        "request_payload": payload,
    }


def _evidence_row_values(evidence: InvestigationEvidence, payload: dict) -> dict:
    return {
        "source_type": evidence.source_type,
        "route": evidence.route,
        "action": evidence.action,
        "status": evidence.status,
        "queue_id": evidence.queue_id,
        "run_id": evidence.run_id,
        "alert_id": evidence.alert_id,
        "thread_id": evidence.thread_id,
        "source_proposal_id": evidence.source_proposal_id,
        "context_hash": evidence.context_hash,
        "actor_id": evidence.actor.actor_id if evidence.actor is not None else None,
        "message": evidence.message,
        "created_at": evidence.created_at,
        "evidence_payload": payload,
    }


def _external_disposition_row_values(record: SocExternalDispositionRecord, payload: dict) -> dict:
    return {
        "tenant_id": record.event.tenant_id,
        "external_system": record.event.external_system,
        "external_case_id": record.event.external_case_id,
        "source_event_id": record.event.source_event_id,
        "source_version": record.event.source_version,
        "external_status": record.event.external_status,
        "canonical_status": record.canonical_status.value,
        "apply_status": record.apply_status.value,
        "idempotency_key": record.idempotency_key,
        "target_run_id": record.target_run_id,
        "target_alert_id": record.target_alert_id,
        "target_queue_id": record.target_queue_id,
        "matched_by": record.matched_by,
        "audit_id": record.audit_id,
        "correction_id": record.correction_id,
        "memory_candidate_id": record.memory_candidate_id,
        "created_at": record.created_at,
        "disposition_payload": payload,
    }


def _memory_candidate_row_values(candidate: SocMemoryCandidate, payload: dict) -> dict:
    return {
        "candidate_type": candidate.candidate_type.value,
        "target_artifact": candidate.target_artifact.value,
        "status": candidate.status.value,
        "tenant_scope": candidate.tenant_scope,
        "tenant_id": candidate.tenant_id,
        "source_type": candidate.source.source_type.value,
        "source_surface": candidate.source.source_surface.value if candidate.source.source_surface is not None else None,
        "source_id": candidate.source.source_id,
        "source_doc": candidate.source.source_doc,
        "source_section": candidate.source.source_section,
        "capability_card_id": candidate.source.capability_card_id,
        "source_run_id": candidate.source.run_id,
        "source_alert_id": candidate.source.alert_id,
        "source_queue_id": candidate.source.queue_id,
        "correction_id": candidate.source.correction_id,
        "eval_sample_id": candidate.source.eval_sample_id,
        "idempotency_key": candidate.idempotency_key,
        "confidence": candidate.confidence,
        "decision_impact": candidate.decision_impact.value,
        "runtime_decision_allowed": candidate.runtime_decision_allowed,
        "review_required": candidate.review_required,
        "review_owner": candidate.review_owner,
        "reviewed_by_actor_id": candidate.reviewed_by.actor_id if candidate.reviewed_by is not None else None,
        "reviewed_at": candidate.reviewed_at,
        "summary": candidate.summary,
        "content": candidate.content,
        "created_at": candidate.created_at,
        "updated_at": candidate.updated_at,
        "candidate_payload": payload,
    }


def _memory_record_row_values(record: SocMemoryRecord, payload: dict) -> dict:
    return {
        "version": record.version,
        "memory_type": record.memory_type.value,
        "target_artifact": record.target_artifact.value,
        "status": record.status.value,
        "tenant_scope": record.tenant_scope,
        "tenant_id": record.tenant_id,
        "source_candidate_id": record.source_candidate_id,
        "source_type": record.source.source_type.value,
        "source_run_id": record.source.run_id,
        "source_alert_id": record.source.alert_id,
        "source_queue_id": record.source.queue_id,
        "content_hash": record.content_hash,
        "facets_hash": record.facets_hash,
        "retrieval_enabled": record.retrieval_enabled,
        "confidence": record.confidence,
        "created_by_actor_id": record.created_by.actor_id,
        "deprecated_by_actor_id": record.deprecated_by.actor_id if record.deprecated_by is not None else None,
        "deprecated_at": record.deprecated_at,
        "summary": record.summary,
        "content": record.content,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "record_payload": payload,
    }


def _normalization_baseline_row_values(baseline: NormalizationSchemaBaseline, payload: dict) -> dict:
    return {
        "version": baseline.version,
        "status": baseline.status.value,
        "tenant_id": baseline.tenant_id,
        "source_system": baseline.source_system,
        "adapter": baseline.adapter,
        "parser_name": baseline.parser_name,
        "parser_version": baseline.parser_version,
        "accepted_fingerprints": baseline.accepted_fingerprints,
        "approved_by_actor_id": baseline.approved_by.actor_id,
        "reason": baseline.reason,
        "created_at": baseline.created_at,
        "updated_at": baseline.updated_at,
        "superseded_at": baseline.superseded_at,
        "baseline_payload": payload,
    }


def _normalization_issue_row_values(issue: NormalizationMaintenanceIssue, payload: dict) -> dict:
    return {
        "dedupe_key": issue.dedupe_key,
        "issue_type": issue.issue_type.value,
        "severity": issue.severity.value,
        "status": issue.status.value,
        "tenant_id": issue.tenant_id,
        "source_system": issue.source_system,
        "adapter": issue.adapter,
        "parser_name": issue.parser_name,
        "parser_version": issue.parser_version,
        "schema_fingerprint": issue.schema_fingerprint,
        "source_path": issue.source_path,
        "expected_target": issue.expected_target,
        "run_id": issue.run_id,
        "alert_id": issue.alert_id,
        "occurrence_count": issue.occurrence_count,
        "first_seen_at": issue.first_seen_at,
        "last_seen_at": issue.last_seen_at,
        "acknowledged_by_actor_id": issue.acknowledged_by.actor_id if issue.acknowledged_by else None,
        "acknowledged_at": issue.acknowledged_at,
        "resolved_by_actor_id": issue.resolved_by.actor_id if issue.resolved_by else None,
        "resolved_at": issue.resolved_at,
        "resolution_reason": issue.resolution_reason,
        "issue_payload": payload,
    }
