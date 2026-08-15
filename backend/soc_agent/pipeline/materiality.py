"""Scope analysis defects to decisions or the exact capabilities they affect."""

from __future__ import annotations

from collections.abc import Iterable

from soc_agent.contracts import (
    AdjudicatedRoleStatus,
    AdjudicatedRoleType,
    AnalysisCapability,
    AnalysisCapabilityGuard,
    AnalysisEvidenceGroundingReport,
    AnalysisEvidenceGroundingStatus,
    AnalysisMaterialityImpact,
    AnalysisMaterialityReport,
    AnalysisOutputQuality,
    AnalysisOutputQualityStatus,
    AnalysisOutputSection,
    AnalysisResult,
    AnalysisSectionMateriality,
    ConflictDisposition,
    ConflictDispositionStatus,
    ConflictResolutionSource,
    DecisionReviewReason,
    LLMAnalysisRequest,
    NetworkDirectionAssessmentStatus,
    RoleAdjudicationStatus,
    RoleAdjudicationVerificationResult,
    RoleCoherenceStatus,
    RoleResolutionStatus,
    RoleVerificationStatus,
)
from soc_agent.utils.hashing import stable_hash

_ROLE_CAPABILITIES = {
    AdjudicatedRoleType.ATTACKER: AnalysisCapability.ATTACKER_TARGETING,
    AdjudicatedRoleType.C2: AnalysisCapability.ATTACKER_TARGETING,
    AdjudicatedRoleType.SCANNER: AnalysisCapability.ATTACKER_TARGETING,
    AdjudicatedRoleType.VICTIM: AnalysisCapability.VICTIM_TARGETING,
    AdjudicatedRoleType.IMPACTED_ASSET: AnalysisCapability.IMPACTED_ASSET_TARGETING,
}


def assess_analysis_materiality(
    analysis: AnalysisResult,
    *,
    request: LLMAnalysisRequest,
    grounding: AnalysisEvidenceGroundingReport,
    output_quality: AnalysisOutputQuality | None,
    role_verification: RoleAdjudicationVerificationResult | None = None,
) -> AnalysisMaterialityReport:
    """Classify local defects without turning every degradation into review."""

    degraded_sections = set(output_quality.degraded_sections if output_quality is not None else ())
    fallback_used = bool(output_quality is not None and output_quality.status is AnalysisOutputQualityStatus.DETERMINISTIC_FALLBACK)
    invalid_evidence_refs = {item.evidence_ref for item in grounding.items if item.status is not AnalysisEvidenceGroundingStatus.GROUNDED}
    invalid_reasoning_refs = {item.reasoning_id for item in grounding.reasoning_items if item.status is not AnalysisEvidenceGroundingStatus.GROUNDED}
    core_evidence_invalid = bool(set(analysis.decision_evidence_refs) & invalid_evidence_refs)
    core_reasoning_invalid = bool(set(analysis.decision_reasoning_refs) & invalid_reasoning_refs)
    core_usable = not fallback_used and AnalysisOutputSection.CORE not in degraded_sections
    decision_usable = core_usable and not (core_evidence_invalid or core_reasoning_invalid)

    review_reasons: list[DecisionReviewReason] = []
    if not core_usable:
        review_reasons.append(DecisionReviewReason.ANALYSIS_OUTPUT_DEGRADED)
    if core_evidence_invalid:
        review_reasons.append(DecisionReviewReason.UNGROUNDED_ANALYSIS_EVIDENCE)
    if core_reasoning_invalid:
        review_reasons.append(DecisionReviewReason.UNGROUNDED_ANALYSIS_REASONING)

    conflict_dispositions = _conflict_dispositions(
        analysis,
        request,
        invalid_evidence_refs=invalid_evidence_refs,
        invalid_reasoning_refs=invalid_reasoning_refs,
    )
    if any(item.impact is AnalysisMaterialityImpact.DECISION_REVIEW for item in conflict_dispositions):
        review_reasons.append(DecisionReviewReason.FACT_CONFLICT)

    blocked: dict[AnalysisCapability, set[str]] = {capability: set() for capability in AnalysisCapability}
    affected_sections: dict[AnalysisCapability, set[AnalysisOutputSection]] = {capability: set() for capability in AnalysisCapability}
    conflict_ids: dict[AnalysisCapability, set[str]] = {capability: set() for capability in AnalysisCapability}

    if not decision_usable:
        _block_all(
            blocked,
            affected_sections,
            reason="core_decision_unusable",
            section=AnalysisOutputSection.CORE,
        )

    _apply_section_guards(
        analysis,
        degraded_sections=degraded_sections,
        invalid_evidence_refs=invalid_evidence_refs,
        invalid_reasoning_refs=invalid_reasoning_refs,
        blocked=blocked,
        affected_sections=affected_sections,
    )
    _apply_conflict_guards(
        conflict_dispositions,
        blocked=blocked,
        conflict_ids=conflict_ids,
    )
    _apply_verifier_materiality(
        role_verification,
        review_reasons=review_reasons,
        blocked=blocked,
    )

    sections = _section_materiality(
        degraded_sections=degraded_sections,
        core_evidence_invalid=core_evidence_invalid,
        core_reasoning_invalid=core_reasoning_invalid,
    )
    guards = [
        AnalysisCapabilityGuard(
            capability=capability,
            allowed=not reasons,
            reason_codes=sorted(reasons),
            affected_sections=sorted(affected_sections[capability], key=lambda item: item.value),
            conflict_ids=sorted(conflict_ids[capability]),
        )
        for capability, reasons in blocked.items()
    ]
    review_reasons = list(dict.fromkeys(review_reasons))
    return AnalysisMaterialityReport(
        core_usable=core_usable,
        decision_usable=decision_usable,
        review_required=bool(review_reasons),
        review_reasons=review_reasons,
        sections=sections,
        conflict_dispositions=conflict_dispositions,
        capability_guards=guards,
        warnings=_materiality_warnings(
            degraded_sections=degraded_sections,
            invalid_evidence_refs=invalid_evidence_refs,
            invalid_reasoning_refs=invalid_reasoning_refs,
        ),
    )


