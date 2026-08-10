"""SQLAlchemy repository implementations for SOC Agent contracts."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from soc_agent.authorization import AuthorizationEnrichmentConflictError
from soc_agent.contracts import (
    AlertSummary,
    AnalysisRun,
    AnalysisRunStatus,
    AuthorizationEnrichmentRecord,
    DecisionAuditRecord,
    GovernedContextFact,
    GovernedContextFactQuery,
    InvestigationEvidence,
    MemoryPatternDataClass,
    MemoryPatternObservation,
    MemoryPatternSourceType,
    NormalizationBaselineStatus,
    NormalizationMaintenanceIssue,
    NormalizationMaintenanceIssueStatus,
    NormalizationSchemaBaseline,
    ReviewQueueItem,
    ReviewQueueStatus,
    SimilarAlertMatch,
    SimilarAlertQuery,
    SkillFeedbackObservation,
    SkillFeedbackSourceType,
    SkillImprovementCandidate,
    SkillImprovementCandidateStatus,
    SocAgentApprovalGrant,
    SocAgentApprovalRequest,
    SocAgentApprovalRequestStatus,
    SocDispositionOutcomeRecord,
    SocDispositionOutcomeReviewKind,
    SocDispositionProposalRecord,
    SocDispositionSampleManifest,
    SocEnrichmentActionAttempt,
    SocEnrichmentExecution,
    SocEvaluationDataClass,
    SocExternalDispositionRecord,
    SocMemoryCandidate,
    SocMemoryCandidateStatus,
    SocMemoryRecord,
    SocMemoryRecordStatus,
    SocMutationAuditRecord,
    SocMutationOperation,
    TenantPolicyDecision,
)
from soc_agent.db.models import (
    SocAlertSummaryRow,
    SocAnalysisRunRow,
    SocApprovalGrantRow,
    SocApprovalRequestRow,
    SocAuthorizationEnrichmentRow,
    SocDecisionAuditLogRow,
    SocDispositionOutcomeRow,
    SocDispositionProposalRow,
    SocDispositionSampleManifestRow,
    SocEnrichmentActionAttemptRow,
    SocEnrichmentExecutionRow,
    SocExternalDispositionRow,
    SocGovernedContextFactRow,
    SocInvestigationEvidenceRow,
    SocMemoryCandidateRow,
    SocMemoryPatternObservationRow,
    SocMemoryRecordRow,
    SocMutationAuditRow,
    SocNormalizationMaintenanceIssueRow,
    SocNormalizationSchemaBaselineRow,
    SocReviewQueueRow,
    SocSkillFeedbackObservationRow,
    SocSkillImprovementCandidateRow,
    SocTenantPolicyDecisionRow,
)
from soc_agent.disposition import DispositionEvaluationConflictError, DispositionProposalConflictError
from soc_agent.domain.correlation import score_similar_alert
from soc_agent.governed_context import (
    GovernedContextFactVersionConflictError,
    validate_governed_context_fact_append,
)
from soc_agent.tenant_policy import TenantPolicyDecisionConflictError

MutationWriteHook = Callable[[int], None]


@dataclass
class _MutationTransactionState:
    session: Session
    write_hook: MutationWriteHook | None = None
    write_count: int = 0
    aborted: bool = False

    def flush_write(self) -> None:
        self.session.flush()
        self.write_count += 1
        if self.write_hook is not None:
            self.write_hook(self.write_count)


class _MutationSessionProxy:
    """Session facade that defers repository commits to the outer UoW."""

    def __init__(self, state: _MutationTransactionState) -> None:
        self._state = state

    def commit(self) -> None:
        self._state.flush_write()

    def rollback(self) -> None:
        self._state.aborted = True
        self._state.session.rollback()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._state.session, name)


class _BorrowedSessionContext(AbstractContextManager[_MutationSessionProxy]):
    def __init__(self, state: _MutationTransactionState) -> None:
        self._proxy = _MutationSessionProxy(state)

    def __enter__(self) -> _MutationSessionProxy:
        return self._proxy

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None


class SqlAlchemyAlertRepository:
    """SQLAlchemy-backed implementation of ``AlertRepository``.

    The repository accepts a sync ``Session`` factory so headless CLI and service
    tests can use the same persistence boundary. Async Gateway adapters
    should call it off the event loop or get a dedicated async adapter later.
    """

    def __init__(
        self,
        session_factory: Callable[[], AbstractContextManager[Session]],
        *,
        mutation_write_hook: MutationWriteHook | None = None,
        _transaction_state: _MutationTransactionState | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._mutation_write_hook = mutation_write_hook
        self._transaction_state = _transaction_state

    @contextmanager
    def mutation_transaction(self):
        """Yield one repository facade whose writes commit atomically."""

        if self._transaction_state is not None:
            yield self
            return

        with self._session_factory() as session:
            state = _MutationTransactionState(
                session=session,
                write_hook=self._mutation_write_hook,
            )
            transaction_repository = SqlAlchemyAlertRepository(
                cast(Callable[[], AbstractContextManager[Session]], lambda: _BorrowedSessionContext(state)),
                mutation_write_hook=self._mutation_write_hook,
                _transaction_state=state,
            )
            try:
                yield transaction_repository
                if state.aborted:
                    raise RuntimeError("SOC mutation transaction was rolled back by a repository operation")
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def save_run(self, run: AnalysisRun) -> None:
        with self._session_factory() as session:
            _upsert_run(session, run)
            session.commit()

    def save_analysis_bundle(
        self,
        *,
        run: AnalysisRun,
        summary: AlertSummary,
        review_item: ReviewQueueItem | None,
        audit_record: DecisionAuditRecord,
    ) -> None:
        """Persist one Runtime result and its read models in one transaction."""

        with self._session_factory() as session:
            _upsert_run(session, run)
            _upsert_summary(session, summary)
            if review_item is not None:
                _upsert_review_item(session, review_item)
            _upsert_audit_record(session, audit_record)
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

    def claim_run_recovery(
        self,
        run: AnalysisRun,
        *,
        expected_status: AnalysisRunStatus = AnalysisRunStatus.RUNNING,
    ) -> bool:
        """Atomically transition one running run into its recovery state."""

        payload = run.model_dump(mode="json")
        now = datetime.now(UTC)
        values = _row_values(run, payload, updated_at=now)
        with self._session_factory() as session:
            result = session.execute(
                update(SocAnalysisRunRow)
                .where(
                    SocAnalysisRunRow.run_id == run.run_id,
                    SocAnalysisRunRow.status == expected_status.value,
                )
                .values(**values)
            )
            session.commit()
            return result.rowcount == 1

    def save_audit_record(self, record: DecisionAuditRecord) -> None:
        with self._session_factory() as session:
            _upsert_audit_record(session, record)
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

    def append_mutation_audit(self, record: SocMutationAuditRecord) -> None:
        payload = record.model_dump(mode="json")
        with self._session_factory() as session:
            if session.get(SocMutationAuditRow, record.audit_id) is not None:
                raise ValueError(f"mutation audit {record.audit_id} already exists")
            session.add(
                SocMutationAuditRow(
                    audit_id=record.audit_id,
                    **_mutation_audit_row_values(record, payload),
                )
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ValueError(f"mutation audit idempotency key {record.idempotency_key} already exists for {record.operation.value}") from exc

    def find_mutation_audit_by_idempotency_key(
        self,
        operation: SocMutationOperation,
        idempotency_key: str,
    ) -> SocMutationAuditRecord | None:
        with self._session_factory() as session:
            row = session.execute(
                select(SocMutationAuditRow)
                .where(
                    SocMutationAuditRow.operation == operation.value,
                    SocMutationAuditRow.idempotency_key == idempotency_key,
                )
                .limit(1)
            ).scalar_one_or_none()
            return _mutation_audit_from_row(row) if row is not None else None

    def list_mutation_audits(
        self,
        *,
        operation: SocMutationOperation | None = None,
        run_id: str | None = None,
        queue_id: str | None = None,
        target_id: str | None = None,
        limit: int = 100,
    ) -> list[SocMutationAuditRecord]:
        query = select(SocMutationAuditRow)
        filters = {
            "operation": operation.value if operation is not None else None,
            "run_id": run_id,
            "queue_id": queue_id,
            "target_id": target_id,
        }
        for name, value in filters.items():
            if value is not None:
                query = query.where(getattr(SocMutationAuditRow, name) == value)
        with self._session_factory() as session:
            rows = session.execute(
                query.order_by(
                    SocMutationAuditRow.occurred_at.desc(),
                    SocMutationAuditRow.audit_id.desc(),
                ).limit(limit)
            ).scalars()
            return [_mutation_audit_from_row(row) for row in rows]

    def save_alert_summary(self, summary: AlertSummary) -> None:
        with self._session_factory() as session:
            _upsert_summary(session, summary)
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

        matches = [match for summary in summaries if (match := score_similar_alert(query, summary)) is not None]
        return sorted(matches, key=lambda item: (item.score, item.summary.updated_at), reverse=True)[: query.limit]

    def save_review_item(self, item: ReviewQueueItem) -> None:
        with self._session_factory() as session:
            _upsert_review_item(session, item)
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

    def get_approval_grant_by_request_id(self, approval_request_id: str) -> SocAgentApprovalGrant | None:
        with self._session_factory() as session:
            result = session.execute(select(SocApprovalGrantRow).where(SocApprovalGrantRow.approval_request_id == approval_request_id).limit(1))
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return SocAgentApprovalGrant.model_validate(row.grant_payload)

    def create_approval_request(self, approval_request: SocAgentApprovalRequest) -> bool:
        payload = approval_request.model_dump(mode="json")
        if self._transaction_state is not None:
            try:
                with self._transaction_state.session.begin_nested():
                    self._transaction_state.session.add(
                        SocApprovalRequestRow(
                            approval_request_id=approval_request.approval_request_id,
                            **_approval_request_row_values(approval_request, payload),
                        )
                    )
                    self._transaction_state.flush_write()
            except IntegrityError:
                return False
            return True
        with self._session_factory() as session:
            session.add(
                SocApprovalRequestRow(
                    approval_request_id=approval_request.approval_request_id,
                    **_approval_request_row_values(approval_request, payload),
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return False
            return True

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

    def resolve_approval_request(
        self,
        approval_request: SocAgentApprovalRequest,
        *,
        expected_status: SocAgentApprovalRequestStatus,
        grant: SocAgentApprovalGrant | None = None,
    ) -> bool:
        payload = approval_request.model_dump(mode="json")
        if self._transaction_state is not None:
            try:
                with self._transaction_state.session.begin_nested():
                    if not self._resolve_approval_request_in_session(
                        self._transaction_state.session,
                        approval_request,
                        payload=payload,
                        expected_status=expected_status,
                        grant=grant,
                    ):
                        return False
                    self._transaction_state.flush_write()
            except IntegrityError:
                return False
            return True
        with self._session_factory() as session:
            if not self._resolve_approval_request_in_session(
                session,
                approval_request,
                payload=payload,
                expected_status=expected_status,
                grant=grant,
            ):
                return False
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return False
            return True

    def _resolve_approval_request_in_session(
        self,
        session: Session,
        approval_request: SocAgentApprovalRequest,
        *,
        payload: dict,
        expected_status: SocAgentApprovalRequestStatus,
        grant: SocAgentApprovalGrant | None,
    ) -> bool:
        row = session.execute(select(SocApprovalRequestRow).where(SocApprovalRequestRow.approval_request_id == approval_request.approval_request_id).with_for_update()).scalar_one_or_none()
        if row is None or row.status != expected_status.value:
            return False
        if grant is not None:
            existing_grant = session.execute(select(SocApprovalGrantRow).where(SocApprovalGrantRow.approval_request_id == approval_request.approval_request_id).limit(1)).scalar_one_or_none()
            if existing_grant is not None:
                return False
            grant_payload = grant.model_dump(mode="json")
            session.add(
                SocApprovalGrantRow(
                    approval_grant_id=grant.approval_grant_id,
                    **_approval_grant_row_values(grant, grant_payload),
                )
            )
        for key, value in _approval_request_row_values(approval_request, payload).items():
            setattr(row, key, value)
        return True

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

    def get_evidence(self, evidence_id: str) -> InvestigationEvidence | None:
        with self._session_factory() as session:
            row = session.get(SocInvestigationEvidenceRow, evidence_id)
            return InvestigationEvidence.model_validate(row.evidence_payload) if row is not None else None

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

    def create_enrichment_execution(self, execution: SocEnrichmentExecution) -> bool:
        payload = execution.model_dump(mode="json")
        with self._session_factory() as session:
            session.add(
                SocEnrichmentExecutionRow(
                    execution_id=execution.execution_id,
                    **_enrichment_execution_row_values(execution, payload),
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return False
            return True

    def get_enrichment_execution(
        self,
        execution_id: str,
    ) -> SocEnrichmentExecution | None:
        with self._session_factory() as session:
            row = session.get(SocEnrichmentExecutionRow, execution_id)
            return SocEnrichmentExecution.model_validate(row.execution_payload) if row is not None else None

    def find_enrichment_execution_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SocEnrichmentExecution | None:
        with self._session_factory() as session:
            row = session.execute(select(SocEnrichmentExecutionRow).where(SocEnrichmentExecutionRow.idempotency_key == idempotency_key).limit(1)).scalar_one_or_none()
            return SocEnrichmentExecution.model_validate(row.execution_payload) if row is not None else None

    def list_enrichment_executions(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        limit: int = 20,
    ) -> list[SocEnrichmentExecution]:
        with self._session_factory() as session:
            query = select(SocEnrichmentExecutionRow)
            if run_id is not None:
                query = query.where(SocEnrichmentExecutionRow.run_id == run_id)
            if alert_id is not None:
                query = query.where(SocEnrichmentExecutionRow.alert_id == alert_id)
            rows = session.execute(
                query.order_by(
                    SocEnrichmentExecutionRow.created_at.desc(),
                    SocEnrichmentExecutionRow.execution_id.desc(),
                ).limit(max(0, limit))
            ).scalars()
            return [SocEnrichmentExecution.model_validate(row.execution_payload) for row in rows]

    def compare_and_set_enrichment_execution(
        self,
        execution: SocEnrichmentExecution,
        *,
        expected_version: int,
    ) -> bool:
        if execution.version != expected_version + 1:
            raise ValueError("enrichment execution CAS must increment version by one")
        payload = execution.model_dump(mode="json")
        values = _enrichment_execution_row_values(execution, payload)
        with self._session_factory() as session:
            result = session.execute(
                update(SocEnrichmentExecutionRow)
                .where(
                    SocEnrichmentExecutionRow.execution_id == execution.execution_id,
                    SocEnrichmentExecutionRow.version == expected_version,
                    SocEnrichmentExecutionRow.idempotency_key == execution.idempotency_key,
                )
                .values(**values)
            )
            session.commit()
            return result.rowcount == 1

    def create_enrichment_action_attempt(
        self,
        attempt: SocEnrichmentActionAttempt,
    ) -> bool:
        payload = attempt.model_dump(mode="json")
        with self._session_factory() as session:
            session.add(
                SocEnrichmentActionAttemptRow(
                    attempt_id=attempt.attempt_id,
                    **_enrichment_attempt_row_values(attempt, payload),
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return False
            return True

    def get_enrichment_action_attempt(
        self,
        attempt_id: str,
    ) -> SocEnrichmentActionAttempt | None:
        with self._session_factory() as session:
            row = session.get(SocEnrichmentActionAttemptRow, attempt_id)
            return SocEnrichmentActionAttempt.model_validate(row.attempt_payload) if row is not None else None

    def compare_and_set_enrichment_action_attempt(
        self,
        attempt: SocEnrichmentActionAttempt,
        *,
        expected_version: int,
    ) -> bool:
        if attempt.version != expected_version + 1:
            raise ValueError("enrichment attempt CAS must increment version by one")
        payload = attempt.model_dump(mode="json")
        values = _enrichment_attempt_row_values(attempt, payload)
        with self._session_factory() as session:
            result = session.execute(
                update(SocEnrichmentActionAttemptRow)
                .where(
                    SocEnrichmentActionAttemptRow.attempt_id == attempt.attempt_id,
                    SocEnrichmentActionAttemptRow.version == expected_version,
                    SocEnrichmentActionAttemptRow.action_idempotency_key == attempt.action_idempotency_key,
                )
                .values(**values)
            )
            session.commit()
            return result.rowcount == 1

    def list_enrichment_action_attempts(
        self,
        execution_id: str,
    ) -> list[SocEnrichmentActionAttempt]:
        with self._session_factory() as session:
            rows = session.execute(
                select(SocEnrichmentActionAttemptRow)
                .where(SocEnrichmentActionAttemptRow.execution_id == execution_id)
                .order_by(
                    SocEnrichmentActionAttemptRow.plan_action_id,
                    SocEnrichmentActionAttemptRow.attempt_number,
                    SocEnrichmentActionAttemptRow.started_at,
                )
            ).scalars()
            return [SocEnrichmentActionAttempt.model_validate(row.attempt_payload) for row in rows]

    def save_authorization_enrichment(self, record: AuthorizationEnrichmentRecord) -> None:
        payload = record.model_dump(mode="json")
        with self._session_factory() as session:
            if session.get(SocAuthorizationEnrichmentRow, record.enrichment_id) is not None:
                raise AuthorizationEnrichmentConflictError(f"authorization enrichment {record.enrichment_id} already exists")
            session.add(
                SocAuthorizationEnrichmentRow(
                    enrichment_id=record.enrichment_id,
                    **_authorization_enrichment_row_values(record, payload),
                )
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise AuthorizationEnrichmentConflictError("authorization enrichment identity already exists") from exc

    def get_authorization_enrichment(
        self,
        enrichment_id: str,
    ) -> AuthorizationEnrichmentRecord | None:
        with self._session_factory() as session:
            row = session.get(SocAuthorizationEnrichmentRow, enrichment_id)
            return _authorization_enrichment_from_row(row) if row is not None else None

    def find_authorization_enrichment_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> AuthorizationEnrichmentRecord | None:
        with self._session_factory() as session:
            result = session.execute(select(SocAuthorizationEnrichmentRow).where(SocAuthorizationEnrichmentRow.idempotency_key == idempotency_key).limit(1))
            row = result.scalar_one_or_none()
            return _authorization_enrichment_from_row(row) if row is not None else None

    def list_authorization_enrichments(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        limit: int = 50,
    ) -> list[AuthorizationEnrichmentRecord]:
        target_filters = []
        if run_id:
            target_filters.append(SocAuthorizationEnrichmentRow.run_id == run_id)
        if alert_id:
            target_filters.append(SocAuthorizationEnrichmentRow.alert_id == alert_id)
        if queue_id:
            target_filters.append(SocAuthorizationEnrichmentRow.queue_id == queue_id)
        with self._session_factory() as session:
            query = select(SocAuthorizationEnrichmentRow)
            if target_filters:
                query = query.where(or_(*target_filters))
            result = session.execute(query.order_by(SocAuthorizationEnrichmentRow.created_at.desc()).limit(limit))
            return [_authorization_enrichment_from_row(row) for row in result.scalars()]

    def save_disposition_proposal(self, proposal: SocDispositionProposalRecord) -> None:
        payload = proposal.model_dump(mode="json")
        with self._session_factory() as session:
            if session.get(SocDispositionProposalRow, proposal.proposal_id) is not None:
                raise DispositionProposalConflictError(f"disposition proposal {proposal.proposal_id} already exists")
            session.add(
                SocDispositionProposalRow(
                    proposal_id=proposal.proposal_id,
                    **_disposition_proposal_row_values(proposal, payload),
                )
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise DispositionProposalConflictError("disposition proposal identity already exists") from exc

    def get_disposition_proposal(
        self,
        proposal_id: str,
    ) -> SocDispositionProposalRecord | None:
        with self._session_factory() as session:
            row = session.get(SocDispositionProposalRow, proposal_id)
            return _disposition_proposal_from_row(row) if row is not None else None

    def find_disposition_proposal_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SocDispositionProposalRecord | None:
        with self._session_factory() as session:
            result = session.execute(select(SocDispositionProposalRow).where(SocDispositionProposalRow.idempotency_key == idempotency_key).limit(1))
            row = result.scalar_one_or_none()
            return _disposition_proposal_from_row(row) if row is not None else None

    def find_disposition_proposal_by_key(
        self,
        proposal_key: str,
    ) -> SocDispositionProposalRecord | None:
        with self._session_factory() as session:
            result = session.execute(select(SocDispositionProposalRow).where(SocDispositionProposalRow.proposal_key == proposal_key).limit(1))
            row = result.scalar_one_or_none()
            return _disposition_proposal_from_row(row) if row is not None else None

    def list_disposition_proposals(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        enrichment_id: str | None = None,
        limit: int = 50,
    ) -> list[SocDispositionProposalRecord]:
        target_filters = []
        if run_id:
            target_filters.append(SocDispositionProposalRow.run_id == run_id)
        if alert_id:
            target_filters.append(SocDispositionProposalRow.alert_id == alert_id)
        if queue_id:
            target_filters.append(SocDispositionProposalRow.queue_id == queue_id)
        if enrichment_id:
            target_filters.append(SocDispositionProposalRow.source_enrichment_id == enrichment_id)
        with self._session_factory() as session:
            query = select(SocDispositionProposalRow)
            for target_filter in target_filters:
                query = query.where(target_filter)
            result = session.execute(query.order_by(SocDispositionProposalRow.created_at.desc()).limit(limit))
            return [_disposition_proposal_from_row(row) for row in result.scalars()]

    def save_tenant_policy_decision(self, decision: TenantPolicyDecision) -> None:
        payload = decision.model_dump(mode="json")
        with self._session_factory() as session:
            if session.get(SocTenantPolicyDecisionRow, decision.decision_id) is not None:
                raise TenantPolicyDecisionConflictError(f"tenant policy decision {decision.decision_id} already exists")
            session.add(
                SocTenantPolicyDecisionRow(
                    decision_id=decision.decision_id,
                    **_tenant_policy_decision_row_values(decision, payload),
                )
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise TenantPolicyDecisionConflictError("tenant policy decision identity already exists") from exc

    def get_tenant_policy_decision(self, decision_id: str) -> TenantPolicyDecision | None:
        with self._session_factory() as session:
            row = session.get(SocTenantPolicyDecisionRow, decision_id)
            return _tenant_policy_decision_from_row(row) if row is not None else None

    def find_tenant_policy_decision_by_key(self, decision_key: str) -> TenantPolicyDecision | None:
        with self._session_factory() as session:
            result = session.execute(select(SocTenantPolicyDecisionRow).where(SocTenantPolicyDecisionRow.decision_key == decision_key).limit(1))
            row = result.scalar_one_or_none()
            return _tenant_policy_decision_from_row(row) if row is not None else None

    def find_tenant_policy_decision_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> TenantPolicyDecision | None:
        with self._session_factory() as session:
            result = session.execute(select(SocTenantPolicyDecisionRow).where(SocTenantPolicyDecisionRow.idempotency_key == idempotency_key).limit(1))
            row = result.scalar_one_or_none()
            return _tenant_policy_decision_from_row(row) if row is not None else None

    def list_tenant_policy_decisions(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        tenant_id: str | None = None,
        policy_id: str | None = None,
        limit: int = 100,
    ) -> list[TenantPolicyDecision]:
        target_filters = []
        if run_id:
            target_filters.append(SocTenantPolicyDecisionRow.run_id == run_id)
        if alert_id:
            target_filters.append(SocTenantPolicyDecisionRow.alert_id == alert_id)
        if tenant_id:
            target_filters.append(SocTenantPolicyDecisionRow.tenant_id == tenant_id)
        if policy_id:
            target_filters.append(SocTenantPolicyDecisionRow.policy_id == policy_id)
        with self._session_factory() as session:
            query = select(SocTenantPolicyDecisionRow)
            for target_filter in target_filters:
                query = query.where(target_filter)
            result = session.execute(query.order_by(SocTenantPolicyDecisionRow.created_at.desc()).limit(limit))
            return [_tenant_policy_decision_from_row(row) for row in result.scalars()]

    def save_disposition_sample_manifest(self, manifest: SocDispositionSampleManifest) -> None:
        payload = manifest.model_dump(mode="json")
        with self._session_factory() as session:
            if session.get(SocDispositionSampleManifestRow, manifest.sample_id) is not None:
                raise DispositionEvaluationConflictError(f"disposition sample {manifest.sample_id} already exists")
            session.add(
                SocDispositionSampleManifestRow(
                    sample_id=manifest.sample_id,
                    sample_key=manifest.sample_key,
                    scope_hash=manifest.scope_hash,
                    population_hash=manifest.population_hash,
                    population_count=manifest.population_count,
                    sample_size=manifest.sample_size,
                    idempotency_key=manifest.idempotency_key,
                    created_by_actor_id=manifest.created_by.actor_id,
                    created_at=manifest.created_at,
                    manifest_payload=payload,
                )
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise DispositionEvaluationConflictError("disposition sample identity already exists") from exc

    def get_disposition_sample_manifest(self, sample_id: str) -> SocDispositionSampleManifest | None:
        with self._session_factory() as session:
            row = session.get(SocDispositionSampleManifestRow, sample_id)
            return _disposition_sample_manifest_from_row(row) if row is not None else None

    def find_disposition_sample_manifest_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SocDispositionSampleManifest | None:
        with self._session_factory() as session:
            row = session.execute(select(SocDispositionSampleManifestRow).where(SocDispositionSampleManifestRow.idempotency_key == idempotency_key).limit(1)).scalar_one_or_none()
            return _disposition_sample_manifest_from_row(row) if row is not None else None

    def find_disposition_sample_manifest_by_key(
        self,
        sample_key: str,
    ) -> SocDispositionSampleManifest | None:
        with self._session_factory() as session:
            row = session.execute(select(SocDispositionSampleManifestRow).where(SocDispositionSampleManifestRow.sample_key == sample_key).limit(1)).scalar_one_or_none()
            return _disposition_sample_manifest_from_row(row) if row is not None else None

    def list_disposition_sample_manifests(
        self,
        *,
        scope_hash: str | None = None,
        limit: int = 100,
    ) -> list[SocDispositionSampleManifest]:
        query = select(SocDispositionSampleManifestRow)
        if scope_hash is not None:
            query = query.where(SocDispositionSampleManifestRow.scope_hash == scope_hash)
        with self._session_factory() as session:
            rows = session.execute(query.order_by(SocDispositionSampleManifestRow.created_at.desc()).limit(limit)).scalars()
            return [_disposition_sample_manifest_from_row(row) for row in rows]

    def save_disposition_outcome(self, outcome: SocDispositionOutcomeRecord) -> None:
        payload = outcome.model_dump(mode="json")
        with self._session_factory() as session:
            if session.get(SocDispositionOutcomeRow, outcome.outcome_id) is not None:
                raise DispositionEvaluationConflictError(f"disposition outcome {outcome.outcome_id} already exists")
            session.add(
                SocDispositionOutcomeRow(
                    outcome_id=outcome.outcome_id,
                    lineage_key=outcome.lineage_key,
                    proposal_id=outcome.proposal_id,
                    run_id=outcome.run_id,
                    alert_id=outcome.alert_id,
                    queue_id=outcome.queue_id,
                    review_kind=outcome.review_kind.value,
                    outcome_status=outcome.outcome_status.value,
                    observed_disposition=outcome.observed_disposition.value,
                    source=outcome.source.value,
                    sample_id=outcome.sample_id,
                    supersedes_outcome_id=outcome.supersedes_outcome_id,
                    idempotency_key=outcome.idempotency_key,
                    reviewed_by_actor_id=outcome.reviewed_by.actor_id,
                    observed_at=outcome.observed_at,
                    created_at=outcome.created_at,
                    outcome_payload=payload,
                )
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise DispositionEvaluationConflictError("disposition outcome identity already exists") from exc

    def get_disposition_outcome(self, outcome_id: str) -> SocDispositionOutcomeRecord | None:
        with self._session_factory() as session:
            row = session.get(SocDispositionOutcomeRow, outcome_id)
            return _disposition_outcome_from_row(row) if row is not None else None

    def find_disposition_outcome_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SocDispositionOutcomeRecord | None:
        with self._session_factory() as session:
            row = session.execute(select(SocDispositionOutcomeRow).where(SocDispositionOutcomeRow.idempotency_key == idempotency_key).limit(1)).scalar_one_or_none()
            return _disposition_outcome_from_row(row) if row is not None else None

    def list_disposition_outcomes(
        self,
        *,
        proposal_id: str | None = None,
        queue_id: str | None = None,
        review_kind: SocDispositionOutcomeReviewKind | None = None,
        sample_id: str | None = None,
        limit: int = 500,
    ) -> list[SocDispositionOutcomeRecord]:
        query = select(SocDispositionOutcomeRow)
        filters = []
        if proposal_id is not None:
            filters.append(SocDispositionOutcomeRow.proposal_id == proposal_id)
        if queue_id is not None:
            filters.append(SocDispositionOutcomeRow.queue_id == queue_id)
        if review_kind is not None:
            filters.append(SocDispositionOutcomeRow.review_kind == review_kind.value)
        if sample_id is not None:
            filters.append(SocDispositionOutcomeRow.sample_id == sample_id)
        for target_filter in filters:
            query = query.where(target_filter)
        with self._session_factory() as session:
            rows = session.execute(
                query.order_by(
                    SocDispositionOutcomeRow.observed_at.desc(),
                    SocDispositionOutcomeRow.created_at.desc(),
                    SocDispositionOutcomeRow.outcome_id.desc(),
                ).limit(limit)
            ).scalars()
            return [_disposition_outcome_from_row(row) for row in rows]

    def list_latest_disposition_outcomes_for_proposals(
        self,
        *,
        proposal_ids: Sequence[str],
        review_kind: SocDispositionOutcomeReviewKind,
        sample_id: str | None = None,
    ) -> list[SocDispositionOutcomeRecord]:
        ordered_ids = list(dict.fromkeys(proposal_ids))
        if not ordered_ids:
            return []

        latest: dict[str, SocDispositionOutcomeRecord] = {}
        with self._session_factory() as session:
            for offset in range(0, len(ordered_ids), 500):
                chunk = ordered_ids[offset : offset + 500]
                rank = func.row_number().over(
                    partition_by=SocDispositionOutcomeRow.proposal_id,
                    order_by=(
                        SocDispositionOutcomeRow.observed_at.desc(),
                        SocDispositionOutcomeRow.created_at.desc(),
                        SocDispositionOutcomeRow.outcome_id.desc(),
                    ),
                )
                ranked = select(
                    SocDispositionOutcomeRow.outcome_id.label("outcome_id"),
                    SocDispositionOutcomeRow.proposal_id.label("proposal_id"),
                    rank.label("outcome_rank"),
                ).where(
                    SocDispositionOutcomeRow.proposal_id.in_(chunk),
                    SocDispositionOutcomeRow.review_kind == review_kind.value,
                )
                if sample_id is not None:
                    ranked = ranked.where(SocDispositionOutcomeRow.sample_id == sample_id)
                ranked_subquery = ranked.subquery()
                rows = session.execute(
                    select(SocDispositionOutcomeRow)
                    .join(
                        ranked_subquery,
                        SocDispositionOutcomeRow.outcome_id == ranked_subquery.c.outcome_id,
                    )
                    .where(ranked_subquery.c.outcome_rank == 1)
                ).scalars()
                for row in rows:
                    latest[row.proposal_id] = _disposition_outcome_from_row(row)
        return [latest[proposal_id] for proposal_id in ordered_ids if proposal_id in latest]

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

    def compare_and_set_memory_record(
        self,
        record: SocMemoryRecord,
        *,
        expected_version: int,
    ) -> bool:
        """Persist one memory transition only when its prior version still matches."""

        payload = record.model_dump(mode="json")
        with self._session_factory() as session:
            result = session.execute(
                update(SocMemoryRecordRow)
                .where(
                    SocMemoryRecordRow.memory_id == record.memory_id,
                    SocMemoryRecordRow.version == expected_version,
                )
                .values(**_memory_record_row_values(record, payload))
            )
            session.commit()
            return result.rowcount == 1

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

    def append_governed_context_fact(
        self,
        fact: GovernedContextFact,
        *,
        expected_latest_version: int | None,
    ) -> None:
        try:
            with self._session_factory() as session, session.begin():
                result = session.execute(select(SocGovernedContextFactRow).where(SocGovernedContextFactRow.current_key == fact.fact_id).with_for_update())
                latest_row = result.scalar_one_or_none()
                latest = _governed_context_fact_from_row(latest_row) if latest_row is not None else None
                validate_governed_context_fact_append(
                    fact,
                    latest=latest,
                    expected_latest_version=expected_latest_version,
                )
                if latest_row is not None:
                    latest_row.current_key = None
                    latest_row.is_latest = False
                    session.flush()
                session.add(
                    SocGovernedContextFactRow(
                        fact_version_id=fact.fact_version_id,
                        **_governed_context_fact_row_values(fact),
                    )
                )
        except IntegrityError as exc:
            raise GovernedContextFactVersionConflictError(f"concurrent governed fact update rejected for {fact.fact_id}") from exc

    def get_governed_context_fact(
        self,
        fact_id: str,
        *,
        version: int | None = None,
    ) -> GovernedContextFact | None:
        with self._session_factory() as session:
            query = select(SocGovernedContextFactRow).where(SocGovernedContextFactRow.fact_id == fact_id)
            if version is None:
                query = query.where(SocGovernedContextFactRow.is_latest.is_(True))
            else:
                query = query.where(SocGovernedContextFactRow.version == version)
            row = session.execute(query.limit(1)).scalar_one_or_none()
            return _governed_context_fact_from_row(row) if row is not None else None

    def list_governed_context_facts(
        self,
        query: GovernedContextFactQuery,
    ) -> list[GovernedContextFact]:
        with self._session_factory() as session:
            statement = select(SocGovernedContextFactRow)
            if query.latest_only:
                statement = statement.where(SocGovernedContextFactRow.is_latest.is_(True))
            filters = {
                "fact_id": query.fact_id,
                "fact_type": query.fact_type.value if query.fact_type is not None else None,
                "status": query.status.value if query.status is not None else None,
                "tenant_id": query.tenant_id,
                "environment": query.environment,
            }
            for name, value in filters.items():
                if value is not None:
                    statement = statement.where(getattr(SocGovernedContextFactRow, name) == value)
            if query.valid_at is not None:
                statement = statement.where(
                    SocGovernedContextFactRow.valid_from <= query.valid_at,
                    SocGovernedContextFactRow.valid_until > query.valid_at,
                )
            result = session.execute(
                statement.order_by(
                    SocGovernedContextFactRow.updated_at.desc(),
                    SocGovernedContextFactRow.version.desc(),
                ).limit(query.limit)
            )
            return [_governed_context_fact_from_row(row) for row in result.scalars()]

    def list_governed_context_fact_versions(
        self,
        fact_id: str,
        *,
        limit: int = 100,
    ) -> list[GovernedContextFact]:
        with self._session_factory() as session:
            result = session.execute(select(SocGovernedContextFactRow).where(SocGovernedContextFactRow.fact_id == fact_id).order_by(SocGovernedContextFactRow.version.desc()).limit(limit))
            return [_governed_context_fact_from_row(row) for row in result.scalars()]

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

    def save_memory_pattern_observation(
        self,
        observation: MemoryPatternObservation,
    ) -> None:
        payload = observation.model_dump(mode="json")
        with self._session_factory() as session:
            existing = session.get(
                SocMemoryPatternObservationRow,
                observation.observation_id,
            )
            if existing is not None:
                if existing.observation_payload != payload:
                    raise ValueError(f"memory pattern observation {observation.observation_id} already exists")
                return
            session.add(
                SocMemoryPatternObservationRow(
                    observation_id=observation.observation_id,
                    **_memory_pattern_observation_row_values(observation, payload),
                )
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ValueError("memory pattern idempotency or aggregation/source identity already exists") from exc

    def get_memory_pattern_observation(
        self,
        observation_id: str,
    ) -> MemoryPatternObservation | None:
        with self._session_factory() as session:
            row = session.get(SocMemoryPatternObservationRow, observation_id)
            return MemoryPatternObservation.model_validate(row.observation_payload) if row is not None else None

    def find_memory_pattern_observation_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> MemoryPatternObservation | None:
        with self._session_factory() as session:
            row = session.execute(select(SocMemoryPatternObservationRow).where(SocMemoryPatternObservationRow.idempotency_key == idempotency_key).limit(1)).scalar_one_or_none()
            return MemoryPatternObservation.model_validate(row.observation_payload) if row is not None else None

    def list_memory_pattern_observations(
        self,
        *,
        aggregation_key: str | None = None,
        lineage_key: str | None = None,
        tenant_id: str | None = None,
        environment: str | None = None,
        data_class: MemoryPatternDataClass | None = None,
        source_type: MemoryPatternSourceType | None = None,
        limit: int = 500,
    ) -> list[MemoryPatternObservation]:
        with self._session_factory() as session:
            query = select(SocMemoryPatternObservationRow)
            if aggregation_key is not None:
                query = query.where(SocMemoryPatternObservationRow.aggregation_key == aggregation_key)
            if lineage_key is not None:
                query = query.where(SocMemoryPatternObservationRow.lineage_key == lineage_key)
            if tenant_id is not None:
                query = query.where(SocMemoryPatternObservationRow.tenant_id == tenant_id)
            if environment is not None:
                query = query.where(SocMemoryPatternObservationRow.environment == environment)
            if data_class is not None:
                query = query.where(SocMemoryPatternObservationRow.data_class == data_class.value)
            if source_type is not None:
                query = query.where(SocMemoryPatternObservationRow.source_type == source_type.value)
            rows = session.execute(
                query.order_by(
                    SocMemoryPatternObservationRow.observed_at.asc(),
                    SocMemoryPatternObservationRow.observation_id.asc(),
                ).limit(limit)
            ).scalars()
            return [MemoryPatternObservation.model_validate(row.observation_payload) for row in rows]

    def save_skill_feedback_observation(self, observation: SkillFeedbackObservation) -> None:
        payload = observation.model_dump(mode="json")
        with self._session_factory() as session:
            existing = session.get(SocSkillFeedbackObservationRow, observation.observation_id)
            if existing is not None:
                if existing.observation_payload != payload:
                    raise ValueError(f"skill feedback observation {observation.observation_id} already exists")
                return
            session.add(
                SocSkillFeedbackObservationRow(
                    observation_id=observation.observation_id,
                    **_skill_feedback_observation_row_values(observation, payload),
                )
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ValueError("skill feedback idempotency or aggregation/source identity already exists") from exc

    def get_skill_feedback_observation(
        self,
        observation_id: str,
    ) -> SkillFeedbackObservation | None:
        with self._session_factory() as session:
            row = session.get(SocSkillFeedbackObservationRow, observation_id)
            return SkillFeedbackObservation.model_validate(row.observation_payload) if row is not None else None

    def find_skill_feedback_observation_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SkillFeedbackObservation | None:
        with self._session_factory() as session:
            row = session.execute(select(SocSkillFeedbackObservationRow).where(SocSkillFeedbackObservationRow.idempotency_key == idempotency_key).limit(1)).scalar_one_or_none()
            return SkillFeedbackObservation.model_validate(row.observation_payload) if row is not None else None

    def list_skill_feedback_observations(
        self,
        *,
        aggregation_key: str | None = None,
        tenant_id: str | None = None,
        data_class: SocEvaluationDataClass | None = None,
        source_type: SkillFeedbackSourceType | None = None,
        limit: int = 500,
    ) -> list[SkillFeedbackObservation]:
        with self._session_factory() as session:
            query = select(SocSkillFeedbackObservationRow)
            if aggregation_key is not None:
                query = query.where(SocSkillFeedbackObservationRow.aggregation_key == aggregation_key)
            if tenant_id is not None:
                query = query.where(SocSkillFeedbackObservationRow.tenant_id == tenant_id)
            if data_class is not None:
                query = query.where(SocSkillFeedbackObservationRow.data_class == data_class.value)
            if source_type is not None:
                query = query.where(SocSkillFeedbackObservationRow.source_type == source_type.value)
            rows = session.execute(
                query.order_by(
                    SocSkillFeedbackObservationRow.observed_at.asc(),
                    SocSkillFeedbackObservationRow.observation_id.asc(),
                ).limit(limit)
            ).scalars()
            return [SkillFeedbackObservation.model_validate(row.observation_payload) for row in rows]

    def save_skill_improvement_candidate(
        self,
        candidate: SkillImprovementCandidate,
    ) -> None:
        payload = candidate.model_dump(mode="json")
        with self._session_factory() as session:
            existing = session.get(SocSkillImprovementCandidateRow, candidate.candidate_id)
            if existing is not None:
                if existing.candidate_payload != payload:
                    raise ValueError(f"skill improvement candidate {candidate.candidate_id} already exists")
                return
            session.add(
                SocSkillImprovementCandidateRow(
                    candidate_id=candidate.candidate_id,
                    **_skill_improvement_candidate_row_values(candidate, payload),
                )
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ValueError(f"skill improvement aggregation key {candidate.aggregation_key} already exists") from exc

    def compare_and_set_skill_improvement_candidate(
        self,
        candidate: SkillImprovementCandidate,
        *,
        expected_version: int,
    ) -> bool:
        if candidate.version != expected_version + 1:
            raise ValueError("skill improvement candidate version must increment by one")
        payload = candidate.model_dump(mode="json")
        with self._session_factory() as session:
            result = session.execute(
                update(SocSkillImprovementCandidateRow)
                .where(
                    SocSkillImprovementCandidateRow.candidate_id == candidate.candidate_id,
                    SocSkillImprovementCandidateRow.version == expected_version,
                )
                .values(**_skill_improvement_candidate_row_values(candidate, payload))
            )
            session.commit()
            return result.rowcount == 1

    def get_skill_improvement_candidate(
        self,
        candidate_id: str,
    ) -> SkillImprovementCandidate | None:
        with self._session_factory() as session:
            row = session.get(SocSkillImprovementCandidateRow, candidate_id)
            return SkillImprovementCandidate.model_validate(row.candidate_payload) if row is not None else None

    def find_skill_improvement_candidate_by_aggregation_key(
        self,
        aggregation_key: str,
    ) -> SkillImprovementCandidate | None:
        with self._session_factory() as session:
            row = session.execute(select(SocSkillImprovementCandidateRow).where(SocSkillImprovementCandidateRow.aggregation_key == aggregation_key).limit(1)).scalar_one_or_none()
            return SkillImprovementCandidate.model_validate(row.candidate_payload) if row is not None else None

    def list_skill_improvement_candidates(
        self,
        *,
        status: SkillImprovementCandidateStatus | None = None,
        tenant_id: str | None = None,
        data_class: SocEvaluationDataClass | None = None,
        skill_name: str | None = None,
        limit: int = 100,
    ) -> list[SkillImprovementCandidate]:
        with self._session_factory() as session:
            query = select(SocSkillImprovementCandidateRow)
            if status is not None:
                query = query.where(SocSkillImprovementCandidateRow.status == status.value)
            if tenant_id is not None:
                query = query.where(SocSkillImprovementCandidateRow.tenant_id == tenant_id)
            if data_class is not None:
                query = query.where(SocSkillImprovementCandidateRow.data_class == data_class.value)
            if skill_name is not None:
                query = query.where(SocSkillImprovementCandidateRow.skill_name == skill_name)
            rows = session.execute(
                query.order_by(
                    SocSkillImprovementCandidateRow.updated_at.desc(),
                    SocSkillImprovementCandidateRow.candidate_id.desc(),
                ).limit(limit)
            ).scalars()
            return [SkillImprovementCandidate.model_validate(row.candidate_payload) for row in rows]


def _upsert_run(session: Session, run: AnalysisRun) -> None:
    payload = run.model_dump(mode="json")
    now = datetime.now(UTC)
    values = _row_values(run, payload, updated_at=now)
    row = session.get(SocAnalysisRunRow, run.run_id)
    if row is None:
        session.add(SocAnalysisRunRow(run_id=run.run_id, created_at=now, **values))
        return
    for key, value in values.items():
        setattr(row, key, value)


def _upsert_audit_record(session: Session, record: DecisionAuditRecord) -> None:
    payload = record.model_dump(mode="json")
    values = _audit_row_values(record, payload)
    row = session.get(SocDecisionAuditLogRow, record.audit_id)
    if row is None:
        session.add(SocDecisionAuditLogRow(audit_id=record.audit_id, **values))
        return
    for key, value in values.items():
        setattr(row, key, value)


def _upsert_summary(session: Session, summary: AlertSummary) -> None:
    payload = summary.model_dump(mode="json")
    values = _summary_row_values(summary, payload)
    row = session.get(SocAlertSummaryRow, summary.run_id)
    if row is None:
        session.add(SocAlertSummaryRow(run_id=summary.run_id, **values))
        return
    for key, value in values.items():
        setattr(row, key, value)


def _upsert_review_item(session: Session, item: ReviewQueueItem) -> None:
    payload = item.model_dump(mode="json")
    values = _review_queue_row_values(item, payload)
    row = session.get(SocReviewQueueRow, item.queue_id)
    if row is None:
        session.add(SocReviewQueueRow(queue_id=item.queue_id, **values))
        return
    for key, value in values.items():
        setattr(row, key, value)


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
        "resolved_at": approval_request.resolved_at,
        "resolved_by_actor_id": approval_request.resolved_by.actor_id if approval_request.resolved_by is not None else None,
        "resolution_reason": approval_request.resolution_reason,
        "resolution_idempotency_key": approval_request.resolution_idempotency_key,
        "approval_grant_id": approval_request.approval_grant_id,
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


def _enrichment_execution_row_values(
    execution: SocEnrichmentExecution,
    payload: dict,
) -> dict:
    return {
        "idempotency_key": execution.idempotency_key,
        "trigger": execution.trigger.value,
        "run_id": execution.run_id,
        "alert_id": execution.alert_id,
        "thread_id": execution.thread_id,
        "plan_id": execution.plan.plan_id,
        "status": execution.status.value,
        "version": execution.version,
        "retryable": execution.retryable,
        "replay_of_execution_id": execution.replay_of_execution_id,
        "created_at": execution.created_at,
        "updated_at": execution.updated_at,
        "completed_at": execution.completed_at,
        "execution_payload": payload,
    }


def _enrichment_attempt_row_values(
    attempt: SocEnrichmentActionAttempt,
    payload: dict,
) -> dict:
    return {
        "execution_id": attempt.execution_id,
        "plan_action_id": attempt.plan_action_id,
        "attempt_number": attempt.attempt_number,
        "action_idempotency_key": attempt.action_idempotency_key,
        "route": attempt.route,
        "action": attempt.action,
        "adapter_id": attempt.adapter_id,
        "status": attempt.status.value,
        "version": attempt.version,
        "retryable": attempt.retryable,
        "evidence_id": attempt.evidence_id,
        "started_at": attempt.started_at,
        "ended_at": attempt.ended_at,
        "attempt_payload": payload,
    }


def _authorization_enrichment_row_values(
    record: AuthorizationEnrichmentRecord,
    payload: dict,
) -> dict:
    return {
        "run_id": record.run_id,
        "alert_id": record.alert_id,
        "queue_id": record.queue_id,
        "match_status": record.match_result.status.value,
        "query_hash": record.query_hash,
        "matcher_policy_version": record.matcher_policy_version,
        "idempotency_key": record.idempotency_key,
        "replay_of_enrichment_id": record.replay_of_enrichment_id,
        "created_by_actor_id": record.created_by.actor_id,
        "created_at": record.created_at,
        "enrichment_payload": payload,
    }


def _authorization_enrichment_from_row(
    row: SocAuthorizationEnrichmentRow,
) -> AuthorizationEnrichmentRecord:
    record = AuthorizationEnrichmentRecord.model_validate(row.enrichment_payload)
    indexed_values = {
        "enrichment_id": row.enrichment_id,
        "run_id": row.run_id,
        "alert_id": row.alert_id,
        "queue_id": row.queue_id,
        "match_status": row.match_status,
        "query_hash": row.query_hash,
        "matcher_policy_version": row.matcher_policy_version,
        "idempotency_key": row.idempotency_key,
        "replay_of_enrichment_id": row.replay_of_enrichment_id,
        "created_by_actor_id": row.created_by_actor_id,
    }
    contract_values = {
        "enrichment_id": record.enrichment_id,
        "run_id": record.run_id,
        "alert_id": record.alert_id,
        "queue_id": record.queue_id,
        "match_status": record.match_result.status.value,
        "query_hash": record.query_hash,
        "matcher_policy_version": record.matcher_policy_version,
        "idempotency_key": record.idempotency_key,
        "replay_of_enrichment_id": record.replay_of_enrichment_id,
        "created_by_actor_id": record.created_by.actor_id,
    }
    if indexed_values != contract_values:
        raise ValueError(f"authorization enrichment row {row.enrichment_id} does not match its typed payload")
    return record


def _disposition_proposal_row_values(
    proposal: SocDispositionProposalRecord,
    payload: dict,
) -> dict:
    return {
        "proposal_key": proposal.proposal_key,
        "run_id": proposal.run_id,
        "alert_id": proposal.alert_id,
        "queue_id": proposal.queue_id,
        "source_enrichment_id": proposal.source_enrichment_id,
        "proposed_disposition": proposal.proposed_disposition.value,
        "reason_code": proposal.reason_code.value,
        "detection_verdict": proposal.detection_truth.verdict.value,
        "policy_version": proposal.policy_version,
        "idempotency_key": proposal.idempotency_key,
        "created_by_actor_id": proposal.created_by.actor_id,
        "created_at": proposal.created_at,
        "proposal_payload": payload,
    }


def _disposition_proposal_from_row(
    row: SocDispositionProposalRow,
) -> SocDispositionProposalRecord:
    proposal = SocDispositionProposalRecord.model_validate(row.proposal_payload)
    indexed_values = {
        "proposal_id": row.proposal_id,
        "proposal_key": row.proposal_key,
        "run_id": row.run_id,
        "alert_id": row.alert_id,
        "queue_id": row.queue_id,
        "source_enrichment_id": row.source_enrichment_id,
        "proposed_disposition": row.proposed_disposition,
        "reason_code": row.reason_code,
        "detection_verdict": row.detection_verdict,
        "policy_version": row.policy_version,
        "idempotency_key": row.idempotency_key,
        "created_by_actor_id": row.created_by_actor_id,
    }
    contract_values = {
        "proposal_id": proposal.proposal_id,
        "proposal_key": proposal.proposal_key,
        "run_id": proposal.run_id,
        "alert_id": proposal.alert_id,
        "queue_id": proposal.queue_id,
        "source_enrichment_id": proposal.source_enrichment_id,
        "proposed_disposition": proposal.proposed_disposition.value,
        "reason_code": proposal.reason_code.value,
        "detection_verdict": proposal.detection_truth.verdict.value,
        "policy_version": proposal.policy_version,
        "idempotency_key": proposal.idempotency_key,
        "created_by_actor_id": proposal.created_by.actor_id,
    }
    if indexed_values != contract_values:
        raise ValueError(f"disposition proposal row {row.proposal_id} does not match its typed payload")
    return proposal


def _tenant_policy_decision_row_values(
    decision: TenantPolicyDecision,
    payload: dict,
) -> dict:
    return {
        "decision_key": decision.decision_key,
        "idempotency_key": decision.idempotency_key,
        "run_id": decision.run_id,
        "alert_id": decision.alert_id,
        "tenant_id": decision.tenant_id,
        "environment": decision.environment,
        "policy_id": decision.policy_id,
        "policy_version": decision.policy_version,
        "policy_hash": decision.policy_hash,
        "policy_time": decision.policy_time,
        "evaluation_status": decision.evaluation_status.value,
        "selected_rule_id": decision.selected_rule_id,
        "detection_verdict": decision.detection_truth.verdict.value,
        "recommended_disposition": (decision.recommended_disposition.value if decision.recommended_disposition is not None else None),
        "created_by_actor_id": decision.evaluated_by.actor_id,
        "created_at": decision.created_at,
        "decision_payload": payload,
    }


def _tenant_policy_decision_from_row(
    row: SocTenantPolicyDecisionRow,
) -> TenantPolicyDecision:
    decision = TenantPolicyDecision.model_validate(row.decision_payload)
    indexed_values = {
        "decision_id": row.decision_id,
        "decision_key": row.decision_key,
        "idempotency_key": row.idempotency_key,
        "run_id": row.run_id,
        "alert_id": row.alert_id,
        "tenant_id": row.tenant_id,
        "environment": row.environment,
        "policy_id": row.policy_id,
        "policy_version": row.policy_version,
        "policy_hash": row.policy_hash,
        "evaluation_status": row.evaluation_status,
        "selected_rule_id": row.selected_rule_id,
        "detection_verdict": row.detection_verdict,
        "recommended_disposition": row.recommended_disposition,
        "created_by_actor_id": row.created_by_actor_id,
    }
    contract_values = {
        "decision_id": decision.decision_id,
        "decision_key": decision.decision_key,
        "idempotency_key": decision.idempotency_key,
        "run_id": decision.run_id,
        "alert_id": decision.alert_id,
        "tenant_id": decision.tenant_id,
        "environment": decision.environment,
        "policy_id": decision.policy_id,
        "policy_version": decision.policy_version,
        "policy_hash": decision.policy_hash,
        "evaluation_status": decision.evaluation_status.value,
        "selected_rule_id": decision.selected_rule_id,
        "detection_verdict": decision.detection_truth.verdict.value,
        "recommended_disposition": (decision.recommended_disposition.value if decision.recommended_disposition is not None else None),
        "created_by_actor_id": decision.evaluated_by.actor_id,
    }
    if indexed_values != contract_values:
        raise ValueError(f"tenant policy decision row {row.decision_id} does not match its typed payload")
    return decision


def _disposition_sample_manifest_from_row(
    row: SocDispositionSampleManifestRow,
) -> SocDispositionSampleManifest:
    manifest = SocDispositionSampleManifest.model_validate(row.manifest_payload)
    indexed_values = {
        "sample_id": row.sample_id,
        "sample_key": row.sample_key,
        "scope_hash": row.scope_hash,
        "population_hash": row.population_hash,
        "population_count": row.population_count,
        "sample_size": row.sample_size,
        "idempotency_key": row.idempotency_key,
        "created_by_actor_id": row.created_by_actor_id,
    }
    contract_values = {
        "sample_id": manifest.sample_id,
        "sample_key": manifest.sample_key,
        "scope_hash": manifest.scope_hash,
        "population_hash": manifest.population_hash,
        "population_count": manifest.population_count,
        "sample_size": manifest.sample_size,
        "idempotency_key": manifest.idempotency_key,
        "created_by_actor_id": manifest.created_by.actor_id,
    }
    if indexed_values != contract_values:
        raise ValueError(f"disposition sample row {row.sample_id} does not match its typed payload")
    return manifest


def _disposition_outcome_from_row(row: SocDispositionOutcomeRow) -> SocDispositionOutcomeRecord:
    outcome = SocDispositionOutcomeRecord.model_validate(row.outcome_payload)
    indexed_values = {
        "outcome_id": row.outcome_id,
        "lineage_key": row.lineage_key,
        "proposal_id": row.proposal_id,
        "run_id": row.run_id,
        "alert_id": row.alert_id,
        "queue_id": row.queue_id,
        "review_kind": row.review_kind,
        "outcome_status": row.outcome_status,
        "observed_disposition": row.observed_disposition,
        "source": row.source,
        "sample_id": row.sample_id,
        "supersedes_outcome_id": row.supersedes_outcome_id,
        "idempotency_key": row.idempotency_key,
        "reviewed_by_actor_id": row.reviewed_by_actor_id,
    }
    contract_values = {
        "outcome_id": outcome.outcome_id,
        "lineage_key": outcome.lineage_key,
        "proposal_id": outcome.proposal_id,
        "run_id": outcome.run_id,
        "alert_id": outcome.alert_id,
        "queue_id": outcome.queue_id,
        "review_kind": outcome.review_kind.value,
        "outcome_status": outcome.outcome_status.value,
        "observed_disposition": outcome.observed_disposition.value,
        "source": outcome.source.value,
        "sample_id": outcome.sample_id,
        "supersedes_outcome_id": outcome.supersedes_outcome_id,
        "idempotency_key": outcome.idempotency_key,
        "reviewed_by_actor_id": outcome.reviewed_by.actor_id,
    }
    if indexed_values != contract_values:
        raise ValueError(f"disposition outcome row {row.outcome_id} does not match its typed payload")
    return outcome


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


def _mutation_audit_row_values(record: SocMutationAuditRecord, payload: dict) -> dict:
    return {
        "operation": record.operation.value,
        "target_type": record.target_type,
        "target_id": record.target_id,
        "run_id": record.run_id,
        "alert_id": record.alert_id,
        "queue_id": record.queue_id,
        "actor_id": record.actor.actor_id,
        "actor_type": record.actor.actor_type.value,
        "actor_surface": record.actor.surface.value,
        "actor_auth_source": record.actor.auth_source.value,
        "request_id": record.request_id,
        "idempotency_key": record.idempotency_key,
        "command_hash": record.command_hash,
        "reason": record.reason,
        "result_status": record.result_status,
        "result_ref": record.result_ref,
        "occurred_at": record.occurred_at,
        "record_payload": payload,
    }


def _mutation_audit_from_row(row: SocMutationAuditRow) -> SocMutationAuditRecord:
    record = SocMutationAuditRecord.model_validate(row.record_payload)
    indexed_values = {
        "audit_id": row.audit_id,
        "operation": row.operation,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "run_id": row.run_id,
        "alert_id": row.alert_id,
        "queue_id": row.queue_id,
        "actor_id": row.actor_id,
        "actor_type": row.actor_type,
        "actor_surface": row.actor_surface,
        "actor_auth_source": row.actor_auth_source,
        "request_id": row.request_id,
        "idempotency_key": row.idempotency_key,
        "command_hash": row.command_hash,
        "result_status": row.result_status,
        "result_ref": row.result_ref,
    }
    contract_values = {
        "audit_id": record.audit_id,
        "operation": record.operation.value,
        "target_type": record.target_type,
        "target_id": record.target_id,
        "run_id": record.run_id,
        "alert_id": record.alert_id,
        "queue_id": record.queue_id,
        "actor_id": record.actor.actor_id,
        "actor_type": record.actor.actor_type.value,
        "actor_surface": record.actor.surface.value,
        "actor_auth_source": record.actor.auth_source.value,
        "request_id": record.request_id,
        "idempotency_key": record.idempotency_key,
        "command_hash": record.command_hash,
        "result_status": record.result_status,
        "result_ref": record.result_ref,
    }
    if indexed_values != contract_values:
        raise ValueError(f"mutation audit row {row.audit_id} does not match its typed payload")
    return record


def _governed_context_fact_row_values(fact: GovernedContextFact) -> dict:
    return {
        "fact_id": fact.fact_id,
        "version": fact.version,
        "current_key": fact.fact_id if fact.is_latest else None,
        "is_latest": fact.is_latest,
        "fact_type": fact.fact_type.value,
        "status": fact.status.value,
        "tenant_id": fact.tenant_id,
        "environment": fact.environment,
        "valid_from": fact.valid_from,
        "valid_until": fact.valid_until,
        "source_type": fact.source.source_type.value,
        "source_ref": fact.source.source_ref,
        "source_version": fact.source.source_version,
        "source_fresh_until": fact.source.fresh_until,
        "owner_id": fact.owner_id,
        "changed_by_actor_id": fact.changed_by.actor_id,
        "reviewed_by_actor_id": fact.reviewed_by.actor_id if fact.reviewed_by is not None else None,
        "content_hash": fact.content_hash,
        "supersedes_version_id": fact.supersedes_version_id,
        "created_at": fact.created_at,
        "updated_at": fact.updated_at,
        "state_changed_at": fact.state_changed_at,
        "fact_payload": fact.model_dump(mode="json", exclude={"is_latest"}),
    }


def _governed_context_fact_from_row(row: SocGovernedContextFactRow) -> GovernedContextFact:
    payload = dict(row.fact_payload)
    payload["is_latest"] = row.is_latest
    fact = GovernedContextFact.model_validate(payload)
    indexed_values = {
        "fact_version_id": row.fact_version_id,
        "fact_id": row.fact_id,
        "version": row.version,
        "fact_type": row.fact_type,
        "status": row.status,
        "tenant_id": row.tenant_id,
        "environment": row.environment,
        "source_type": row.source_type,
        "source_ref": row.source_ref,
        "source_version": row.source_version,
        "owner_id": row.owner_id,
        "content_hash": row.content_hash,
        "supersedes_version_id": row.supersedes_version_id,
    }
    contract_values = {
        "fact_version_id": fact.fact_version_id,
        "fact_id": fact.fact_id,
        "version": fact.version,
        "fact_type": fact.fact_type.value,
        "status": fact.status.value,
        "tenant_id": fact.tenant_id,
        "environment": fact.environment,
        "source_type": fact.source.source_type.value,
        "source_ref": fact.source.source_ref,
        "source_version": fact.source.source_version,
        "owner_id": fact.owner_id,
        "content_hash": fact.content_hash,
        "supersedes_version_id": fact.supersedes_version_id,
    }
    if indexed_values != contract_values:
        raise ValueError(f"governed context fact row {row.fact_version_id} does not match its typed payload")
    return fact


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


def _memory_pattern_observation_row_values(
    observation: MemoryPatternObservation,
    payload: dict,
) -> dict:
    return {
        "idempotency_key": observation.idempotency_key,
        "aggregation_key": observation.aggregation_key,
        "lineage_key": observation.lineage_key,
        "content_hash": observation.content_hash,
        "tenant_id": observation.tenant_id,
        "environment": observation.environment,
        "data_class": observation.data_class.value,
        "source_type": observation.source.source_type.value,
        "source_id": observation.source.source_id,
        "run_id": observation.source.run_id,
        "alert_id": observation.source.alert_id,
        "pattern_dimension": observation.signature.dimension.value,
        "pattern_value": observation.signature.value,
        "mocked": observation.mocked,
        "observed_at": observation.source.observed_at,
        "window_start": observation.window_start,
        "window_end": observation.window_end,
        "created_at": observation.created_at,
        "observation_payload": payload,
    }


def _skill_feedback_observation_row_values(
    observation: SkillFeedbackObservation,
    payload: dict,
) -> dict:
    return {
        "idempotency_key": observation.idempotency_key,
        "aggregation_key": observation.aggregation_key,
        "content_hash": observation.content_hash,
        "tenant_id": observation.tenant_id,
        "data_class": observation.data_class.value,
        "source_type": observation.source.source_type.value,
        "source_id": observation.source.source_id,
        "skill_name": observation.target_skill.skill_name,
        "package_hash": observation.target_skill.package_hash,
        "scenario_key": observation.scenario_key,
        "failure_facet": observation.failure_facet.value,
        "mocked": observation.mocked,
        "observed_at": observation.source.observed_at,
        "created_at": observation.created_at,
        "observation_payload": payload,
    }


def _skill_improvement_candidate_row_values(
    candidate: SkillImprovementCandidate,
    payload: dict,
) -> dict:
    return {
        "aggregation_key": candidate.aggregation_key,
        "aggregation_policy_version": candidate.aggregation_policy_version,
        "candidate_content_hash": candidate.candidate_content_hash,
        "version": candidate.version,
        "status": candidate.status.value,
        "tenant_id": candidate.tenant_id,
        "data_class": candidate.data_class.value,
        "skill_name": candidate.target_skill.skill_name,
        "package_hash": candidate.target_skill.package_hash,
        "scenario_key": candidate.scenario_key,
        "failure_facet": candidate.failure_facet.value,
        "occurrence_count": candidate.occurrence_count,
        "mocked": candidate.mocked,
        "reviewed_by_actor_id": (candidate.reviewed_by.actor_id if candidate.reviewed_by is not None else None),
        "reviewed_at": candidate.reviewed_at,
        "created_at": candidate.created_at,
        "updated_at": candidate.updated_at,
        "candidate_payload": payload,
    }
