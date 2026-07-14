"""Deterministic SOC domain triage handlers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from soc_agent.contracts import (
    AlertSourceType,
    AnalysisRun,
    InvestigationEvidence,
    SocDomainFinding,
    SocDomainFindingDisposition,
    SocDomainFindingSeverity,
    SocDomainName,
    SocDomainTriageRequest,
    SocDomainTriageResult,
    SocSkillContext,
)
from soc_agent.domain.evidence import evidence_is_mocked, successful_evidence
from soc_agent.domain.scenarios import (
    evidence_profile_for_request as _evidence_profile_for_request,
)
from soc_agent.domain.scenarios import (
    finding_conclusion as _finding_conclusion,
)
from soc_agent.domain.scenarios import (
    scenario_findings as _scenario_findings,
)


class SocDomainTriageHandler(Protocol):
    """One bounded domain handler that only returns findings."""

    domain: SocDomainName
    handler_id: str

    def triage(self, request: SocDomainTriageRequest) -> SocDomainTriageResult: ...


class SocDomainTriageService:
    """Route one analysis run to a bounded deterministic domain handler."""

    def __init__(self, handlers: list[SocDomainTriageHandler] | None = None) -> None:
        self._handlers = {handler.domain: handler for handler in (handlers or _default_handlers())}

    def triage(self, request: SocDomainTriageRequest) -> SocDomainTriageResult:
        domain = request.domain or _infer_domain(request.run, _effective_skill_context(request))
        handler = self._handlers.get(domain) or _GenericDomainTriageHandler()
        effective_request = request.model_copy(
            update={
                "domain": domain,
                "skill_context": _effective_skill_context(request),
            }
        )
        return handler.triage(effective_request)


class _AptDomainTriageHandler:
    domain = SocDomainName.APT
    handler_id = "soc.domain.apt.v1"

    def triage(self, request: SocDomainTriageRequest) -> SocDomainTriageResult:
        evidence = request.investigation_evidence
        skill_names = _skill_names(request.skill_context)
        conflict_refs = _conflict_refs(request.run)
        reputation_evidence = _evidence_by_route(
            evidence,
            "threat_intel.ip_reputation.lookup",
            found_key="reputation_found",
            include_mocked=True,
        )
        tag_evidence = _active_security_tags(evidence, include_mocked=True)
        reputation_hits = [item for item in reputation_evidence if not evidence_is_mocked(item)]
        active_tags = [item for item in tag_evidence if not evidence_is_mocked(item)]
        mocked_evidence = [item for item in [*reputation_evidence, *tag_evidence] if evidence_is_mocked(item)]
        score = _max_reputation_score(reputation_hits)
        evidence_refs = _evidence_ids(reputation_evidence + tag_evidence) + conflict_refs
        limitations: list[str] = []
        if not reputation_hits:
            limitations.append("No positive threat-intelligence evidence was attached.")
        if not active_tags:
            limitations.append("No active authorization or maintenance tag was attached.")
        if mocked_evidence:
            limitations.append("Mock investigation evidence is visible for flow validation but does not raise finding confidence.")
        if conflict_refs:
            limitations.append("Direction and role facts contain conflicts; raw evidence needs analyst review.")

        severity = SocDomainFindingSeverity.HIGH if score >= 70 or conflict_refs else SocDomainFindingSeverity.MEDIUM
        disposition = SocDomainFindingDisposition.SUSPICIOUS if reputation_hits else SocDomainFindingDisposition.NEEDS_MORE_EVIDENCE
        confidence = 0.72 if reputation_hits else 0.55
        finding = SocDomainFinding(
            domain=self.domain,
            title="APT/network direction and reputation triage",
            summary=("Network/APT evidence should be reviewed with raw direction reconstruction, threat-intelligence results, and authorization tags before choosing a response target."),
            severity=severity,
            disposition=disposition,
            confidence=confidence,
            evidence_profile=_evidence_profile_for_request(
                request,
                used_sources=["raw_log", "threat_intel", "security_tag"],
                gaps=limitations,
            ),
            current_conclusion=_finding_conclusion(
                "当前结论：APT/network 告警需要结合原始方向、威胁情报、授权标签和历史反馈复核；不建议仅凭上游攻击方向字段自动处置。",
                risk_level=severity,
                confidence=confidence,
                recommended_action="manual_review",
                recommended_queue="network_review",
                rationale=[
                    "APT/network 告警可能存在攻击方向或角色冲突。",
                    "威胁情报和授权标签只能作为证据输入，不能单独决定 verdict。",
                ],
            ),
            evidence_refs=evidence_refs,
            capability_card_refs=_merge_refs(request.capability_card_refs, ["PA-APT-001", "PA-APT-003", "PA-APT-004"]),
            skill_names=skill_names,
            recommendations=[
                "Confirm attacker, victim, impacted asset, and response target from trusted raw evidence.",
                "Use threat-intelligence and security-tag evidence as supporting evidence, not as the sole verdict.",
            ],
            limitations=limitations,
            human_checklist=[
                "确认攻击方、受害方、影响资产和处置目标是否来自可信 raw evidence。",
                "核对上游攻击方向字段是否与原始连接方向冲突。",
                "结合威胁情报、历史相似预警和外部处置理由判断是否为重复误报。",
            ],
            metadata={
                "max_reputation_score": score,
                "conflict_count": len(conflict_refs),
                "mock_evidence_count": len(mocked_evidence),
            },
        )
        return _result(request, self.domain, self.handler_id, [finding])


class _EdrDomainTriageHandler:
    domain = SocDomainName.EDR
    handler_id = "soc.domain.edr.v1"

    def triage(self, request: SocDomainTriageRequest) -> SocDomainTriageResult:
        evidence = request.investigation_evidence
        skill_names = _skill_names(request.skill_context)
        all_process_tree_evidence = _evidence_by_route(
            evidence,
            "endpoint.process_tree.lookup",
            found_key="process_tree_found",
            include_mocked=True,
        )
        process_tree_evidence = [item for item in all_process_tree_evidence if not evidence_is_mocked(item)]
        risk_tags = sorted(_risk_tags_from_process_tree(process_tree_evidence))
        evidence_refs = _evidence_ids(all_process_tree_evidence)
        limitations: list[str] = []
        if not process_tree_evidence:
            limitations.append("No endpoint process-tree evidence was attached.")
        if not risk_tags:
            limitations.append("No process risk tags were present in attached endpoint evidence.")
        if len(all_process_tree_evidence) > len(process_tree_evidence):
            limitations.append("Mock process-tree evidence is visible for flow validation but does not raise finding confidence.")

        severe_tags = {"credential_access", "lateral_movement_candidate", "remote_registry", "persistence"}
        severity = SocDomainFindingSeverity.HIGH if severe_tags.intersection(risk_tags) else SocDomainFindingSeverity.MEDIUM
        disposition = SocDomainFindingDisposition.SUSPICIOUS if risk_tags else SocDomainFindingDisposition.NEEDS_MORE_EVIDENCE
        confidence = 0.75 if risk_tags else 0.5
        finding = SocDomainFinding(
            domain=self.domain,
            title="EDR process-tree triage",
            summary=("Endpoint evidence should be reviewed through parent-child process chain, command line, user context, and network activity before deciding containment."),
            severity=severity,
            disposition=disposition,
            confidence=confidence,
            evidence_profile=_evidence_profile_for_request(
                request,
                used_sources=["raw_log", "endpoint_process_tree", "similar_alerts", "confirmed_memory"],
                gaps=limitations,
            ),
            current_conclusion=_finding_conclusion(
                "当前结论：EDR 告警需要结合进程树、命令行、用户上下文、历史相似处置和 memory 判断；证据不足时仍应给出复核路径，不直接中止。",
                risk_level=severity,
                confidence=confidence,
                recommended_action="manual_review",
                recommended_queue="endpoint_review",
                rationale=[
                    "进程树和命令行是 endpoint 场景的核心证据。",
                    "历史相似处置和 confirmed memory 应作为常规研判输入，而不是工具缺失后的降级替代。",
                ],
            ),
            evidence_refs=evidence_refs,
            capability_card_refs=_merge_refs(request.capability_card_refs, ["PA-EDR-001", "PA-EDR-002"]),
            skill_names=skill_names,
            recommendations=[
                "Review suspicious parent-child process chain and remote network connections.",
                "If containment is needed, generate a high-risk action proposal and send it through approval.",
            ],
            limitations=limitations,
            human_checklist=[
                "确认父进程、子进程、命令行和执行账号是否符合业务预期。",
                "核对同主机、同用户、同 rule 的历史相似预警处置结论。",
                "若需要隔离或封禁，先生成高风险处置 proposal 并走审批。",
            ],
            metadata={
                "risk_tags": risk_tags,
                "mock_evidence_count": len(all_process_tree_evidence) - len(process_tree_evidence),
            },
        )
        return _result(request, self.domain, self.handler_id, [finding])


class _HidsDomainTriageHandler:
    domain = SocDomainName.HIDS
    handler_id = "soc.domain.hids.v1"

    def triage(self, request: SocDomainTriageRequest) -> SocDomainTriageResult:
        evidence = request.investigation_evidence
        skill_names = _skill_names(request.skill_context)
        all_host_context = _evidence_by_route(
            evidence,
            "host.event_context.lookup",
            found_key="host_event_context_found",
            include_mocked=True,
        )
        all_active_tags = _active_security_tags(evidence, include_mocked=True)
        host_context = [item for item in all_host_context if not evidence_is_mocked(item)]
        active_tags = [item for item in all_active_tags if not evidence_is_mocked(item)]
        mocked_evidence = [item for item in [*all_host_context, *all_active_tags] if evidence_is_mocked(item)]
        evidence_refs = _evidence_ids(all_host_context + all_active_tags)
        limitations: list[str] = []
        if not host_context:
            limitations.append("No HIDS host-event context evidence was attached.")
        if not active_tags:
            limitations.append("No active maintenance or authorization tag was attached.")
        if mocked_evidence:
            limitations.append("Mock host/tag evidence is visible for flow validation but does not raise finding confidence.")

        disposition = SocDomainFindingDisposition.BENIGN_AUTHORIZED_CANDIDATE if active_tags else SocDomainFindingDisposition.NEEDS_MORE_EVIDENCE
        confidence = 0.68 if active_tags and host_context else 0.5
        severity = SocDomainFindingSeverity.LOW if active_tags else SocDomainFindingSeverity.MEDIUM
        finding = SocDomainFinding(
            domain=self.domain,
            title="HIDS host-event context triage",
            summary=("Host-event context and security tags can explain some HIDS events, but this remains a reviewable finding until analyst confirmation."),
            severity=severity,
            disposition=disposition,
            confidence=confidence,
            evidence_profile=_evidence_profile_for_request(
                request,
                used_sources=["raw_log", "host_event_context", "security_tag", "similar_alerts", "confirmed_memory"],
                gaps=limitations,
            ),
            current_conclusion=_finding_conclusion(
                "当前结论：HIDS 主机事件需要结合事件上下文、登录账号、维护标签、历史相似处置和 memory 复核；存在授权标签时可作为误报/授权运维候选。",
                risk_level=severity,
                confidence=confidence,
                recommended_action="manual_review",
                recommended_queue="host_review",
                rationale=[
                    "HIDS 事件常受授权运维、批处理和主机上下文影响。",
                    "授权标签只能形成 benign candidate，仍需分析师确认后才能沉淀 memory。",
                ],
            ),
            evidence_refs=evidence_refs,
            capability_card_refs=_merge_refs(request.capability_card_refs, ["PA-HIDS-001", "PA-HIDS-003"]),
            skill_names=skill_names,
            recommendations=[
                "Check host-event context, logged-in user, command sequence, and maintenance tags together.",
                "If this is a recurring benign operation, propose a tenant memory candidate instead of editing public skills.",
            ],
            limitations=limitations,
            human_checklist=[
                "确认命令、登录账号、主机用途和执行时间是否符合维护窗口。",
                "核对安全标签是否仍在有效期内，避免过期白名单污染判断。",
                "如果分析师确认是重复授权行为，生成 tenant memory candidate 而不是修改 public skill。",
            ],
            metadata={
                "active_security_tag_count": len(active_tags),
                "host_context_count": len(host_context),
                "mock_evidence_count": len(mocked_evidence),
            },
        )
        return _result(request, self.domain, self.handler_id, [finding])


class _GenericDomainTriageHandler:
    domain = SocDomainName.GENERIC
    handler_id = "soc.domain.generic.v1"

    def triage(self, request: SocDomainTriageRequest) -> SocDomainTriageResult:
        finding = SocDomainFinding(
            domain=self.domain,
            title="Generic SOC domain triage",
            summary="No specific APT, EDR, or HIDS domain handler matched this alert.",
            severity=SocDomainFindingSeverity.INFO,
            disposition=SocDomainFindingDisposition.NEEDS_MORE_EVIDENCE,
            confidence=0.3,
            evidence_profile=_evidence_profile_for_request(
                request,
                used_sources=["raw_log", "similar_alerts", "external_feedback", "confirmed_memory"],
                gaps=["No specific domain handler matched this alert."],
            ),
            current_conclusion=_finding_conclusion(
                "当前结论：未匹配到专用领域 handler，但仍应基于 raw log、历史相似预警、外部处置反馈和 confirmed memory 给出当前复核结论。",
                risk_level=SocDomainFindingSeverity.INFO,
                confidence=0.3,
                recommended_action="manual_review",
                recommended_queue="soc_review",
                rationale=["未知来源不代表无法研判；先使用通用证据融合，再决定是否补专用 handler。"],
            ),
            evidence_refs=_evidence_ids(request.investigation_evidence),
            capability_card_refs=request.capability_card_refs,
            skill_names=_skill_names(request.skill_context),
            recommendations=["Collect domain-specific evidence before escalating this finding."],
            limitations=["No PA-10 domain-specific handler matched the alert source type or skill context."],
            human_checklist=[
                "确认数据源、rule name、category 和 raw message 是否能映射到已有通用场景。",
                "检索同 rule、同实体、同 vendor scenario 的历史相似预警。",
                "必要时把新模式整理成 capability card 或 pending memory candidate。",
            ],
        )
        return _result(request, self.domain, self.handler_id, [finding])


def _default_handlers() -> list[SocDomainTriageHandler]:
    return [_AptDomainTriageHandler(), _EdrDomainTriageHandler(), _HidsDomainTriageHandler(), _GenericDomainTriageHandler()]


def _infer_domain(run: AnalysisRun, skill_context: SocSkillContext) -> SocDomainName:
    source_type = run.normalization_report.source_type if run.normalization_report is not None else AlertSourceType.UNKNOWN
    if source_type is AlertSourceType.EDR:
        return SocDomainName.EDR
    if source_type is AlertSourceType.HIDS:
        return SocDomainName.HIDS
    if source_type in {AlertSourceType.NDR, AlertSourceType.NIDS, AlertSourceType.THREAT_INTEL}:
        return SocDomainName.APT
    if source_type in {AlertSourceType.WAF, AlertSourceType.F5}:
        return SocDomainName.WAF_F5
    names = set(_skill_names(skill_context))
    if "soc-endpoint-triage" in names:
        return SocDomainName.EDR
    if "soc-network-apt-triage" in names:
        return SocDomainName.APT
    if "soc-waf-f5-triage" in names:
        return SocDomainName.WAF_F5
    return SocDomainName.GENERIC


def _effective_skill_context(request: SocDomainTriageRequest) -> SocSkillContext:
    if request.skill_context.selected_skills:
        return request.skill_context
    if request.run.llm_analysis_request is not None:
        return request.run.llm_analysis_request.skill_context
    return request.skill_context


def _result(
    request: SocDomainTriageRequest,
    domain: SocDomainName,
    handler_id: str,
    findings: list[SocDomainFinding],
) -> SocDomainTriageResult:
    all_findings = [*findings, *_scenario_findings(request, domain)]
    return SocDomainTriageResult(
        request_id=request.request_id,
        run_id=request.run.run_id,
        alert_id=request.run.alert_id,
        domain=domain,
        handler_id=handler_id,
        findings=all_findings,
        evidence_ref_count=len({ref for finding in all_findings for ref in finding.evidence_refs}),
        metadata={
            "finding_count": len(all_findings),
            "scenario_finding_count": len(all_findings) - len(findings),
            "handler_output_only": True,
            "writes_db": False,
            "executes_actions": False,
        },
    )


def _skill_names(skill_context: SocSkillContext) -> list[str]:
    return [item.skill_name for item in skill_context.selected_skills]


def _conflict_refs(run: AnalysisRun) -> list[str]:
    if run.fact_reconstruction is None:
        return []
    return [f"conflict:{item.conflict_type}" for item in run.fact_reconstruction.conflict_reports]


def _evidence_by_route(
    evidence: list[InvestigationEvidence],
    route: str,
    *,
    found_key: str | None = None,
    include_mocked: bool = False,
) -> list[InvestigationEvidence]:
    result = [item for item in successful_evidence(evidence, include_mocked=include_mocked) if item.route == route or item.action == route]
    if found_key is not None:
        result = [item for item in result if item.result_payload.get(found_key) is True]
    return result


def _active_security_tags(
    evidence: list[InvestigationEvidence],
    *,
    include_mocked: bool = False,
) -> list[InvestigationEvidence]:
    return [
        item
        for item in _evidence_by_route(
            evidence,
            "security_tag.lookup",
            include_mocked=include_mocked,
        )
        if item.result_payload.get("has_active") is True
    ]


def _evidence_ids(evidence: list[InvestigationEvidence]) -> list[str]:
    return [item.evidence_id for item in evidence]


def _max_reputation_score(evidence: list[InvestigationEvidence]) -> int:
    scores: list[int] = []
    for item in evidence:
        reputation = item.result_payload.get("reputation")
        if isinstance(reputation, Mapping):
            score = reputation.get("score")
            if isinstance(score, int):
                scores.append(score)
    return max(scores, default=0)


def _risk_tags_from_process_tree(evidence: list[InvestigationEvidence]) -> set[str]:
    risk_tags: set[str] = set()
    for item in evidence:
        process_tree = item.result_payload.get("process_tree")
        if not isinstance(process_tree, Mapping):
            continue
        processes = process_tree.get("processes")
        if not isinstance(processes, list):
            continue
        for process in processes:
            if not isinstance(process, Mapping):
                continue
            tags = process.get("risk_tags")
            if isinstance(tags, list):
                risk_tags.update(str(tag) for tag in tags if tag)
    return risk_tags


def _merge_refs(existing: list[str], additions: list[str]) -> list[str]:
    merged: list[str] = []
    for item in [*existing, *additions]:
        if item not in merged:
            merged.append(item)
    return merged