def _section_materiality(
    *,
    degraded_sections: set[AnalysisOutputSection],
    core_evidence_invalid: bool,
    core_reasoning_invalid: bool,
) -> list[AnalysisSectionMateriality]:
    results: list[AnalysisSectionMateriality] = []
    for section in AnalysisOutputSection:
        reasons: list[str] = []
        if section in degraded_sections:
            reasons.append("output_section_degraded")
        if section is AnalysisOutputSection.CORE and core_evidence_invalid:
            reasons.append("core_evidence_reference_invalid")
        if section is AnalysisOutputSection.CORE and core_reasoning_invalid:
            reasons.append("core_reasoning_reference_invalid")
        if section is AnalysisOutputSection.CORE and reasons:
            impact = AnalysisMaterialityImpact.DECISION_REVIEW
        elif (
            section
            in {
                AnalysisOutputSection.SCENARIO_ASSESSMENTS,
                AnalysisOutputSection.NETWORK_DIRECTION,
                AnalysisOutputSection.ROLE_ADJUDICATION,
            }
            and reasons
        ):
            impact = AnalysisMaterialityImpact.ACTION_ONLY
        else:
            impact = AnalysisMaterialityImpact.NONE
        results.append(
            AnalysisSectionMateriality(
                section=section,
                accepted=section not in degraded_sections,
                impact=impact,
                reason_codes=reasons,
            )
        )
    return results


def _apply_section_guards(
    analysis: AnalysisResult,
    *,
    degraded_sections: set[AnalysisOutputSection],
    invalid_evidence_refs: set[str],
    invalid_reasoning_refs: set[str],
    blocked: dict[AnalysisCapability, set[str]],
    affected_sections: dict[AnalysisCapability, set[AnalysisOutputSection]],
) -> None:
    scenario_refs = _assessment_refs(analysis.scenario_assessments)
    if not analysis.scenario_assessments or _has_invalid_refs(
        scenario_refs,
        invalid_evidence_refs,
        invalid_reasoning_refs,
    ):
        _block(
            AnalysisCapability.SCENARIO_ROUTING,
            blocked,
            affected_sections,
            reason="scenario_section_unavailable",
            section=AnalysisOutputSection.SCENARIO_ASSESSMENTS,
        )

    direction = analysis.network_direction
    direction_usable = direction.status in {
        NetworkDirectionAssessmentStatus.OBSERVED,
        NetworkDirectionAssessmentStatus.INFERRED,
    } and not _has_invalid_refs(
        (direction.evidence_refs, direction.reasoning_refs),
        invalid_evidence_refs,
        invalid_reasoning_refs,
    )
    if AnalysisOutputSection.NETWORK_DIRECTION in degraded_sections or not direction_usable:
        _block(
            AnalysisCapability.NETWORK_DIRECTION,
            blocked,
            affected_sections,
            reason="network_direction_unavailable",
            section=AnalysisOutputSection.NETWORK_DIRECTION,
        )

    resolved_capabilities: set[AnalysisCapability] = set()
    for role in analysis.role_adjudication.roles:
        capability = _ROLE_CAPABILITIES.get(role.role)
        if capability is None:
            continue
        refs_valid = not _has_invalid_refs(
            (role.evidence_refs, role.reasoning_refs),
            invalid_evidence_refs,
            invalid_reasoning_refs,
        )
        if role.status is AdjudicatedRoleStatus.RESOLVED_FROM_EVIDENCE and role.value is not None and refs_valid:
            resolved_capabilities.add(capability)
    for capability in (
        AnalysisCapability.ATTACKER_TARGETING,
        AnalysisCapability.VICTIM_TARGETING,
        AnalysisCapability.IMPACTED_ASSET_TARGETING,
    ):
        if capability not in resolved_capabilities:
            _block(
                capability,
                blocked,
                affected_sections,
                reason="resolved_role_target_unavailable",
                section=AnalysisOutputSection.ROLE_ADJUDICATION,
            )


