"""Deterministic operator-facing projection of one governed SOC case outcome."""

from __future__ import annotations

from collections.abc import Sequence

from soc_agent.contracts import (
    AnalysisCapability,
    AnalysisContextReferenceKind,
    AnalysisRun,
    AnalysisRunStatus,
    SocActionExecutionRecord,
    SocActionExecutionStatus,
    SocAutomationContributorKind,
    SocCaseClosureStatus,
    SocCaseContribution,
    SocCaseContributionKind,
    SocCaseDecisionChange,
    SocCaseEvidenceGapImpact,
    SocCaseOutcomeBasis,
    SocCaseOutcomeBasisKind,
    SocCaseOutcomeView,
    SocDecisionStageKind,
    SocDecisionStageStatus,
    SocDecisionTransitionKind,
    SocDecisionTransitionRecord,
    SocExternalDispositionRecord,
    SocOperationalDisposition,
    Verdict,
)
from soc_agent.core.case_progress import follow_up_reason_codes, progress_text
from soc_agent.core.handling import concrete_disposition, handling_blockers, policy_requires_follow_up, project_operational_handling, resolve_operational_disposition
from soc_agent.core.operator_language import operator_text
from soc_agent.memory.matching_facts import memory_matching_facts

_TERMINAL_DISPOSITIONS = frozenset(
    {
        SocOperationalDisposition.CLOSED_TRUE_POSITIVE,
        SocOperationalDisposition.CLOSED_FALSE_POSITIVE,
        SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE,
        SocOperationalDisposition.SUPPRESSED,
        SocOperationalDisposition.IGNORED,
        SocOperationalDisposition.DUPLICATE,
    }
)


