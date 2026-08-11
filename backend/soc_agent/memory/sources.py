"""Factories that turn SOC workflow outputs into reviewable memory candidates."""

from __future__ import annotations

import hashlib
from typing import Protocol

from soc_agent.contracts import (
    AlertInput,
    AnalysisRun,
    CorrectionRecord,
    EntrySurface,
    ReviewNoteCommand,
    ReviewNoteOrigin,
    ReviewQueueItem,
    ServiceRequestContext,
    SocDomainFinding,
    SocDomainFindingDisposition,
    SocDomainTriageResult,
    SocMemoryCandidate,
    SocMemoryCandidateCreateCommand,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryDecisionImpact,
    SocMemoryTargetArtifact,
    Verdict,
)
from soc_agent.normalizers import normalize_alert_payload


class MemoryCandidateProposer(Protocol):
    """Minimal protocol implemented by SocMemoryService."""

    def propose_candidate(
        self,
        command: SocMemoryCandidateCreateCommand,
        *,
        context: ServiceRequestContext | None = None,
    ) -> SocMemoryCandidate: ...


class SocMemoryCandidateSourceBridge:
    """Create candidate memory through SocMemoryService from stable SOC sources."""

    def __init__(self, memory_service: MemoryCandidateProposer) -> None:
        self._memory_service = memory_service

    def propose_from_correction(
        self,
        run: AnalysisRun,
        correction: CorrectionRecord,
        *,
        queue_item: ReviewQueueItem | None = None,
        context: ServiceRequestContext | None = None,
    ) -> SocMemoryCandidate:
        return self._memory_service.propose_candidate(
            memory_candidate_command_from_correction(run, correction, queue_item=queue_item),
            context=context,
        )

    def propose_from_domain_finding(
        self,
        result: SocDomainTriageResult,
        finding: SocDomainFinding,
        *,
        queue_id: str | None = None,
        tenant_id: str | None = None,
        analyst_feedback: str | None = None,
        context: ServiceRequestContext | None = None,
    ) -> SocMemoryCandidate:
        return self._memory_service.propose_candidate(
            memory_candidate_command_from_domain_finding(
                result,
                finding,
                queue_id=queue_id,
                tenant_id=tenant_id,
                analyst_feedback=analyst_feedback,
            ),
            context=context,
        )

    def propose_from_domain_triage_result(
        self,
        result: SocDomainTriageResult,
        *,
        queue_id: str | None = None,
        tenant_id: str | None = None,
        analyst_feedback: str | None = None,
        context: ServiceRequestContext | None = None,
    ) -> list[SocMemoryCandidate]:
        return [
            self.propose_from_domain_finding(
                result,
                finding,
                queue_id=queue_id,
                tenant_id=tenant_id,
                analyst_feedback=analyst_feedback,
                context=context,
            )
            for finding in result.findings
        ]

    def propose_from_review_note(
        self,
        run: AnalysisRun,
        command: ReviewNoteCommand,
        *,
        queue_item: ReviewQueueItem,
        context: ServiceRequestContext | None = None,
    ) -> SocMemoryCandidate:
        return self._memory_service.propose_candidate(
            memory_candidate_command_from_review_note(
                run,
                command,
                queue_item=queue_item,
                source_surface=context.actor.surface if context is not None else None,
            ),
            context=context,
        )


