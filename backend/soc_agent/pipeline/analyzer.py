"""Deterministic stub analysis node for local tests and replay.

This module intentionally avoids LLM calls. It provides stable golden-sample
behavior without claiming production model quality.
"""

from __future__ import annotations

from soc_agent.contracts import (
    AnalysisNodeOutput,
    AnalysisResult,
    EvidenceItem,
    LLMAnalysisRequest,
    TriageActivityStage,
    TriageScenarioAssessment,
    TriageScenarioOrigin,
    Verdict,
)

FALSE_POSITIVE_HINTS = ("approved", "scanner", "securityscan", "nmap", "nessus")
TRUE_POSITIVE_HINTS = ("malicious", "mimikatz", "cobalt", "ransom", "ioc", "backdoor")
STUB_ANALYZER_MODEL_NAME = "stub"
STUB_ANALYZER_PROMPT_VERSION = "stub"


class StubLLMAnalyzer:
    """Deterministic analyzer used when the LLM feature path is disabled."""

    step_name = "analyze_stub"
    model_name = STUB_ANALYZER_MODEL_NAME
    prompt_version = STUB_ANALYZER_PROMPT_VERSION

    def analyze(self, request: LLMAnalysisRequest) -> AnalysisNodeOutput:
        return AnalysisNodeOutput(
            analysis=analyze_stub(request),
            model_name=STUB_ANALYZER_MODEL_NAME,
            prompt_version=STUB_ANALYZER_PROMPT_VERSION,
            metadata={"analyzer": "stub"},
        )


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
        evidence = [
            EvidenceItem(
                source="detection",
                description="规则或命令包含扫描器线索",
                value=detection.detection_key,
            )
        ]
        if entities.processes:
            evidence.append(
                EvidenceItem(
                    source="entities",
                    description="抽取到的进程实体",
                    value=", ".join(entities.processes),
                )
            )
        evidence.extend(context_evidence)
        return AnalysisResult(
            verdict=Verdict.FALSE_POSITIVE,
            confidence=0.82,
            summary="告警命中已知扫描器或批准工具特征，deterministic stub 判定为高概率误报候选。",
            evidence=evidence,
            scenario_assessments=[
                TriageScenarioAssessment(
                    scenario_name="授权扫描或安全工具活动",
                    scenario_key="authorized_security_activity",
                    is_primary=True,
                    origin=TriageScenarioOrigin.INFERRED,
                    confidence=0.72,
                    activity_stage=TriageActivityStage.DETECTION_HIT,
                    evidence_indices=[0],
                    rationale="规则或实体文本命中扫描器、批准工具等启发式线索。",
                    competing_explanations=["未经授权的扫描或攻击工具伪装"],
                )
            ],
            evidence_gaps=["缺少带有效期、范围和来源的授权活动事实。"],
            manual_checks=["核对该工具、源资产、目标范围和事件时间是否落在有效授权窗口内。"],
            reason=f"当前证据更符合授权扫描或安全工具活动，但 deterministic stub 不自动关闭告警。{reason_suffix}",
            recommended_action="review_and_close_if_approved",
        )

    if any(hint in haystack for hint in TRUE_POSITIVE_HINTS):
        evidence = [
            EvidenceItem(
                source="detection",
                description="规则命中高危攻击线索",
                value=detection.detection_key,
            )
        ]
        if process.command_line:
            evidence.append(
                EvidenceItem(
                    source="command_line",
                    description="命令行或进程包含攻击特征",
                    value=process.command_line,
                )
            )
        evidence.extend(context_evidence)
        return AnalysisResult(
            verdict=Verdict.TRUE_POSITIVE,
            confidence=0.9,
            summary="告警包含恶意 IOC、攻击工具或高危行为线索，deterministic stub 判定为真阳性候选。",
            evidence=evidence,
            scenario_assessments=[
                TriageScenarioAssessment(
                    scenario_name="高风险攻击行为或恶意工具活动",
                    scenario_key="high_risk_security_behavior",
                    is_primary=True,
                    origin=TriageScenarioOrigin.INFERRED,
                    confidence=0.8,
                    activity_stage=TriageActivityStage.ATTEMPT_OBSERVED,
                    evidence_indices=[0],
                    rationale="规则、IOC、进程或命令文本命中高风险启发式线索。",
                    competing_explanations=["安全测试工具、误标 IOC 或合法运维行为"],
                )
            ],
            evidence_gaps=["缺少独立的执行结果、主机影响或业务影响证据。"],
            manual_checks=["核对进程树、网络连接和资产侧结果，确认是否产生实际效果或影响。"],
            reason=f"检测到高风险关键字，需要分析师优先复核和升级调查。{reason_suffix}",
            recommended_action="escalate_to_analyst",
        )

    return AnalysisResult(
        verdict=Verdict.UNKNOWN,
        confidence=0.45,
        summary="当前字段不足以稳定判断真伪，deterministic stub 将该告警交给人工复核。",
        evidence=[
            EvidenceItem(source="alert_id", description="告警已进入固定分析流程", value=request.alert_id),
            *context_evidence,
        ],
        evidence_gaps=["缺少可稳定识别场景及判断真伪的行为、历史或环境证据。"],
        manual_checks=["补查原始行为上下文、资产归属和同时间窗相关事件后重新研判。"],
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