def project_soc_case_outcome(
    run: AnalysisRun,
    *,
    decision_transition: SocDecisionTransitionRecord | None = None,
    external_dispositions: Sequence[SocExternalDispositionRecord] = (),
    action_executions: Sequence[SocActionExecutionRecord] = (),
    memory_context_count: int | None = None,
    pattern_support_count: int | None = None,
) -> SocCaseOutcomeView:
    """Collapse Runtime and governed post-processing into one operator answer.

    This projection does not make a new security decision. It explains the
    already-persisted final verdict, handling state, and material limits without
    exposing the internal state machine as competing conclusions.
    """

    analysis = run.analysis
    direct = run.direct_resolution
    action_executions = tuple(item for item in action_executions if item.run_id == run.run_id and item.alert_id == run.alert_id)
    external_dispositions = tuple(item for item in external_dispositions if item.target_run_id == run.run_id or (item.target_run_id is None and item.target_alert_id == run.alert_id))
    base_decision = run.decision
    effective = decision_transition.after if decision_transition is not None else base_decision
    transition_conflicted = bool(decision_transition is not None and decision_transition.transition_kind is SocDecisionTransitionKind.CONFLICTED)
    materiality = run.analysis_materiality
    blockers = handling_blockers(needs_review=base_decision.needs_review if base_decision else False, review_reasons=base_decision.review_reasons if base_decision else (), transition=decision_transition, materiality=materiality)
    failed = bool(run.status is AnalysisRunStatus.FAILED or (analysis is None and direct is None) or effective is None)
    final_verdict = effective.verdict if effective is not None else None
    decision_usable = bool(not failed and not blockers and (direct is not None or final_verdict not in {Verdict.UNKNOWN, Verdict.NEEDS_REVIEW}))
    policy_review_only = policy_requires_follow_up(decision_transition)
    follow_up_required = bool(not failed and not decision_usable)

    decision_change, change_summary = _decision_change(decision_transition)
    memory_applied = (direct is not None and direct.source_kind == "memory") or decision_change in {
        SocCaseDecisionChange.MEMORY_REINFORCED,
        SocCaseDecisionChange.MEMORY_OVERRIDDEN,
    }
    gaps = [] if direct is not None else _evidence_gaps(run)
    prior_analysis_gaps: list[str] = []
    # A governed directive replaces the Base decision, not missing source evidence.
    # Keep superseded Base prose in audit; never use prose to clear a material guard.
    if memory_applied and not follow_up_required and analysis is not None:
        prior_analysis_gaps = _dedupe_text(analysis.evidence_gaps)[:20]
        gaps = _coverage_gaps(run)
    blocked_capabilities = _blocked_capabilities(run)
    action_limits_relevant = bool(blocked_capabilities and (action_executions or (analysis is not None and analysis.role_adjudication.response_target_proposals)))
    gap_impact = _gap_impact(
        has_gaps=bool(gaps),
        follow_up_required=follow_up_required,
        blocked_capabilities=blocked_capabilities if action_limits_relevant else (),
    )
    operational_disposition, external_basis = resolve_operational_disposition(
        run_id=run.run_id,
        alert_id=run.alert_id,
        decision_transition=decision_transition,
        external_dispositions=external_dispositions,
    )
    if direct is not None and external_basis is None and decision_transition is None:
        operational_disposition = direct.disposition
        policy_review_only = direct.source_kind == "tenant_policy" and direct.disposition is SocOperationalDisposition.ESCALATED
    # Keep historical plans in lineage, but do not present a blocked plan as adopted.
    if blockers and external_basis is None:
        operational_disposition = None
    recommended_handling, recommended_handling_basis = project_operational_handling(verdict=final_verdict, disposition=operational_disposition, blockers=blockers, policy_review_only=policy_review_only, failed=failed)
    latest_execution = max(
        action_executions,
        key=lambda item: (item.started_at, item.execution_id),
        default=None,
    )
    closure_status, closure_reasons = _closure_status(
        failed=failed,
        follow_up_required=follow_up_required,
        gap_impact=gap_impact,
        disposition=operational_disposition,
        execution=latest_execution,
        policy_review_only=policy_review_only and external_basis is None,
        disposition_confirmed=external_basis is not None,
    )
    if closure_status is SocCaseClosureStatus.FOLLOW_UP_REQUIRED:
        closure_reasons.extend(follow_up_reason_codes(run, conflicted=transition_conflicted, verdict=final_verdict, transition=decision_transition))
    progress_label, progress_detail = progress_text(closure_status, closure_reasons)
    memory_count = memory_context_count if memory_context_count is not None else _memory_context_count(run)
    tenant_applied = (direct is not None and direct.source_kind == "tenant_policy") or _tenant_policy_applied(decision_transition)
    event_summary = _bounded_text(
        analysis.summary if analysis is not None else direct.summary if direct is not None else run.failure.message if run.failure is not None else "本次研判未形成可用结果。",
        limit=4000,
    )
    base_decision_reason = base_decision.reason if base_decision is not None else analysis.reason if analysis is not None else None
    handling_recommendation = effective.suggested_action if effective is not None else analysis.recommended_action if analysis is not None else None
    # Old transitions may have replaced useful Base/Memory advice with an abstaining policy.
    # Read its pre-policy snapshot without rewriting the persisted lineage.
    if decision_transition is not None:
        stage = next((item for item in decision_transition.stages if item.stage is SocDecisionStageKind.TENANT_POLICY), None)
        if (
            stage is not None
            and stage.status is SocDecisionStageStatus.APPLIED
            and stage.before is not None
            and concrete_disposition(stage.disposition_after) is None
            and concrete_disposition(decision_transition.effective_disposition) is None
            and stage.after == decision_transition.after
        ):
            handling_recommendation = stage.before.suggested_action
    if policy_review_only and final_verdict is Verdict.FALSE_POSITIVE:
        handling_recommendation = "按企业策略转交复核，具体核查范围见策略依据。"
    next_steps = _next_steps(
        run,
        closure_status=closure_status,
        handling_recommendation=handling_recommendation,
        closure_reason_codes=closure_reasons,
    )
    basis = _outcome_basis(
        run,
        decision_transition=decision_transition,
        external_basis=external_basis,
    )
    decision_reason = _effective_decision_reason(
        base_decision_reason,
        decision_change=decision_change,
        change_summary=change_summary,
        basis=basis,
    )
    handling_reason = decision_reason
    if external_basis is not None:
        handling_reason = external_basis.apply_reason
    elif tenant_applied and (operational_disposition is not None or policy_review_only):
        policy_basis = next((item.summary for item in basis if item.kind is SocCaseOutcomeBasisKind.TENANT_POLICY), None)
        if policy_basis:
            handling_reason = policy_basis
            if final_verdict is Verdict.FALSE_POSITIVE and operational_disposition is SocOperationalDisposition.ESCALATED:
                handling_reason = "研判倾向误报，但企业策略要求转交。" + policy_basis
    if blockers and not failed:
        handling_reason = "本次转交确认：" + progress_detail
        handling_recommendation = _bounded_text("；".join(next_steps), limit=1000)
    contributions = _contributions(
        run,
        memory_context_count=memory_count,
        memory_directive_applied=memory_applied,
        tenant_policy_applied=tenant_applied,
        pattern_support_count=pattern_support_count,
    )

    return SocCaseOutcomeView(
        processing_path=direct.source_kind if direct is not None else "model_analysis",
        event_summary=event_summary,
        security_verdict=final_verdict,
        base_verdict=(base_decision.verdict if base_decision is not None and direct is None else None),
        confidence=(effective.confidence if effective is not None else None),
        decision_usable=decision_usable,
        decision_reason=(_bounded_text(decision_reason, limit=8000) if decision_reason is not None else None),
        memory_matching_facts=memory_matching_facts(run),
        decision_change=decision_change,
        change_summary=change_summary,
        operational_disposition=operational_disposition,
        recommended_handling=recommended_handling,
        recommended_handling_basis=recommended_handling_basis,
        handling_reason=_bounded_text(handling_reason, limit=3000) if handling_reason else None,
        handling_recommendation=handling_recommendation,
        closure_status=closure_status,
        closure_reason_codes=closure_reasons,
        progress_label=progress_label,
        progress_detail=progress_detail,
        evidence_gap_impact=gap_impact,
        evidence_gaps=gaps,
        blocked_capabilities=blocked_capabilities,
        action_limits_relevant=action_limits_relevant,
        conclusion_support=(analysis.conclusion_support if analysis is not None and final_verdict == analysis.verdict else None),
        prior_analysis_gaps=prior_analysis_gaps,
        next_steps=next_steps,
        basis=basis,
        contributions=contributions,
        memory_context_count=memory_count,
        memory_directive_applied=memory_applied,
        tenant_policy_applied=tenant_applied,
    )


