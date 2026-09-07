from __future__ import annotations

import pytest

from soc_agent.contracts import (
    ActorContext,
    AnalysisCapability,
    AnalysisCapabilityGuard,
    AnalysisMaterialityImpact,
    AnalysisMaterialityReport,
    AnalysisOutputSection,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunStatus,
    AnalysisSectionMateriality,
    Decision,
    DecisionEvidenceState,
    DecisionReviewReason,
    SocAutomationContributorKind,
    SocAutomationContributorRef,
    SocAutomationContributorRole,
    SocCaseClosureStatus,
    SocCaseDecisionChange,
    SocCaseEvidenceGapImpact,
    SocDecisionSnapshot,
    SocDecisionStageEvaluation,
    SocDecisionStageKind,
    SocDecisionStageStatus,
    SocDecisionTransitionKind,
    SocDecisionTransitionRecord,
    SocOperationalDisposition,
    Verdict,
)
from soc_agent.core.case_outcomes import project_soc_case_outcome


def _analysis(
    *,
    verdict: Verdict,
    evidence_gaps: list[str] | None = None,
    manual_checks: list[str] | None = None,
) -> AnalysisResult:
    return AnalysisResult.model_construct(
        verdict=verdict,
        confidence=0.82,
        summary="当前告警命中了反向连接行为。",
        evidence=[],
        reasoning=[],
        decision_evidence_refs=[],
        decision_reasoning_refs=[],
        scenario_assessments=[],
        evidence_gaps=evidence_gaps or [],
        manual_checks=manual_checks or [],
        reason="当前行为与检测规则及告警事实一致。",
        recommended_action="转交运营确认。",
        knowledge_candidates=[],
    )


def _decision(
    *,
    verdict: Verdict,
    needs_review: bool = False,
    evidence_state: DecisionEvidenceState = DecisionEvidenceState.SUFFICIENT,
) -> Decision:
    return Decision(
        verdict=verdict,
        confidence=0.82,
        evidence_state=evidence_state,
        suggested_action="转交运营确认。",
        needs_review=needs_review,
        review_reasons=([DecisionReviewReason.FACT_CONFLICT] if needs_review else []),
        reason="当前行为与检测规则及告警事实一致。",
    )


def _run(
    *,
    verdict: Verdict = Verdict.SUSPICIOUS,
    evidence_gaps: list[str] | None = None,
    manual_checks: list[str] | None = None,
    materiality: AnalysisMaterialityReport | None = None,
    needs_review: bool = False,
) -> AnalysisRun:
    return AnalysisRun(
        run_id="RUN-OUTCOME-1",
        alert_id="ALERT-OUTCOME-1",
        status=(AnalysisRunStatus.NEEDS_REVIEW if needs_review else AnalysisRunStatus.SUCCESS),
        analysis=_analysis(
            verdict=verdict,
            evidence_gaps=evidence_gaps,
            manual_checks=manual_checks,
        ),
        analysis_materiality=materiality,
        decision=_decision(verdict=verdict, needs_review=needs_review),
    )


def _snapshot(
    verdict: Verdict,
    *,
    needs_review: bool = False,
) -> SocDecisionSnapshot:
    return SocDecisionSnapshot(
        verdict=verdict,
        confidence=0.82,
        evidence_state=DecisionEvidenceState.SUFFICIENT,
        suggested_action="忽略并保留审计。",
        needs_review=needs_review,
        policy_version="test-policy.v1",
    )


