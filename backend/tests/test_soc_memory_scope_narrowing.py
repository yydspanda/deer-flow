"""Selected optional values must constrain matching, not just the display."""

import pytest
from test_soc_memory_retrieval_v2 import _record

from soc_agent.contracts import SocMemoryApplicabilitySpec, SocMemoryQuery
from soc_agent.memory.lessons import promote_memory_applicability_facets
from soc_agent.memory.scoring import evaluate_memory_applicability


def scope():
    return SocMemoryApplicabilitySpec(
        profile_id="generic.soc",
        profile_version="1",
        feature_schema_version="test.v1",
        required_facets={"detection_key": ["rule:1"], "behavior_fingerprint": ["hash"]},
        optional_facets={"source_type": ["ndr"], "entity": ["ip:10.0.0.1", "rule:1"], "behavior_component_strong": ["technique:t1190"]},
        context_only_required_facet_keys=["detection_key"],
        context_only_missing_facet_keys=["behavior_fingerprint"],
        context_only_similarity_facet_keys=["behavior_component_strong"],
    )


def test_selected_values_narrow_reuse_without_mutating_reference_or_candidate():
    original = scope()
    result = promote_memory_applicability_facets(original, ["source_type"], {"entity": ["ip:10.0.0.1"]})
    assert {item.condition_key: item.values for item in result.reuse_conditions} == {"entity/ip": ["ip:10.0.0.1"], "source_type/*": ["ndr"]}
    assert result.required_facets == original.required_facets
    assert result.optional_facets == original.optional_facets
    assert result.context_only_required_facet_keys == original.context_only_required_facet_keys
    assert "entity" not in original.required_facets
    assert SocMemoryApplicabilitySpec.model_validate(result.model_dump()) == result


@pytest.mark.parametrize("selected", [{"entity": []}, {"entity": ["ip:new"]}, {"unknown": ["x"]}, {"detection_key": ["rule:1"]}, {"behavior_component_strong": ["technique:t1190"]}])
def test_invalid_or_similarity_selections_are_rejected(selected):
    with pytest.raises(ValueError):
        promote_memory_applicability_facets(scope(), [], selected)


def test_selected_subset_takes_precedence_over_legacy_whole_group_promotion():
    result = promote_memory_applicability_facets(scope(), ["entity"], {"entity": ["ip:10.0.0.1"]})
    assert result.reuse_conditions[0].values == ["ip:10.0.0.1"]


def test_optional_threshold_cannot_be_weakened_by_promotion():
    original = scope().model_copy(update={"minimum_optional_matches": 3})
    result = promote_memory_applicability_facets(original, [], {"entity": ["ip:10.0.0.1"]})
    assert result.minimum_optional_matches == 3
    assert result.optional_facets == original.optional_facets


@pytest.mark.parametrize("fingerprint", ["hash", "other-behavior"])
def test_selected_entity_blocks_reuse_but_keeps_reference_with_shared_behavior(fingerprint):
    spec = promote_memory_applicability_facets(scope(), [], {"entity": ["ip:10.0.0.1"]})
    facets = {**spec.required_facets, **spec.optional_facets}
    record = _record("MEM-SELECTED-SCOPE", facets=facets).model_copy(update={"applicability": spec})
    query = SocMemoryQuery(
        facets={**facets, "entity": ["rule:1"], "behavior_fingerprint": [fingerprint]},
        metadata={"memory_profile_id": spec.profile_id, "memory_profile_version": spec.profile_version, "memory_feature_schema_version": spec.feature_schema_version},
    )
    report = evaluate_memory_applicability(record, query, {})
    assert report.status.value == "partial"
    assert "entity" not in report.missing_required_facet_keys
    assert report.missing_reuse_conditions[0].values == ["ip:10.0.0.1"]
    assert report.context_only_allowed
    query.facets["entity"] = ["ip:10.0.0.1"]
    matched = evaluate_memory_applicability(record, query, {})
    if fingerprint == "hash":
        assert matched.status.value == "applicable"
    else:
        assert matched.context_only_allowed
