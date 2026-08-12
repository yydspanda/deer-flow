"""Shared relevance scoring for governed SOC memory retrieval."""

from __future__ import annotations

from soc_agent.contracts import (
    SocMemoryCandidateType,
    SocMemoryQuery,
    SocMemoryRecord,
)

MEMORY_RETRIEVAL_POLICY_V2 = "soc.memory_retrieval_policy.v2"

_STRONG_ANCHOR_KEYS_BY_MEMORY_TYPE: dict[SocMemoryCandidateType, frozenset[str]] = {
    SocMemoryCandidateType.PROCEDURE: frozenset(
        {
            "behavior_fingerprint",
            "conflict_type",
            "detection_key",
            "rule_code",
            "scenario_key",
            "skill",
        }
    ),
    SocMemoryCandidateType.DETECTION_LESSON: frozenset(
        {
            "behavior_fingerprint",
            "detection_key",
            "entity",
            "role_entity",
            "rule_code",
            "scenario_key",
        }
    ),
    SocMemoryCandidateType.BENIGN_PATTERN: frozenset(
        {
            "behavior_fingerprint",
            "detection_key",
            "entity",
            "role_entity",
            "rule_code",
            "scenario_key",
        }
    ),
    SocMemoryCandidateType.ENVIRONMENT_FACT: frozenset({"asset", "entity", "host", "ip", "role_entity", "user"}),
    SocMemoryCandidateType.IDENTITY_PATTERN: frozenset({"asset", "entity", "host", "ip", "role_entity", "user"}),
    SocMemoryCandidateType.RESPONSE_POLICY_HINT: frozenset(
        {
            "behavior_fingerprint",
            "detection_key",
            "role_entity",
            "rule_code",
            "scenario_key",
        }
    ),
    SocMemoryCandidateType.NEGATIVE_MEMORY: frozenset(
        {
            "behavior_fingerprint",
            "conflict_type",
            "detection_key",
            "rule_code",
            "scenario_key",
            "skill",
        }
    ),
    SocMemoryCandidateType.ADAPTER_MAPPING: frozenset({"integration_name", "product", "source_system"}),
    SocMemoryCandidateType.EVAL_FIXTURE: frozenset(
        {
            "behavior_fingerprint",
            "detection_key",
            "rule_code",
            "scenario_key",
        }
    ),
}


def score_memory_record(
    record: SocMemoryRecord,
    query: SocMemoryQuery,
) -> tuple[float, list[str], dict[str, list[str]]]:
    """Score one record using only stable, auditable query signals."""

    score = float(record.confidence)
    match_reasons: list[str] = []
    matched_facets: dict[str, list[str]] = {}

    has_specific_signal = bool(query.facets or query.text_terms or query.evidence_refs)
    if query.memory_types and record.memory_type in query.memory_types and not has_specific_signal:
        score += 2.0
        match_reasons.append(f"memory_type:{record.memory_type.value}")

    record_facets = normalize_memory_facets(record.facets)
    query_facets = normalize_memory_facets(query.facets)
    for key, query_values in query_facets.items():
        record_values = record_facets.get(key, set())
        overlap = sorted(query_values & record_values)
        if not overlap:
            continue
        weight = memory_facet_weight(key)
        score += weight * len(overlap)
        matched_facets[key] = overlap
        match_reasons.append(f"facet:{key}={','.join(overlap[:3])}")

    haystack = f"{record.summary}\n{record.content}".casefold()
    for term in query.text_terms:
        normalized_term = term.casefold()
        if normalized_term and normalized_term in haystack:
            score += 1.25
            match_reasons.append(f"text:{term[:40]}")

    evidence_overlap = sorted(set(record.evidence_refs) & set(query.evidence_refs))
    if evidence_overlap:
        score += 3.0 * len(evidence_overlap)
        match_reasons.append(f"evidence:{','.join(evidence_overlap[:3])}")

    if not match_reasons and not query.facets and not query.text_terms and not query.evidence_refs:
        match_reasons.append("broad:policy_allowed")

    return round(score, 3), match_reasons, matched_facets


def normalize_memory_facets(
    facets: dict[str, list[str]],
) -> dict[str, set[str]]:
    """Normalize facet keys and values identically for indexing and scoring."""

    normalized: dict[str, set[str]] = {}
    for key, values in facets.items():
        normalized_key = str(key).strip().casefold()
        if not normalized_key:
            continue
        normalized_values = {str(value).strip().casefold() for value in values if str(value).strip()}
        if normalized_values:
            normalized[normalized_key] = normalized_values
    return normalized


def evaluate_memory_anchor_gate(
    record: SocMemoryRecord,
    query: SocMemoryQuery,
    matched_facets: dict[str, list[str]],
) -> tuple[bool, list[str], dict[str, list[str]]]:
    """Apply the v2 exact-anchor gate after broad multi-lane recall."""

    if query.policy_version != MEMORY_RETRIEVAL_POLICY_V2:
        return True, ["anchor_gate:legacy_v1_not_required"], {}

    allowed_keys = _STRONG_ANCHOR_KEYS_BY_MEMORY_TYPE[record.memory_type]
    anchors = {key: values for key, values in matched_facets.items() if key in allowed_keys and values}
    if not anchors:
        return False, [f"anchor_gate:missing_for_{record.memory_type.value}"], {}
    reasons = [f"anchor:{key}={','.join(values[:3])}" for key, values in sorted(anchors.items())]
    return True, reasons, anchors


def memory_facet_weight(key: str) -> float:
    """Return the stable deterministic weight for one normalized facet key."""

    if key in {"behavior_fingerprint", "detection_key", "rule_code", "canonical_detection", "vendor_alias"}:
        return 4.0
    if key in {"role_entity", "scenario_key"}:
        return 3.5
    if key in {"integration_name", "topic", "skill", "skill_reason", "category", "candidate_type"}:
        return 2.5
    if key in {"entity", "asset", "host", "user", "ip", "environment"}:
        return 2.0
    if key in {"source_type", "source_system", "severity", "conflict_type", "action"}:
        return 1.5
    return 1.0


__all__ = [
    "evaluate_memory_anchor_gate",
    "memory_facet_weight",
    "normalize_memory_facets",
    "score_memory_record",
]
