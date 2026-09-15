"""Explain stored scope without changing applicability, ranking or persistence."""

from soc_agent.contracts import SocMemoryApplicabilitySpec
from soc_agent.contracts.memory_scope import MemoryScopeOption, MemoryScopeView
from soc_agent.memory.profiles import SocMemoryProfileRegistry


def build_memory_scope_view(
    spec: SocMemoryApplicabilitySpec | None,
    facets: dict[str, list[str]],
    *,
    registry: SocMemoryProfileRegistry,
) -> MemoryScopeView | None:
    if spec is None:
        return None
    profile = registry.get(spec.profile_id)
    details: dict[str, dict[str, list[str]]] = {}
    explain = getattr(profile, "explain_scope_facets", None)
    if profile and callable(explain) and profile.identity.profile_version == spec.profile_version and profile.identity.feature_schema_version == spec.feature_schema_version:
        details = explain(spec, facets)

    implied: dict[str, set[str]] = {}
    for group in details.values():
        for key, values in group.items():
            implied.setdefault(key, set()).update(value.casefold() for value in values)
    options = []
    for key, values in spec.optional_facets.items():
        covered = [value for value in values if value.casefold() in implied.get(key, set())]
        independent = [value for value in values if value not in covered]
        # Similarity keys must stay optional: promotion would remove the fallback gate.
        if key in spec.context_only_similarity_facet_keys or key in {"behavior_component_core", "behavior_component_strong", "behavior_component_weak"}:
            kind = "similarity"
            independent = values
        else:
            kind = "additional" if independent else "covered"
        options.append(MemoryScopeOption(key=key, values=independent, covered_values=covered, kind=kind))
    return MemoryScopeView(
        required_details=details,
        unresolved_fingerprint_keys=[key for key in spec.required_facets if ("fingerprint" in key or "signature" in key) and key not in details],
        options=options,
    )
