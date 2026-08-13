"""Deterministic operational decision policy for bounded SOC analysis."""

from __future__ import annotations

from soc_agent.contracts import (
    AnalysisEvidenceGroundingReport,
    AnalysisOutputQuality,
    AnalysisOutputQualityStatus,
    AnalysisResult,
    Decision,
    DecisionConfidenceSource,
    DecisionEvidenceState,
    DecisionReviewReason,
    LLMAnalysisRequest,
    MessageSchemaStatus,
    RoleAdjudicationVerificationResult,
    RoleVerificationStatus,
    Verdict,
)
from soc_agent.core.validator import validate_decision

SOC_DECISION_POLICY_VERSION = "soc.decision_policy.v6"
_INFORMATIONAL_WARNING_PREFIXES = ("bounded evidence compacted one or more encoded spans;",)


class SocDecisionPolicy:
    """Turn analyzer output into a guarded, auditable operational decision.

    Analyzer confidence remains an uncalibrated diagnostic input. Review is
    driven by explicit uncertainty or structural evidence/output blockers,
    never by the model's raw score or verdict label alone.
    """

    def decide(
        self,
        analysis: AnalysisResult,
        *,
        request: LLMAnalysisRequest,
        grounding: AnalysisEvidenceGroundingReport,
        analyzer_step_name: str,
        output_quality: AnalysisOutputQuality | None = None,
        role_verification: RoleAdjudicationVerificationResult | None = None,
    ) -> Decision:
        confidence_source = _confidence_source(analyzer_step_name)
        review_reasons = _review_reasons(
            analysis,
            request=request,
            grounding=grounding,
            confidence_source=confidence_source,
            output_quality=output_quality,
            role_verification=role_verification,
        )
        decision = Decision(
            verdict=analysis.verdict,
            confidence=analysis.confidence,
            confidence_source=confidence_source,
            confidence_is_calibrated=False,
            calibrated_probability=None,
            calibration_profile_version=None,
            evidence_state=_evidence_state(
                request,
                grounding=grounding,
                output_quality=output_quality,
                role_verification=role_verification,
            ),
            suggested_action=analysis.recommended_action,
            needs_review=bool(review_reasons),
            review_reasons=review_reasons,
            reason=analysis.reason,
            policy_version=SOC_DECISION_POLICY_VERSION,
            automation_allowed=False,
        )
        return validate_decision(decision)


def _confidence_source(analyzer_step_name: str) -> DecisionConfidenceSource:
    if analyzer_step_name == "analyze_llm":
        return DecisionConfidenceSource.LLM_SELF_REPORT
    if analyzer_step_name == "analyze_stub":
        return DecisionConfidenceSource.STUB_HEURISTIC
    return DecisionConfidenceSource.UNKNOWN


def _review_reasons(
    analysis: AnalysisResult,
    *,
    request: LLMAnalysisRequest,
    grounding: AnalysisEvidenceGroundingReport,
    confidence_source: DecisionConfidenceSource,
    output_quality: AnalysisOutputQuality | None,
    role_verification: RoleAdjudicationVerificationResult | None,
) -> list[DecisionReviewReason]:
    reasons: list[DecisionReviewReason] = []
    if output_quality is not None and output_quality.status in {
        AnalysisOutputQualityStatus.DEGRADED,
        AnalysisOutputQualityStatus.DETERMINISTIC_FALLBACK,
    }:
        reasons.append(DecisionReviewReason.ANALYSIS_OUTPUT_DEGRADED)
    if analysis.verdict in {Verdict.UNKNOWN, Verdict.NEEDS_REVIEW}:
        reasons.append(DecisionReviewReason.UNCERTAIN_VERDICT)

    if request.fact_reconstruction.conflict_reports:
        reasons.append(DecisionReviewReason.FACT_CONFLICT)

    schema_statuses = {item.status for item in request.evidence_coverage.message_schemas}
    if MessageSchemaStatus.DEGRADED in schema_statuses:
        reasons.append(DecisionReviewReason.DEGRADED_MESSAGE_SCHEMA)
    if MessageSchemaStatus.UNSUPPORTED in schema_statuses:
        reasons.append(DecisionReviewReason.UNSUPPORTED_MESSAGE_SCHEMA)
    if request.evidence_coverage.high_value_gaps:
        reasons.append(DecisionReviewReason.HIGH_VALUE_EVIDENCE_GAP)
    if grounding.ungrounded_count:
        reasons.append(DecisionReviewReason.UNGROUNDED_ANALYSIS_EVIDENCE)
    if grounding.reasoning_ungrounded_count:
        reasons.append(DecisionReviewReason.UNGROUNDED_ANALYSIS_REASONING)
    if any("outcome-success claim" in warning for warning in grounding.warnings):
        reasons.append(DecisionReviewReason.UNPROVEN_OUTCOME_CLAIM)
    if role_verification is not None:
        if role_verification.status is RoleVerificationStatus.CHALLENGED:
            reasons.append(DecisionReviewReason.ROLE_VERIFICATION_CHALLENGED)
        elif role_verification.status is RoleVerificationStatus.UNRESOLVED:
            reasons.append(DecisionReviewReason.ROLE_VERIFICATION_UNRESOLVED)
        elif role_verification.status is RoleVerificationStatus.UNAVAILABLE:
            reasons.append(DecisionReviewReason.ROLE_VERIFIER_UNAVAILABLE)
    if confidence_source is DecisionConfidenceSource.STUB_HEURISTIC:
        reasons.append(DecisionReviewReason.STUB_ANALYZER)
    return list(dict.fromkeys(reasons))


def _evidence_state(
    request: LLMAnalysisRequest,
    *,
    grounding: AnalysisEvidenceGroundingReport,
    output_quality: AnalysisOutputQuality | None,
    role_verification: RoleAdjudicationVerificationResult | None,
) -> DecisionEvidenceState:
    if request.fact_reconstruction.conflict_reports:
        return DecisionEvidenceState.CONFLICTED
    if role_verification is not None and role_verification.status is RoleVerificationStatus.CHALLENGED:
        return DecisionEvidenceState.CONFLICTED

    schema_statuses = {item.status for item in request.evidence_coverage.message_schemas}
    if (
        (
            output_quality is not None
            and output_quality.status
            in {
                AnalysisOutputQualityStatus.DEGRADED,
                AnalysisOutputQualityStatus.DETERMINISTIC_FALLBACK,
            }
        )
        or MessageSchemaStatus.DEGRADED in schema_statuses
        or MessageSchemaStatus.UNSUPPORTED in schema_statuses
        or request.evidence_coverage.high_value_gaps
        or grounding.ungrounded_count
        or grounding.reasoning_ungrounded_count
        or (
            role_verification is not None
            and role_verification.status
            in {
                RoleVerificationStatus.UNRESOLVED,
                RoleVerificationStatus.UNAVAILABLE,
            }
        )
    ):
        return DecisionEvidenceState.DEGRADED

    if _has_partial_evidence(request):
        return DecisionEvidenceState.PARTIAL
    return DecisionEvidenceState.SUFFICIENT


def _has_partial_evidence(request: LLMAnalysisRequest) -> bool:
    coverage = request.evidence_coverage
    if coverage.omissions or coverage.llm_truncated_evidence_paths:
        return True
    if any(observation.warnings for observation in coverage.message_schemas):
        return True
    return any(not warning.startswith(_INFORMATIONAL_WARNING_PREFIXES) for warning in request.warnings)


__all__ = ["SOC_DECISION_POLICY_VERSION", "SocDecisionPolicy"]
