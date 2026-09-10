"""Chinese display copy for known system text; persisted audit text stays intact."""

_SYSTEM_TEXT = {
    "Immutable Runtime decision before governed post-processing.": "保存后续经验复用与企业策略应用前的初判，不覆盖原始结果。",
    (
        "Immutable Runtime decision after analysis of current evidence and retrieved context (including reviewed Memory when available), before governed directive and policy application."
    ): "基于当前证据及检索到的上下文形成初判，其中可能已使用参考经验；后续的经验结论复用和企业策略另行记录。",
    "Final governed decision after Memory, tenant policy, and optional automation policy evaluation.": "综合已审核经验、企业策略及已配置的自动化策略，形成最终采用结果；不代表外部处置已执行。",
    "Tenant policy application is disabled by operator configuration.": "企业策略未启用，保留前序研判结果。",
    "No persisted tenant policy decision is available.": "本次没有已保存的企业策略结果。",
    "No persisted tenant policy decision matches this run and environment.": "没有与本次运行及使用环境对应的企业策略结果。",
    "Conflicting reviewed Memory directives required review and blocked downstream automation.": "可直接复用的已审核经验结论存在冲突，需要人工确认，相关自动处置已暂停。",
    "An eligible reviewed Memory directive changed the effective detection state.": "符合复用条件的已审核经验调整了本次判断，前后结果均已保留。",
    "Eligible reviewed Memory directives reinforced the base detection state.": "符合复用条件的已审核经验支持初判，沿用同向结论。",
    "Reviewed Memory directives were present but none applied to this base decision.": "已召回经验，但没有满足本次直接复用结论的条件，保留初判。",
    "No eligible reviewed Memory decision directive was present.": "没有可直接复用结论的已审核经验；模型仍可能使用参考经验。",
    "No tenant disposition rule matched; retain the Runtime decision and normal review path.": "未命中企业处置规则，保留前序研判及既有复核要求。",
    "Tenant policy evaluation completed without an applicable rule.": "企业策略检查已完成，没有适用于本次告警的规则。",
}
_BLOCKER_PREFIX = "Decision-level review prevents adopting a handling plan or automatic action: "
_BLOCKER_SUFFIX = ". Original verdict and policy advice remain in prior stages."


def operator_text(value: str) -> str:
    """Localize exact system templates only, never infer or replace model advice."""
    if value.startswith(_BLOCKER_PREFIX) and value.endswith(_BLOCKER_SUFFIX):
        codes = value[len(_BLOCKER_PREFIX) : -len(_BLOCKER_SUFFIX)]
        return f"存在需要人工确认的关键问题，暂不采用处置方案或自动执行：{codes}。原始判断与策略建议保留在前序阶段。"
    return _SYSTEM_TEXT.get(value, value)
