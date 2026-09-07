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
    SocExternalDispositionApplyStatus,
    SocExternalDispositionRecord,
    SocOperationalDisposition,
    Verdict,
)

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
    base_decision = run.decision
    effective = decision_transition.after if decision_transition is not None else base_decision
    transition_conflicted = bool(decision_transition is not None and decision_transition.transition_kind is SocDecisionTransitionKind.CONFLICTED)
    materiality = run.analysis_materiality
    material_decision_block = bool(materiality is not None and (not materiality.decision_usable or materiality.review_required))
    failed = bool(run.status is AnalysisRunStatus.FAILED or analysis is None or effective is None)
    final_verdict = effective.verdict if effective is not None else None
    decision_usable = bool(not failed and not transition_conflicted and not material_decision_block and final_verdict not in {Verdict.UNKNOWN, Verdict.NEEDS_REVIEW})
    policy_handoff_only = _policy_handoff_only(decision_transition)
    follow_up_required = bool(not failed and (not decision_usable or (effective is not None and effective.needs_review and not policy_handoff_only)))

    gaps = _evidence_gaps(run)
    blocked_capabilities = _blocked_capabilities(run)
    gap_impact = _gap_impact(
        has_gaps=bool(gaps),
        follow_up_required=follow_up_required,
        blocked_capabilities=blocked_capabilities,
    )
    operational_disposition, external_basis = _operational_disposition(
        decision_transition=decision_transition,
        external_dispositions=external_dispositions,
        conflicted=transition_conflicted,
    )
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
        policy_handoff_only=policy_handoff_only and external_basis is None,
    )
    decision_change, change_summary = _decision_change(decision_transition)
    memory_count = memory_context_count if memory_context_count is not None else _memory_context_count(run)
    memory_applied = decision_change in {
        SocCaseDecisionChange.MEMORY_REINFORCED,
        SocCaseDecisionChange.MEMORY_OVERRIDDEN,
    }
    tenant_applied = _tenant_policy_applied(decision_transition)
    event_summary = _bounded_text(
        analysis.summary if analysis is not None else run.failure.message if run.failure is not None else "本次研判未形成可用结果。",
        limit=4000,
    )
    base_decision_reason = base_decision.reason if base_decision is not None else analysis.reason if analysis is not None else None
    handling_recommendation = effective.suggested_action if effective is not None else analysis.recommended_action if analysis is not None else None
    next_steps = _next_steps(
        run,
        closure_status=closure_status,
        handling_recommendation=handling_recommendation,
        latest_execution=latest_execution,
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
    contributions = _contributions(
        run,
        memory_context_count=memory_count,
        memory_directive_applied=memory_applied,
        tenant_policy_applied=tenant_applied,
        pattern_support_count=pattern_support_count,
    )

    return SocCaseOutcomeView(
        event_summary=event_summary,
        security_verdict=final_verdict,
        base_verdict=(base_decision.verdict if base_decision is not None else None),
        confidence=(effective.confidence if effective is not None else None),
        decision_usable=decision_usable,
        decision_reason=(_bounded_text(decision_reason, limit=8000) if decision_reason is not None else None),
        decision_change=decision_change,
        change_summary=change_summary,
        operational_disposition=operational_disposition,
        handling_reason=(
            "研判为误报；企业规则要求此类告警仍须转交复核。"
            if "tenant_policy_handoff_pending" in closure_reasons and final_verdict is Verdict.FALSE_POSITIVE
            else "研判已完成；企业规则要求此类告警转交复核。"
            if "tenant_policy_handoff_pending" in closure_reasons
            else None
        ),
        handling_recommendation=handling_recommendation,
        closure_status=closure_status,
        closure_reason_codes=closure_reasons,
        evidence_gap_impact=gap_impact,
        evidence_gaps=gaps,
        blocked_capabilities=blocked_capabilities,
        next_steps=next_steps,
        basis=basis,
        contributions=contributions,
        memory_context_count=memory_count,
        memory_directive_applied=memory_applied,
        tenant_policy_applied=tenant_applied,
    )


def _evidence_gaps(run: AnalysisRun) -> list[str]:
    gaps = list(run.analysis.evidence_gaps if run.analysis is not None else ())
    request = run.llm_analysis_request
    if request is not None:
        gaps.extend(f"{item.reason}（{item.field_path}）" for item in request.evidence_coverage.high_value_gaps)
    return _dedupe_text(gaps)[:20]


def _policy_handoff_only(transition: SocDecisionTransitionRecord | None) -> bool:
    """Identify review introduced solely by an applied operational handoff rule."""
    if transition is None or transition.transition_kind is SocDecisionTransitionKind.CONFLICTED:
        return False
    stage = next((item for item in transition.stages if item.stage is SocDecisionStageKind.TENANT_POLICY), None)
    return bool(
        stage is not None
        and stage.status is SocDecisionStageStatus.APPLIED
        and stage.disposition_after is SocOperationalDisposition.ESCALATED
        and transition.effective_disposition is SocOperationalDisposition.ESCALATED
        and stage.before is not None
        and not stage.before.needs_review
        and stage.after.needs_review
        and stage.before.verdict is stage.after.verdict
        and stage.after == transition.after
    )


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


def _operational_disposition(
    *,
    decision_transition: SocDecisionTransitionRecord | None,
    external_dispositions: Sequence[SocExternalDispositionRecord],
    conflicted: bool,
) -> tuple[SocOperationalDisposition | None, SocExternalDispositionRecord | None]:
    if conflicted:
        return None, None
    mapped = [item for item in external_dispositions if item.apply_status is SocExternalDispositionApplyStatus.MAPPED and item.canonical_status is not SocOperationalDisposition.UNKNOWN]
    latest_external = max(
        mapped,
        key=lambda item: (item.created_at, item.disposition_id),
        default=None,
    )
    if latest_external is not None:
        return latest_external.canonical_status, latest_external
    if decision_transition is not None:
        return decision_transition.effective_disposition, None
    return None, None


def _closure_status(
    *,
    failed: bool,
    follow_up_required: bool,
    gap_impact: SocCaseEvidenceGapImpact,
    disposition: SocOperationalDisposition | None,
    execution: SocActionExecutionRecord | None,
    policy_handoff_only: bool,
) -> tuple[SocCaseClosureStatus, list[str]]:
    if failed:
        return SocCaseClosureStatus.FAILED, ["runtime_result_unavailable"]
    if follow_up_required:
        return SocCaseClosureStatus.FOLLOW_UP_REQUIRED, ["material_follow_up_required"]
    execution_succeeded = bool(execution is not None and execution.status is SocActionExecutionStatus.SUCCEEDED)
    if disposition in _TERMINAL_DISPOSITIONS or execution_succeeded:
        if gap_impact in {
            SocCaseEvidenceGapImpact.ADVISORY,
            SocCaseEvidenceGapImpact.CAPABILITY_LIMITED,
        }:
            return SocCaseClosureStatus.CLOSED_WITH_LIMITATIONS, [
                "handling_applied",
                "non_blocking_limitations_present",
            ]
        return SocCaseClosureStatus.CLOSED, ["handling_applied"]
    if execution is not None and execution.status in {
        SocActionExecutionStatus.FAILED_RETRYABLE,
        SocActionExecutionStatus.FAILED_TERMINAL,
    }:
        return SocCaseClosureStatus.HANDLING_PENDING, ["action_execution_failed"]
    if disposition is SocOperationalDisposition.ESCALATED:
        if policy_handoff_only:
            return SocCaseClosureStatus.HANDLING_PENDING, ["tenant_policy_handoff_pending"]
        return SocCaseClosureStatus.HANDLING_PENDING, ["handoff_confirmation_pending"]
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
    latest_execution: SocActionExecutionRecord | None,
    closure_reason_codes: Sequence[str],
) -> list[str]:
    if closure_status is SocCaseClosureStatus.FAILED:
        return ["修复运行失败原因后重新执行研判。"]
    if closure_status is SocCaseClosureStatus.FOLLOW_UP_REQUIRED:
        checks = _dedupe_text(run.analysis.manual_checks if run.analysis is not None else ())
        return checks[:20] or ["核实关键事实冲突或缺口后更新最终判断。"]
    if closure_status is SocCaseClosureStatus.HANDLING_PENDING:
        if latest_execution is not None and latest_execution.error_message:
            return [f"重试或人工处理失败动作：{latest_execution.error_message}"]
        if "tenant_policy_handoff_pending" in closure_reason_codes:
            return ["按企业规则转交复核，并记录接收方及处理结果。"]
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
    if run.decision is not None:
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
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."


__all__ = ["project_soc_case_outcome"]