def _evidence_gaps(run: AnalysisRun) -> list[str]:
    gaps = list(run.analysis.evidence_gaps if run.analysis is not None else ())
    return _dedupe_text([*gaps, *_coverage_gaps(run)])[:20]


def _coverage_gaps(run: AnalysisRun) -> list[str]:
    request = run.llm_analysis_request
    return [f"{item.reason}（{item.field_path}）" for item in request.evidence_coverage.high_value_gaps][:20] if request is not None else []


def _blocked_capabilities(run: AnalysisRun) -> list[AnalysisCapability]:
    if run.analysis_materiality is None:
        return []
    return sorted(
        {item.capability for item in run.analysis_materiality.capability_guards if not item.allowed},
        key=lambda item: item.value,
    )


def _gap_impact(
    *,
    has_gaps: bool,
    follow_up_required: bool,
    blocked_capabilities: Sequence[AnalysisCapability],
) -> SocCaseEvidenceGapImpact:
    if follow_up_required:
        return SocCaseEvidenceGapImpact.DECISION_BLOCKING
    if has_gaps and blocked_capabilities:
        return SocCaseEvidenceGapImpact.CAPABILITY_LIMITED
    if has_gaps:
        return SocCaseEvidenceGapImpact.ADVISORY
    return SocCaseEvidenceGapImpact.NONE