def _transition(
    *,
    before: SocDecisionSnapshot,
    after: SocDecisionSnapshot,
    kind: SocDecisionTransitionKind,
    disposition: SocOperationalDisposition | None = None,
    memory_status: SocDecisionStageStatus = SocDecisionStageStatus.NO_INPUT,
    tenant_status: SocDecisionStageStatus = SocDecisionStageStatus.NO_INPUT,
) -> SocDecisionTransitionRecord:
    memory_contributors = (
        [
            SocAutomationContributorRef(
                kind=SocAutomationContributorKind.CONFIRMED_MEMORY,
                role=SocAutomationContributorRole.OVERRIDES,
                ref_id="M-AAAAAAAAAAAA",
                detail="已审核经验确认该行为是内部服务调用。",
            )
        ]
        if memory_status
        in {
            SocDecisionStageStatus.REINFORCED,
            SocDecisionStageStatus.OVERRIDDEN,
            SocDecisionStageStatus.CONFLICTED,
        }
        else []
    )
    memory_after = after if memory_contributors else before
    tenant_after = after
    tenant_contributors = (
        [
            SocAutomationContributorRef(
                kind=SocAutomationContributorKind.TENANT_POLICY,
                role=SocAutomationContributorRole.OVERRIDES,
                ref_id="TPD-TEST",
                detail="已授权演练按企业规则忽略。",
            )
        ]
        if tenant_status is SocDecisionStageStatus.APPLIED
        else []
    )
    return SocDecisionTransitionRecord(
        transition_key="a" * 64,
        run_id="RUN-OUTCOME-1",
        alert_id="ALERT-OUTCOME-1",
        before=before,
        after=after,
        effective_disposition=disposition,
        transition_kind=kind,
        stages=[
            SocDecisionStageEvaluation(
                stage=SocDecisionStageKind.BASE,
                status=SocDecisionStageStatus.OBSERVED,
                after=before,
                summary="模型初判。",
            ),
            SocDecisionStageEvaluation(
                stage=SocDecisionStageKind.MEMORY,
                status=memory_status,
                before=before,
                after=memory_after,
                contributors=memory_contributors,
                summary="已审核经验参与研判。",
            ),
            SocDecisionStageEvaluation(
                stage=SocDecisionStageKind.TENANT_POLICY,
                status=tenant_status,
                before=memory_after,
                after=tenant_after,
                disposition_after=(disposition if tenant_status is SocDecisionStageStatus.APPLIED else None),
                contributors=tenant_contributors,
                summary="企业规则映射运营处置。",
            ),
            SocDecisionStageEvaluation(
                stage=SocDecisionStageKind.EFFECTIVE,
                status=(SocDecisionStageStatus.CONFLICTED if kind is SocDecisionTransitionKind.CONFLICTED else SocDecisionStageStatus.APPLIED),
                before=tenant_after,
                after=after,
                disposition_before=(disposition if tenant_status is SocDecisionStageStatus.APPLIED else None),
                disposition_after=disposition,
                summary="最终受治理结果。",
            ),
        ],
        contributors=[*memory_contributors, *tenant_contributors],
        policy_id="test-policy",
        policy_version="test-policy.v1",
        policy_hash="b" * 64,
        created_by=ActorContext(),
    )


def test_optional_evidence_gap_does_not_erase_a_usable_verdict() -> None:
    run = _run(
        verdict=Verdict.FALSE_POSITIVE,
        evidence_gaps=["缺少 CMDB 资产负责人。"],
    )

    outcome = project_soc_case_outcome(run)

    assert outcome.security_verdict is Verdict.FALSE_POSITIVE
    assert outcome.closure_status is SocCaseClosureStatus.HANDLING_PENDING
    assert outcome.evidence_gap_impact is SocCaseEvidenceGapImpact.ADVISORY
    assert outcome.evidence_gaps == ["缺少 CMDB 资产负责人。"]
    assert outcome.decision_usable is True


def test_applied_ignore_with_optional_gap_is_closed_with_limitations() -> None:
    run = _run(
        verdict=Verdict.FALSE_POSITIVE,
        evidence_gaps=["缺少 CMDB 资产负责人。"],
    )
    snapshot = _snapshot(Verdict.FALSE_POSITIVE)
    transition = _transition(
        before=snapshot,
        after=snapshot,
        kind=SocDecisionTransitionKind.UNCHANGED,
        disposition=SocOperationalDisposition.IGNORED,
        tenant_status=SocDecisionStageStatus.APPLIED,
    )

    outcome = project_soc_case_outcome(run, decision_transition=transition)

    assert outcome.closure_status is SocCaseClosureStatus.CLOSED_WITH_LIMITATIONS
    assert outcome.operational_disposition is SocOperationalDisposition.IGNORED
    assert outcome.tenant_policy_applied is True


