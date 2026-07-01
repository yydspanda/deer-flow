"""Stub analysis node for Phase 1 runtime scaffolding.

This module intentionally avoids LLM calls. It gives deterministic golden
sample behavior while the runtime, contracts, trace, and CLI stabilize.
"""

from __future__ import annotations

from soc_agent.contracts import (
    AnalysisResult,
    EvidenceItem,
    LLMAnalysisRequest,
    Verdict,
)

FALSE_POSITIVE_HINTS = ("approved", "scanner", "securityscan", "nmap", "nessus")
TRUE_POSITIVE_HINTS = ("malicious", "mimikatz", "cobalt", "ransom", "ioc", "backdoor")


def analyze_stub(request: LLMAnalysisRequest) -> AnalysisResult:
    detection = request.detection
    network = request.canonical_entities.network
    process = request.canonical_entities.process
    http = request.canonical_entities.http
    entities = request.extracted_entities
    context_evidence = _context_evidence(request)
    reason_suffix = _reason_suffix(request)

    haystack = " ".join(
        value.lower()
        for value in [
            detection.rule_code or "",
            detection.rule_name or "",
            detection.detection_key or "",
            detection.rule_category or "",
            request.source.source_type.value,
            request.source.source_system or "",
            request.classification.category or "",
            process.process_name or "",
            process.command_line or "",
            network.url or "",
            http.url or "",
            network.domain or "",
            http.host or "",
            request.classification.severity or "",
            *entities.rules,
            *entities.processes,
            *entities.domains,
            *entities.urls,
            *request.conflict_types,
        ]
    )

    if any(hint in haystack for hint in FALSE_POSITIVE_HINTS):
        return AnalysisResult(
            verdict=Verdict.FALSE_POSITIVE,
            confidence=0.82,
            summary="告警命中已知扫描器或批准工具特征，Phase 1 判定为高概率误报候选。",
            evidence=[
                EvidenceItem(
                    source="detection",
                    description="规则或命令包含扫描器线索",
                    value=detection.detection_key,
                ),
                EvidenceItem(source="entities", description="抽取到的进程实体", value=", ".join(entities.processes)),
                *context_evidence,
            ],
            reason=f"当前证据更符合授权扫描或安全工具活动，但 Phase 1 不自动关闭告警。{reason_suffix}",
            recommended_action="review_and_close_if_approved",
        )

    if any(hint in haystack for hint in TRUE_POSITIVE_HINTS):
        return AnalysisResult(
            verdict=Verdict.TRUE_POSITIVE,
            confidence=0.9,
            summary="告警包含恶意 IOC、攻击工具或高危行为线索，Phase 1 判定为真阳性候选。",
            evidence=[
                EvidenceItem(
                    source="detection",
                    description="规则命中高危攻击线索",
                    value=detection.detection_key,
                ),
                EvidenceItem(source="command_line", description="命令行或进程包含攻击特征", value=process.command_line),
                *context_evidence,
            ],
            reason=f"检测到高风险关键字，需要分析师优先复核和升级调查。{reason_suffix}",
            recommended_action="escalate_to_analyst",
        )

    return AnalysisResult(
        verdict=Verdict.UNKNOWN,
        confidence=0.45,
        summary="当前字段不足以稳定判断真伪，Phase 1 将该告警交给人工复核。",
        evidence=[
            EvidenceItem(source="alert_id", description="告警已进入固定分析流程", value=request.alert_id),
            *context_evidence,
        ],
        reason=f"缺少历史关联、环境知识或明确 IOC，不能可靠自动判断。{reason_suffix}",
        recommended_action="needs_human_review",
    )


def _context_evidence(request: LLMAnalysisRequest) -> list[EvidenceItem]:
    evidence: list[EvidenceItem] = []
    if request.conflict_count:
        evidence.append(
            EvidenceItem(
                source="fact_reconstruction",
                description="事实重建发现字段冲突",
                value=", ".join(request.conflict_types),
            )
        )
    fallback_warnings = [warning for warning in request.warnings if "fallback" in warning.lower()]
    if fallback_warnings:
        evidence.append(
            EvidenceItem(
                source="fact_reconstruction",
                description="事实重建使用低可信 fallback",
                value="; ".join(fallback_warnings),
            )
        )
    return evidence


def _reason_suffix(request: LLMAnalysisRequest) -> str:
    notes: list[str] = []
    if request.conflict_count:
        notes.append(f"事实重建发现 {request.conflict_count} 个字段/角色冲突")
    if any("fallback" in warning.lower() for warning in request.warnings):
        notes.append("当前主证据使用低可信 fallback")
    if not notes:
        return ""
    return " " + "；".join(notes) + "。"