def memory_candidate_command_from_correction(
    run: AnalysisRun,
    correction: CorrectionRecord,
    *,
    queue_item: ReviewQueueItem | None = None,
) -> SocMemoryCandidateCreateCommand:
    """Build a pending candidate from an analyst or external correction."""

    alert = _normalized_alert(run)
    queue_id = queue_item.queue_id if queue_item is not None else None
    evidence_refs = _base_evidence_refs(run, queue_id=queue_id)
    evidence_refs.insert(0, f"correction:{correction.correction_id}")
    evidence_refs.extend(_correction_evidence_refs(correction))
    content = _correction_content(run, correction)
    same_verdict = correction.previous_verdict is correction.corrected_verdict
    summary_prefix = "Review confirmation" if same_verdict else "Correction feedback"
    return SocMemoryCandidateCreateCommand(
        candidate_type=_candidate_type_for_correction(correction.corrected_verdict),
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        summary=f"{summary_prefix} for {run.run_id}: {correction.corrected_verdict.value}",
        content=content,
        tenant_scope=_tenant_scope(alert),
        tenant_id=alert.tenant_id if alert is not None else None,
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.CORRECTION,
            source_id=correction.correction_id,
            run_id=run.run_id,
            alert_id=run.alert_id,
            queue_id=queue_id,
            correction_id=correction.correction_id,
            metadata={
                "previous_verdict": correction.previous_verdict.value if correction.previous_verdict is not None else None,
                "corrected_verdict": correction.corrected_verdict.value,
                "actor_id": correction.actor.actor_id,
                "actor_surface": correction.actor.surface.value,
            },
        ),
        evidence_refs=_dedupe(evidence_refs),
        validity=SocMemoryCandidateValidity(notes="Correction feedback must be reviewed before it becomes reusable SOC memory."),
        idempotency_key=f"memory_candidate:correction:{correction.correction_id}",
        confidence=_correction_confidence(correction),
        facets={
            **_run_facets(run, alert=alert),
            "candidate_source": ["correction"],
            "corrected_verdict": [correction.corrected_verdict.value],
            **({"previous_verdict": [correction.previous_verdict.value]} if correction.previous_verdict is not None else {}),
        },
        decision_impact=_decision_impact_for_correction(correction.corrected_verdict),
        review_owner="soc_analyst",
        labels=["correction", "candidate-only", correction.corrected_verdict.value],
        metadata={
            "runtime_decision_allowed": False,
            "source": "correction",
            "correction_id": correction.correction_id,
            "correction_reason_length": len(correction.reason),
        },
    )


def memory_candidate_command_from_domain_finding(
    result: SocDomainTriageResult,
    finding: SocDomainFinding,
    *,
    queue_id: str | None = None,
    tenant_id: str | None = None,
    analyst_feedback: str | None = None,
) -> SocMemoryCandidateCreateCommand:
    """Build a pending candidate from a bounded domain/scenario finding."""

    stable_key = _stable_domain_finding_key(result, finding)
    feedback = _normalize_feedback(analyst_feedback)
    evidence_refs = [
        f"domain_finding:{stable_key}",
        f"domain_triage:{result.request_id}",
        f"run:{result.run_id}",
        f"alert:{result.alert_id}",
        *(f"review_queue:{queue_id}" for _ in [queue_id] if queue_id),
        *finding.evidence_refs,
    ]
    return SocMemoryCandidateCreateCommand(
        candidate_type=_candidate_type_for_finding(finding),
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        summary=f"{finding.domain.value} finding: {finding.title}",
        content=_domain_finding_content(finding, analyst_feedback=feedback),
        tenant_scope=tenant_id or "global",
        tenant_id=tenant_id,
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.DOMAIN_FINDING,
            source_id=f"domain_finding:{stable_key}",
            run_id=result.run_id,
            alert_id=result.alert_id,
            queue_id=queue_id,
            metadata={
                "finding_id": finding.finding_id,
                "handler_id": result.handler_id,
                "domain": result.domain.value,
                "disposition": finding.disposition.value,
                "severity": finding.severity.value,
            },
        ),
        evidence_refs=_dedupe(evidence_refs),
        validity=SocMemoryCandidateValidity(notes="Domain/scenario finding is analyst context until reviewed as reusable memory."),
        idempotency_key=f"memory_candidate:domain_finding:{stable_key}",
        confidence=finding.confidence,
        facets={
            "candidate_source": ["domain_finding"],
            "domain": [finding.domain.value],
            **({"scenario_key": [finding.scenario_key]} if finding.scenario_key else {}),
            **({"scenario_name": [finding.scenario_name]} if finding.scenario_name else {}),
            "handler_id": [result.handler_id],
            "disposition": [finding.disposition.value],
            "severity": [finding.severity.value],
            "alert_id": [result.alert_id],
            "run_id": [result.run_id],
            **({"queue_id": [queue_id]} if queue_id else {}),
            **({"skill": finding.skill_names} if finding.skill_names else {}),
            **({"capability_card": finding.capability_card_refs} if finding.capability_card_refs else {}),
            **({"feedback_source": ["analyst"]} if feedback else {}),
        },
        decision_impact=SocMemoryDecisionImpact.REVIEW_HINT,
        review_owner="soc_analyst",
        labels=["domain-finding", "candidate-only", finding.domain.value, finding.disposition.value, *(["analyst-feedback"] if feedback else [])],
        metadata={
            "runtime_decision_allowed": False,
            "source": "domain_finding",
            "finding_id": finding.finding_id,
            "handler_id": result.handler_id,
            "recommendation_count": len(finding.recommendations),
            "limitation_count": len(finding.limitations),
            "analyst_feedback_present": bool(feedback),
            **({"analyst_feedback_length": len(feedback)} if feedback else {}),
        },
    )


