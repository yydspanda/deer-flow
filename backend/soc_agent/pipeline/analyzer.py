"""Stub analysis node for Phase 1 runtime scaffolding.

This module intentionally avoids LLM calls. It gives deterministic golden
sample behavior while the runtime, contracts, trace, and CLI stabilize.
"""

from __future__ import annotations

from soc_agent.contracts import (
    AlertInput,
    AnalysisResult,
    EvidenceItem,
    ExtractedEntities,
    Verdict,
)

FALSE_POSITIVE_HINTS = ("approved", "scanner", "securityscan", "nmap", "nessus")
TRUE_POSITIVE_HINTS = ("malicious", "mimikatz", "cobalt", "ransom", "ioc", "backdoor")


def analyze_stub(alert: AlertInput, entities: ExtractedEntities) -> AnalysisResult:
    haystack = " ".join(
        value.lower()
        for value in [
            alert.rule_name or "",
            alert.process_name or "",
            alert.command_line or "",
            alert.severity or "",
            *entities.rules,
            *entities.processes,
            *entities.domains,
        ]
    )

    if any(hint in haystack for hint in FALSE_POSITIVE_HINTS):
        return AnalysisResult(
            verdict=Verdict.FALSE_POSITIVE,
            confidence=0.82,
            summary="告警命中已知扫描器或批准工具特征，Phase 1 判定为高概率误报候选。",
            evidence=[
                EvidenceItem(source="rule_name", description="规则或命令包含扫描器线索", value=alert.rule_name),
                EvidenceItem(source="entities", description="抽取到的进程实体", value=", ".join(entities.processes)),
            ],
            reason="当前证据更符合授权扫描或安全工具活动，但 Phase 1 不自动关闭告警。",
            recommended_action="review_and_close_if_approved",
        )

    if any(hint in haystack for hint in TRUE_POSITIVE_HINTS):
        return AnalysisResult(
            verdict=Verdict.TRUE_POSITIVE,
            confidence=0.9,
            summary="告警包含恶意 IOC、攻击工具或高危行为线索，Phase 1 判定为真阳性候选。",
            evidence=[
                EvidenceItem(source="rule_name", description="规则命中高危攻击线索", value=alert.rule_name),
                EvidenceItem(source="command_line", description="命令行或进程包含攻击特征", value=alert.command_line),
            ],
            reason="检测到高风险关键字，需要分析师优先复核和升级调查。",
            recommended_action="escalate_to_analyst",
        )

    return AnalysisResult(
        verdict=Verdict.UNKNOWN,
        confidence=0.45,
        summary="当前字段不足以稳定判断真伪，Phase 1 将该告警交给人工复核。",
        evidence=[
            EvidenceItem(source="alert_id", description="告警已进入固定分析流程", value=alert.alert_id),
        ],
        reason="缺少历史关联、环境知识或明确 IOC，不能可靠自动判断。",
        recommended_action="needs_human_review",
    )
