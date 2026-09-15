"""Reviewer-selected behavior scope; the original fingerprint stays immutable."""

from soc_agent.contracts import SocMemoryApplicabilitySpec
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.memory.scope_view import build_memory_scope_view


def select_memory_behavior_components(
    spec: SocMemoryApplicabilitySpec,
    facets: dict[str, list[str]],
    selected: list[str] | None,
    *,
    registry: SocMemoryProfileRegistry,
) -> SocMemoryApplicabilitySpec:
    if selected is None:
        return spec
    view = build_memory_scope_view(spec, facets, registry=registry)
    allowed = view.required_details.get("behavior_fingerprint", {}).get("behavior_component", []) if view else []
    values = sorted({value.strip().casefold() for value in selected if value.strip()})
    if not allowed:
        raise ValueError("behavior selection requires verified fingerprint ingredients")
    if not values or not set(values) <= {value.casefold() for value in allowed}:
        raise ValueError("select at least one behavior from the verified candidate")
    return SocMemoryApplicabilitySpec.model_validate(
        {
            **spec.model_dump(),
            "selected_behavior_components": values,
            "policy_version": "soc.memory_applicability_policy.v3",
        }
    )


def required_directive_keys(spec: SocMemoryApplicabilitySpec) -> list[str]:
    keys = set(spec.required_facets)
    if spec.selected_behavior_components is not None:
        keys.discard("behavior_fingerprint")
        keys.add("behavior_component")
    return sorted(keys)