def _closure_status(
    *,
    failed: bool,
    follow_up_required: bool,
    gap_impact: SocCaseEvidenceGapImpact,
    disposition: SocOperationalDisposition | None,
    execution: SocActionExecutionRecord | None,
    policy_review_only: bool,
    disposition_confirmed: bool,
) -> tuple[SocCaseClosureStatus, list[str]]:
    if failed:
        return SocCaseClosureStatus.FAILED, ["runtime_result_unavailable"]
    if follow_up_required:
        return SocCaseClosureStatus.FOLLOW_UP_REQUIRED, ["material_follow_up_required"]
    if disposition_confirmed and disposition in _TERMINAL_DISPOSITIONS:
        if gap_impact in {
            SocCaseEvidenceGapImpact.ADVISORY,
            SocCaseEvidenceGapImpact.CAPABILITY_LIMITED,
        }:
            return SocCaseClosureStatus.CLOSED_WITH_LIMITATIONS, [
                "handling_applied",
                "non_blocking_limitations_present",
            ]
        return SocCaseClosureStatus.CLOSED, ["handling_applied"]
    if disposition_confirmed and disposition is SocOperationalDisposition.ESCALATED:
        return SocCaseClosureStatus.HANDLING_PENDING, ["handoff_recorded"]
    if execution is not None:
        reason = {
            SocActionExecutionStatus.PENDING: "action_execution_pending",
            SocActionExecutionStatus.FAILED_RETRYABLE: "action_execution_failed",
            SocActionExecutionStatus.FAILED_TERMINAL: "action_execution_failed",
            SocActionExecutionStatus.SKIPPED: "action_execution_skipped",
            SocActionExecutionStatus.SUCCEEDED: "action_result_recorded",
        }[execution.status]
        return SocCaseClosureStatus.HANDLING_PENDING, [reason]
    if disposition is SocOperationalDisposition.ESCALATED:
        if policy_review_only:
            return SocCaseClosureStatus.HANDLING_PENDING, ["tenant_policy_handoff_pending"]
        return SocCaseClosureStatus.HANDLING_PENDING, ["handoff_confirmation_pending"]
    if policy_review_only:
        return SocCaseClosureStatus.HANDLING_PENDING, ["tenant_policy_review_pending"]
    if disposition in _TERMINAL_DISPOSITIONS:
        return SocCaseClosureStatus.HANDLING_PENDING, ["disposition_decided"]
    return SocCaseClosureStatus.HANDLING_PENDING, ["handling_not_applied"]


def _decision_change(
    transition: SocDecisionTransitionRecord | None,
) -> tuple[SocCaseDecisionChange, str | None]:
    if transition is None:
        return SocCaseDecisionChange.UNCHANGED, None
    if transition.transition_kind is SocDecisionTransitionKind.CONFLICTED:
        return (
            SocCaseDecisionChange.CONFLICTED,
            "当前证据与受治理决策来源存在冲突，系统没有把任一来源伪装成最终结论。",
        )
    memory_stage = next(
        (item for item in transition.stages if item.stage is SocDecisionStageKind.MEMORY),
        None,
    )
    if memory_stage is None:
        return SocCaseDecisionChange.UNCHANGED, None
    if memory_stage.status is SocDecisionStageStatus.OVERRIDDEN or (memory_stage.before is not None and memory_stage.before.verdict is not memory_stage.after.verdict):
        return (
            SocCaseDecisionChange.MEMORY_OVERRIDDEN,
            "已审核经验将模型初判调整为当前最终安全判断，完整前后变化已保留。",
        )
    if memory_stage.status is SocDecisionStageStatus.REINFORCED:
        return (
            SocCaseDecisionChange.MEMORY_REINFORCED,
            "已审核经验强化了当前安全判断，结论方向没有改变。",
        )
    return SocCaseDecisionChange.UNCHANGED, None


def _tenant_policy_applied(
    transition: SocDecisionTransitionRecord | None,
) -> bool:
    return bool(transition is not None and any(item.stage is SocDecisionStageKind.TENANT_POLICY and item.status is SocDecisionStageStatus.APPLIED for item in transition.stages))


def _memory_context_count(run: AnalysisRun) -> int:
    if run.llm_analysis_request is None:
        return 0
    return sum(item.kind is AnalysisContextReferenceKind.CONFIRMED_MEMORY for item in run.llm_analysis_request.context_catalog)


