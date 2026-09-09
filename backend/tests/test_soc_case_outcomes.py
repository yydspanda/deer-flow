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


@pytest.mark.parametrize(
    ("reason", "code", "label"),
    [
        (DecisionReviewReason.FACT_CONFLICT, "current_fact_conflict", "待确认事实冲突"),
        (DecisionReviewReason.HIGH_VALUE_EVIDENCE_GAP, "critical_input_missing", "待补齐关键输入"),
        (DecisionReviewReason.ANALYSIS_OUTPUT_DEGRADED, "analysis_validation_failed", "待处理结果校验问题"),
        (DecisionReviewReason.UNGROUNDED_ANALYSIS_REASONING, "analysis_validation_failed", "待处理结果校验问题"),
        (DecisionReviewReason.ROLE_VERIFICATION_CHALLENGED, "role_verification_challenged", "待确认角色分歧"),
    ],
)
def test_follow_up_explains_the_actual_problem_not_a_generic_fact_gap(reason, code, label):
    run = _run(needs_review=True)
    run.decision.review_reasons = [reason]
    before = run.model_dump_json()
    outcome = project_soc_case_outcome(run)
    assert outcome.closure_status is SocCaseClosureStatus.FOLLOW_UP_REQUIRED
    assert code in outcome.closure_reason_codes
    assert outcome.progress_label == label
    assert outcome.progress_detail
    assert run.model_dump_json() == before


def test_unknown_and_unattributed_review_are_not_invented_fact_conflicts():
    outcome = project_soc_case_outcome(_run(verdict=Verdict.UNKNOWN))
    assert outcome.progress_label == "尚未形成明确判断"
    run = _run(needs_review=True)
    run.decision.review_reasons = []
    outcome = project_soc_case_outcome(run)
    assert outcome.progress_label == "待确认复核原因"
    assert "review_requirement_unattributed" in outcome.closure_reason_codes


def test_handling_has_one_reason_and_failed_runs_do_not_suggest_a_business_action():
    run = _run()
    outcome = project_soc_case_outcome(run)
    assert outcome.recommended_handling == "transfer"
    assert outcome.handling_reason == outcome.decision_reason
    failed = run.model_copy(update={"status": AnalysisRunStatus.FAILED})
    outcome = project_soc_case_outcome(failed)
    assert outcome.recommended_handling == "undetermined"


def _execution(status, *, run_id="RUN-OUTCOME-1"):
    from soc_agent.contracts import SocActionExecutionRecord

    return SocActionExecutionRecord(
        execution_key="c" * 64,
        authorization_id="AUTH-TEST",
        run_id=run_id,
        alert_id="ALERT-OUTCOME-1",
        route="response",
        action="ip.block",
        adapter_id="test",
        status=status,
        idempotency_key="test-action",
        executed_by=ActorContext(),
        result_payload={"mocked": True},
    )


@pytest.mark.parametrize(
    ("status", "code", "label"),
    [
        ("pending", "action_execution_pending", "动作等待执行结果"),
        ("failed_retryable", "action_execution_failed", "动作执行失败"),
        ("failed_terminal", "action_execution_failed", "动作执行失败"),
        ("skipped", "action_execution_skipped", "动作未执行"),
        ("succeeded", "action_result_recorded", "动作已有结果，待确认处置"),
    ],
)
def test_action_execution_is_not_case_closure(status, code, label):
    outcome = project_soc_case_outcome(_run(), action_executions=[_execution(status)])
    assert outcome.closure_status is SocCaseClosureStatus.HANDLING_PENDING
    assert outcome.closure_reason_codes == [code]
    assert outcome.progress_label == label


def test_unrelated_run_execution_cannot_affect_progress():
    outcome = project_soc_case_outcome(_run(), action_executions=[_execution("failed_terminal", run_id="OTHER")])
    assert outcome.closure_reason_codes == ["handling_not_applied"]


