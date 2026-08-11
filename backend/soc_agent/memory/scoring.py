"""Shared relevance scoring for governed SOC memory retrieval."""

from __future__ import annotations

from soc_agent.contracts import SocMemoryQuery, SocMemoryRecord


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


def memory_facet_weight(key: str) -> float:
    """Return the stable deterministic weight for one normalized facet key."""

    if key in {"detection_key", "rule_code", "canonical_detection", "vendor_alias"}:
        return 4.0
    if key in {"topic", "skill", "skill_reason", "category", "candidate_type"}:
        return 2.5
    if key in {"entity", "asset", "host", "user", "ip"}:
        return 2.0
    if key in {"source_type", "source_system", "severity", "conflict_type", "action"}:
        return 1.5
    return 1.0


__all__ = ["memory_facet_weight", "normalize_memory_facets", "score_memory_record"]