def _next_steps(
    run: AnalysisRun,
    *,
    closure_status: SocCaseClosureStatus,
    handling_recommendation: str | None,
    closure_reason_codes: Sequence[str],
) -> list[str]:
    if closure_status is SocCaseClosureStatus.FAILED:
        return ["修复运行失败原因后重新执行研判。"]
    if closure_status is SocCaseClosureStatus.FOLLOW_UP_REQUIRED:
        if "memory_review_required" in closure_reason_codes:
            return ["查看已应用经验指令的复核要求和使用边界，按该经验要求处理。"]
        if "decision_source_conflict" in closure_reason_codes:
            return ["查看 Memory 与企业策略阶段的分歧或应用限制，确认采用的依据；保留原始研判记录。"]
        if "analysis_validation_failed" in closure_reason_codes or "critical_input_missing" in closure_reason_codes:
            return ["检查本次运行的输入覆盖、输出结构和引用校验记录，修复具体问题后再决定是否重跑。"]
        if "review_requirement_unattributed" in closure_reason_codes:
            return ["查看各决策阶段的复核要求与来源，补充具体复核原因。"]
        checks = _dedupe_text(run.analysis.manual_checks if run.analysis is not None else ())
        return checks[:20] or [progress_text(closure_status, closure_reason_codes)[1]]
    if closure_status is SocCaseClosureStatus.HANDLING_PENDING:
        if "action_execution_failed" in closure_reason_codes:
            return ["查看动作执行错误及可重试状态，处理接口问题；无需因此重新调用模型研判。"]
        if "action_execution_pending" in closure_reason_codes:
            return ["查看已有动作的执行结果；不要重复发起相同动作。"]
        if "action_execution_skipped" in closure_reason_codes:
            return ["查看动作跳过原因，确认是否需要后续处理。"]
        if "action_result_recorded" in closure_reason_codes or "handoff_recorded" in closure_reason_codes:
            return ["等待或记录最终处置反馈；不重复执行已完成的动作。"]
        if "disposition_decided" in closure_reason_codes:
            return ["按已确定的处置方案处理，并记录实际结果；当前方案不代表外部系统已完成。"]
        if "tenant_policy_handoff_pending" in closure_reason_codes:
            return ["按企业策略转交复核，并记录接收方及处理结果。"]
        if "tenant_policy_review_pending" in closure_reason_codes:
            return _dedupe_text([*([handling_recommendation] if handling_recommendation else []), *(run.analysis.manual_checks if run.analysis is not None else [])])[:20]
        if handling_recommendation:
            return [handling_recommendation]
        return ["确认并记录本次告警的运营处置结果。"]
    return []


