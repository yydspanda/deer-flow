from __future__ import annotations

from soc_agent.contracts import (
    AdjudicatedRole,
    AdjudicatedRoleStatus,
    AdjudicatedRoleType,
    AnalysisCapability,
    AnalysisEvidenceGroundingStatus,
    AnalysisMaterialityImpact,
    AnalysisOutputQuality,
    AnalysisOutputQualityStatus,
    AnalysisOutputSection,
    AnalysisReasoningBasis,
    AnalysisReasoningItem,
    AnalysisResult,
    ConflictReport,
    DecisionReviewReason,
    EvidenceItem,
    RoleAdjudicationResult,
    RoleAdjudicationStatus,
    RoleCoherenceAssessment,
    RoleCoherenceRelationship,
    RoleCoherenceRelationshipStatus,
    RoleCoherenceStatus,
    Verdict,
)
from soc_agent.core.decision_policy import SocDecisionPolicy
from soc_agent.core.runtime import build_analysis_request_for_payload
from soc_agent.pipeline.evidence_grounding import ground_analysis_evidence
from soc_agent.pipeline.materiality import assess_analysis_materiality


def _request():
    return build_analysis_request_for_payload(
        {
            "schema_version": "soc.alert.v1",
            "tenant_id": "tenant-a",
            "alert_id": "ALT-MATERIALITY-1",
            "source": {
                "source_type": "nids",
                "source_system": "test",
            },
            "detection": {
                "detection_key": "DET-MATERIALITY-1",
                "rule_name": "Reverse connection",
            },
            "classification": {
                "severity": "high",
                "category": "network",
            },
            "entities": {
                "network": {
                    "source_ip": "30.116.114.150",
                    "destination_ip": "30.174.29.44",
                }
            },
            "evidence": [],
            "raw": {},
        }
    )


def _analysis(request) -> AnalysisResult:  # noqa: ANN001
    fact = request.evidence_catalog[0]
    return AnalysisResult(
        verdict=Verdict.TRUE_POSITIVE,
        confidence=0.91,
        summary="当前规则命中和网络事实支持真实风险结论。",
        evidence=[
            EvidenceItem(
                evidence_ref=fact.evidence_ref,
                source=fact.source_path,
                description="Runtime current-alert fact",
                value=fact.value,
            )
        ],
        reasoning=[
            AnalysisReasoningItem(
                reasoning_id="R-00",
                statement="当前规则命中和网络事实支持真实风险结论。",
                basis=[AnalysisReasoningBasis.CURRENT_EVIDENCE],
                evidence_refs=[fact.evidence_ref],
                confidence=0.91,
            )
        ],
        decision_evidence_refs=[fact.evidence_ref],
        decision_reasoning_refs=["R-00"],
        reason="当前告警证据足以形成结论。",
        recommended_action="continue governed investigation",
    )


def _guard(report, capability: AnalysisCapability):  # noqa: ANN001, ANN202
    return next(item for item in report.capability_guards if item.capability is capability)


def test_optional_role_failure_keeps_verdict_and_blocks_only_role_targets() -> None:
    request = _request()
    analysis = _analysis(request)
    grounding = ground_analysis_evidence(analysis, request)
    quality = AnalysisOutputQuality(
        status=AnalysisOutputQualityStatus.DEGRADED,
        accepted_sections=[section for section in AnalysisOutputSection if section is not AnalysisOutputSection.ROLE_ADJUDICATION],
        degraded_sections=[AnalysisOutputSection.ROLE_ADJUDICATION],
    )

    report = assess_analysis_materiality(
        analysis,
        request=request,
        grounding=grounding,
        output_quality=quality,
    )
    decision = SocDecisionPolicy().decide(
        analysis,
        request=request,
        grounding=grounding,
        analyzer_step_name="analyze_llm",
        output_quality=quality,
        materiality=report,
    )

    assert report.decision_usable is True
    assert report.review_required is False
    assert _guard(report, AnalysisCapability.RESPONSE_ACTION).allowed is True
    assert _guard(report, AnalysisCapability.ATTACKER_TARGETING).allowed is False
    assert _guard(report, AnalysisCapability.VICTIM_TARGETING).allowed is False
    assert decision.verdict is Verdict.TRUE_POSITIVE
    assert decision.needs_review is False


