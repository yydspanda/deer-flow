"""New reuse limits stay bounded without changing source or matching facts."""

from types import SimpleNamespace

import pytest
from test_soc_memory_coverage import scope
from test_soc_memory_lesson_drafting import _candidate, _review_context
from test_soc_memory_patterns import _run

from soc_agent.contracts import Verdict
from soc_agent.contracts.schemas import SocMemoryReuseCondition, SocMemoryScopeBinding
from soc_agent.core import SocMemoryLessonDraftService, SocMemoryService, SocServiceConflictError
from soc_agent.memory.lessons import promote_memory_applicability_facets
from soc_agent.memory.scope_options import candidate_with_scope_selections, option_page, scope_lesson_sources, scope_samples


def _source_case(facet_key, prefix, *, binding=False):
    candidate = _candidate()
    short = prefix + ":" + "a" * (511 - len(prefix))
    long = prefix + ":" + "a" * (512 - len(prefix))
    candidate = candidate.model_copy(update={"metadata": {"observation_ids": ["observation-1"]}})
    spec = candidate.applicability
    observation = SimpleNamespace(
        tenant_id=candidate.tenant_id,
        profile_id=spec.profile_id,
        profile_version=spec.profile_version,
        feature_schema_version=spec.feature_schema_version,
        environment="prd",
        source=SimpleNamespace(source_id="source-1", alert_id=candidate.source.alert_id, run_id=candidate.source.run_id),
        signature=SimpleNamespace(
            facets=candidate.facets if binding else {**candidate.facets, facet_key: [short, long]},
            scope_bindings=[SocMemoryScopeBinding(source_ref="object:1", facets={facet_key: [short, long]})] if binding else [],
        ),
    )
    repository = SimpleNamespace(get_memory_pattern_observation=lambda identifier: observation, get_memory_candidate=lambda identifier: candidate)
    return candidate, repository, short, long


@pytest.mark.parametrize("facet_key,prefix", [("entity", "host"), ("role_entity", "destination")])
@pytest.mark.parametrize("binding", [False, True], ids=["signature", "object-binding"])
def test_optional_directory_omits_oversized_values_but_preserves_sources(facet_key, prefix, binding):
    candidate, repository, short, long = _source_case(facet_key, prefix, binding=binding)
    before = candidate.model_dump_json()
    sources = scope_samples(candidate, repository)

    page = option_page(candidate, repository, facet_key=facet_key, prefix=prefix)

    assert [item["value"] for item in page["items"]] == [short]
    assert page["total"] == 1
    assert page["source_sample_count"] == 1
    assert long in sources[0]["facets"][facet_key]
    assert scope_samples(candidate, repository) == sources
    assert candidate.model_dump_json() == before


def test_directory_fallback_keeps_original_run_bindings_out_of_new_choices():
    candidate = _candidate()
    run = _run(1)
    run.llm_analysis_request.tenant_id = candidate.tenant_id
    long = "host:" + "a" * 600
    run.llm_analysis_request.canonical_entities.host.host_name = long.removeprefix("host:")
    repository = SimpleNamespace(get_run=lambda run_id: run)
    before = run.model_dump_json()

    page = option_page(candidate, repository, facet_key="entity", prefix="host")
    samples = scope_samples(candidate, repository)

    assert page["items"] == []
    assert {"facet_key": "entity", "value_prefix": "host"} not in page["groups"]
    assert long in samples[0]["facets"]["entity"]
    assert any(long in binding.facets.get("entity", []) for binding in samples[0]["scope_bindings"])
    assert run.model_dump_json() == before


@pytest.mark.parametrize("facet_key,prefix", [("entity", "host"), ("role_entity", "destination")])
def test_scope_selection_rejects_oversized_source_values_before_expansion(facet_key, prefix):
    candidate, repository, _, long = _source_case(facet_key, prefix, binding=True)
    before = candidate.model_dump_json()

    with pytest.raises(ValueError, match="512") as error:
        candidate_with_scope_selections(candidate, repository, {facet_key: [long]})

    assert long not in str(error.value)
    assert candidate.model_dump_json() == before


@pytest.mark.parametrize("surface", ["preview", "draft"])
def test_direct_service_calls_cannot_bypass_optional_entity_limit(surface):
    candidate, repository, _, long = _source_case("entity", "host", binding=True)
    before = candidate.model_dump_json()
    selections = {"entity": [long]}
    calls = []
    if surface == "preview":
        service = SocMemoryService(candidate_repository=repository)
        with pytest.raises(ValueError, match="512"):
            service.preview_candidate_governance(candidate.candidate_id, promoted_facet_values=selections)
    else:
        service = SocMemoryLessonDraftService(candidate_repository=repository, drafter=SimpleNamespace(draft=lambda *args, **kwargs: calls.append(args)))
        with pytest.raises(SocServiceConflictError, match="512"):
            service.draft_business_lesson(candidate.candidate_id, reviewer_verdict=Verdict.FALSE_POSITIVE, promoted_facet_values=selections, context=_review_context())
    assert calls == []
    assert candidate.model_dump_json() == before


@pytest.mark.parametrize("value", ["host:" + "a" * 506 + "ß", "host:a" + " " * 500 + "b"])
def test_legal_saved_selections_keep_existing_unicode_and_whitespace_semantics(value):
    candidate = _candidate()
    condition = SocMemoryReuseCondition(facet_key="entity", value_prefix="host", values=[value])
    spec = candidate.applicability.model_copy(update={"optional_facets": {"entity": [value]}, "reuse_conditions": [condition]})
    candidate = candidate.model_copy(update={"applicability": spec})
    before = candidate.model_dump_json()

    selected = candidate_with_scope_selections(candidate, SimpleNamespace(), {"entity": [value]})
    narrowed = promote_memory_applicability_facets(selected.applicability, [], {"entity": [value]})

    assert narrowed.reuse_conditions == [condition]
    assert candidate.model_dump_json() == before


def test_narrowed_draft_does_not_reintroduce_oversized_binding_facets():
    parent = _candidate()
    spec = scope(reuse_conditions=[SocMemoryReuseCondition(facet_key="role_entity", value_prefix="destination", values=["destination:192.0.2.1"])])
    parent = parent.model_copy(update={"applicability": spec, "metadata": {"observation_ids": ["o1", "o2"]}})
    long = "host:" + "x" * 600
    observations = {}
    for index in (1, 2):
        role = f"destination:192.0.2.{index}"
        observations[f"o{index}"] = SimpleNamespace(
            tenant_id=parent.tenant_id,
            profile_id=spec.profile_id,
            profile_version=spec.profile_version,
            feature_schema_version=spec.feature_schema_version,
            environment="dev",
            source=SimpleNamespace(source_id=f"s{index}", alert_id=f"a{index}", run_id=f"r{index}"),
            signature=SimpleNamespace(
                facets={"detection_key": ["rule:test"], "behavior_component_core": spec.selected_behavior_components},
                scope_bindings=[SocMemoryScopeBinding(source_ref=f"object:{index}", facets={"role_entity": [role], "entity": ["host:retained", long]})],
            ),
            lesson=None,
        )
    repository = SimpleNamespace(get_memory_pattern_observation=observations.get)
    sources = scope_samples(parent, repository)
    before = parent.model_dump_json()

    narrowed = scope_lesson_sources(parent, repository, spec)

    assert narrowed.facets["entity"] == ["host:retained"]
    assert narrowed.metadata["observation_ids"] == ["o1"]
    assert scope_samples(parent, repository) == sources
    assert long in sources[0]["facets"]["entity"]
    assert parent.model_dump_json() == before