@pytest.mark.parametrize("has_gap", [False, True])
def test_mapped_final_feedback_is_completion_evidence(has_gap):
    from soc_agent.contracts import SocExternalDispositionApplyStatus, SocExternalDispositionRecord

    run = _run(evidence_gaps=["可补充资产负责人。"] if has_gap else [])
    feedback = SocExternalDispositionRecord.model_construct(
        canonical_status=SocOperationalDisposition.IGNORED,
        apply_status=SocExternalDispositionApplyStatus.MAPPED,
        target_run_id=run.run_id,
        target_alert_id=run.alert_id,
        apply_reason="运营已完成处置。",
    )
    outcome = project_soc_case_outcome(run, external_dispositions=[feedback])
    assert outcome.closure_status is (SocCaseClosureStatus.CLOSED_WITH_LIMITATIONS if has_gap else SocCaseClosureStatus.CLOSED)
    assert "处置已确认" in outcome.progress_label
    assert "已记录最终处置反馈" in outcome.progress_detail
    other = feedback.model_copy(update={"target_run_id": "OTHER"})
    assert project_soc_case_outcome(run, external_dispositions=[other]).closure_status is SocCaseClosureStatus.HANDLING_PENDING


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
    assert outcome.recommended_handling == "ignore"


@pytest.mark.parametrize("disposition", [None, SocOperationalDisposition.IGNORED])
def test_unresolved_decision_conflict_transfers_instead_of_ignoring(disposition):
    run = _run(verdict=Verdict.FALSE_POSITIVE, needs_review=True, manual_checks=["确认本次行为是否超出已审核范围。"])
    snapshot = _snapshot(Verdict.FALSE_POSITIVE, needs_review=True)
    transition = _transition(before=snapshot, after=snapshot, kind=SocDecisionTransitionKind.UNCHANGED, disposition=disposition)
    before = run.model_dump_json(), transition.model_dump_json()
    outcome = project_soc_case_outcome(run, decision_transition=transition)
    assert outcome.recommended_handling == "transfer"
    assert outcome.recommended_handling_basis == "decision_review:fact_conflict"
    assert "事实冲突" in outcome.handling_reason
    assert outcome.handling_recommendation == "确认本次行为是否超出已审核范围。"
    assert outcome.security_verdict is Verdict.FALSE_POSITIVE
    assert outcome.operational_disposition is None
    from soc_agent.integrations.pingan.legacy_compat.result_mapper import PingAnLegacyResultMapper

    callback = PingAnLegacyResultMapper().project(run, decision_transitions=[transition], action_executions=[])
    assert callback["alert_action"] == "转交"
    assert callback["disposal_action"] == ""
    assert callback["soc_lineage"]["recorded_disposition"] == (disposition.value if disposition else None)
    assert (run.model_dump_json(), transition.model_dump_json()) == before


@pytest.mark.parametrize("reason", [DecisionReviewReason.ROLE_VERIFICATION_UNRESOLVED, DecisionReviewReason.TRUNCATED_ANALYSIS_EVIDENCE, DecisionReviewReason.CONFIDENCE_NOT_CALIBRATED])
def test_advisory_legacy_review_flag_does_not_force_transfer(reason):
    run = _run(verdict=Verdict.FALSE_POSITIVE, needs_review=True, evidence_gaps=["可补查资产负责人。"])
    run.decision.review_reasons = [reason]
    outcome = project_soc_case_outcome(run)
    assert outcome.recommended_handling == "ignore"
    assert outcome.evidence_gap_impact is SocCaseEvidenceGapImpact.ADVISORY
    assert outcome.closure_status is SocCaseClosureStatus.HANDLING_PENDING


def test_memory_clear_cannot_bypass_independent_material_decision_conflict():
    run = _run(verdict=Verdict.FALSE_POSITIVE, needs_review=True)
    run.analysis_materiality = AnalysisMaterialityReport(review_required=True, review_reasons=[DecisionReviewReason.FACT_CONFLICT])
    transition = _transition(
        before=_snapshot(Verdict.FALSE_POSITIVE, needs_review=True),
        after=_snapshot(Verdict.FALSE_POSITIVE),
        kind=SocDecisionTransitionKind.REINFORCED,
        disposition=SocOperationalDisposition.IGNORED,
        memory_status=SocDecisionStageStatus.REINFORCED,
    )
    outcome = project_soc_case_outcome(run, decision_transition=transition)
    assert outcome.recommended_handling == "transfer"
    assert outcome.security_verdict is Verdict.FALSE_POSITIVE
    assert outcome.evidence_gap_impact is SocCaseEvidenceGapImpact.DECISION_BLOCKING


