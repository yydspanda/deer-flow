"""Deterministic alert-result presentation and human-task admission policy."""

from __future__ import annotations

from soc_agent.contracts import (
    AlertSummary,
    AnalysisRunStatus,
    DecisionReviewReason,
    ReviewQueueItem,
    SocAlertAttentionLevel,
    SocAlertResult,
    SocDecisionUsability,
    Verdict,
)

SOC_ALERT_ATTENTION_POLICY_VERSION = "soc.alert_attention.v1"

# These reasons indicate a contradictory current fact that the Runtime could
# not resolve. Model uncertainty, evidence gaps, and provider failures remain
# visible on the alert result but do not manufacture analyst work.
_REQUIRED_HUMAN_INTERVENTION_REASONS = frozenset({DecisionReviewReason.FACT_CONFLICT})

_DEGRADED_DECISION_REASONS = frozenset(
    {
        DecisionReviewReason.UNCERTAIN_VERDICT,
        DecisionReviewReason.HIGH_VALUE_EVIDENCE_GAP,
        DecisionReviewReason.FACT_CONFLICT,
        DecisionReviewReason.UNGROUNDED_ANALYSIS_EVIDENCE,
        DecisionReviewReason.UNGROUNDED_ANALYSIS_REASONING,
        DecisionReviewReason.UNPROVEN_OUTCOME_CLAIM,
        DecisionReviewReason.ROLE_VERIFICATION_CHALLENGED,
        DecisionReviewReason.ROLE_VERIFICATION_UNRESOLVED,
        DecisionReviewReason.ANALYSIS_OUTPUT_DEGRADED,
    }
)


def classify_alert_result(
    summary: AlertSummary,
    *,
    queue_item: ReviewQueueItem | None = None,
) -> SocAlertResult:
    """Separate result quality from the optional human-task lifecycle."""

    reasons = list(summary.review_reasons)
    required_reasons = [reason for reason in reasons if reason in _REQUIRED_HUMAN_INTERVENTION_REASONS]
    usability = _decision_usability(summary)
    if required_reasons:
        attention_level = SocAlertAttentionLevel.REQUIRED
    elif summary.needs_review or usability is not SocDecisionUsability.USABLE:
        attention_level = SocAlertAttentionLevel.ADVISORY
    else:
        attention_level = SocAlertAttentionLevel.NONE
    return SocAlertResult(
        summary=summary,
        attention_level=attention_level,
        attention_reasons=required_reasons or reasons,
        decision_usability=usability,
        requires_human_intervention=bool(required_reasons),
        queue_item=queue_item,
    )


def required_human_intervention_reason(
    summary: AlertSummary,
) -> DecisionReviewReason | None:
    """Return the stable reason that admits an alert into ReviewQueue."""

    return next(
        (reason for reason in summary.review_reasons if reason in _REQUIRED_HUMAN_INTERVENTION_REASONS),
        None,
    )


def is_required_human_intervention_item(item: ReviewQueueItem) -> bool:
    """Return whether a persisted queue item belongs in the intervention inbox."""

    reasons = {item.reason, *item.review_reasons}
    return bool(reasons.intersection(_REQUIRED_HUMAN_INTERVENTION_REASONS))


def _decision_usability(summary: AlertSummary) -> SocDecisionUsability:
    if summary.status is AnalysisRunStatus.FAILED or summary.verdict is None:
        return SocDecisionUsability.FAILED
    if summary.verdict in {Verdict.UNKNOWN, Verdict.NEEDS_REVIEW}:
        return SocDecisionUsability.DEGRADED
    if any(reason in _DEGRADED_DECISION_REASONS for reason in summary.review_reasons):
        return SocDecisionUsability.DEGRADED
    return SocDecisionUsability.USABLE


__all__ = [
    "SOC_ALERT_ATTENTION_POLICY_VERSION",
    "classify_alert_result",
    "is_required_human_intervention_item",
    "required_human_intervention_reason",
]
