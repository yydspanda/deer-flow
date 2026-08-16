"""Shared relevance scoring for governed SOC memory retrieval."""

from __future__ import annotations

from soc_agent.contracts import (
    SocMemoryApplicabilityReport,
    SocMemoryApplicabilitySpec,
    SocMemoryApplicabilityStatus,
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

    allowed_keys = memory_strong_anchor_keys(record.memory_type)
    anchors = {key: values for key, values in matched_facets.items() if key in allowed_keys and values}
    if not anchors:
        return False, [f"anchor_gate:missing_for_{record.memory_type.value}"], {}
    reasons = [f"anchor:{key}={','.join(values[:3])}" for key, values in sorted(anchors.items())]
    return True, reasons, anchors


def evaluate_memory_applicability(
    record: SocMemoryRecord,
    query: SocMemoryQuery,
    matched_facets: dict[str, list[str]],
) -> SocMemoryApplicabilityReport:
    """Evaluate the reviewer-owned exact scope independently of ranking."""

    spec = record.applicability
    if spec is None:
        return SocMemoryApplicabilityReport(
            status=SocMemoryApplicabilityStatus.LEGACY_ANCHOR_ONLY,
            policy_version="soc.memory_applicability_policy.legacy",
            matched_strong_anchor_count=len(set(matched_facets) & memory_strong_anchor_keys(record.memory_type)),
            reason_codes=["legacy_record_without_typed_applicability"],
        )

    query_profile_id = _metadata_text(query, "memory_profile_id")
    query_profile_version = _metadata_text(query, "memory_profile_version")
    query_feature_schema = _metadata_text(
        query,
        "memory_feature_schema_version",
    )
    profile_reasons: list[str] = []
    if query_profile_id != spec.profile_id:
        profile_reasons.append("profile_id_mismatch")
    if query_profile_version != spec.profile_version:
        profile_reasons.append("profile_version_mismatch")
    if query_feature_schema != spec.feature_schema_version:
        profile_reasons.append("feature_schema_version_mismatch")

    query_facets = normalize_memory_facets(query.facets)
    matched_required = _facet_overlaps(spec.required_facets, query_facets)
    missing_required = sorted(set(spec.required_facets) - set(matched_required))
    matched_optional = _facet_overlaps(spec.optional_facets, query_facets)
    excluded_hits = _facet_overlaps(spec.excluded_facets, query_facets)
    matched_strong_count = len(set(memory_strong_anchor_keys(record.memory_type)) & (set(matched_required) | set(matched_optional)))
    reason_codes = list(profile_reasons)
    if missing_required:
        reason_codes.append("required_facets_missing")
    if len(matched_optional) < spec.minimum_optional_matches:
        reason_codes.append("optional_facet_threshold_not_met")
    if excluded_hits:
        reason_codes.append("excluded_facet_hit")
    if matched_strong_count < spec.minimum_strong_anchor_matches:
        reason_codes.append("strong_anchor_threshold_not_met")

    context_only_allowed = _context_only_applicability_satisfied(
        spec,
        matched_required=matched_required,
        missing_required=missing_required,
        matched_optional=matched_optional,
        profile_reasons=profile_reasons,
        excluded_hits=excluded_hits,
    )
    if context_only_allowed:
        reason_codes.append("context_only_similarity_satisfied")

    if not reason_codes:
        status = SocMemoryApplicabilityStatus.APPLICABLE
        reason_codes = ["typed_applicability_satisfied"]
    elif profile_reasons or excluded_hits:
        status = SocMemoryApplicabilityStatus.NOT_APPLICABLE
    else:
        status = SocMemoryApplicabilityStatus.PARTIAL
    return SocMemoryApplicabilityReport(
        status=status,
        policy_version=spec.policy_version,
        profile_id=spec.profile_id,
        profile_version=spec.profile_version,
        matched_required_facets=matched_required,
        missing_required_facet_keys=missing_required,
        matched_optional_facets=matched_optional,
        excluded_facet_hits=excluded_hits,
        matched_strong_anchor_count=matched_strong_count,
        context_only_allowed=context_only_allowed,
        reason_codes=reason_codes,
    )


def _context_only_applicability_satisfied(
    spec: SocMemoryApplicabilitySpec,
    *,
    matched_required: dict[str, list[str]],
    missing_required: list[str],
    matched_optional: dict[str, list[str]],
    profile_reasons: list[str],
    excluded_hits: dict[str, list[str]],
) -> bool:
    if profile_reasons or excluded_hits or not missing_required:
        return False
    context_required = set(spec.context_only_required_facet_keys)
    context_missing = set(spec.context_only_missing_facet_keys)
    context_similarity = set(spec.context_only_similarity_facet_keys)
    if not context_required or not context_missing or not context_similarity:
        return False
    if not context_required <= set(matched_required):
        return False
    if not set(missing_required) <= context_missing:
        return False
    if len(matched_optional) < spec.minimum_optional_matches:
        return False
    return bool(context_similarity & set(matched_optional))


def memory_strong_anchor_keys(
    memory_type: SocMemoryCandidateType,
) -> frozenset[str]:
    """Return the exact facet keys that can admit one memory type."""

    return _STRONG_ANCHOR_KEYS_BY_MEMORY_TYPE[memory_type]


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


def _facet_overlaps(
    expected: dict[str, list[str]],
    query_facets: dict[str, set[str]],
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for raw_key, raw_values in expected.items():
        key = str(raw_key).strip().casefold()
        expected_values = {str(value).strip().casefold() for value in raw_values if str(value).strip()}
        overlap = sorted(expected_values & query_facets.get(key, set()))
        if overlap:
            result[key] = overlap
    return result


def _metadata_text(query: SocMemoryQuery, key: str) -> str | None:
    value = query.metadata.get(key)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


__all__ = [
    "evaluate_memory_anchor_gate",
    "evaluate_memory_applicability",
    "memory_facet_weight",
    "memory_strong_anchor_keys",
    "normalize_memory_facets",
    "score_memory_record",
]
