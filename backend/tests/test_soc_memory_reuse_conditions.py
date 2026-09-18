"""Extra reuse limits must not silently turn off relevant model context."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_soc_memory_retrieval_v2 import _record
from test_soc_memory_scope_narrowing import scope

from soc_agent.contracts import SocMemoryApplicabilitySpec, SocMemoryDecisionDirective, SocMemoryDecisionImpact, SocMemoryQuery
from soc_agent.core.service import SocMemoryService, SocServiceError, _validate_memory_record_applicability
from soc_agent.memory import InMemoryMemoryCandidateRepository
from soc_agent.memory.governance import scope_relation
from soc_agent.memory.lessons import promote_memory_applicability_facets
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.memory.retrieval import memory_context_item
from soc_agent.memory.scoring import evaluate_memory_applicability


def evaluate(spec, *, fingerprint="hash", entities=None, roles=None):
    facets = {**spec.required_facets, **spec.optional_facets}
    query = SocMemoryQuery(
        facets={**facets, "behavior_fingerprint": [fingerprint], "entity": entities or ["ip:other"], "role_entity": roles or []},
        metadata={"memory_profile_id": spec.profile_id, "memory_profile_version": spec.profile_version, "memory_feature_schema_version": spec.feature_schema_version},
    )
    return evaluate_memory_applicability(_record("MEM-REUSE", facets=facets).model_copy(update={"applicability": spec}), query, {})


@pytest.mark.parametrize("fingerprint", ["hash", "different"])
def test_missing_reuse_limit_keeps_relevant_context(fingerprint):
    base = scope()
    before = deepcopy(base.model_dump())
    narrowed = promote_memory_applicability_facets(base, [], {"entity": ["ip:10.0.0.1"]})
    result = evaluate(narrowed, fingerprint=fingerprint)
    assert result.status.value == "partial"
    assert result.context_only_allowed
    assert result.missing_reuse_conditions[0].values == ["ip:10.0.0.1"]
    assert narrowed.required_facets == base.required_facets
    assert narrowed.optional_facets == base.optional_facets
    assert base.model_dump() == before
    assert SocMemoryApplicabilitySpec.model_validate(narrowed.model_dump()) == narrowed


def test_matching_reuse_limit_allows_exact_scope():
    narrowed = promote_memory_applicability_facets(scope(), [], {"entity": ["ip:10.0.0.1"]})
    result = evaluate(narrowed, entities=["ip:10.0.0.1"])
    assert result.status.value == "applicable"
    assert not result.context_only_allowed


def test_source_and_destination_are_separate_and_conditions():
    base = scope()
    base.optional_facets["role_entity"] = ["source:10.0.0.1", "source:10.0.0.2", "destination:10.0.0.3"]
    narrowed = promote_memory_applicability_facets(base, [], {"role_entity": base.optional_facets["role_entity"]})
    assert len(narrowed.reuse_conditions) == 2
    assert evaluate(narrowed, roles=["source:10.0.0.1"]).status.value == "partial"
    assert evaluate(narrowed, roles=["source:10.0.0.2", "destination:10.0.0.3"]).status.value == "applicable"


def test_old_required_limit_still_blocks_context():
    base = scope()
    base.required_facets["entity"] = base.optional_facets.pop("entity")[:1]
    base.context_only_required_facet_keys.append("entity")
    assert not evaluate(base).context_only_allowed


def test_scope_identity_and_comparison_include_reuse_limits():
    base = scope()
    narrowed = promote_memory_applicability_facets(base, [], {"entity": ["ip:10.0.0.1"]})
    registry = SocMemoryProfileRegistry()
    assert scope_relation(base, narrowed, registry) == "strict_superset"
    assert scope_relation(narrowed, base, registry) == "strict_subset"


def test_internal_aliases_are_not_new_business_limits():
    base = scope()
    base.optional_facets["behavior_component_core"] = ["protocol:udp"]
    with pytest.raises(ValueError, match="internal"):
        promote_memory_applicability_facets(base, [], {"behavior_component_core": ["protocol:udp"]})


@pytest.mark.parametrize("fingerprint", ["hash", "different"])
def test_real_retrieval_path_retains_reference_and_projects_the_unmet_limit(fingerprint):
    spec = promote_memory_applicability_facets(scope(), [], {"entity": ["ip:10.0.0.1"]})
    facets = {**spec.required_facets, **spec.optional_facets}
    directive = SocMemoryDecisionDirective(effect="override", target_verdict="false_positive", rationale="Reviewed bounded lesson", required_facet_keys=["behavior_fingerprint"])
    record = _record("MEM-REUSE-RETRIEVAL", facets=facets).model_copy(update={"applicability": spec, "decision_directive": directive, "decision_impact": SocMemoryDecisionImpact.DETECTION_DECISION})
    repository = InMemoryMemoryCandidateRepository()
    repository.save_memory_record(record)
    service = SocMemoryService(record_repository=repository)
    query = SocMemoryQuery(
        tenant_scope="pingan",
        tenant_id="pingan",
        facets={**facets, "entity": ["ip:10.0.0.2"], "behavior_fingerprint": [fingerprint]},
        metadata={"memory_profile_id": spec.profile_id, "memory_profile_version": spec.profile_version, "memory_feature_schema_version": spec.feature_schema_version},
    )
    result = service.find_relevant_records(query)
    assert result.returned_context_only_count == 1
    item = memory_context_item(result.matches[0], query_facets=query.facets, retrieval_policy_version=query.policy_version)
    assert item.memory_comparison.use_mode.value == "context_only"
    assert not item.memory_comparison.decision_directive_applicable
    assert item.memory_comparison.missing_reuse_conditions[0].values == ["ip:10.0.0.1"]
    query.facets = facets
    exact = service.find_relevant_records(query)
    item = memory_context_item(exact.matches[0], query_facets=query.facets, retrieval_policy_version=query.policy_version)
    assert item.memory_comparison.decision_directive_applicable


def test_missed_reuse_limit_does_not_bypass_original_exclusions():
    base = scope()
    base.excluded_facets["product"] = ["excluded-product"]
    spec = promote_memory_applicability_facets(base, [], {"entity": ["ip:10.0.0.1"]})
    facets = {**spec.required_facets, **spec.optional_facets, "entity": ["ip:other"], "product": ["excluded-product"]}
    query = SocMemoryQuery(facets=facets, metadata={"memory_profile_id": spec.profile_id, "memory_profile_version": spec.profile_version, "memory_feature_schema_version": spec.feature_schema_version})
    report = evaluate_memory_applicability(_record("MEM-EXCLUDED", facets=facets).model_copy(update={"applicability": spec}), query, {})
    assert report.status.value == "not_applicable"
    assert not report.context_only_allowed


def test_review_cannot_change_reference_scope_while_adding_reuse_only_limit():
    base = scope()
    narrowed = promote_memory_applicability_facets(base, [], {"entity": ["ip:10.0.0.1"]})
    candidate = SimpleNamespace(applicability=base)
    _validate_memory_record_applicability(candidate, narrowed)
    changed = narrowed.model_copy(update={"context_only_similarity_facet_keys": []})
    with pytest.raises(SocServiceError, match="preserve"):
        _validate_memory_record_applicability(candidate, changed)


def test_review_cannot_silently_drop_saved_reuse_limit():
    base = promote_memory_applicability_facets(scope(), [], {"entity": ["ip:10.0.0.1"]})
    changed = base.model_copy(update={"reuse_conditions": []})
    with pytest.raises(SocServiceError, match="cannot remove saved"):
        _validate_memory_record_applicability(SimpleNamespace(applicability=base), changed)