def _outcome_basis(
    run: AnalysisRun,
    *,
    decision_transition: SocDecisionTransitionRecord | None,
    external_basis: SocExternalDispositionRecord | None,
) -> list[SocCaseOutcomeBasis]:
    items: list[SocCaseOutcomeBasis] = []
    if run.direct_resolution is not None:
        direct = run.direct_resolution
        items.append(
            SocCaseOutcomeBasis(
                kind=SocCaseOutcomeBasisKind.TENANT_POLICY if direct.source_kind == "tenant_policy" else SocCaseOutcomeBasisKind.CONFIRMED_MEMORY,
                summary=_bounded_text(direct.summary, limit=3000),
                source_id=direct.policy_snapshot["decision_id"] if direct.policy_snapshot else direct.source_id,
            )
        )
    elif run.decision is not None:
        items.append(
            SocCaseOutcomeBasis(
                kind=SocCaseOutcomeBasisKind.CURRENT_ANALYSIS,
                summary=_bounded_text(run.decision.reason, limit=3000),
                source_id=run.run_id,
            )
        )
    if decision_transition is not None:
        for stage in decision_transition.stages:
            if stage.stage is SocDecisionStageKind.MEMORY and stage.status in {
                SocDecisionStageStatus.REINFORCED,
                SocDecisionStageStatus.OVERRIDDEN,
                SocDecisionStageStatus.CONFLICTED,
            }:
                for contributor in stage.contributors:
                    if contributor.kind is SocAutomationContributorKind.CONFIRMED_MEMORY:
                        items.append(
                            SocCaseOutcomeBasis(
                                kind=SocCaseOutcomeBasisKind.CONFIRMED_MEMORY,
                                summary=_bounded_text(
                                    contributor.detail or stage.summary,
                                    limit=3000,
                                ),
                                source_id=contributor.ref_id,
                            )
                        )
            if stage.stage is SocDecisionStageKind.TENANT_POLICY and stage.status is SocDecisionStageStatus.APPLIED:
                items.append(
                    SocCaseOutcomeBasis(
                        kind=SocCaseOutcomeBasisKind.TENANT_POLICY,
                        summary=_bounded_text(stage.summary, limit=3000),
                        source_id=stage.source_decision_id or stage.source_id,
                    )
                )
    if external_basis is not None:
        items.append(
            SocCaseOutcomeBasis(
                kind=SocCaseOutcomeBasisKind.EXTERNAL_FEEDBACK,
                summary=_bounded_text(external_basis.apply_reason, limit=3000),
                source_id=external_basis.disposition_id,
            )
        )
    deduped: list[SocCaseOutcomeBasis] = []
    seen: set[tuple[SocCaseOutcomeBasisKind, str | None, str]] = set()
    for item in items:
        identity = (item.kind, item.source_id, item.summary)
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(item)
    return deduped[:10]


def _contributions(
    run: AnalysisRun,
    *,
    memory_context_count: int,
    memory_directive_applied: bool,
    tenant_policy_applied: bool,
    pattern_support_count: int | None,
) -> list[SocCaseContribution]:
    items: list[SocCaseContribution] = []
    evidence_count = len(run.analysis.evidence) if run.analysis is not None else 0
    if evidence_count:
        items.append(
            SocCaseContribution(
                kind=SocCaseContributionKind.EVIDENCE_TRACE,
                count=evidence_count,
                summary=f"保留了 {evidence_count} 条可追踪研判证据。",
            )
        )
    if memory_context_count:
        items.append(
            SocCaseContribution(
                kind=SocCaseContributionKind.REVIEWED_MEMORY_REUSED,
                count=memory_context_count,
                summary=(f"检索到 {memory_context_count} 条已审核经验，其中精确匹配经验参与了最终判断。" if memory_directive_applied else f"引用了 {memory_context_count} 条已审核经验作为研判背景。"),
            )
        )
    if tenant_policy_applied:
        items.append(
            SocCaseContribution(
                kind=SocCaseContributionKind.TENANT_POLICY_APPLIED,
                count=1,
                summary="企业规则已参与本次处置与复核判断。",
            )
        )
    if pattern_support_count is not None and pattern_support_count > 1:
        items.append(
            SocCaseContribution(
                kind=SocCaseContributionKind.RECURRING_PATTERN,
                count=pattern_support_count,
                summary=f"识别到该行为模式已累计出现 {pattern_support_count} 次。",
            )
        )
    return items


def _effective_decision_reason(
    base_reason: str | None,
    *,
    decision_change: SocCaseDecisionChange,
    change_summary: str | None,
    basis: Sequence[SocCaseOutcomeBasis],
) -> str | None:
    if decision_change in {
        SocCaseDecisionChange.MEMORY_REINFORCED,
        SocCaseDecisionChange.MEMORY_OVERRIDDEN,
    }:
        memory_basis = next(
            (item.summary for item in basis if item.kind is SocCaseOutcomeBasisKind.CONFIRMED_MEMORY),
            None,
        )
        if memory_basis:
            return memory_basis
    if decision_change is SocCaseDecisionChange.CONFLICTED:
        return change_summary
    return base_reason


def _dedupe_text(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if isinstance(value, str) and value.strip()))


def _bounded_text(value: str, *, limit: int) -> str:
    value = operator_text(value)
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."


__all__ = ["project_soc_case_outcome"]