def memory_candidate_command_from_review_note(
    run: AnalysisRun,
    command: ReviewNoteCommand,
    *,
    queue_item: ReviewQueueItem,
    source_surface: EntrySurface | None = None,
) -> SocMemoryCandidateCreateCommand:
    """Build a pending candidate from a free-form analyst review note."""

    alert = _normalized_alert(run)
    accepted_lead_agent = command.origin is ReviewNoteOrigin.ACCEPTED_LEAD_AGENT_CONCLUSION
    stable_key = _stable_review_note_key(run, command, queue_item=queue_item)
    evidence_refs = _review_note_evidence_refs(run, command, queue_item=queue_item)
    facets = {
        **_run_facets(run, alert=alert),
        "candidate_source": ["review_note"],
        "review_note_origin": [command.origin.value],
        "queue_id": [queue_item.queue_id],
        **({"scenario_key": [command.scenario_key]} if command.scenario_key else {}),
        **({"domain": [command.domain.value]} if command.domain is not None else {}),
        **({"finding_id": [command.finding_id]} if command.finding_id else {}),
    }
    return SocMemoryCandidateCreateCommand(
        candidate_type=(SocMemoryCandidateType.DETECTION_LESSON if accepted_lead_agent else SocMemoryCandidateType.PROCEDURE),
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        summary=(f"Analyst-accepted Lead Agent conclusion for {run.run_id}" if accepted_lead_agent else f"Review note for {run.run_id}"),
        content=_review_note_content(run, command, queue_item=queue_item),
        tenant_scope=_tenant_scope(alert),
        tenant_id=alert.tenant_id if alert is not None else None,
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.REVIEW_NOTE,
            source_surface=source_surface,
            source_id=f"review_note:{stable_key}",
            run_id=run.run_id,
            alert_id=run.alert_id,
            queue_id=queue_item.queue_id,
            thread_id=command.source_thread_id,
            message_id=command.source_message_id,
            metadata={
                **command.metadata,
                "origin": command.origin.value,
                "scenario_key": command.scenario_key,
                "domain": command.domain.value if command.domain is not None else None,
                "finding_id": command.finding_id,
                "note_length": len(command.note),
                "acceptance_reason_length": len(command.acceptance_reason) if command.acceptance_reason is not None else None,
            },
        ),
        evidence_refs=_dedupe(evidence_refs),
        validity=SocMemoryCandidateValidity(
            notes=("Analyst-accepted Lead Agent conclusion must be reviewed before it becomes reusable SOC memory." if accepted_lead_agent else "Review note must be confirmed before it becomes reusable SOC memory.")
        ),
        idempotency_key=f"memory_candidate:review_note:{stable_key}",
        confidence=command.confidence,
        facets=facets,
        decision_impact=SocMemoryDecisionImpact.REVIEW_HINT,
        review_owner="soc_analyst",
        labels=[
            "review-note",
            "candidate-only",
            *(["lead-agent-accepted"] if accepted_lead_agent else []),
            *(["scenario-feedback"] if command.scenario_key else []),
        ],
        metadata={
            "runtime_decision_allowed": False,
            "source": "review_note",
            "review_note_origin": command.origin.value,
            "note_length": len(command.note),
            "human_acceptance_required": True,
            "lead_agent_output_auto_persisted": False,
            "scenario_key_present": bool(command.scenario_key),
            "finding_id_present": bool(command.finding_id),
        },
    )


def _normalized_alert(run: AnalysisRun) -> AlertInput | None:
    if run.input_payload is None:
        return None
    try:
        return normalize_alert_payload(run.input_payload)
    except Exception:  # noqa: BLE001 - candidate source should survive malformed historical payloads
        return None


def _tenant_scope(alert: AlertInput | None) -> str:
    return alert.tenant_id or "global" if alert is not None else "global"


