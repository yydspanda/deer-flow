from __future__ import annotations

from soc_agent.contracts import (
    AnalysisEvidenceGroundingReport,
    AnalysisReasoningBasis,
    AnalysisReasoningItem,
    AnalysisResult,
    DecisionEvidenceState,
    DecisionReviewReason,
    EvidenceCoverageGap,
    EvidenceCoverageOmission,
    EvidenceCoverageReport,
    EvidenceItem,
    LLMAnalysisRequest,
    MessageSchemaObservation,
    MessageSchemaStatus,
    Verdict,
)
from soc_agent.core.decision_policy import SocDecisionPolicy


def _analysis() -> AnalysisResult:
    return AnalysisResult(
        verdict=Verdict.TRUE_POSITIVE,
        confidence=0.9,
        summary="Bounded test analysis.",
        evidence=[
            EvidenceItem(
                evidence_ref="E-000000000001",
                source="alert_id",
                description="Runtime alert identifier.",
                value="ALT-POLICY-001",
            )
        ],
        reasoning=[
            AnalysisReasoningItem(
                reasoning_id="R-01",
                statement="The current alert fact requires policy evaluation.",
                basis=[AnalysisReasoningBasis.CURRENT_EVIDENCE],
                evidence_refs=["E-000000000001"],
                confidence=0.9,
            )
        ],
        reason="Policy semantics test.",
        recommended_action="review",
    )


def _decide(
    request: LLMAnalysisRequest,
    *,
    analysis: AnalysisResult | None = None,
    analyzer_step_name: str = "analyze_llm",
):  # noqa: ANN202
    return SocDecisionPolicy().decide(
        analysis or _analysis(),
        request=request,
        grounding=AnalysisEvidenceGroundingReport(),
        analyzer_step_name=analyzer_step_name,
    )


def test_routine_bounded_omission_is_partial_not_degraded() -> None:
    request = LLMAnalysisRequest(
        alert_id="ALT-POLICY-001",
        evidence_coverage=EvidenceCoverageReport(
            llm_truncated_evidence_paths=["raw.message"],
            omissions=[
                EvidenceCoverageOmission(
                    field_path="raw.message#parsed.vendor_detail",
                    reason="bounded_projection_budget",
                )
            ],
            warnings=["bounded evidence omitted one or more fields; inspect coverage omissions for exact paths"],
        ),
        warnings=["bounded evidence omitted one or more fields; inspect coverage omissions for exact paths"],
    )

    decision = _decide(request)

    assert decision.evidence_state is DecisionEvidenceState.PARTIAL
    assert DecisionReviewReason.TRUNCATED_ANALYSIS_EVIDENCE not in decision.review_reasons
    assert decision.policy_version == "soc.decision_policy.v7"


def test_encoded_compaction_alone_is_informational() -> None:
    warning = "bounded evidence compacted one or more encoded spans; original values remain in raw input"
    request = LLMAnalysisRequest(
        alert_id="ALT-POLICY-001",
        evidence_coverage=EvidenceCoverageReport(
            llm_compacted_encoded_paths=["raw.message#parsed.payload"],
            warnings=[warning],
        ),
        warnings=[warning],
    )

    decision = _decide(request)

    assert decision.evidence_state is DecisionEvidenceState.SUFFICIENT


def test_nested_decode_warning_preserves_recognized_outer_schema() -> None:
    warning = "nested JSON repair rejected: payload.req_body"
    request = LLMAnalysisRequest(
        alert_id="ALT-POLICY-001",
        evidence_coverage=EvidenceCoverageReport(
            message_schemas=[
                MessageSchemaObservation(
                    source_path="raw.message",
                    status=MessageSchemaStatus.RECOGNIZED,
                    warnings=[warning],
                )
            ]
        ),
    )

    decision = _decide(request)

    assert decision.evidence_state is DecisionEvidenceState.PARTIAL
    assert DecisionReviewReason.DEGRADED_MESSAGE_SCHEMA not in decision.review_reasons


def test_high_value_gap_remains_degraded() -> None:
    request = LLMAnalysisRequest(
        alert_id="ALT-POLICY-001",
        evidence_coverage=EvidenceCoverageReport(
            high_value_gaps=[
                EvidenceCoverageGap(
                    field_path="raw.message#parsed.command_line",
                    expected_target="llm_analysis_request.evidence",
                    reason="critical field has no bounded projection or typed compensation",
                    rule_id="test.command-line-gap",
                    importance="critical",
                )
            ]
        ),
    )

    decision = _decide(request)

    assert decision.evidence_state is DecisionEvidenceState.DEGRADED
    assert DecisionReviewReason.HIGH_VALUE_EVIDENCE_GAP in decision.review_reasons


def test_verdict_label_and_uncalibrated_score_do_not_create_review_work() -> None:
    request = LLMAnalysisRequest(alert_id="ALT-POLICY-001")

    for verdict in (Verdict.TRUE_POSITIVE, Verdict.FALSE_POSITIVE, Verdict.SUSPICIOUS):
        decision = _decide(
            request,
            analysis=_analysis().model_copy(
                update={
                    "verdict": verdict,
                    "confidence": 0.2,
                }
            ),
        )

        assert decision.needs_review is False
        assert decision.review_reasons == []
        assert decision.confidence_is_calibrated is False
        assert decision.automation_allowed is False


def test_explicit_unknown_or_needs_review_verdict_enters_review() -> None:
    request = LLMAnalysisRequest(alert_id="ALT-POLICY-001")

    for verdict in (Verdict.UNKNOWN, Verdict.NEEDS_REVIEW):
        decision = _decide(
            request,
            analysis=_analysis().model_copy(update={"verdict": verdict}),
        )

        assert decision.needs_review is True
        assert decision.review_reasons == [DecisionReviewReason.UNCERTAIN_VERDICT]


def test_stub_analyzer_remains_an_explicit_review_blocker() -> None:
    decision = _decide(
        LLMAnalysisRequest(alert_id="ALT-POLICY-001"),
        analyzer_step_name="analyze_stub",
    )

    assert decision.needs_review is True
    assert decision.review_reasons == [DecisionReviewReason.STUB_ANALYZER]
