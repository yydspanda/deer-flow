"""Reviewed coverage is not just a positive subset of the current behavior."""

import pytest

from soc_agent.contracts import SocMemoryApplicabilitySpec, SocMemoryCandidateType, SocMemoryQuery
from soc_agent.contracts.schemas import SocMemoryReuseCondition
from soc_agent.memory.governance import scope_relation
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.memory.scoring import evaluate_memory_scope


def scope(**updates):
    return SocMemoryApplicabilitySpec(
        profile_id="generic.soc",
        profile_version="1",
        feature_schema_version="test.v1",
        required_facets={"detection_key": ["rule:test"], "behavior_fingerprint": ["historical"]},
        selected_behavior_components=["network_service:tcp/80", "tool:p"],
        covered_behavior_components=["network_service:tcp/80", "tool:p"],
        policy_version="soc.memory_applicability_policy.v4",
        **updates,
    )


def query(*components):
    return SocMemoryQuery(
        facets={"detection_key": ["rule:test"], "behavior_component_core": list(components)},
        metadata={"memory_profile_id": "generic.soc", "memory_profile_version": "1", "memory_feature_schema_version": "test.v1"},
    )


def evaluate(spec, request):
    return evaluate_memory_scope(spec, SocMemoryCandidateType.DETECTION_LESSON, request, {})


def test_new_behavior_is_reference_not_direct_reuse():
    result = evaluate(scope(), query("network_service:tcp/80", "tool:p", "file_action:read_credentials"))
    assert result.status.value == "partial"
    assert result.context_only_allowed
    assert result.uncovered_behavior_components == ["file_action:read_credentials"]
    assert "uncovered_core_behavior" in result.reason_codes


def test_order_duplicates_and_unlimited_ips_do_not_change_coverage():
    request = query("tool:p", "network_service:tcp/80", "tool:p")
    request.facets["role_entity"] = ["source:192.0.2.10"]
    assert evaluate(scope(), request).status.value == "applicable"


def test_unchecked_known_behavior_is_optional_not_an_unknown_behavior_wildcard():
    spec = scope().model_copy(update={"selected_behavior_components": ["tool:p"]})
    assert evaluate(spec, query("tool:p")).status.value == "applicable"
    assert evaluate(spec, query("tool:p", "network_service:tcp/443")).status.value == "partial"


def test_covered_behavior_must_retain_selected_components():
    with pytest.raises(ValueError):
        SocMemoryApplicabilitySpec.model_validate({**scope().model_dump(), "covered_behavior_components": ["tool:p"]})


def test_narrow_scope_is_proved_by_conditions_not_their_number():
    a = scope()
    b = a.model_copy(update={"reuse_conditions": [SocMemoryReuseCondition(facet_key="role_entity", value_prefix="destination", values=["destination:192.0.2.1"])]})
    c = a.model_copy(update={"reuse_conditions": [SocMemoryReuseCondition(facet_key="entity", value_prefix="account", values=["account:alice"])]})
    registry = SocMemoryProfileRegistry()
    assert scope_relation(b, a, registry) == "strict_subset"
    assert scope_relation(a, b, registry) == "strict_superset"
    assert scope_relation(b, c, registry) == "overlap"
    assert scope_relation(a, a.model_copy(update={"profile_version": "2"}), registry) == "unknown"


def test_entity_conditions_cannot_join_two_unrelated_connections():
    from soc_agent.contracts.schemas import SocMemoryScopeBinding

    spec = scope(
        reuse_conditions=[
            SocMemoryReuseCondition(facet_key="role_entity", value_prefix="source", values=["source:192.0.2.1"]),
            SocMemoryReuseCondition(facet_key="role_entity", value_prefix="destination", values=["destination:192.0.2.2"]),
        ]
    )
    request = query("tool:p", "network_service:tcp/80")
    request.facets["role_entity"] = ["source:192.0.2.1", "destination:192.0.2.2"]
    request.scope_bindings = [
        SocMemoryScopeBinding(source_ref="network:1", facets={"role_entity": ["source:192.0.2.1", "destination:192.0.2.3"]}),
        SocMemoryScopeBinding(source_ref="network:2", facets={"role_entity": ["source:192.0.2.4", "destination:192.0.2.2"]}),
    ]
    result = evaluate(spec, request)
    assert result.status.value == "partial"
    assert "reuse_object_scope_not_covered" in result.reason_codes
    request.scope_bindings = [SocMemoryScopeBinding(source_ref="network:1", facets={"role_entity": request.facets["role_entity"]})]
    assert evaluate(spec, request).status.value == "applicable"


def test_unconsumed_detection_prevents_automatic_reuse_not_context():
    request = query("tool:p", "network_service:tcp/80")
    request.projection_gaps = ["detection:unbound"]
    result = evaluate(scope(), request)
    assert result.context_only_allowed
    assert result.status.value == "partial"