def _run_facets(run: AnalysisRun, *, alert: AlertInput | None) -> dict[str, list[str]]:
    facets: dict[str, list[str]] = {}
    if alert is not None:
        _add_facet(facets, "source_type", alert.source.source_type.value)
        _add_facet(facets, "source_system", alert.source.source_system)
        _add_facet(facets, "vendor", alert.source.vendor)
        _add_facet(facets, "product", alert.source.product)
        _add_facet(facets, "detection_key", alert.detection.detection_key)
        _add_facet(facets, "rule_code", alert.detection.rule_code)
        _add_facet(facets, "rule_name", alert.detection.rule_name)
        _add_facet(facets, "category", alert.classification.category)
        _add_facet(facets, "severity", alert.classification.severity)
    elif run.normalization_report is not None:
        _add_facet(facets, "source_type", run.normalization_report.source_type.value)
        _add_facet(facets, "source_system", run.normalization_report.source_system)

    _add_facet(facets, "alert_id", run.alert_id)
    _add_facet(facets, "run_id", run.run_id)
    if run.entities is not None:
        for mention in run.entities.mentions[:20]:
            _add_facet(facets, "entity", mention.key)
        for value in run.entities.rule_codes:
            _add_facet(facets, "rule_code", value)
        for value in run.entities.rule_names:
            _add_facet(facets, "rule_name", value)
    return facets


def _base_evidence_refs(run: AnalysisRun, *, queue_id: str | None) -> list[str]:
    return [
        f"run:{run.run_id}",
        f"alert:{run.alert_id}",
        *(f"review_queue:{queue_id}" for _ in [queue_id] if queue_id),
    ]


def _correction_evidence_refs(correction: CorrectionRecord) -> list[str]:
    refs: list[str] = []
    for index, evidence in enumerate(correction.evidence, start=1):
        refs.append(f"correction_evidence:{correction.correction_id}:{index}:{evidence.source}")
    return refs


def _correction_content(run: AnalysisRun, correction: CorrectionRecord) -> str:
    previous = correction.previous_verdict.value if correction.previous_verdict is not None else "unknown"
    summary = run.analysis.summary if run.analysis is not None else "No runtime analysis summary."
    if correction.previous_verdict is correction.corrected_verdict:
        disposition = f"Analyst confirmed the existing verdict as {correction.corrected_verdict.value}."
    else:
        disposition = f"Analyst correction changed verdict from {previous} to {correction.corrected_verdict.value}."
    return f"{disposition}\nReason: {correction.reason}\nRuntime summary: {summary}"


def _domain_finding_content(finding: SocDomainFinding, *, analyst_feedback: str | None = None) -> str:
    lines = [
        f"Finding summary: {finding.summary}",
        f"Disposition: {finding.disposition.value}",
        f"Severity: {finding.severity.value}",
        f"Current conclusion: {finding.current_conclusion.summary}",
    ]
    if finding.scenario_key:
        lines.append(f"Scenario: {finding.scenario_key} ({finding.scenario_name or 'unnamed'})")
    if finding.evidence_profile.gaps:
        lines.append("Evidence gaps: " + " | ".join(finding.evidence_profile.gaps))
    if finding.recommendations:
        lines.append("Recommendations: " + " | ".join(finding.recommendations))
    if finding.limitations:
        lines.append("Limitations: " + " | ".join(finding.limitations))
    feedback = _normalize_feedback(analyst_feedback)
    if feedback:
        lines.append(f"Analyst feedback: {feedback}")
    return "\n".join(lines)


def _review_note_content(
    run: AnalysisRun,
    command: ReviewNoteCommand,
    *,
    queue_item: ReviewQueueItem,
) -> str:
    accepted_lead_agent = command.origin is ReviewNoteOrigin.ACCEPTED_LEAD_AGENT_CONCLUSION
    lines = [
        (f"Analyst-accepted Lead Agent conclusion: {_normalize_feedback(command.note) or command.note}" if accepted_lead_agent else f"Analyst review note: {_normalize_feedback(command.note) or command.note}"),
        f"Queue: {queue_item.queue_id}",
        f"Run: {run.run_id}",
        f"Alert: {run.alert_id}",
    ]
    if accepted_lead_agent:
        lines.extend(
            [
                f"Lead Agent thread: {command.source_thread_id}",
                f"Lead Agent message: {command.source_message_id}",
                f"Analyst acceptance reason: {_normalize_feedback(command.acceptance_reason) or command.acceptance_reason}",
            ]
        )
    if command.domain is not None:
        lines.append(f"Domain: {command.domain.value}")
    if command.scenario_key:
        lines.append(f"Scenario: {command.scenario_key}")
    if command.finding_id:
        lines.append(f"Finding: {command.finding_id}")
    if run.analysis is not None:
        lines.append(f"Runtime summary: {run.analysis.summary}")
        lines.append(f"Runtime reason: {run.analysis.reason}")
    if run.decision is not None:
        lines.append(f"Runtime verdict: {run.decision.verdict.value} ({run.decision.confidence:.2f})")
    return "\n".join(lines)