def _conflict_dispositions(
    analysis: AnalysisResult,
    request: LLMAnalysisRequest,
    *,
    invalid_evidence_refs: set[str],
    invalid_reasoning_refs: set[str],
) -> list[ConflictDisposition]:
    results: list[ConflictDisposition] = []
    coherence = request.fact_reconstruction.role_coherence
    for report in request.fact_reconstruction.conflict_reports:
        conflict_id = (
            "CF-"
            + stable_hash(
                {
                    "type": report.conflict_type,
                    "fields": report.involved_fields,
                    "values": report.candidate_values,
                }
            )[:12].upper()
        )
        if report.resolution_status is RoleResolutionStatus.CONFIRMED:
            status = ConflictDispositionStatus.RESOLVED
            impact = AnalysisMaterialityImpact.NONE
            source = ConflictResolutionSource.FACT_RECONSTRUCTION
            rationale = "Fact reconstruction confirmed a selected value."
        elif not report.blocks_automation:
            status = ConflictDispositionStatus.ACCEPTED_VARIANCE
            impact = AnalysisMaterialityImpact.NONE
            source = ConflictResolutionSource.FACT_RECONSTRUCTION
            rationale = "The source adapter marked this variance as non-blocking."
        elif coherence.status is RoleCoherenceStatus.COHERENT and report.conflict_type.startswith("reverse_connection_"):
            status = ConflictDispositionStatus.RESOLVED
            impact = AnalysisMaterialityImpact.NONE
            source = ConflictResolutionSource.RUNTIME_SEMANTICS
            rationale = "Deterministic reverse-connection role coherence resolves the apparent tuple-role mismatch."
        elif _model_resolves_conflict(
            report.conflict_type,
            analysis,
            invalid_evidence_refs=invalid_evidence_refs,
            invalid_reasoning_refs=invalid_reasoning_refs,
        ):
            status = ConflictDispositionStatus.RESOLVED
            impact = AnalysisMaterialityImpact.NONE
            source = ConflictResolutionSource.MODEL_ADJUDICATION
            rationale = "The accepted role section resolved the affected semantic role with grounded references."
        elif report.severity == "critical":
            status = ConflictDispositionStatus.UNRESOLVED
            impact = AnalysisMaterialityImpact.DECISION_REVIEW
            source = ConflictResolutionSource.NONE
            rationale = "A critical current-fact contradiction remains unresolved."
        else:
            status = ConflictDispositionStatus.UNRESOLVED
            impact = AnalysisMaterialityImpact.ACTION_ONLY
            source = ConflictResolutionSource.NONE
            rationale = "The variance does not erase the verdict but blocks dependent automatic targeting."
        results.append(
            ConflictDisposition(
                conflict_id=conflict_id,
                conflict_type=report.conflict_type,
                status=status,
                impact=impact,
                resolution_source=source,
                rationale=rationale,
            )
        )
    return results


def _model_resolves_conflict(
    conflict_type: str,
    analysis: AnalysisResult,
    *,
    invalid_evidence_refs: set[str],
    invalid_reasoning_refs: set[str],
) -> bool:
    if analysis.role_adjudication.status is not RoleAdjudicationStatus.RESOLVED_FROM_EVIDENCE:
        return False
    role_name = next(
        (role for role in ("attacker", "victim", "impacted_asset") if role in conflict_type),
        None,
    )
    if role_name is None:
        return False
    return any(
        role.role.value == role_name
        and role.status is AdjudicatedRoleStatus.RESOLVED_FROM_EVIDENCE
        and role.value is not None
        and not _has_invalid_refs(
            (role.evidence_refs, role.reasoning_refs),
            invalid_evidence_refs,
            invalid_reasoning_refs,
        )
        for role in analysis.role_adjudication.roles
    )


