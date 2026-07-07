"""Correlation service for deterministic SOC alert similarity review."""

from __future__ import annotations

from soc_agent.contracts import (
    AlertSummary,
    CorrelationEvidenceRef,
    CorrelationMatch,
    CorrelationQuery,
    CorrelationResult,
    InvestigationEvidence,
    SimilarAlertQuery,
)
from soc_agent.protocols import AlertSummaryRepository, InvestigationEvidenceRepository

from .service import SocServiceNotFoundError, SocServiceNotImplementedError


class SocCorrelationService:
    """Find similar historical alerts and reusable investigation evidence."""

    def __init__(
        self,
        *,
        summary_repository: AlertSummaryRepository | None = None,
        evidence_repository: InvestigationEvidenceRepository | None = None,
    ) -> None:
        self._summary_repository = summary_repository
        self._evidence_repository = evidence_repository

    def correlate(self, query: CorrelationQuery) -> CorrelationResult:
        if self._summary_repository is None:
            raise SocServiceNotImplementedError("correlate requires an AlertSummaryRepository")

        subject = self._summary_repository.get_alert_summary(query.run_id)
        if subject is None:
            raise SocServiceNotFoundError(f"alert summary for run {query.run_id} not found")

        similar_matches = self._summary_repository.find_similar_alert_summaries(_similar_alert_query_from_correlation(query, subject))
        matches = [
            CorrelationMatch(
                summary=match.summary,
                score=match.score,
                matched_reasons=match.matched_reasons,
                reusable_evidence=self._reusable_evidence_for_match(match.summary, query=query),
            )
            for match in similar_matches
        ]
        return CorrelationResult(
            query=query,
            subject_summary=subject,
            matches=matches,
            reusable_evidence_count=sum(len(match.reusable_evidence) for match in matches),
        )

    def _reusable_evidence_for_match(
        self,
        summary: AlertSummary,
        *,
        query: CorrelationQuery,
    ) -> list[CorrelationEvidenceRef]:
        if self._evidence_repository is None or query.evidence_limit_per_match == 0:
            return []
        evidence = self._evidence_repository.list_evidence(
            run_id=summary.run_id,
            alert_id=summary.alert_id,
            limit=query.evidence_limit_per_match,
        )
        return [_evidence_ref(item) for item in evidence]


def _similar_alert_query_from_correlation(query: CorrelationQuery, summary: AlertSummary) -> SimilarAlertQuery:
    return SimilarAlertQuery(
        run_id=summary.run_id,
        detection_key=summary.detection_key,
        rule_code=summary.rule_code,
        source_type=summary.source_type,
        category=summary.category,
        entity_keys=summary.entity_keys,
        limit=query.limit,
        candidate_limit=query.candidate_limit,
    )


def _evidence_ref(evidence: InvestigationEvidence) -> CorrelationEvidenceRef:
    return CorrelationEvidenceRef(
        evidence_id=evidence.evidence_id,
        route=evidence.route,
        action=evidence.action,
        status=evidence.status,
        message=evidence.message,
        result_payload=evidence.result_payload,
        queue_id=evidence.queue_id,
        run_id=evidence.run_id,
        alert_id=evidence.alert_id,
        source_proposal_id=evidence.source_proposal_id,
        created_at=evidence.created_at,
    )
