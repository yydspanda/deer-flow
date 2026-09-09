"""Read-only progress explanations; never change decisions or schedule work."""

from collections.abc import Sequence

from soc_agent.contracts import AnalysisRun, DecisionReviewReason, SocCaseClosureStatus, SocDecisionStageKind, SocDecisionStageStatus, SocDecisionTransitionRecord, Verdict


def follow_up_reason_codes(run: AnalysisRun, *, conflicted: bool, verdict: Verdict | None, transition: SocDecisionTransitionRecord | None = None) -> list[str]:
    reasons = set(run.analysis_materiality.review_reasons if run.analysis_materiality else ())
    if run.decision is not None:
        reasons.update(run.decision.review_reasons)
    codes = []
    if conflicted:
        codes.append("decision_source_conflict")
    mapping = {
        DecisionReviewReason.FACT_CONFLICT: "current_fact_conflict",
        DecisionReviewReason.HIGH_VALUE_EVIDENCE_GAP: "critical_input_missing",
        DecisionReviewReason.ANALYSIS_OUTPUT_DEGRADED: "analysis_validation_failed",
        DecisionReviewReason.UNGROUNDED_ANALYSIS_EVIDENCE: "analysis_validation_failed",
        DecisionReviewReason.UNGROUNDED_ANALYSIS_REASONING: "analysis_validation_failed",
        DecisionReviewReason.ROLE_VERIFICATION_CHALLENGED: "role_verification_challenged",
        DecisionReviewReason.STUB_ANALYZER: "analysis_fallback_used",
    }
    codes.extend(code for reason, code in mapping.items() if reason in reasons)
    if run.analysis_materiality and not run.analysis_materiality.decision_usable and not codes:
        codes.append("analysis_validation_failed")
    if verdict in {Verdict.UNKNOWN, Verdict.NEEDS_REVIEW}:
        codes.append("decision_not_established")
    if not codes and transition is not None:
        memory = next((item for item in transition.stages if item.stage is SocDecisionStageKind.MEMORY), None)
        if memory is not None and memory.status in {SocDecisionStageStatus.REINFORCED, SocDecisionStageStatus.OVERRIDDEN} and memory.before is not None and not memory.before.needs_review and memory.after.needs_review:
            codes.append("memory_review_required")
    return list(dict.fromkeys(codes)) or ["review_requirement_unattributed"]


_PROGRESS_REASONS = {
    "runtime_result_unavailable": ("研判运行失败", "本次运行未生成可用结果；查看失败步骤后再决定是否重试。"),
    "decision_source_conflict": ("待解决决策分歧", "Memory 或企业策略阶段存在未解决的冲突或应用限制；查看冲突阶段的来源与理由，不代表原始告警不可信。"),
    "current_fact_conflict": ("待确认事实冲突", "本次记录存在尚未裁决的实质事实冲突；当前判断保留，但相关问题尚待确认。"),
    "critical_input_missing": ("待补齐关键输入", "输入覆盖报告记录了未进入研判的关键证据；这是输入处理问题，不是要求额外采购或查询安全数据。"),
    "analysis_validation_failed": ("待处理结果校验问题", "核心输出或引用校验尚未通过；先检查技术审计，不应把结构或引用问题当作业务事实缺失。"),
    "role_verification_challenged": ("待确认角色分歧", "角色复核对当前方向或角色提出了实质反证；请查看具体争议，不需要重新怀疑所有告警字段。"),
    "analysis_fallback_used": ("本次使用兜底结果", "当前结果来自确定性兜底，不应当作真实模型已经完成研判。"),
    "decision_not_established": ("尚未形成明确判断", "模型已运行，但最终判断仍为暂无法判断或需要确认；具体原因见研判说明，不自动生成额外人工任务。"),
    "review_requirement_unattributed": ("待确认复核原因", "记录要求复核，但现有阶段信息不足以确定具体原因；不推断成证据缺失，也不自动清除该要求。"),
    "memory_review_required": ("经验要求专项复核", "已应用的经验指令新增了复核要求；查看该经验的使用边界，不推断成当前告警缺少事实。"),
    "tenant_policy_handoff_pending": ("待转交复核", "企业策略已选择转交复核；当前视图没有转交完成反馈，不表示后台正在自动转交。"),
    "tenant_policy_review_pending": ("待按建议排查", "企业策略要求继续排查，但未指定明确处置；沿用处理建议，不代表本次研判运行失败。"),
    "handoff_confirmation_pending": ("待转交复核", "当前处置方案为转交；尚未记录转交完成反馈。"),
    "handoff_recorded": ("已转交，待处理结果", "已记录外部转交反馈；接收方的最终处理结果尚未记录。"),
    "handling_not_applied": ("研判完成，未记录处置", "本次判断已形成；处理建议不是执行记录，当前视图尚未记录后续处置。"),
    "disposition_decided": ("已确定处置方案", "本系统已采用当前忽略、抑制、合并或结案方案；尚未记录最终处置反馈，不能据此认定外部系统已处理。"),
    "action_execution_pending": ("动作等待执行结果", "已有待执行动作记录；尚未取得成功或失败结果。"),
    "action_execution_failed": ("动作执行失败", "动作记录显示执行失败；安全判断仍保留。需要处理执行错误，而不是重新补充告警证据。"),
    "action_execution_skipped": ("动作未执行", "动作记录显示已跳过；查看跳过原因，不把未执行当作成功。"),
    "action_result_recorded": ("动作已有结果，待确认处置", "已有成功动作记录（可能包含 Mock 演练）；单个动作成功不等于整条告警已结案。"),
}


def progress_text(status: SocCaseClosureStatus, reasons: Sequence[str]) -> tuple[str, str]:
    if status in {SocCaseClosureStatus.CLOSED, SocCaseClosureStatus.CLOSED_WITH_LIMITATIONS}:
        return (
            "处置已确认" if status is SocCaseClosureStatus.CLOSED else "处置已确认，有补充信息",
            "已记录最终处置反馈。" + ("剩余补充信息不阻断该结果。" if status is SocCaseClosureStatus.CLOSED_WITH_LIMITATIONS else ""),
        )
    explanations = [_PROGRESS_REASONS[code] for code in reasons if code in _PROGRESS_REASONS]
    if not explanations:
        return "处理状态待核对", "现有记录没有可识别的进度原因；请查看技术审计。"
    return explanations[0][0], " ".join(dict.fromkeys(detail for _, detail in explanations))