def test_partially_recovered_role_section_preserves_exact_surviving_capability() -> None:
    request = _request()
    analysis = _analysis(request)
    fact = analysis.evidence[0]
    analysis = analysis.model_copy(
        update={
            "role_adjudication": RoleAdjudicationResult(
                status=RoleAdjudicationStatus.RESOLVED_FROM_EVIDENCE,
                roles=[
                    AdjudicatedRole(
                        role=AdjudicatedRoleType.VICTIM,
                        entity_type="ip",
                        value=str(fact.value),
                        status=AdjudicatedRoleStatus.RESOLVED_FROM_EVIDENCE,
                        confidence=0.9,
                        evidence_refs=[fact.evidence_ref],
                        reasoning_refs=["R-00"],
                        rationale="当前受害资产由精确事实引用支持。",
                    )
                ],
                rationale="另一个无效角色项已被局部隔离。",
            )
        }
    )
    quality = AnalysisOutputQuality(
        status=AnalysisOutputQualityStatus.DEGRADED,
        accepted_sections=[section for section in AnalysisOutputSection if section is not AnalysisOutputSection.ROLE_ADJUDICATION],
        degraded_sections=[AnalysisOutputSection.ROLE_ADJUDICATION],
    )

    report = assess_analysis_materiality(
        analysis,
        request=request,
        grounding=ground_analysis_evidence(analysis, request),
        output_quality=quality,
    )

    assert report.review_required is False
    assert _guard(report, AnalysisCapability.VICTIM_TARGETING).allowed is True
    assert _guard(report, AnalysisCapability.ATTACKER_TARGETING).allowed is False


def test_invalid_core_reference_requires_review_without_rejudging_semantics() -> None:
    request = _request()
    analysis = _analysis(request)
    analysis = analysis.model_copy(update={"evidence": [analysis.evidence[0].model_copy(update={"value": "changed-value"})]})
    grounding = ground_analysis_evidence(analysis, request)

    report = assess_analysis_materiality(
        analysis,
        request=request,
        grounding=grounding,
        output_quality=AnalysisOutputQuality(),
    )

    assert grounding.items[0].status is AnalysisEvidenceGroundingStatus.VALUE_NOT_FOUND
    assert report.decision_usable is False
    assert report.review_required is True
    assert DecisionReviewReason.UNGROUNDED_ANALYSIS_EVIDENCE in report.review_reasons


def test_warning_conflict_blocks_dependent_action_without_forcing_review() -> None:
    request = _request()
    request = request.model_copy(
        update={
            "fact_reconstruction": request.fact_reconstruction.model_copy(
                update={
                    "conflict_reports": [
                        ConflictReport(
                            conflict_type="attacker_candidate_conflict",
                            severity="warning",
                            description="Two attacker candidates remain.",
                            involved_fields=["attacker"],
                            candidate_values={"attacker": ["30.174.29.44", "30.174.29.45"]},
                            blocks_automation=True,
                        )
                    ]
                }
            )
        }
    )
    analysis = _analysis(request)
    grounding = ground_analysis_evidence(analysis, request)

    report = assess_analysis_materiality(
        analysis,
        request=request,
        grounding=grounding,
        output_quality=AnalysisOutputQuality(),
    )

    assert report.review_required is False
    assert report.conflict_dispositions[0].impact is AnalysisMaterialityImpact.ACTION_ONLY
    assert _guard(report, AnalysisCapability.ATTACKER_TARGETING).allowed is False


