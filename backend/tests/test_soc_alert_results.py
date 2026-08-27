from __future__ import annotations

from app.gateway.routers import soc_alerts
from soc_agent.contracts import (
    AlertSummary,
    AnalysisRun,
    AnalysisRunStatus,
    DecisionReviewReason,
    ReviewQueueItem,
    ReviewQueueStatus,
    SimilarAlertMatch,
    SimilarAlertQuery,
    SocAlertAttentionLevel,
    SocDecisionUsability,
    Verdict,
)
from soc_agent.core import SocReviewService
from soc_agent.core.alert_results import (
    classify_alert_result,
    is_required_human_intervention_item,
)


class InMemoryAlertResultRepository:
    def __init__(self) -> None:
        self.runs: dict[str, AnalysisRun] = {}
        self.summaries: dict[str, AlertSummary] = {}
        self.review_items: dict[str, ReviewQueueItem] = {}

    def get_run(self, run_id: str) -> AnalysisRun | None:
        return self.runs.get(run_id)

    def get_alert_summary(self, run_id: str) -> AlertSummary | None:
        return self.summaries.get(run_id)

    def list_alert_summaries(self, *, limit: int = 50) -> list[AlertSummary]:
        return list(self.summaries.values())[:limit]

    def get_open_review_item_by_run(self, run_id: str) -> ReviewQueueItem | None:
        return next(
            (item for item in self.review_items.values() if item.run_id == run_id and item.status is ReviewQueueStatus.OPEN),
            None,
        )

    def list_review_items(
        self,
        *,
        status: ReviewQueueStatus | None = ReviewQueueStatus.OPEN,
        limit: int = 50,
    ) -> list[ReviewQueueItem]:
        return [item for item in self.review_items.values() if status is None or item.status is status][:limit]

    def find_similar_alert_summaries(
        self,
        query: SimilarAlertQuery,
    ) -> list[SimilarAlertMatch]:
        return []


def _summary(
    *,
    run_id: str = "RUN-1",
    status: AnalysisRunStatus = AnalysisRunStatus.NEEDS_REVIEW,
    verdict: Verdict = Verdict.SUSPICIOUS,
    reasons: list[DecisionReviewReason] | None = None,
) -> AlertSummary:
    review_reasons = reasons or []
    return AlertSummary(
        run_id=run_id,
        alert_id=f"ALERT-{run_id}",
        status=status,
        verdict=verdict,
        needs_review=bool(review_reasons),
        review_reasons=review_reasons,
        summary="Current best-effort conclusion",
    )


def test_advisory_uncertainty_does_not_become_a_human_task() -> None:
    result = classify_alert_result(
        _summary(reasons=[DecisionReviewReason.UNCERTAIN_VERDICT]),
    )

    assert result.attention_level is SocAlertAttentionLevel.ADVISORY
    assert result.decision_usability is SocDecisionUsability.DEGRADED
    assert result.requires_human_intervention is False


def test_unresolved_critical_fact_conflict_requires_human_intervention() -> None:
    result = classify_alert_result(
        _summary(reasons=[DecisionReviewReason.FACT_CONFLICT]),
    )

    assert result.attention_level is SocAlertAttentionLevel.REQUIRED
    assert result.decision_usability is SocDecisionUsability.DEGRADED
    assert result.requires_human_intervention is True
    assert result.attention_reasons == [DecisionReviewReason.FACT_CONFLICT]


def test_human_intervention_queue_filter_excludes_legacy_advisory_items() -> None:
    repository = InMemoryAlertResultRepository()
    required = ReviewQueueItem(
        run_id="RUN-REQUIRED",
        alert_id="ALERT-REQUIRED",
        reason=DecisionReviewReason.FACT_CONFLICT.value,
        review_reasons=[DecisionReviewReason.FACT_CONFLICT],
    )
    advisory = ReviewQueueItem(
        run_id="RUN-ADVISORY",
        alert_id="ALERT-ADVISORY",
        reason=DecisionReviewReason.HIGH_VALUE_EVIDENCE_GAP.value,
        review_reasons=[DecisionReviewReason.HIGH_VALUE_EVIDENCE_GAP],
    )
    repository.review_items[required.queue_id] = required
    repository.review_items[advisory.queue_id] = advisory

    assert is_required_human_intervention_item(required) is True
    assert is_required_human_intervention_item(advisory) is False
    items = SocReviewService(
        review_queue_repository=repository,
    ).list_queue(human_intervention_only=True)

    assert items == [required]


def test_failed_runtime_is_an_operational_issue_not_an_analyst_task() -> None:
    result = classify_alert_result(
        _summary(
            status=AnalysisRunStatus.FAILED,
            verdict=Verdict.UNKNOWN,
            reasons=[DecisionReviewReason.ANALYSIS_FAILED],
        ),
    )

    assert result.attention_level is SocAlertAttentionLevel.ADVISORY
    assert result.decision_usability is SocDecisionUsability.FAILED
    assert result.requires_human_intervention is False


def test_review_service_lists_all_results_independently_of_review_queue() -> None:
    repository = InMemoryAlertResultRepository()
    summary = _summary(reasons=[DecisionReviewReason.STUB_ANALYZER])
    repository.summaries[summary.run_id] = summary

    results = SocReviewService(
        repository=repository,
        summary_repository=repository,
        review_queue_repository=repository,
    ).list_alert_results()

    assert len(results) == 1
    assert results[0].summary == summary
    assert results[0].queue_item is None
    assert results[0].attention_level is SocAlertAttentionLevel.ADVISORY


def test_review_service_opens_alert_context_by_run_without_queue_item() -> None:
    repository = InMemoryAlertResultRepository()
    summary = _summary(reasons=[DecisionReviewReason.STUB_ANALYZER])
    run = AnalysisRun(
        run_id=summary.run_id,
        alert_id=summary.alert_id,
        status=summary.status,
    )
    repository.summaries[summary.run_id] = summary
    repository.runs[run.run_id] = run

    context = SocReviewService(
        repository=repository,
        summary_repository=repository,
        review_queue_repository=repository,
    ).get_alert_investigation_context(run.run_id)

    assert context.run == run
    assert context.result.summary == summary
    assert context.result.queue_item is None
    assert context.result.requires_human_intervention is False


def test_alert_router_exposes_result_list_and_run_context() -> None:
    repository = InMemoryAlertResultRepository()
    summary = _summary(reasons=[DecisionReviewReason.STUB_ANALYZER])
    run = AnalysisRun(
        run_id=summary.run_id,
        alert_id=summary.alert_id,
        status=summary.status,
    )
    repository.summaries[summary.run_id] = summary
    repository.runs[run.run_id] = run
    service = SocReviewService(
        repository=repository,
        summary_repository=repository,
        review_queue_repository=repository,
    )

    response = soc_alerts.list_alert_results(
        service,
        attention_level=None,
        limit=50,
    )
    context = soc_alerts.get_alert_investigation_context(run.run_id, service)

    assert response.items[0].summary.run_id == run.run_id
    assert context.run.run_id == run.run_id
