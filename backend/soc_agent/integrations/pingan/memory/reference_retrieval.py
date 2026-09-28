"""Narrow compatibility for saved reviewed scopes, with context-only authority."""

from soc_agent.contracts import (
    SocMemoryApplicabilityReport,
    SocMemoryApplicabilityStatus,
    SocMemoryDecisionImpact,
    SocMemoryQuery,
    SocMemoryRecord,
)
from soc_agent.memory.scoring import evaluate_memory_applicability, normalize_memory_facets

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
_SEMANTIC_PROFILE_VERSIONS = frozenset({"9", "10", "11", "12"})
_SEMANTIC_REFERENCE_KEYS = _STABLE_REFERENCE_KEYS | {"network_service", "behavior_strength"}
_CONCRETE_BEHAVIOR_PREFIXES = ("detected_behavior:", "detected_file:", "observed_process:", "observed_process_edge:", "http_observation:")


def reference_applicability(
    record: SocMemoryRecord,
    query: SocMemoryQuery,
    report: SocMemoryApplicabilityReport,
    conflicts: list[str],
) -> SocMemoryApplicabilityReport | None:
    """Keep legacy detector references and modern reviewed behavior scopes separate."""
    semantic = _semantic_reference_applicability(record, query, conflicts)
    return semantic if semantic is not None else _detector_reference_applicability(record, query, report, conflicts)


def _semantic_reference_applicability(record: SocMemoryRecord, query: SocMemoryQuery, conflicts: list[str]) -> SocMemoryApplicabilityReport | None:
    """Compare frozen reviewed ingredients; never regenerate historical facts."""
    spec = record.applicability
    if (
        spec is None
        or spec.profile_version not in _SEMANTIC_PROFILE_VERSIONS
        or not spec.selected_behavior_components
        or not spec.covered_behavior_components
        or not _DETECTOR_SCOPE <= set(spec.required_facets)
        or not {"behavior_fingerprint", "behavior_strength"} <= set(spec.required_facets)
        or set(spec.required_facets) - (_SEMANTIC_REFERENCE_KEYS | {"behavior_fingerprint"})
        or set(spec.excluded_facets) - _SEMANTIC_REFERENCE_KEYS
        or any(condition.facet_key not in _SEMANTIC_REFERENCE_KEYS for condition in spec.reuse_conditions)
        or not query.tenant_id
        or query.tenant_id != record.tenant_id
        or query.tenant_scope != record.tenant_scope
        or conflicts
    ):
        return None

    from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile

    query_identity = {
        "profile_id": query.metadata.get("memory_profile_id"),
        "profile_version": query.metadata.get("memory_profile_version"),
        "feature_schema_version": query.metadata.get("memory_feature_schema_version"),
    }
    record_identity = {key: getattr(spec, key) for key in query_identity}
    if query_identity == record_identity or query_identity["profile_version"] not in _SEMANTIC_PROFILE_VERSIONS:
        return None
    try:
        PingAnSocMemoryProfile().for_identity(query_identity)
        saved_profile = PingAnSocMemoryProfile().for_identity(record_identity)
    except ValueError:
        return None

    expansion = saved_profile.explain_scope_facets(spec, record.facets).get("behavior_fingerprint", {})
    verified = {value.casefold() for value in expansion.get("behavior_component", [])}
    selected = set(spec.selected_behavior_components)
    covered = set(spec.covered_behavior_components)
    if not selected <= covered or not covered <= verified:
        return None
    query_facets = normalize_memory_facets(query.facets)
    optional = normalize_memory_facets(spec.optional_facets)
    current = query_facets.get("behavior_component_core", query_facets.get("behavior_component", set()))
    shared_strong = selected & optional.get("behavior_component_strong", set()) & current & query_facets.get("behavior_component_strong", set())
    if not any(value.startswith(_CONCRETE_BEHAVIOR_PREFIXES) for value in shared_strong):
        return None
    missing = selected - current
    if any(not _service_component(value) for value in missing):
        return None
    # A detector bound to a different known service is counterevidence even
    # when the top-level service facet is absent or has another overlapping value.
    current_services = (
        query_facets.get("network_service", set())
        | {value.removeprefix("network_service:") for value in current if value.startswith("network_service:")}
        | {value.partition("@service:")[2] for value in current if value.startswith("detected_behavior:network:") and "@service:" in value}
    )
    for value in missing:
        if value.startswith("detected_behavior:network:"):
            detector, _, service = value.partition("@service:")
            if any(other.startswith(detector + "@service:") and other != value for other in current):
                return None
        else:
            service = value.removeprefix("network_service:")
        if current_services and service not in current_services:
            return None

    comparison_query = query.model_copy(update={"metadata": {**query.metadata, **{f"memory_{key}": value for key, value in record_identity.items()}}})
    checked = evaluate_memory_applicability(record, comparison_query, {})
    if (
        checked.excluded_facet_hits
        or checked.missing_reuse_conditions
        or set(checked.missing_required_facet_keys) - {"behavior_component"}
        or "reuse_object_scope_not_covered" in checked.reason_codes
        or not (checked.status is SocMemoryApplicabilityStatus.APPLICABLE or (checked.status is SocMemoryApplicabilityStatus.PARTIAL and checked.context_only_allowed))
    ):
        return None
    reasons = [reason for reason in checked.reason_codes if reason != "typed_applicability_satisfied"]
    reasons.append("compatible_semantic_scope_reference_only")
    if missing:
        reasons.append("missing_service_reference_only")
    return checked.model_copy(update={"status": SocMemoryApplicabilityStatus.PARTIAL, "context_only_allowed": True, "reason_codes": list(dict.fromkeys(reasons))})


def _service_component(value: str) -> bool:
    return value.startswith("network_service:") or (value.startswith("detected_behavior:network:") and "@service:" in value)


def _detector_reference_applicability(
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
