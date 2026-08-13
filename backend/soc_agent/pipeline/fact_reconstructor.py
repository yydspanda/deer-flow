"""Vendor-neutral fact reconstruction and conflict-aware role resolution."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from soc_agent.contracts import (
    AlertInput,
    CanonicalFieldProvenance,
    ConflictReport,
    EvidenceInputPolicy,
    EvidenceLayer,
    EvidenceTrustLevel,
    FactReconstructionResult,
    FieldReasoningStatus,
    FieldTrust,
    ParsedRawMessageEvidence,
    RoleClaim,
    RoleClaimType,
    RoleCoherenceAssessment,
    RoleCoherenceRelationship,
    RoleCoherenceRelationshipStatus,
    RoleCoherenceStatus,
    RoleResolution,
    RoleResolutionStatus,
    ScenarioHypothesis,
    ScenarioSignal,
    SourceFieldSemantic,
)

_ROLES = ("source", "destination", "attacker", "victim", "impacted_asset")
_REVERSE_CONNECTION_TERMS = ("反弹shell", "反弹 shell", "reverse shell", "回连")
_OUTBOUND_C2_TERMS = (
    "恶意外联",
    "远控木马",
    "c2",
    "command and control",
    "beacon",
    "sliver",
    "cobalt strike",
)
_LATERAL_MOVEMENT_TERMS = ("横向移动", "lateral movement")
_COMMAND_EXECUTION_TERMS = ("命令执行", "command execution", "command injection", "命令注入")
_WEB_ATTACK_TERMS = (
    "web攻击",
    "web attack",
    "代码执行",
    "远程代码执行",
    "remote code execution",
    "rce",
    "webshell",
    "弱口令",
    "sql注入",
    "xss",
)


def reconstruct_facts(alert: AlertInput) -> FactReconstructionResult:
    """Resolve observable and semantic roles without treating vendor claims as truth."""

    warnings: list[str] = []
    policy = _evidence_policy(alert, warnings)
    selected_input_path = policy.selected_input_path if policy is not None else None
    selected_input_available = _resolve_path(alert.raw, selected_input_path) is not None if selected_input_path else False
    parsed_message = _parsed_message_evidence(alert, selected_input_path)

    if policy is None:
        warnings.append("missing evidence input policy")
    elif not selected_input_available:
        warnings.append(f"selected evidence input unavailable: {selected_input_path}")
    elif policy.trust_level is EvidenceTrustLevel.LOW:
        warnings.append("evidence input policy selected low-trust structured fallback")
    elif policy.selected_layer is EvidenceLayer.RAW_MESSAGE and parsed_message is None:
        warnings.append("selected raw message has no deterministic parser output")

    role_claims = _role_claims(alert, policy, warnings)
    scenario_hypotheses = _scenario_hypotheses(alert)
    role_claims = _add_scenario_claims(role_claims, scenario_hypotheses, selected_input_path)
    role_resolutions = _role_resolutions(
        role_claims,
        selected_input_path,
        _source_field_semantics(alert),
    )
    conflict_reports = _conflict_reports(
        role_claims,
        role_resolutions,
        scenario_hypotheses,
        selected_input_path,
    )
    role_coherence = _role_coherence_assessment(
        role_claims,
        role_resolutions,
        scenario_hypotheses,
    )
    canonical_provenance = _merge_canonical_field_provenance(
        _canonical_field_provenance(alert, role_claims, selected_input_path),
        _adapter_canonical_field_provenance(alert, warnings),
    )
    field_trusts = _field_trusts(alert, policy, canonical_provenance)

    return FactReconstructionResult(
        evidence_policy=policy,
        selected_input_path=selected_input_path,
        selected_input_available=selected_input_available,
        field_trusts=field_trusts,
        canonical_field_provenance=canonical_provenance,
        role_claims=role_claims,
        scenario_hypotheses=scenario_hypotheses,
        role_resolutions=role_resolutions,
        role_coherence=role_coherence,
        conflict_reports=conflict_reports,
        warnings=_dedupe(warnings),
    )


def _evidence_policy(alert: AlertInput, warnings: list[str]) -> EvidenceInputPolicy | None:
    value = alert.extensions.get("evidence_input_policy")
    if value is None:
        return None
    try:
        return EvidenceInputPolicy.model_validate(value)
    except ValidationError as exc:
        warnings.append(f"invalid evidence input policy: {exc}")
        return None


def _field_trusts(
    alert: AlertInput,
    policy: EvidenceInputPolicy | None,
    canonical_provenance: Sequence[CanonicalFieldProvenance],
) -> list[FieldTrust]:
    trusts: list[FieldTrust] = []
    if policy is not None and policy.selected_input_path:
        trusts.append(
            FieldTrust(
                field_path=policy.selected_input_path,
                layer=policy.selected_layer,
                source_trust=policy.trust_level,
                reasoning_status=FieldReasoningStatus.SELECTED_EVIDENCE,
                participates=True,
                reason="selected by source normalizer evidence policy",
            )
        )
        trusts.extend(
            FieldTrust(
                field_path=path,
                layer=EvidenceLayer.RAW_MESSAGE,
                source_trust=EvidenceTrustLevel.HIGH,
                reasoning_status=FieldReasoningStatus.SUPPLEMENTARY_EVIDENCE,
                participates=True,
                reason="supplementary raw message participates as an independent claim source",
            )
            for path in policy.supplementary_input_paths
        )
    if policy is not None and policy.fallback_input_path and policy.fallback_input_path != policy.selected_input_path:
        trusts.append(
            FieldTrust(
                field_path=policy.fallback_input_path,
                layer=EvidenceLayer.RAW_STRUCTURED,
                source_trust=EvidenceTrustLevel.UNKNOWN,
                reasoning_status=FieldReasoningStatus.EXCLUDED_UNSELECTED_FALLBACK,
                participates=False,
                reason="unselected structured fallback is retained for audit only",
            )
        )

    canonical_participates = not (policy is not None and policy.ignore_processed_fields_for_reasoning)
    for field_path, value in [
        ("entities.network.source_ip", alert.entities.network.source_ip),
        ("entities.network.destination_ip", alert.entities.network.destination_ip),
        ("entities.host.asset_id", alert.entities.host.asset_id),
        (
            "entities.host.ip_addresses[0]",
            alert.entities.host.ip_addresses[0] if alert.entities.host.ip_addresses else None,
        ),
    ]:
        if value:
            trusts.append(
                FieldTrust(
                    field_path=field_path,
                    layer=EvidenceLayer.PROCESSED_FIELD,
                    source_trust=_canonical_source_trust(
                        field_path,
                        canonical_provenance,
                        policy,
                        participates=canonical_participates,
                    ),
                    reasoning_status=(FieldReasoningStatus.INCLUDED_CANONICAL_PROJECTION if canonical_participates else FieldReasoningStatus.EXCLUDED_DUPLICATE_PROJECTION),
                    participates=canonical_participates,
                    reason=("canonical projection is eligible because processed fields are the selected evidence path" if canonical_participates else "canonical projection duplicates already selected source evidence"),
                )
            )
    return trusts


def _canonical_source_trust(
    field_path: str,
    canonical_provenance: Sequence[CanonicalFieldProvenance],
    policy: EvidenceInputPolicy | None,
    *,
    participates: bool,
) -> EvidenceTrustLevel:
    provenance = next(
        (item for item in canonical_provenance if item.canonical_path == field_path),
        None,
    )
    if provenance is not None:
        return provenance.trust_level
    if participates and policy is not None:
        return policy.trust_level
    return EvidenceTrustLevel.UNKNOWN


def _role_claims(
    alert: AlertInput,
    policy: EvidenceInputPolicy | None,
    warnings: list[str],
) -> list[RoleClaim]:
    values = alert.extensions.get("role_claims")
    claims: list[RoleClaim] = []
    if isinstance(values, list):
        for value in values:
            try:
                claims.append(RoleClaim.model_validate(value))
            except ValidationError as exc:
                warnings.append(f"invalid role claim ignored: {exc}")
    if claims:
        return _dedupe_claims(claims)

    canonical_participates = not (policy is not None and policy.ignore_processed_fields_for_reasoning)
    if not canonical_participates:
        return []
    for role, value, path in [
        ("source", alert.entities.network.source_ip, "entities.network.source_ip"),
        ("destination", alert.entities.network.destination_ip, "entities.network.destination_ip"),
        (
            "impacted_asset",
            (alert.entities.host.ip_addresses[0] if alert.entities.host.ip_addresses else None) or alert.entities.host.asset_id,
            "entities.host.ip_addresses" if alert.entities.host.ip_addresses else "entities.host.asset_id",
        ),
    ]:
        if value:
            claims.append(
                _new_claim(
                    role=role,
                    value=str(value),
                    claim_type=RoleClaimType.OBSERVATION if role in {"source", "destination"} else RoleClaimType.DERIVED_HYPOTHESIS,
                    evidence_path=path,
                    layer=EvidenceLayer.PROCESSED_FIELD,
                    trust=EvidenceTrustLevel.MEDIUM,
                    semantic_confidence=0.65,
                    rationale="clean canonical adapter supplied this role candidate",
                )
            )
    return claims


def _scenario_hypotheses(alert: AlertInput) -> list[ScenarioHypothesis]:
    signals = _scenario_signals(alert)
    searchable = [(signal, _normalize_scenario_text(signal.text)) for signal in signals]
    definitions = [
        ("reverse_connection", _REVERSE_CONNECTION_TERMS, 0.9, "explicit reverse-shell or callback evidence"),
        ("outbound_c2", _OUTBOUND_C2_TERMS, 0.78, "outbound command-and-control evidence"),
        ("lateral_movement", _LATERAL_MOVEMENT_TERMS, 0.82, "lateral-movement evidence"),
        ("command_execution", _COMMAND_EXECUTION_TERMS, 0.8, "command-execution evidence"),
        ("web_attack", _WEB_ATTACK_TERMS, 0.72, "web-attack evidence"),
    ]
    hypotheses: list[ScenarioHypothesis] = []
    for scenario_type, terms, confidence, rationale in definitions:
        matched = [signal for signal, text in searchable if any(term in text for term in terms)]
        if matched:
            hypotheses.append(
                ScenarioHypothesis(
                    scenario_type=scenario_type,
                    confidence=confidence,
                    evidence_paths=_dedupe([signal.evidence_path for signal in matched]),
                    rationale=rationale,
                )
            )
    return hypotheses


def _scenario_signals(alert: AlertInput) -> list[ScenarioSignal]:
    signals: list[ScenarioSignal] = []
    values = alert.extensions.get("scenario_signals")
    if isinstance(values, list):
        for value in values:
            try:
                signals.append(ScenarioSignal.model_validate(value))
            except ValidationError:
                continue
    for text, path in [
        (alert.detection.rule_name, "detection.rule_name"),
        (alert.detection.rule_category, "detection.rule_category"),
        (alert.classification.category, "classification.category"),
    ]:
        if text:
            signals.append(
                ScenarioSignal(
                    text=text,
                    evidence_path=path,
                    source_layer=EvidenceLayer.PROCESSED_FIELD,
                    evidence_trust=EvidenceTrustLevel.LOW,
                )
            )
    return _dedupe_signals(signals)


def _add_scenario_claims(
    claims: list[RoleClaim],
    hypotheses: Sequence[ScenarioHypothesis],
    selected_input_path: str | None,
) -> list[RoleClaim]:
    reverse = next((item for item in hypotheses if item.scenario_type == "reverse_connection"), None)
    if reverse is None:
        return claims

    source = _best_claim([claim for claim in claims if claim.role == "source"], selected_input_path)
    destination = _best_claim([claim for claim in claims if claim.role == "destination"], selected_input_path)
    evidence_path = reverse.evidence_paths[0] if reverse.evidence_paths else "scenario.reverse_connection"
    additions: list[RoleClaim] = []
    if destination is not None:
        additions.append(
            _new_claim(
                role="attacker",
                value=destination.value,
                claim_type=RoleClaimType.DERIVED_HYPOTHESIS,
                evidence_path=evidence_path,
                layer=EvidenceLayer.AGENT_INFERENCE,
                trust=EvidenceTrustLevel.MEDIUM,
                semantic_confidence=0.78,
                rationale="reverse-connection scenario makes the network destination the likely attacker side",
                observation_scope=_selected_observation_scope(selected_input_path),
            )
        )
    if source is not None:
        additions.extend(
            [
                _new_claim(
                    role="victim",
                    value=source.value,
                    claim_type=RoleClaimType.DERIVED_HYPOTHESIS,
                    evidence_path=evidence_path,
                    layer=EvidenceLayer.AGENT_INFERENCE,
                    trust=EvidenceTrustLevel.MEDIUM,
                    semantic_confidence=0.78,
                    rationale="reverse-connection scenario makes the network source the likely victim side",
                    observation_scope=_selected_observation_scope(selected_input_path),
                ),
                _new_claim(
                    role="impacted_asset",
                    value=source.value,
                    claim_type=RoleClaimType.DERIVED_HYPOTHESIS,
                    evidence_path=evidence_path,
                    layer=EvidenceLayer.AGENT_INFERENCE,
                    trust=EvidenceTrustLevel.MEDIUM,
                    semantic_confidence=0.72,
                    rationale="the likely victim in a reverse connection is the provisional impacted asset",
                    observation_scope=_selected_observation_scope(selected_input_path),
                ),
            ]
        )
    return _dedupe_claims([*claims, *additions])


def _role_resolutions(
    claims: list[RoleClaim],
    selected_input_path: str | None,
    source_field_semantics: Sequence[SourceFieldSemantic],
) -> list[RoleResolution]:
    resolutions: list[RoleResolution] = []
    for role in _ROLES:
        candidates = _claims_in_selected_observation(
            [claim for claim in claims if claim.role == role],
            selected_input_path,
        )
        best = _best_claim(candidates, selected_input_path)
        if best is None:
            resolutions.append(
                RoleResolution(
                    role=role,  # type: ignore[arg-type]
                    status=RoleResolutionStatus.UNRESOLVED,
                    rationale="no role claim is available",
                    evidence_gaps=_evidence_gaps(role),
                    manual_checks=_manual_checks(role),
                )
            )
            continue

        supporting = [claim for claim in candidates if claim.value == best.value]
        contradicting = [claim for claim in candidates if claim.value != best.value]
        if contradicting:
            status = RoleResolutionStatus.CONFLICTED
        elif any(claim.claim_type is RoleClaimType.HUMAN_CONFIRMATION for claim in supporting):
            status = RoleResolutionStatus.CONFIRMED
        elif role in {"source", "destination"} and all(claim.claim_type is RoleClaimType.OBSERVATION for claim in supporting):
            status = RoleResolutionStatus.OBSERVED
        else:
            status = RoleResolutionStatus.TENTATIVE

        confidence = max(claim.semantic_confidence for claim in supporting)
        if len({claim.evidence_path for claim in supporting}) > 1:
            confidence = min(confidence + 0.05, 0.95)
        if contradicting:
            confidence *= 0.75
        exact_session_role = not contradicting and _has_exact_session_role_contract(
            role,
            best,
            source_field_semantics,
        )
        resolutions.append(
            RoleResolution(
                role=role,  # type: ignore[arg-type]
                status=status,
                selected_value=best.value,
                semantic_confidence=round(confidence, 3),
                supporting_claim_ids=[claim.claim_id for claim in supporting],
                contradicting_claim_ids=[claim.claim_id for claim in contradicting],
                rationale=("provisional value selected from the strongest claim; contradictory values remain unresolved" if contradicting else "selected from mutually consistent claims"),
                evidence_gaps=([] if status is RoleResolutionStatus.CONFIRMED or exact_session_role else _evidence_gaps(role)),
                manual_checks=([] if status is RoleResolutionStatus.CONFIRMED or exact_session_role else _manual_checks(role)),
                # Fact confirmation is necessary but never sufficient for an
                # operational action; policy and approval own that decision.
                automation_allowed=False,
            )
        )
    return resolutions


def _conflict_reports(
    claims: list[RoleClaim],
    resolutions: Sequence[RoleResolution],
    hypotheses: Sequence[ScenarioHypothesis],
    selected_input_path: str | None,
) -> list[ConflictReport]:
    reports: list[ConflictReport] = []
    resolution_by_role = {item.role: item for item in resolutions}
    by_role = {
        role: _claims_in_selected_observation(
            [claim for claim in claims if claim.role == role],
            selected_input_path,
        )
        for role in _ROLES
    }

    for role, role_claims in by_role.items():
        values = _unique_values(role_claims)
        if len(values) <= 1:
            continue
        resolution = resolution_by_role[role]
        reports.append(
            ConflictReport(
                conflict_type=f"{role}_candidate_conflict",
                severity="warning",
                description=f"multiple candidate values found for role {role}",
                involved_fields=_dedupe([claim.evidence_path for claim in role_claims]),
                candidate_values={role: values},
                resolution_status=resolution.status,
                provisional_value=resolution.selected_value,
                resolution_reason=resolution.rationale,
            )
        )

    source_values = set(_unique_values(by_role["source"]))
    destination_values = set(_unique_values(by_role["destination"]))
    overlap = source_values & destination_values
    if overlap:
        reports.append(
            ConflictReport(
                conflict_type="source_destination_overlap",
                severity="warning",
                description="source and destination claims contain the same value",
                involved_fields=_dedupe([claim.evidence_path for claim in [*by_role["source"], *by_role["destination"]]]),
                candidate_values={"overlap": sorted(overlap)},
                resolution_status=RoleResolutionStatus.UNRESOLVED,
                resolution_reason="network direction requires packet/session or proxy-chain corroboration",
            )
        )

    if any(item.scenario_type == "reverse_connection" for item in hypotheses):
        reports.extend(_reverse_connection_conflicts(resolution_by_role, by_role))
    return reports


def _reverse_connection_conflicts(
    resolutions: Mapping[str, RoleResolution],
    claims: Mapping[str, list[RoleClaim]],
) -> list[ConflictReport]:
    reports: list[ConflictReport] = []
    for semantic_role, network_role, conflict_type in [
        ("attacker", "destination", "reverse_connection_attacker_destination_mismatch"),
        ("victim", "source", "reverse_connection_victim_source_mismatch"),
    ]:
        semantic_value = resolutions[semantic_role].selected_value
        network_value = resolutions[network_role].selected_value
        if semantic_value is None or network_value is None or semantic_value == network_value:
            continue
        reports.append(
            ConflictReport(
                conflict_type=conflict_type,
                severity="warning",
                description=f"{semantic_role} does not match {network_role} under the reverse-connection hypothesis",
                involved_fields=_dedupe([claim.evidence_path for claim in [*claims[semantic_role], *claims[network_role]]]),
                candidate_values={semantic_role: [semantic_value], network_role: [network_value]},
                resolution_status=RoleResolutionStatus.CONFLICTED,
                provisional_value=semantic_value,
                resolution_reason="reverse-connection role semantics conflict with current claims",
            )
        )
    return reports


def _role_coherence_assessment(
    claims: Sequence[RoleClaim],
    resolutions: Sequence[RoleResolution],
    hypotheses: Sequence[ScenarioHypothesis],
) -> RoleCoherenceAssessment:
    """Evaluate scenario-defined role relationships without deciding alert truth."""

    if not any(item.scenario_type == "reverse_connection" for item in hypotheses):
        return RoleCoherenceAssessment()

    resolution_by_role = {item.role: item for item in resolutions}
    claims_by_id = {item.claim_id: item for item in claims}
    relationships: list[RoleCoherenceRelationship] = []
    counterevidence_paths: list[str] = []
    for semantic_role, network_role in (
        ("attacker", "destination"),
        ("victim", "source"),
    ):
        semantic_resolution = resolution_by_role.get(semantic_role)
        network_resolution = resolution_by_role.get(network_role)
        semantic_value = semantic_resolution.selected_value if semantic_resolution is not None else None
        network_value = network_resolution.selected_value if network_resolution is not None else None
        if semantic_value is None or network_value is None:
            status = RoleCoherenceRelationshipStatus.UNAVAILABLE
        elif any(item.status is RoleResolutionStatus.CONFLICTED for item in (semantic_resolution, network_resolution) if item is not None):
            status = RoleCoherenceRelationshipStatus.CONFLICTED
            claim_ids = {claim_id for item in (semantic_resolution, network_resolution) if item is not None for claim_id in (*item.supporting_claim_ids, *item.contradicting_claim_ids)}
            counterevidence_paths.extend(claims_by_id[claim_id].evidence_path for claim_id in claim_ids if claim_id in claims_by_id)
        elif semantic_value == network_value:
            status = RoleCoherenceRelationshipStatus.ALIGNED
        else:
            status = RoleCoherenceRelationshipStatus.MISMATCH
            counterevidence_paths.extend(claim.evidence_path for claim in claims if claim.role in {semantic_role, network_role} and claim.value in {semantic_value, network_value})
        relationships.append(
            RoleCoherenceRelationship(
                semantic_role=semantic_role,  # type: ignore[arg-type]
                network_role=network_role,  # type: ignore[arg-type]
                semantic_value=semantic_value,
                network_value=network_value,
                status=status,
            )
        )

    statuses = {item.status for item in relationships}
    if statuses.intersection(
        {
            RoleCoherenceRelationshipStatus.MISMATCH,
            RoleCoherenceRelationshipStatus.CONFLICTED,
        }
    ):
        return RoleCoherenceAssessment(
            scenario_type="reverse_connection",
            status=RoleCoherenceStatus.CONFLICTED,
            relationships=relationships,
            counterevidence_paths=_dedupe(counterevidence_paths),
            rationale=("At least one attacker/victim relationship contradicts the reverse-connection source/destination mapping."),
        )
    if statuses == {RoleCoherenceRelationshipStatus.ALIGNED}:
        return RoleCoherenceAssessment(
            scenario_type="reverse_connection",
            status=RoleCoherenceStatus.COHERENT,
            relationships=relationships,
            rationale=("Attacker aligns with the session destination and victim aligns with the session source under the reverse-connection hypothesis."),
        )
    return RoleCoherenceAssessment(
        scenario_type="reverse_connection",
        relationships=relationships,
        rationale="One or more roles required for reverse-connection coherence are unavailable.",
    )


def _source_field_semantics(alert: AlertInput) -> list[SourceFieldSemantic]:
    values = alert.extensions.get("source_field_semantics")
    if not isinstance(values, list):
        return []
    semantics: list[SourceFieldSemantic] = []
    for value in values:
        try:
            semantics.append(SourceFieldSemantic.model_validate(value))
        except ValidationError:
            continue
    return semantics


def _has_exact_session_role_contract(
    role: str,
    claim: RoleClaim,
    semantics: Sequence[SourceFieldSemantic],
) -> bool:
    semantic_type = {
        "source": "provider_reported_session_initiator",
        "destination": "provider_reported_session_responder",
    }.get(role)
    if semantic_type is None or claim.evidence_trust is not EvidenceTrustLevel.HIGH:
        return False
    return any(item.field_path == claim.evidence_path and item.semantic_type == semantic_type and item.participates_in_reasoning for item in semantics)


def _canonical_field_provenance(
    alert: AlertInput,
    claims: list[RoleClaim],
    selected_input_path: str | None,
) -> list[CanonicalFieldProvenance]:
    result: list[CanonicalFieldProvenance] = []
    for canonical_path, role, value in [
        ("entities.network.source_ip", "source", alert.entities.network.source_ip),
        ("entities.network.destination_ip", "destination", alert.entities.network.destination_ip),
        (
            "entities.host.ip_addresses[0]" if alert.entities.host.ip_addresses else "entities.host.asset_id",
            "impacted_asset",
            (alert.entities.host.ip_addresses[0] if alert.entities.host.ip_addresses else None) or alert.entities.host.asset_id,
        ),
    ]:
        if not value:
            continue
        matching = [claim for claim in claims if claim.role == role and claim.value == value]
        selected = _best_claim(matching, selected_input_path)
        if selected is None:
            continue
        relevant_claims = _claims_in_selected_observation(
            [claim for claim in claims if claim.role == role],
            selected_input_path,
        )
        alternatives = sorted({claim.value for claim in relevant_claims if claim.value != value})
        result.append(
            CanonicalFieldProvenance(
                canonical_path=canonical_path,
                selected_value=str(value),
                selected_from=selected.evidence_path,
                source_layer=selected.source_layer,
                trust_level=selected.evidence_trust,
                selection_reason="raw_message_first" if selected.source_layer is EvidenceLayer.RAW_MESSAGE else "best_available_evidence",
                alternative_values=alternatives,
            )
        )
    return result


def _adapter_canonical_field_provenance(
    alert: AlertInput,
    warnings: list[str],
) -> list[CanonicalFieldProvenance]:
    values = alert.extensions.get("canonical_field_provenance")
    if not isinstance(values, list):
        return []
    result: list[CanonicalFieldProvenance] = []
    for value in values:
        try:
            result.append(CanonicalFieldProvenance.model_validate(value))
        except ValidationError as exc:
            warnings.append(f"invalid adapter canonical provenance ignored: {exc}")
    return result


def _merge_canonical_field_provenance(
    derived: Sequence[CanonicalFieldProvenance],
    adapter: Sequence[CanonicalFieldProvenance],
) -> list[CanonicalFieldProvenance]:
    by_path = {item.canonical_path: item for item in derived}
    for item in adapter:
        by_path[item.canonical_path] = item
    return list(by_path.values())


def _best_claim(claims: Sequence[RoleClaim], selected_input_path: str | None) -> RoleClaim | None:
    if not claims:
        return None
    return max(claims, key=lambda claim: _claim_score(claim, selected_input_path))


def _claim_score(claim: RoleClaim, selected_input_path: str | None) -> tuple[int, int, int, float]:
    selected_bonus = 1 if selected_input_path and claim.evidence_path.startswith(f"{selected_input_path}#parsed") else 0
    return (
        selected_bonus,
        _trust_rank(claim.evidence_trust),
        _layer_rank(claim.source_layer),
        claim.semantic_confidence,
    )


def _new_claim(
    *,
    role: str,
    value: str,
    claim_type: RoleClaimType,
    evidence_path: str,
    layer: EvidenceLayer,
    trust: EvidenceTrustLevel,
    semantic_confidence: float,
    rationale: str,
    observation_scope: str | None = None,
) -> RoleClaim:
    digest = hashlib.sha256(f"{role}|{value}|{evidence_path}|{claim_type.value}".encode()).hexdigest()[:16]
    return RoleClaim(
        claim_id=f"claim:{digest}",
        role=role,  # type: ignore[arg-type]
        value=value,
        claim_type=claim_type,
        evidence_path=evidence_path,
        observation_scope=observation_scope,
        source_layer=layer,
        evidence_trust=trust,
        semantic_confidence=semantic_confidence,
        rationale=rationale,
    )


def _claims_in_selected_observation(
    claims: Sequence[RoleClaim],
    selected_input_path: str | None,
) -> list[RoleClaim]:
    selected_scope = _selected_observation_scope(selected_input_path)
    if selected_scope is None:
        return list(claims)
    scoped = [claim for claim in claims if claim.observation_scope == selected_scope]
    unscoped = [claim for claim in claims if claim.observation_scope is None]
    return [*scoped, *unscoped] if scoped else list(claims)


def _selected_observation_scope(selected_input_path: str | None) -> str | None:
    if not selected_input_path:
        return None
    return selected_input_path.split("#", 1)[0].removesuffix(".message")


def _evidence_gaps(role: str) -> list[str]:
    if role in {"attacker", "victim", "impacted_asset"}:
        return ["asset ownership evidence", "independent endpoint or session evidence"]
    return ["independent packet/session evidence"]


def _manual_checks(role: str) -> list[str]:
    if role == "impacted_asset":
        return ["verify candidate ownership in CMDB", "verify initiating process or endpoint telemetry"]
    if role in {"attacker", "victim"}:
        return ["verify traffic pattern against the scenario hypothesis", "compare CMDB and endpoint evidence"]
    return ["verify packet direction, proxy chain, and NAT translation"]


def _parsed_message_evidence(alert: AlertInput, selected_input_path: str | None) -> ParsedRawMessageEvidence | None:
    values = alert.extensions.get("parsed_raw_messages")
    if not isinstance(values, list):
        return None
    for value in values:
        try:
            parsed = ParsedRawMessageEvidence.model_validate(value)
        except ValidationError:
            continue
        if parsed.source_path == selected_input_path:
            return parsed
    return None


def _trust_rank(trust_level: EvidenceTrustLevel) -> int:
    return {
        EvidenceTrustLevel.UNKNOWN: 0,
        EvidenceTrustLevel.LOW: 1,
        EvidenceTrustLevel.MEDIUM: 2,
        EvidenceTrustLevel.HIGH: 3,
    }[trust_level]


def _layer_rank(layer: EvidenceLayer) -> int:
    return {
        EvidenceLayer.AGENT_INFERENCE: 0,
        EvidenceLayer.PROCESSED_FIELD: 1,
        EvidenceLayer.RAW_STRUCTURED: 2,
        EvidenceLayer.RAW_MESSAGE: 3,
        EvidenceLayer.HUMAN_CONFIRMED: 4,
    }[layer]


def _unique_values(claims: Sequence[RoleClaim]) -> list[str]:
    return sorted({claim.value for claim in claims})


def _dedupe_claims(claims: Sequence[RoleClaim]) -> list[RoleClaim]:
    return list({claim.claim_id: claim for claim in claims}.values())


def _dedupe_signals(signals: Sequence[ScenarioSignal]) -> list[ScenarioSignal]:
    result: list[ScenarioSignal] = []
    seen: set[tuple[str, str]] = set()
    for signal in signals:
        key = (signal.text, signal.evidence_path)
        if key not in seen:
            seen.add(key)
            result.append(signal)
    return result


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _normalize_scenario_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _resolve_path(payload: Mapping[str, Any], path: str | None) -> Any:
    if not path:
        return None
    value: Any = payload
    for segment in path.split("."):
        match = re.fullmatch(r"([^\[\]]+)(?:\[(\d+)\])?", segment)
        if match is None:
            return None
        key, index = match.groups()
        if not isinstance(value, Mapping) or key not in value:
            return None
        value = value[key]
        if index is not None:
            if not isinstance(value, list) or int(index) >= len(value):
                return None
            value = value[int(index)]
    return value


__all__ = ["reconstruct_facts"]
