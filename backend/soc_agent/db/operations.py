"""Exact SQL aggregates for the read-only SOC operations snapshot."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from soc_agent.contracts import (
    NormalizationBaselineStatus,
    NormalizationMaintenanceIssueStatus,
    NormalizationMaintenanceSeverity,
    ReviewQueueStatus,
    SocAgentApprovalRequestStatus,
    SocMemoryCandidateStatus,
    SocPersistedOperationsMetrics,
)
from soc_agent.db.models import (
    SocAnalysisRunRow,
    SocApprovalRequestRow,
    SocMemoryCandidateRow,
    SocNormalizationMaintenanceIssueRow,
    SocNormalizationSchemaBaselineRow,
    SocReviewQueueRow,
)
from soc_agent.protocols import SocOperationsRepositoryError


class SqlAlchemySocOperationsRepository:
    """Read exact, unpaginated aggregates without loading business payloads."""

    def __init__(self, session_factory: Callable[[], AbstractContextManager[Session]]) -> None:
        self._session_factory = session_factory

    def read_persisted_metrics(self) -> SocPersistedOperationsMetrics:
        try:
            with self._session_factory() as session:
                status_rows = session.execute(select(SocAnalysisRunRow.status, func.count(SocAnalysisRunRow.run_id)).group_by(SocAnalysisRunRow.status)).all()
                status_counts = {str(status): int(count) for status, count in status_rows}

                open_review_count, oldest_open_review = session.execute(
                    select(
                        func.count(SocReviewQueueRow.queue_id),
                        func.min(SocReviewQueueRow.created_at),
                    ).where(SocReviewQueueRow.status == ReviewQueueStatus.OPEN.value)
                ).one()
                pending_approval_count, oldest_pending_approval = session.execute(
                    select(
                        func.count(SocApprovalRequestRow.approval_request_id),
                        func.min(SocApprovalRequestRow.created_at),
                    ).where(SocApprovalRequestRow.status == SocAgentApprovalRequestStatus.PENDING.value)
                ).one()

                return SocPersistedOperationsMetrics(
                    analysis_run_count=sum(status_counts.values()),
                    analysis_run_status_counts=status_counts,
                    latest_analysis_started_at=session.scalar(select(func.max(SocAnalysisRunRow.started_at))),
                    latest_analysis_completed_at=session.scalar(select(func.max(SocAnalysisRunRow.ended_at))),
                    open_review_count=int(open_review_count),
                    oldest_open_review_created_at=oldest_open_review,
                    pending_approval_request_count=int(pending_approval_count),
                    oldest_pending_approval_created_at=oldest_pending_approval,
                    open_normalization_issue_count=_count_where(
                        session,
                        SocNormalizationMaintenanceIssueRow.issue_id,
                        SocNormalizationMaintenanceIssueRow.status == NormalizationMaintenanceIssueStatus.OPEN.value,
                    ),
                    critical_open_normalization_issue_count=_count_where(
                        session,
                        SocNormalizationMaintenanceIssueRow.issue_id,
                        SocNormalizationMaintenanceIssueRow.status == NormalizationMaintenanceIssueStatus.OPEN.value,
                        SocNormalizationMaintenanceIssueRow.severity == NormalizationMaintenanceSeverity.CRITICAL.value,
                    ),
                    active_normalization_baseline_count=_count_where(
                        session,
                        SocNormalizationSchemaBaselineRow.baseline_id,
                        SocNormalizationSchemaBaselineRow.status == NormalizationBaselineStatus.ACTIVE.value,
                    ),
                    pending_memory_candidate_count=_count_where(
                        session,
                        SocMemoryCandidateRow.candidate_id,
                        SocMemoryCandidateRow.status == SocMemoryCandidateStatus.PENDING_REVIEW.value,
                    ),
                )
        except SQLAlchemyError as exc:
            raise SocOperationsRepositoryError("SOC operations database query failed") from exc


def _count_where(session: Session, column, *criteria) -> int:
    value = session.scalar(select(func.count(column)).where(*criteria))
    return int(value or 0)


__all__ = ["SqlAlchemySocOperationsRepository"]