def _apply_conflict_guards(
    dispositions: Iterable[ConflictDisposition],
    *,
    blocked: dict[AnalysisCapability, set[str]],
    conflict_ids: dict[AnalysisCapability, set[str]],
) -> None:
    for item in dispositions:
        if item.impact is AnalysisMaterialityImpact.NONE:
            continue
        if item.impact is AnalysisMaterialityImpact.DECISION_REVIEW:
            blocked[AnalysisCapability.RESPONSE_ACTION].add("unresolved_decision_conflict")
            conflict_ids[AnalysisCapability.RESPONSE_ACTION].add(item.conflict_id)
        searchable = item.conflict_type.casefold()
        capabilities: set[AnalysisCapability] = set()
        if "attacker" in searchable:
            capabilities.add(AnalysisCapability.ATTACKER_TARGETING)
        if "victim" in searchable:
            capabilities.add(AnalysisCapability.VICTIM_TARGETING)
        if "impacted" in searchable:
            capabilities.add(AnalysisCapability.IMPACTED_ASSET_TARGETING)
        if "source" in searchable:
            capabilities.update(
                {
                    AnalysisCapability.NETWORK_DIRECTION,
                    AnalysisCapability.SOURCE_TARGETING,
                }
            )
        if "destination" in searchable:
            capabilities.update(
                {
                    AnalysisCapability.NETWORK_DIRECTION,
                    AnalysisCapability.DESTINATION_TARGETING,
                }
            )
        if "direction" in searchable:
            capabilities.add(AnalysisCapability.NETWORK_DIRECTION)
        if any(token in searchable for token in ("user", "account", "credential")):
            capabilities.add(AnalysisCapability.USER_TARGETING)
        if not capabilities:
            capabilities.add(AnalysisCapability.RESPONSE_ACTION)
        for capability in capabilities:
            blocked[capability].add("unresolved_action_conflict")
            conflict_ids[capability].add(item.conflict_id)


def _apply_verifier_materiality(
    verification: RoleAdjudicationVerificationResult | None,
    *,
    review_reasons: list[DecisionReviewReason],
    blocked: dict[AnalysisCapability, set[str]],
) -> None:
    if verification is None:
        return
    if verification.status is RoleVerificationStatus.CHALLENGED:
        review_reasons.append(DecisionReviewReason.ROLE_VERIFICATION_CHALLENGED)
    if verification.status in {
        RoleVerificationStatus.CHALLENGED,
        RoleVerificationStatus.UNRESOLVED,
        RoleVerificationStatus.UNAVAILABLE,
    }:
        for capability in (
            AnalysisCapability.NETWORK_DIRECTION,
            AnalysisCapability.ATTACKER_TARGETING,
            AnalysisCapability.VICTIM_TARGETING,
            AnalysisCapability.IMPACTED_ASSET_TARGETING,
        ):
            blocked[capability].add(f"role_verification_{verification.status.value}")


def _assessment_refs(items: Iterable[object]) -> tuple[list[str], list[str]]:
    evidence: list[str] = []
    reasoning: list[str] = []
    for item in items:
        evidence.extend(getattr(item, "evidence_refs", ()))
        reasoning.extend(getattr(item, "reasoning_refs", ()))
    return evidence, reasoning


def _has_invalid_refs(
    refs: tuple[Iterable[str], Iterable[str]],
    invalid_evidence_refs: set[str],
    invalid_reasoning_refs: set[str],
) -> bool:
    evidence_refs, reasoning_refs = refs
    return bool(set(evidence_refs) & invalid_evidence_refs or set(reasoning_refs) & invalid_reasoning_refs)


def _block(
    capability: AnalysisCapability,
    blocked: dict[AnalysisCapability, set[str]],
    affected_sections: dict[AnalysisCapability, set[AnalysisOutputSection]],
    *,
    reason: str,
    section: AnalysisOutputSection,
) -> None:
    blocked[capability].add(reason)
    affected_sections[capability].add(section)


def _block_all(
    blocked: dict[AnalysisCapability, set[str]],
    affected_sections: dict[AnalysisCapability, set[AnalysisOutputSection]],
    *,
    reason: str,
    section: AnalysisOutputSection,
) -> None:
    for capability in AnalysisCapability:
        _block(
            capability,
            blocked,
            affected_sections,
            reason=reason,
            section=section,
        )


def _materiality_warnings(
    *,
    degraded_sections: set[AnalysisOutputSection],
    invalid_evidence_refs: set[str],
    invalid_reasoning_refs: set[str],
) -> list[str]:
    warnings: list[str] = []
    if degraded_sections:
        warnings.append("model output contains locally degraded sections: " + ", ".join(sorted(item.value for item in degraded_sections)))
    if invalid_evidence_refs:
        warnings.append(f"{len(invalid_evidence_refs)} evidence reference(s) failed grounding")
    if invalid_reasoning_refs:
        warnings.append(f"{len(invalid_reasoning_refs)} reasoning reference(s) failed grounding")
    return warnings


__all__ = ["assess_analysis_materiality"]