def test_unused_target_limit_does_not_make_benign_case_incomplete() -> None:
    run = _run(verdict=Verdict.FALSE_POSITIVE, evidence_gaps=["可补充资产负责人。"])
    run.analysis_materiality = AnalysisMaterialityReport(
        core_usable=True,
        decision_usable=True,
        review_required=False,
        capability_guards=[AnalysisCapabilityGuard(capability=AnalysisCapability.ATTACKER_TARGETING, allowed=False, reason_codes=["resolved_role_target_unavailable"])],
    )
    outcome = project_soc_case_outcome(run)
    assert outcome.evidence_gap_impact is SocCaseEvidenceGapImpact.ADVISORY
    assert outcome.blocked_capabilities == [AnalysisCapability.ATTACKER_TARGETING]
    assert outcome.action_limits_relevant is False


def test_memory_explanation_and_future_triggers_are_not_current_gaps() -> None:
    from soc_agent.contracts.schemas import AnalysisConclusionSupport

    run = _run(verdict=Verdict.FALSE_POSITIVE)
    run.analysis.conclusion_support = AnalysisConclusionSupport(
        context_refs=["M-AAAAAAAAAAAA"],
        resolved_questions=["相同业务模式已由经验解释。"],
        reassessment_triggers=["出现超出已审核范围的恶意载荷。"],
    )
    outcome = project_soc_case_outcome(run)
    assert outcome.conclusion_support == run.analysis.conclusion_support
    assert outcome.evidence_gaps == []
    assert outcome.evidence_gap_impact is SocCaseEvidenceGapImpact.NONE


def test_memory_explanation_never_clears_real_material_block() -> None:
    from soc_agent.contracts.schemas import AnalysisConclusionSupport

    run = _run(verdict=Verdict.SUSPICIOUS, needs_review=True, evidence_gaps=["当前行为与授权范围存在实质矛盾。"], manual_checks=["核实本次执行范围。"])
    run.analysis.conclusion_support = AnalysisConclusionSupport(context_refs=["M-AAAAAAAAAAAA"], resolved_questions=["历史业务用途已知。"])
    outcome = project_soc_case_outcome(run)
    assert outcome.evidence_gap_impact is SocCaseEvidenceGapImpact.DECISION_BLOCKING
    assert outcome.evidence_gaps == run.analysis.evidence_gaps
    assert outcome.next_steps == ["核实本次执行范围。"]


@pytest.mark.parametrize("disposition", [None, SocOperationalDisposition.UNKNOWN])
def test_review_only_policy_preserves_prior_advice_and_does_not_invent_critical_gaps(disposition) -> None:
    run = _run(evidence_gaps=["缺少端点进程证据。"], manual_checks=["检查主机计划任务。"])
    before = _snapshot(Verdict.SUSPICIOUS).model_copy(update={"suggested_action": "检查主机进程，并根据结果处置。"})
    after = before.model_copy(update={"needs_review": True, "suggested_action": "Policy asks for additional verification."})
    transition = _transition(
        before=before,
        after=after,
        kind=SocDecisionTransitionKind.REINFORCED,
        disposition=disposition,
        tenant_status=SocDecisionStageStatus.APPLIED,
    )

    outcome = project_soc_case_outcome(run, decision_transition=transition)

    assert outcome.security_verdict is Verdict.SUSPICIOUS
    assert outcome.decision_usable is True
    assert outcome.operational_disposition is None
    assert outcome.recommended_handling == "transfer"
    assert outcome.recommended_handling_basis == "governed_review_required"
    assert outcome.handling_recommendation == before.suggested_action
    assert outcome.closure_status is SocCaseClosureStatus.HANDLING_PENDING
    assert outcome.closure_reason_codes == ["tenant_policy_review_pending"]
    assert outcome.evidence_gap_impact is SocCaseEvidenceGapImpact.ADVISORY
    assert before.suggested_action in outcome.next_steps
    assert "检查主机计划任务。" in outcome.next_steps
    assert transition.after.suggested_action == "Policy asks for additional verification."


def test_ignore_recommendation_alone_does_not_mean_handling_was_executed() -> None:
    outcome = project_soc_case_outcome(_run(verdict=Verdict.FALSE_POSITIVE))

    assert outcome.recommended_handling == "ignore"
    assert outcome.operational_disposition is None
    assert outcome.closure_status is SocCaseClosureStatus.HANDLING_PENDING


def test_policy_ignore_is_a_plan_not_a_completed_external_disposition() -> None:
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

    assert outcome.closure_status is SocCaseClosureStatus.HANDLING_PENDING
    assert outcome.closure_reason_codes == ["disposition_decided"]
    assert outcome.progress_label == "已确定处置方案"
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
    assert "输出结构和引用校验" in outcome.next_steps[0]
    assert outcome.progress_label == "待处理结果校验问题"
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
    assert outcome.closure_status is SocCaseClosureStatus.HANDLING_PENDING
    assert "内部服务调用" in (outcome.decision_reason or "")
    assert "可疑" not in outcome.event_summary