def _normalize_feedback(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _candidate_type_for_correction(verdict: Verdict) -> SocMemoryCandidateType:
    if verdict is Verdict.FALSE_POSITIVE:
        return SocMemoryCandidateType.BENIGN_PATTERN
    if verdict in {Verdict.TRUE_POSITIVE, Verdict.SUSPICIOUS}:
        return SocMemoryCandidateType.DETECTION_LESSON
    return SocMemoryCandidateType.PROCEDURE


def _candidate_type_for_finding(finding: SocDomainFinding) -> SocMemoryCandidateType:
    if finding.disposition in {
        SocDomainFindingDisposition.LIKELY_FALSE_POSITIVE,
        SocDomainFindingDisposition.BENIGN_AUTHORIZED_CANDIDATE,
    }:
        return SocMemoryCandidateType.BENIGN_PATTERN
    if finding.disposition in {
        SocDomainFindingDisposition.SUSPICIOUS,
        SocDomainFindingDisposition.LIKELY_TRUE_POSITIVE,
    }:
        return SocMemoryCandidateType.DETECTION_LESSON
    return SocMemoryCandidateType.PROCEDURE


def _decision_impact_for_correction(verdict: Verdict) -> SocMemoryDecisionImpact:
    if verdict is Verdict.FALSE_POSITIVE:
        return SocMemoryDecisionImpact.SUPPRESSION_HINT
    if verdict in {Verdict.TRUE_POSITIVE, Verdict.SUSPICIOUS}:
        return SocMemoryDecisionImpact.REVIEW_HINT
    return SocMemoryDecisionImpact.NONE


def _correction_confidence(correction: CorrectionRecord) -> float:
    if correction.corrected_confidence is not None:
        return min(max(correction.corrected_confidence, 0.35), 0.95)
    return 0.75


def _stable_domain_finding_key(result: SocDomainTriageResult, finding: SocDomainFinding) -> str:
    raw = "|".join(
        [
            result.run_id,
            result.alert_id,
            result.handler_id,
            finding.domain.value,
            finding.title,
            finding.disposition.value,
            finding.severity.value,
            ",".join(sorted(finding.evidence_refs)),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _stable_review_note_key(
    run: AnalysisRun,
    command: ReviewNoteCommand,
    *,
    queue_item: ReviewQueueItem,
) -> str:
    normalized_note = _normalize_feedback(command.note) or command.note.strip()
    raw = "|".join(
        [
            queue_item.queue_id,
            run.run_id,
            run.alert_id,
            command.domain.value if command.domain is not None else "",
            command.scenario_key or "",
            command.finding_id or "",
            command.origin.value,
            command.source_thread_id or "",
            command.source_message_id or "",
            _normalize_feedback(command.acceptance_reason) or "",
            normalized_note,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _review_note_evidence_refs(
    run: AnalysisRun,
    command: ReviewNoteCommand,
    *,
    queue_item: ReviewQueueItem,
) -> list[str]:
    refs = _base_evidence_refs(run, queue_id=queue_item.queue_id)
    refs.insert(0, f"review_note:{queue_item.queue_id}")
    if command.origin is ReviewNoteOrigin.ACCEPTED_LEAD_AGENT_CONCLUSION:
        refs.extend(
            [
                f"lead_agent_thread:{command.source_thread_id}",
                f"lead_agent_message:{command.source_message_id}",
            ]
        )
    if command.finding_id:
        refs.append(f"domain_finding:{command.finding_id}")
    if command.scenario_key:
        refs.append(f"scenario:{command.scenario_key}")
    return refs


def _add_facet(facets: dict[str, list[str]], key: str, value: str | None) -> None:
    if value is None:
        return
    normalized = str(value).strip()
    if not normalized:
        return
    values = facets.setdefault(key, [])
    if normalized not in values:
        values.append(normalized)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


__all__ = [
    "SocMemoryCandidateSourceBridge",
    "memory_candidate_command_from_correction",
    "memory_candidate_command_from_domain_finding",
    "memory_candidate_command_from_review_note",
]