def test_material_decision_gap_blocks_closure_and_exposes_next_step() -> None:
    materiality = AnalysisMaterialityReport(
        core_usable=False,
        decision_usable=False,
        review_required=True,
        review_reasons=[DecisionReviewReason.ANALYSIS_OUTPUT_DEGRADED],
        sections=[
            AnalysisSectionMateriality(
                section=AnalysisOutputSection.CORE,
                accepted=False,
                impact=AnalysisMaterialityImpact.DECISION_REVIEW,
                reason_codes=["output_section_degraded"],
            )
        ],
    )
    run = _run(
        verdict=Verdict.SUSPICIOUS,
        evidence_gaps=["缺少能够确认命令是否执行成功的结果。"],
        manual_checks=["补查目标主机的命令执行记录。"],
        materiality=materiality,
    )

    outcome = project_soc_case_outcome(run)

    assert outcome.closure_status is SocCaseClosureStatus.FOLLOW_UP_REQUIRED
    assert outcome.evidence_gap_impact is SocCaseEvidenceGapImpact.DECISION_BLOCKING
    assert outcome.next_steps == ["补查目标主机的命令执行记录。"]
    assert outcome.decision_usable is False


def test_reviewed_memory_override_is_one_final_security_verdict() -> None:
    run = _run(verdict=Verdict.SUSPICIOUS)
    before = _snapshot(Verdict.SUSPICIOUS, needs_review=True)
    after = _snapshot(Verdict.FALSE_POSITIVE)
    transition = _transition(
        before=before,
        after=after,
        kind=SocDecisionTransitionKind.OVERRIDDEN,
        disposition=SocOperationalDisposition.IGNORED,
        memory_status=SocDecisionStageStatus.OVERRIDDEN,
    )

    outcome = project_soc_case_outcome(run, decision_transition=transition)

    assert outcome.security_verdict is Verdict.FALSE_POSITIVE
    assert outcome.base_verdict is Verdict.SUSPICIOUS
    assert outcome.decision_change is SocCaseDecisionChange.MEMORY_OVERRIDDEN
    assert outcome.memory_directive_applied is True
    assert outcome.closure_status is SocCaseClosureStatus.CLOSED
    assert "内部服务调用" in (outcome.decision_reason or "")
    assert "可疑" not in outcome.event_summary


def test_tenant_policy_changes_handling_without_rewriting_attack_truth() -> None:
    run = _run(verdict=Verdict.TRUE_POSITIVE)
    snapshot = _snapshot(Verdict.TRUE_POSITIVE)
    transition = _transition(
        before=snapshot,
        after=snapshot,
        kind=SocDecisionTransitionKind.REINFORCED,
        disposition=SocOperationalDisposition.IGNORED,
        tenant_status=SocDecisionStageStatus.APPLIED,
    )

    outcome = project_soc_case_outcome(run, decision_transition=transition)

    assert outcome.security_verdict is Verdict.TRUE_POSITIVE
    assert outcome.operational_disposition is SocOperationalDisposition.IGNORED
    assert outcome.tenant_policy_applied is True
    assert outcome.decision_change is SocCaseDecisionChange.UNCHANGED
    assert outcome.closure_status is SocCaseClosureStatus.CLOSED


def test_policy_only_handoff_is_not_an_evidence_gap() -> None:
    run = _run(
        verdict=Verdict.FALSE_POSITIVE,
        evidence_gaps=["缺少资产归属信息。"],
        manual_checks=["可补查资产负责人。"],
        materiality=AnalysisMaterialityReport(
            capability_guards=[
                AnalysisCapabilityGuard(
                    capability=AnalysisCapability.ATTACKER_TARGETING,
                    allowed=False,
                    reason_codes=["resolved_role_target_unavailable"],
                )
            ],
        ),
    )
    transition = _transition(
        before=_snapshot(Verdict.FALSE_POSITIVE),
        after=_snapshot(Verdict.FALSE_POSITIVE, needs_review=True),
        kind=SocDecisionTransitionKind.REINFORCED,
        disposition=SocOperationalDisposition.ESCALATED,
        tenant_status=SocDecisionStageStatus.APPLIED,
    )

    outcome = project_soc_case_outcome(run, decision_transition=transition)

    assert outcome.security_verdict is Verdict.FALSE_POSITIVE
    assert outcome.decision_usable is True
    assert outcome.closure_status is SocCaseClosureStatus.HANDLING_PENDING
    assert outcome.closure_reason_codes == ["tenant_policy_handoff_pending"]
    assert outcome.evidence_gap_impact is SocCaseEvidenceGapImpact.CAPABILITY_LIMITED
    assert outcome.evidence_gaps == ["缺少资产归属信息。"]
    assert outcome.handling_reason == "研判为误报；企业规则要求此类告警仍须转交复核。"
    assert outcome.next_steps == ["按企业规则转交复核，并记录接收方及处理结果。"]