def test_reviewed_directive_retains_superseded_base_gaps_in_audit() -> None:
    run = _run(verdict=Verdict.SUSPICIOUS, needs_review=True, evidence_gaps=["基础研判尚不知道业务用途。"])
    run.decision.review_reasons = [DecisionReviewReason.UNCERTAIN_VERDICT]
    transition = _transition(
        before=_snapshot(Verdict.SUSPICIOUS, needs_review=True),
        after=_snapshot(Verdict.FALSE_POSITIVE),
        kind=SocDecisionTransitionKind.OVERRIDDEN,
        disposition=SocOperationalDisposition.IGNORED,
        memory_status=SocDecisionStageStatus.OVERRIDDEN,
    )
    outcome = project_soc_case_outcome(run, decision_transition=transition)
    assert outcome.evidence_gaps == []
    assert outcome.prior_analysis_gaps == run.analysis.evidence_gaps
    assert outcome.evidence_gap_impact is SocCaseEvidenceGapImpact.NONE
    assert run.analysis.evidence_gaps == ["基础研判尚不知道业务用途。"]
    run.analysis_materiality = AnalysisMaterialityReport(core_usable=False, decision_usable=False, review_required=True, review_reasons=[DecisionReviewReason.ANALYSIS_OUTPUT_DEGRADED])
    blocked = project_soc_case_outcome(run, decision_transition=transition)
    assert blocked.evidence_gaps == run.analysis.evidence_gaps
    assert blocked.prior_analysis_gaps == []
    assert blocked.closure_status is SocCaseClosureStatus.FOLLOW_UP_REQUIRED


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
    assert outcome.closure_status is SocCaseClosureStatus.HANDLING_PENDING


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
    assert outcome.evidence_gap_impact is SocCaseEvidenceGapImpact.ADVISORY
    assert outcome.evidence_gaps == ["缺少资产归属信息。"]
    assert outcome.handling_reason.startswith("研判倾向误报，但企业策略要求转交。")
    assert outcome.next_steps == ["按企业策略转交复核，并记录接收方及处理结果。"]
    assert outcome.progress_label == "待转交复核"


@pytest.mark.parametrize("runtime_review,material_review", [(True, False), (False, True)])
@pytest.mark.parametrize("disposition", [None, SocOperationalDisposition.UNKNOWN, SocOperationalDisposition.ESCALATED])
def test_policy_handoff_preserves_independent_decision_review(runtime_review: bool, material_review: bool, disposition) -> None:
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
        disposition=disposition,
        tenant_status=SocDecisionStageStatus.APPLIED,
    )

    outcome = project_soc_case_outcome(run, decision_transition=transition)

    assert outcome.closure_status is SocCaseClosureStatus.FOLLOW_UP_REQUIRED
    assert outcome.evidence_gap_impact is SocCaseEvidenceGapImpact.DECISION_BLOCKING
    assert "tenant_policy_handoff_pending" not in outcome.closure_reason_codes
    assert "tenant_policy_review_pending" not in outcome.closure_reason_codes


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


def test_memory_owned_review_is_explained_without_a_fact_gap():
    transition = _transition(
        before=_snapshot(Verdict.SUSPICIOUS),
        after=_snapshot(Verdict.SUSPICIOUS, needs_review=True),
        kind=SocDecisionTransitionKind.REINFORCED,
        memory_status=SocDecisionStageStatus.REINFORCED,
    )
    result = project_soc_case_outcome(_run(), decision_transition=transition)
    assert result.closure_status is SocCaseClosureStatus.FOLLOW_UP_REQUIRED
    assert result.progress_label == "经验要求专项复核"
    assert "memory_review_required" in result.closure_reason_codes
    assert "使用边界" in result.next_steps[0]


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
    from soc_agent.contracts.schemas import ResponseTargetProposal

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
    run.analysis.role_adjudication.response_target_proposals = [ResponseTargetProposal.model_construct(proposal_id="RT-01")]

    outcome = project_soc_case_outcome(run)

    assert outcome.security_verdict is Verdict.TRUE_POSITIVE
    assert outcome.evidence_gap_impact is SocCaseEvidenceGapImpact.CAPABILITY_LIMITED
    assert outcome.blocked_capabilities == [AnalysisCapability.ATTACKER_TARGETING]
