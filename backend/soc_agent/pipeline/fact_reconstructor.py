"""Deterministic fact reconstruction before bounded analysis."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from soc_agent.contracts import (
    AlertInput,
    ConflictReport,
    EvidenceInputPolicy,
    EvidenceLayer,
    EvidenceTrustLevel,
    FactReconstructionResult,
    FieldTrust,
    RoleAssignment,
)


@dataclass(frozen=True)
class _Candidate:
    role: str
    value: str
    evidence_path: str
    layer: EvidenceLayer
    trust_level: EvidenceTrustLevel


def reconstruct_facts(alert: AlertInput) -> FactReconstructionResult:
    """Build a pre-LLM fact layer from evidence policy and normalized fields."""

    warnings: list[str] = []
    policy = _evidence_policy(alert, warnings)
    selected_input_path = policy.selected_input_path if policy is not None else None
    selected_input_available = _resolve_path(alert.raw, selected_input_path) is not None if selected_input_path else False
    evidence_root_path = policy.fallback_input_path or selected_input_path if policy is not None else None
    evidence_root = _resolve_path(alert.raw, evidence_root_path)
    raw_event = dict(evidence_root) if isinstance(evidence_root, Mapping) else {}

    if policy is None:
        warnings.append("missing evidence input policy")
    elif not selected_input_available:
        warnings.append(f"selected evidence input unavailable: {selected_input_path}")
    elif policy.trust_level is EvidenceTrustLevel.LOW:
        warnings.append("evidence input policy selected low-trust structured fallback")

    field_trusts = _field_trusts(alert, policy)
    candidates = _role_candidates(alert, raw_event, evidence_root_path, policy)
    role_assignments = _role_assignments(candidates)
    conflict_reports = _conflict_reports(candidates)

    return FactReconstructionResult(
        evidence_policy=policy,
        selected_input_path=selected_input_path,
        selected_input_available=selected_input_available,
        field_trusts=field_trusts,
        role_assignments=role_assignments,
        conflict_reports=conflict_reports,
        warnings=warnings,
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


def _field_trusts(alert: AlertInput, policy: EvidenceInputPolicy | None) -> list[FieldTrust]:
    trusts: list[FieldTrust] = []
    if policy is not None and policy.selected_input_path:
        trusts.append(
            FieldTrust(
                field_path=policy.selected_input_path,
                layer=policy.selected_layer,
                trust_level=policy.trust_level,
                participates_in_fact_reconstruction=True,
                reason="selected by source normalizer evidence policy",
            )
        )
    if policy is not None and policy.fallback_input_path:
        trusts.append(
            FieldTrust(
                field_path=policy.fallback_input_path,
                layer=EvidenceLayer.RAW_STRUCTURED,
                trust_level=EvidenceTrustLevel.LOW if policy.trust_level is EvidenceTrustLevel.LOW else EvidenceTrustLevel.MEDIUM,
                participates_in_fact_reconstruction=policy.selected_layer is EvidenceLayer.RAW_STRUCTURED,
                reason="structured fallback evidence package",
            )
        )

    canonical_participates = not (policy is not None and policy.ignore_processed_fields_for_reasoning)
    canonical_trust = EvidenceTrustLevel.LOW if not canonical_participates else EvidenceTrustLevel.MEDIUM
    for field_path, value in [
        ("entities.network.source_ip", alert.entities.network.source_ip),
        ("entities.network.destination_ip", alert.entities.network.destination_ip),
        ("entities.host.asset_id", alert.entities.host.asset_id),
    ]:
        if value:
            trusts.append(
                FieldTrust(
                    field_path=field_path,
                    layer=EvidenceLayer.PROCESSED_FIELD,
                    trust_level=canonical_trust,
                    participates_in_fact_reconstruction=canonical_participates,
                    reason="canonical normalized field",
                )
            )
    return trusts


def _role_candidates(
    alert: AlertInput,
    raw_event: dict[str, Any],
    raw_event_path: str | None,
    policy: EvidenceInputPolicy | None,
) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    canonical_participates = not (policy is not None and policy.ignore_processed_fields_for_reasoning)
    canonical_trust = EvidenceTrustLevel.LOW if not canonical_participates else EvidenceTrustLevel.MEDIUM
    _add_candidate(
        candidates,
        "source",
        alert.entities.network.source_ip,
        "entities.network.source_ip",
        EvidenceLayer.PROCESSED_FIELD,
        canonical_trust,
    )
    _add_candidate(
        candidates,
        "destination",
        alert.entities.network.destination_ip,
        "entities.network.destination_ip",
        EvidenceLayer.PROCESSED_FIELD,
        canonical_trust,
    )
    _add_candidate(
        candidates,
        "impacted_asset",
        alert.entities.host.asset_id or alert.entities.network.destination_ip,
        "entities.host.asset_id" if alert.entities.host.asset_id else "entities.network.destination_ip",
        EvidenceLayer.PROCESSED_FIELD,
        canonical_trust,
    )
    _add_candidate(
        candidates,
        "response_target",
        alert.entities.network.destination_ip,
        "entities.network.destination_ip",
        EvidenceLayer.PROCESSED_FIELD,
        canonical_trust,
    )

    raw_path = raw_event_path or "raw"
    for role, aliases in {
        "source": ("sip", "source_ip", "src_addr"),
        "destination": ("dip", "dst_addr"),
        "attacker": ("attacker", "attack_sip"),
        "victim": ("victim", "alarm_sip"),
        "impacted_asset": ("alarm_sip", "dip", "dst_addr", "device__ip", "str_source_ip"),
        "response_target": ("alarm_sip", "dip", "dst_addr"),
    }.items():
        for alias in aliases:
            _add_candidate(
                candidates,
                role,
                raw_event.get(alias),
                f"{raw_path}.{alias}",
                EvidenceLayer.RAW_STRUCTURED,
                policy.trust_level if policy is not None else EvidenceTrustLevel.MEDIUM,
            )
    return candidates


def _role_assignments(candidates: list[_Candidate]) -> list[RoleAssignment]:
    assignments: list[RoleAssignment] = []
    for role in ["source", "destination", "attacker", "victim", "impacted_asset", "response_target"]:
        candidate = _best_candidate([item for item in candidates if item.role == role])
        if candidate is None:
            continue
        assignments.append(
            RoleAssignment(
                role=role,  # type: ignore[arg-type]
                value=candidate.value,
                confidence=_confidence(candidate),
                evidence_path=candidate.evidence_path,
                source_layer=candidate.layer,
                trust_level=candidate.trust_level,
                rationale="deterministic candidate selected before LLM analysis",
            )
        )
    return assignments


def _conflict_reports(candidates: list[_Candidate]) -> list[ConflictReport]:
    reports: list[ConflictReport] = []
    by_role: dict[str, list[_Candidate]] = {}
    for candidate in candidates:
        by_role.setdefault(candidate.role, []).append(candidate)

    for role, role_candidates in by_role.items():
        values = _unique_values(role_candidates)
        if len(values) > 1:
            reports.append(
                ConflictReport(
                    conflict_type=f"{role}_candidate_conflict",
                    severity="warning",
                    description=f"multiple candidate values found for role {role}",
                    involved_fields=_unique_paths(role_candidates),
                    candidate_values={role: values},
                )
            )

    for left_role, right_role, conflict_type in [
        ("attacker", "source", "attacker_source_mismatch"),
        ("victim", "destination", "victim_destination_mismatch"),
    ]:
        left_values = set(_unique_values(by_role.get(left_role, [])))
        right_values = set(_unique_values(by_role.get(right_role, [])))
        if left_values and right_values and left_values.isdisjoint(right_values):
            reports.append(
                ConflictReport(
                    conflict_type=conflict_type,
                    severity="warning",
                    description=f"{left_role} candidates do not match {right_role} candidates",
                    involved_fields=[*_unique_paths(by_role.get(left_role, [])), *_unique_paths(by_role.get(right_role, []))],
                    candidate_values={
                        left_role: sorted(left_values),
                        right_role: sorted(right_values),
                    },
                )
            )

    source_values = set(_unique_values(by_role.get("source", [])))
    destination_values = set(_unique_values(by_role.get("destination", [])))
    overlap = source_values & destination_values
    if overlap:
        reports.append(
            ConflictReport(
                conflict_type="source_destination_overlap",
                severity="warning",
                description="source and destination contain the same value",
                involved_fields=[*_unique_paths(by_role.get("source", [])), *_unique_paths(by_role.get("destination", []))],
                candidate_values={"overlap": sorted(overlap)},
            )
        )
    return reports


def _add_candidate(
    candidates: list[_Candidate],
    role: str,
    value: Any,
    evidence_path: str,
    layer: EvidenceLayer,
    trust_level: EvidenceTrustLevel,
) -> None:
    normalized = _normalize_candidate_value(value)
    if normalized is None:
        return
    candidates.append(
        _Candidate(
            role=role,
            value=normalized,
            evidence_path=evidence_path,
            layer=layer,
            trust_level=trust_level,
        )
    )


def _best_candidate(candidates: list[_Candidate]) -> _Candidate | None:
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (_trust_rank(item.trust_level), _layer_rank(item.layer)), reverse=True)[0]


def _confidence(candidate: _Candidate) -> float:
    if candidate.trust_level is EvidenceTrustLevel.HIGH:
        return 0.85
    if candidate.trust_level is EvidenceTrustLevel.MEDIUM:
        return 0.65
    if candidate.trust_level is EvidenceTrustLevel.LOW:
        return 0.4
    return 0.25


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


def _unique_values(candidates: list[_Candidate]) -> list[str]:
    return sorted({candidate.value for candidate in candidates})


def _unique_paths(candidates: list[_Candidate]) -> list[str]:
    return sorted({candidate.evidence_path for candidate in candidates})


def _normalize_candidate_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    return re.sub(r"\s+", " ", text)


def _resolve_path(payload: Mapping[str, Any], path: str | None) -> Any:
    if not path:
        return None
    value: Any = payload
    for segment in path.split("."):
        if not segment:
            return None
        match = re.fullmatch(r"([^\[\]]+)(?:\[(\d+)\])?", segment)
        if match is None:
            return None
        key, index = match.groups()
        if not isinstance(value, Mapping) or key not in value:
            return None
        value = value[key]
        if index is not None:
            if not isinstance(value, list):
                return None
            position = int(index)
            if position >= len(value):
                return None
            value = value[position]
    return value