@pytest.mark.parametrize("runtime_review,material_review", [(True, False), (False, True)])
def test_policy_handoff_preserves_independent_decision_review(runtime_review: bool, material_review: bool) -> None:
    run = _run(
        verdict=Verdict.FALSE_POSITIVE,
        needs_review=runtime_review,
        materiality=AnalysisMaterialityReport(
            decision_usable=not material_review,
            review_required=material_review,
            review_reasons=([DecisionReviewReason.FACT_CONFLICT] if material_review else []),
        ),
        evidence_gaps=["当前结论存在独立证据冲突。"],
    )
    transition = _transition(
        before=_snapshot(Verdict.FALSE_POSITIVE, needs_review=runtime_review),
        after=_snapshot(Verdict.FALSE_POSITIVE, needs_review=True),
        kind=SocDecisionTransitionKind.REINFORCED,
        disposition=SocOperationalDisposition.ESCALATED,
        tenant_status=SocDecisionStageStatus.APPLIED,
    )

    outcome = project_soc_case_outcome(run, decision_transition=transition)

    assert outcome.closure_status is SocCaseClosureStatus.FOLLOW_UP_REQUIRED
    assert outcome.evidence_gap_impact is SocCaseEvidenceGapImpact.DECISION_BLOCKING
    assert "tenant_policy_handoff_pending" not in outcome.closure_reason_codes


def test_unattributed_review_is_not_assumed_to_be_policy_handoff() -> None:
    run = _run(verdict=Verdict.FALSE_POSITIVE)
    transition = _transition(
        before=_snapshot(Verdict.FALSE_POSITIVE),
        after=_snapshot(Verdict.FALSE_POSITIVE, needs_review=True),
        kind=SocDecisionTransitionKind.REINFORCED,
        disposition=SocOperationalDisposition.ESCALATED,
    )

    outcome = project_soc_case_outcome(run, decision_transition=transition)

    assert outcome.closure_status is SocCaseClosureStatus.FOLLOW_UP_REQUIRED


def test_conflicting_governed_sources_never_present_a_closed_result() -> None:
    run = _run(verdict=Verdict.SUSPICIOUS)
    snapshot = _snapshot(Verdict.SUSPICIOUS, needs_review=True)
    transition = _transition(
        before=snapshot,
        after=snapshot,
        kind=SocDecisionTransitionKind.CONFLICTED,
        disposition=SocOperationalDisposition.IGNORED,
        memory_status=SocDecisionStageStatus.CONFLICTED,
    )

    outcome = project_soc_case_outcome(run, decision_transition=transition)

    assert outcome.closure_status is SocCaseClosureStatus.FOLLOW_UP_REQUIRED
    assert outcome.decision_change is SocCaseDecisionChange.CONFLICTED
    assert outcome.operational_disposition is None
    assert outcome.next_steps


def test_blocked_targeting_is_a_capability_limit_not_a_second_verdict() -> None:
    materiality = AnalysisMaterialityReport(
        capability_guards=[
            AnalysisCapabilityGuard(
                capability=AnalysisCapability.ATTACKER_TARGETING,
                allowed=False,
                reason_codes=["attacker_role_unavailable"],
            )
        ]
    )
    run = _run(
        verdict=Verdict.TRUE_POSITIVE,
        evidence_gaps=["攻击者角色尚未精确定位。"],
        materiality=materiality,
    )

    outcome = project_soc_case_outcome(run)

    assert outcome.security_verdict is Verdict.TRUE_POSITIVE
    assert outcome.evidence_gap_impact is SocCaseEvidenceGapImpact.CAPABILITY_LIMITED
    assert outcome.blocked_capabilities == [AnalysisCapability.ATTACKER_TARGETING]