def test_source_conflict_blocks_only_source_target_and_direction() -> None:
    request = _request()
    request = request.model_copy(
        update={
            "fact_reconstruction": request.fact_reconstruction.model_copy(
                update={
                    "conflict_reports": [
                        ConflictReport(
                            conflict_type="source_candidate_conflict",
                            severity="warning",
                            description="Two source candidates remain.",
                            involved_fields=["source"],
                            candidate_values={"source": ["30.116.114.150", "30.116.114.151"]},
                            blocks_automation=True,
                        )
                    ]
                }
            )
        }
    )
    analysis = _analysis(request)

    report = assess_analysis_materiality(
        analysis,
        request=request,
        grounding=ground_analysis_evidence(analysis, request),
        output_quality=AnalysisOutputQuality(),
    )

    assert report.review_required is False
    assert _guard(report, AnalysisCapability.SOURCE_TARGETING).allowed is False
    assert _guard(report, AnalysisCapability.NETWORK_DIRECTION).allowed is False
    assert _guard(report, AnalysisCapability.DESTINATION_TARGETING).allowed is True
    assert _guard(report, AnalysisCapability.RESPONSE_ACTION).allowed is True


def test_critical_current_fact_conflict_still_requires_review() -> None:
    request = _request()
    request = request.model_copy(
        update={
            "fact_reconstruction": request.fact_reconstruction.model_copy(
                update={
                    "conflict_reports": [
                        ConflictReport(
                            conflict_type="victim_identity_conflict",
                            severity="critical",
                            description="Two incompatible victim identities remain.",
                            involved_fields=["victim"],
                            candidate_values={"victim": ["30.116.114.150", "30.116.114.151"]},
                            blocks_automation=True,
                        )
                    ]
                }
            )
        }
    )
    analysis = _analysis(request)

    report = assess_analysis_materiality(
        analysis,
        request=request,
        grounding=ground_analysis_evidence(analysis, request),
        output_quality=AnalysisOutputQuality(),
    )

    assert report.review_required is True
    assert DecisionReviewReason.FACT_CONFLICT in report.review_reasons
    assert report.conflict_dispositions[0].impact is AnalysisMaterialityImpact.DECISION_REVIEW
    assert _guard(report, AnalysisCapability.RESPONSE_ACTION).allowed is False
    assert _guard(report, AnalysisCapability.VICTIM_TARGETING).allowed is False


def test_coherent_reverse_connection_resolves_apparent_tuple_role_conflict() -> None:
    request = _request()
    coherence = RoleCoherenceAssessment(
        scenario_type="reverse_connection",
        status=RoleCoherenceStatus.COHERENT,
        relationships=[
            RoleCoherenceRelationship(
                semantic_role="victim",
                network_role="source",
                semantic_value="30.116.114.150",
                network_value="30.116.114.150",
                status=RoleCoherenceRelationshipStatus.ALIGNED,
            )
        ],
        rationale="Reverse connection mapping is internally coherent.",
    )
    request = request.model_copy(
        update={
            "fact_reconstruction": request.fact_reconstruction.model_copy(
                update={
                    "role_coherence": coherence,
                    "conflict_reports": [
                        ConflictReport(
                            conflict_type="reverse_connection_attacker_source_mismatch",
                            severity="critical",
                            description="Tuple and semantic roles differ by design.",
                            involved_fields=["source", "attacker"],
                            candidate_values={
                                "source": ["30.116.114.150"],
                                "attacker": ["30.174.29.44"],
                            },
                            blocks_automation=True,
                        )
                    ],
                }
            )
        }
    )
    analysis = _analysis(request)

    report = assess_analysis_materiality(
        analysis,
        request=request,
        grounding=ground_analysis_evidence(analysis, request),
        output_quality=AnalysisOutputQuality(),
    )

    assert report.review_required is False
    assert report.conflict_dispositions[0].status.value == "resolved"
    assert report.conflict_dispositions[0].impact is AnalysisMaterialityImpact.NONE
