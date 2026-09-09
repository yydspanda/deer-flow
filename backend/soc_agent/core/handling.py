"""Shared read-only handling classification, never an execution authorization."""

from collections.abc import Sequence
from typing import Literal

from soc_agent.contracts import (
    AnalysisMaterialityReport,
    DecisionReviewReason,
    SocDecisionStageKind,
    SocDecisionStageStatus,
    SocDecisionTransitionKind,
    SocDecisionTransitionRecord,
    SocExternalDispositionApplyStatus,
    SocExternalDispositionRecord,
    SocOperationalDisposition,
    Verdict,
)

_DECISION_BLOCKERS = frozenset(
    {
        DecisionReviewReason.FACT_CONFLICT,
        DecisionReviewReason.HIGH_VALUE_EVIDENCE_GAP,
        DecisionReviewReason.UNGROUNDED_ANALYSIS_EVIDENCE,
        DecisionReviewReason.UNGROUNDED_ANALYSIS_REASONING,
        DecisionReviewReason.ROLE_VERIFICATION_CHALLENGED,
        DecisionReviewReason.ANALYSIS_OUTPUT_DEGRADED,
        DecisionReviewReason.STUB_ANALYZER,
    }
)


def policy_requires_follow_up(transition: SocDecisionTransitionRecord | None) -> bool:
    """A policy-owned review is handling work, not a missing current fact."""
    if transition is None or transition.transition_kind is SocDecisionTransitionKind.CONFLICTED:
        return False
    stage = next((item for item in transition.stages if item.stage is SocDecisionStageKind.TENANT_POLICY), None)
    return bool(
        stage is not None
        and stage.status is SocDecisionStageStatus.APPLIED
        and concrete_disposition(stage.disposition_after) in {None, SocOperationalDisposition.ESCALATED}
        and concrete_disposition(transition.effective_disposition) == concrete_disposition(stage.disposition_after)
        and stage.before is not None
        and not stage.before.needs_review
        and stage.after.needs_review
        and stage.before.verdict is stage.after.verdict
        and stage.after == transition.after
    )


def handling_blockers(
    *,
    needs_review: bool,
    review_reasons: Sequence[DecisionReviewReason],
    transition: SocDecisionTransitionRecord | None = None,
    materiality: AnalysisMaterialityReport | None = None,
) -> list[str]:
    """Reuse decision-level reasons; prose gaps and action-only guards are not blockers."""
    reasons = set(review_reasons).intersection(_DECISION_BLOCKERS)
    if materiality is not None:
        reasons.update(materiality.review_reasons)
        if not materiality.decision_usable:
            reasons.add(DecisionReviewReason.ANALYSIS_OUTPUT_DEGRADED)
    result = sorted(reason.value for reason in reasons)
    if transition is not None and transition.transition_kind is SocDecisionTransitionKind.CONFLICTED:
        result.insert(0, "decision_source_conflict")
    effective_needs_review = transition.after.needs_review if transition is not None else needs_review
    governed_handling = bool(
        transition is not None
        and concrete_disposition(transition.effective_disposition) is not None
        and any(stage.stage is SocDecisionStageKind.EFFECTIVE and stage.selected_rule_id and stage.status is SocDecisionStageStatus.APPLIED for stage in transition.stages)
    )
    if not result and effective_needs_review and not review_reasons and not policy_requires_follow_up(transition) and not governed_handling:
        result.append("review_requirement_unattributed")
    return result


def concrete_disposition(disposition: SocOperationalDisposition | None) -> SocOperationalDisposition | None:
    return None if disposition is SocOperationalDisposition.UNKNOWN else disposition


def resolve_operational_disposition(
    *, run_id: str, alert_id: str, decision_transition: SocDecisionTransitionRecord | None, external_dispositions: Sequence[SocExternalDispositionRecord] = ()
) -> tuple[SocOperationalDisposition | None, SocExternalDispositionRecord | None]:
    """Use scoped feedback before the governed plan, without loading a full run."""
    if decision_transition is not None and decision_transition.transition_kind is SocDecisionTransitionKind.CONFLICTED:
        return None, None
    mapped = [
        item
        for item in external_dispositions
        if (item.target_run_id == run_id or (item.target_run_id is None and item.target_alert_id == alert_id))
        and item.apply_status is SocExternalDispositionApplyStatus.MAPPED
        and item.canonical_status is not SocOperationalDisposition.UNKNOWN
    ]
    latest = max(mapped, key=lambda item: (item.created_at, item.disposition_id), default=None)
    if latest is not None:
        return latest.canonical_status, latest
    return (concrete_disposition(decision_transition.effective_disposition) if decision_transition is not None else None), None


def project_operational_handling(
    *,
    verdict: Verdict | None,
    disposition: SocOperationalDisposition | None,
    blockers: Sequence[str] = (),
    policy_review_only: bool = False,
    failed: bool = False,
) -> tuple[Literal["ignore", "transfer", "undetermined"], str | None]:
    """A material blocker prevents ignore without rewriting detection truth."""
    if failed:
        return "undetermined", "runtime_result_unavailable"
    if blockers:
        return "transfer", "decision_review:" + blockers[0]
    if policy_review_only and disposition is None:
        return "transfer", "governed_review_required"
    if disposition in {
        SocOperationalDisposition.CLOSED_FALSE_POSITIVE,
        SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE,
        SocOperationalDisposition.SUPPRESSED,
        SocOperationalDisposition.IGNORED,
        SocOperationalDisposition.DUPLICATE,
    }:
        return "ignore", f"disposition:{disposition.value}"
    if disposition in {SocOperationalDisposition.CLOSED_TRUE_POSITIVE, SocOperationalDisposition.ESCALATED}:
        return "transfer", f"disposition:{disposition.value}"
    if verdict is Verdict.FALSE_POSITIVE:
        return "ignore", f"verdict:{verdict.value}"
    if verdict in {Verdict.TRUE_POSITIVE, Verdict.SUSPICIOUS}:
        return "transfer", f"verdict:{verdict.value}"
    return "undetermined", f"verdict:{verdict.value}" if verdict is not None else None
