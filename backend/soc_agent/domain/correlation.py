"""Vendor-neutral deterministic alert-similarity scoring."""

from __future__ import annotations

from soc_agent.contracts import (
    CORRELATION_SCORING_POLICY_VERSION,
    AlertSummary,
    SimilarAlertMatch,
    SimilarAlertQuery,
)


def score_similar_alert(
    query: SimilarAlertQuery,
    summary: AlertSummary,
) -> SimilarAlertMatch | None:
    """Score one historical summary using stable, explainable match reasons."""

    score = 0.0
    reasons: list[str] = []

    if query.detection_key and summary.detection_key == query.detection_key:
        score += 50
        reasons.append(f"detection_key:{query.detection_key}")
    if query.rule_code and summary.rule_code == query.rule_code:
        score += 40
        reasons.append(f"rule_code:{query.rule_code}")
    if query.source_type is not None and summary.source_type == query.source_type:
        score += 8
        reasons.append(f"source_type:{query.source_type.value}")
    if query.category and summary.category == query.category:
        score += 6
        reasons.append(f"category:{query.category}")

    shared_entity_keys = sorted(set(query.entity_keys).intersection(summary.entity_keys))
    if shared_entity_keys:
        score += min(len(shared_entity_keys) * 15, 60)
        reasons.extend(f"entity_key:{value}" for value in shared_entity_keys[:10])

    if score == 0:
        return None
    return SimilarAlertMatch(
        summary=summary,
        score=score,
        matched_reasons=reasons,
    )


__all__ = ["CORRELATION_SCORING_POLICY_VERSION", "score_similar_alert"]
