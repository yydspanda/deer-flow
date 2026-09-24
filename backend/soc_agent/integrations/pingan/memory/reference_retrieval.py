"""Narrow compatibility for reviewed detector-context lessons, never directives."""

from soc_agent.contracts import (
    SocMemoryApplicabilityReport,
    SocMemoryApplicabilityStatus,
    SocMemoryDecisionImpact,
    SocMemoryQuery,
    SocMemoryRecord,
)
from soc_agent.memory.scoring import evaluate_memory_applicability

_DETECTOR_SCOPE = frozenset({"detection_key", "detection_signature", "environment"})
_STABLE_REFERENCE_KEYS = _DETECTOR_SCOPE | {
    "source_type",
    "source_system",
    "product",
    "integration_name",
    "rule_code",
    "rule_name",
    "category",
    "severity",
    "entity",
    "role_entity",
    "scenario_key",
    "vulnerability_id",
    "attack_behavior_family",
}


def reference_applicability(
    record: SocMemoryRecord,
    query: SocMemoryQuery,
    report: SocMemoryApplicabilityReport,
    conflicts: list[str],
) -> SocMemoryApplicabilityReport | None:
    """Retain an approved rule lesson as context despite optional service drift.

    Existing scopes are evaluated unchanged. Only known, detector-scoped,
    non-directive lessons may cross a feature-version boundary, and the result
    is always partial/context-only. No old fingerprint is reinterpreted.
    """
    spec = record.applicability
    if (
        spec is None
        or record.decision_impact is not SocMemoryDecisionImpact.REVIEW_HINT
        or record.decision_directive is not None
        or set(spec.required_facets) != _DETECTOR_SCOPE
        or spec.selected_behavior_components is not None
        or spec.covered_behavior_components is not None
        or not query.tenant_id
        or query.tenant_id != record.tenant_id
        or query.tenant_scope != record.tenant_scope
        or set(conflicts) - {"network_service_mismatch"}
    ):
        return None

    from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile

    query_identity = {
        "profile_id": query.metadata.get("memory_profile_id"),
        "profile_version": query.metadata.get("memory_profile_version"),
        "feature_schema_version": query.metadata.get("memory_feature_schema_version"),
    }
    record_identity = {key: getattr(spec, key) for key in query_identity}
    try:
        PingAnSocMemoryProfile().for_identity(query_identity)
        PingAnSocMemoryProfile().for_identity(record_identity)
    except ValueError:
        return None
    version_changed = query_identity != record_identity
    if not version_changed and not conflicts:
        return None

    # An old opaque exclusion must not disappear just because its encoding
    # changed. Such scopes stay on their existing exact compatibility path.
    if version_changed and (set(spec.excluded_facets) - _STABLE_REFERENCE_KEYS or any(condition.facet_key not in _STABLE_REFERENCE_KEYS for condition in spec.reuse_conditions)):
        return None
    if conflicts and (
        "network_service" in spec.excluded_facets
        or any(key.startswith("behavior_component") or key == "behavior_fingerprint" for key in spec.excluded_facets)
        or any(condition.facet_key == "network_service" or condition.facet_key.startswith("behavior_component") or condition.facet_key == "behavior_fingerprint" for condition in spec.reuse_conditions)
    ):
        return None

    comparison_query = query.model_copy(
        update={
            "metadata": {
                **query.metadata,
                "memory_profile_id": spec.profile_id,
                "memory_profile_version": spec.profile_version,
                "memory_feature_schema_version": spec.feature_schema_version,
            }
        }
    )
    checked = evaluate_memory_applicability(record, comparison_query, {}) if version_changed else report
    if checked.status is not SocMemoryApplicabilityStatus.APPLICABLE and not (checked.status is SocMemoryApplicabilityStatus.PARTIAL and checked.context_only_allowed):
        return None
    reasons = [reason for reason in checked.reason_codes if reason != "typed_applicability_satisfied"]
    if version_changed:
        reasons.append("compatible_profile_reference_only")
    if "network_service_mismatch" in conflicts:
        reasons.append("optional_network_service_difference")
    return checked.model_copy(update={"status": SocMemoryApplicabilityStatus.PARTIAL, "context_only_allowed": True, "reason_codes": list(dict.fromkeys(reasons))})
