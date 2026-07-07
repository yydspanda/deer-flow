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
        reputation_hits = _evidence_by_route(evidence, "threat_intel.ip_reputation.lookup", found_key="reputation_found")
        active_tags = _active_security_tags(evidence)
        score = _max_reputation_score(reputation_hits)
        evidence_refs = _evidence_ids(reputation_hits + active_tags) + conflict_refs
        limitations: list[str] = []
        if not reputation_hits:
            limitations.append("No positive threat-intelligence evidence was attached.")
        if not active_tags:
            limitations.append("No active authorization or maintenance tag was attached.")
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
            evidence_refs=evidence_refs,
            capability_card_refs=_merge_refs(request.capability_card_refs, ["PA-APT-001", "PA-APT-003", "PA-APT-004"]),
            skill_names=skill_names,
            recommendations=[
                "Confirm attacker, victim, impacted asset, and response target from trusted raw evidence.",
                "Use threat-intelligence and security-tag evidence as supporting evidence, not as the sole verdict.",
            ],
            limitations=limitations,
            metadata={"max_reputation_score": score, "conflict_count": len(conflict_refs)},
        )
        return _result(request, self.domain, self.handler_id, [finding])


class _EdrDomainTriageHandler:
    domain = SocDomainName.EDR
    handler_id = "soc.domain.edr.v1"

    def triage(self, request: SocDomainTriageRequest) -> SocDomainTriageResult:
        evidence = request.investigation_evidence
        skill_names = _skill_names(request.skill_context)
        process_tree_evidence = _evidence_by_route(evidence, "endpoint.process_tree.lookup", found_key="process_tree_found")
        risk_tags = sorted(_risk_tags_from_process_tree(process_tree_evidence))
        evidence_refs = _evidence_ids(process_tree_evidence)
        limitations: list[str] = []
        if not process_tree_evidence:
            limitations.append("No endpoint process-tree evidence was attached.")
        if not risk_tags:
            limitations.append("No process risk tags were present in attached endpoint evidence.")

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
            evidence_refs=evidence_refs,
            capability_card_refs=_merge_refs(request.capability_card_refs, ["PA-EDR-001", "PA-EDR-002"]),
            skill_names=skill_names,
            recommendations=[
                "Review suspicious parent-child process chain and remote network connections.",
                "If containment is needed, generate a high-risk action proposal and send it through approval.",
            ],
            limitations=limitations,
            metadata={"risk_tags": risk_tags},
        )
        return _result(request, self.domain, self.handler_id, [finding])


class _HidsDomainTriageHandler:
    domain = SocDomainName.HIDS
    handler_id = "soc.domain.hids.v1"

    def triage(self, request: SocDomainTriageRequest) -> SocDomainTriageResult:
        evidence = request.investigation_evidence
        skill_names = _skill_names(request.skill_context)
        host_context = _evidence_by_route(evidence, "host.event_context.lookup", found_key="host_event_context_found")
        active_tags = _active_security_tags(evidence)
        evidence_refs = _evidence_ids(host_context + active_tags)
        limitations: list[str] = []
        if not host_context:
            limitations.append("No HIDS host-event context evidence was attached.")
        if not active_tags:
            limitations.append("No active maintenance or authorization tag was attached.")

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
            evidence_refs=evidence_refs,
            capability_card_refs=_merge_refs(request.capability_card_refs, ["PA-HIDS-001", "PA-HIDS-003"]),
            skill_names=skill_names,
            recommendations=[
                "Check host-event context, logged-in user, command sequence, and maintenance tags together.",
                "If this is a recurring benign operation, propose a tenant memory candidate instead of editing public skills.",
            ],
            limitations=limitations,
            metadata={"active_security_tag_count": len(active_tags), "host_context_count": len(host_context)},
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
            evidence_refs=_evidence_ids(request.investigation_evidence),
            capability_card_refs=request.capability_card_refs,
            skill_names=_skill_names(request.skill_context),
            recommendations=["Collect domain-specific evidence before escalating this finding."],
            limitations=["No PA-10 domain-specific handler matched the alert source type or skill context."],
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
    return SocDomainTriageResult(
        request_id=request.request_id,
        run_id=request.run.run_id,
        alert_id=request.run.alert_id,
        domain=domain,
        handler_id=handler_id,
        findings=findings,
        evidence_ref_count=len({ref for finding in findings for ref in finding.evidence_refs}),
        metadata={
            "finding_count": len(findings),
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
) -> list[InvestigationEvidence]:
    result = [item for item in evidence if item.route == route or item.action == route]
    if found_key is not None:
        result = [item for item in result if item.result_payload.get(found_key) is True]
    return result


def _active_security_tags(evidence: list[InvestigationEvidence]) -> list[InvestigationEvidence]:
    return [item for item in _evidence_by_route(evidence, "security_tag.lookup") if item.result_payload.get("has_active") is True]


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
