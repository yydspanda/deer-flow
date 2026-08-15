"""Deterministic operational decision policy for bounded SOC analysis."""

from __future__ import annotations

from soc_agent.contracts import (
    AnalysisEvidenceGroundingReport,
    AnalysisMaterialityReport,
    AnalysisOutputQuality,
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
from soc_agent.pipeline.materiality import assess_analysis_materiality

SOC_DECISION_POLICY_VERSION = "soc.decision_policy.v7"
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
        materiality: AnalysisMaterialityReport | None = None,
    ) -> Decision:
        materiality = materiality or assess_analysis_materiality(
            analysis,
            request=request,
            grounding=grounding,
            output_quality=output_quality,
            role_verification=role_verification,
        )
        confidence_source = _confidence_source(analyzer_step_name)
        review_reasons = _review_reasons(
            analysis,
            request=request,
            confidence_source=confidence_source,
            materiality=materiality,
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
                materiality=materiality,
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
    confidence_source: DecisionConfidenceSource,
    materiality: AnalysisMaterialityReport,
) -> list[DecisionReviewReason]:
    reasons: list[DecisionReviewReason] = list(materiality.review_reasons)
    if analysis.verdict in {Verdict.UNKNOWN, Verdict.NEEDS_REVIEW}:
        reasons.append(DecisionReviewReason.UNCERTAIN_VERDICT)

    if request.evidence_coverage.high_value_gaps:
        reasons.append(DecisionReviewReason.HIGH_VALUE_EVIDENCE_GAP)
    if confidence_source is DecisionConfidenceSource.STUB_HEURISTIC:
        reasons.append(DecisionReviewReason.STUB_ANALYZER)
    return list(dict.fromkeys(reasons))


def _evidence_state(
    request: LLMAnalysisRequest,
    *,
    grounding: AnalysisEvidenceGroundingReport,
    output_quality: AnalysisOutputQuality | None,
    role_verification: RoleAdjudicationVerificationResult | None,
    materiality: AnalysisMaterialityReport,
) -> DecisionEvidenceState:
    del grounding, output_quality
    if DecisionReviewReason.FACT_CONFLICT in materiality.review_reasons:
        return DecisionEvidenceState.CONFLICTED
    if role_verification is not None and role_verification.status is RoleVerificationStatus.CHALLENGED:
        return DecisionEvidenceState.CONFLICTED

    schema_statuses = {item.status for item in request.evidence_coverage.message_schemas}
    if not materiality.decision_usable or request.evidence_coverage.high_value_gaps:
        return DecisionEvidenceState.DEGRADED

    if _has_partial_evidence(request) or MessageSchemaStatus.DEGRADED in schema_statuses or MessageSchemaStatus.UNSUPPORTED in schema_statuses:
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
