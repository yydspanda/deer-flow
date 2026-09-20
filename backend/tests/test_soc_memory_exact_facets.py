"""Long exact entities stay bounded without losing evidence or reuse scope."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_soc_memory_coverage import evaluate, query, scope
from test_soc_pingan_memory_profile import _observe, _run, _service

from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.contracts import EntityKind, EntityMention, MemoryPatternDataClass, MemoryPatternSourceType
from soc_agent.contracts.schemas import RoleResolution, RoleResolutionStatus, SocMemoryReuseCondition, SocMemoryScopeBinding
from soc_agent.memory import InMemoryMemoryPatternRepository, memory_pattern_command_from_run, memory_query_from_analysis_request
from soc_agent.memory.facets import memory_facets_from_analysis_request, memory_facets_from_analysis_run, merge_memory_facets
from soc_agent.memory.scope_options import option_page, scope_samples


@pytest.fixture(autouse=True)
def fixed_profile(monkeypatch):
    monkeypatch.setenv("SOC_NORMALIZATION_ASSIST_MODE", "off")


def with_entity(value, *, kind=EntityKind.URL):
    run = _run(1)
    run.llm_analysis_request.extracted_entities.mentions = [EntityMention(kind=kind, value=value, key=f"{kind.value}:{value}")]
    return run


@pytest.mark.parametrize("kind", [EntityKind.URL, EntityKind.PROCESS, EntityKind.HOST, EntityKind.USER, EntityKind.EMAIL])
def test_long_exact_entities_share_write_and_query_projection_without_changing_evidence(kind):
    value = "Example/" + "路径" * 400 + "?tail=one"
    run = with_entity(value, kind=kind)
    original = run.model_dump(mode="json")
    stored = memory_facets_from_analysis_run(run)["entity"]
    queried = memory_query_from_analysis_request(run.llm_analysis_request).facets["entity"]
    changed = memory_facets_from_analysis_run(with_entity(value + "two", kind=kind))["entity"]
    case_equivalent = memory_facets_from_analysis_run(with_entity(value.swapcase(), kind=kind))["entity"]

    assert len(stored[0]) <= 512
    assert stored[0].startswith(f"{kind.value}:sha256:")
    assert stored == queried == case_equivalent
    assert stored != changed
    assert merge_memory_facets({"entity": stored}, {"entity": queried}) == {"entity": stored}
    assert run.model_dump(mode="json") == original


@pytest.mark.parametrize("value", ["a" * 507, "a" * 508, "a" * 509, "a " * 250 + " " * 50])
def test_short_and_legacy_whitespace_values_keep_their_original_identity(value):
    run = with_entity(value)
    raw_key = run.llm_analysis_request.extracted_entities.mentions[0].key
    facet = memory_facets_from_analysis_run(run)["entity"][0]
    if len(" ".join(raw_key.split())) <= 512:
        assert facet == raw_key.strip()
    else:
        assert len(facet) <= 512
        assert facet != raw_key


def test_a_short_literal_cannot_impersonate_a_generated_fingerprint():
    fingerprint = memory_facets_from_analysis_run(with_entity("x" * 900))["entity"][0]
    literal = with_entity(fingerprint.removeprefix("url:"))
    assert memory_facets_from_analysis_run(literal)["entity"] != [fingerprint]


def test_legacy_long_whitespace_uses_the_existing_pattern_identity_everywhere():
    run = with_entity("a" + " " * 700 + "z")
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)
    observation = _observe(service, run, "legacy-whitespace:1").observation
    assert observation.signature.facets["entity"] == ["url:a z"]
    assert memory_facets_from_analysis_run(run)["entity"] == ["url:a z"]
    assert memory_query_from_analysis_request(run.llm_analysis_request).facets["entity"] == ["url:a z"]
    assert run.llm_analysis_request.extracted_entities.mentions[0].value == "a" + " " * 700 + "z"


def test_long_role_values_preserve_roles_and_use_complete_values():
    run = _run(1)
    request = run.llm_analysis_request
    value = "endpoint/" + "a" * 700
    request.fact_reconstruction.role_resolutions = [RoleResolution(role=role, status=RoleResolutionStatus.CONFIRMED, selected_value=value, rationale="Synthetic confirmed role.", confidence=0.9) for role in ("attacker", "victim")]
    values = memory_facets_from_analysis_request(request)["role_entity"]
    assert all(len(item) <= 512 for item in values)
    assert values[0].startswith("attacker:sha256:")
    assert values[1].startswith("victim:sha256:")
    assert values[0] != values[1]


def test_long_url_can_accumulate_and_match_without_changing_behavior_identity():
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)
    value = "https://example.test/query?sql=" + "a" * 1500
    first = _run(1, service_url=value)
    second = _run(2, service_url=value)
    first_result = _observe(service, first, "long-url:1")
    result = _observe(service, second, "long-url:2")
    assert result.candidate is not None
    assert _observe(service, first, "long-url:1").observation == first_result.observation
    profile = build_soc_memory_profile_registry().resolve_run(second)
    request = second.llm_analysis_request
    facets = profile.project_query_facets(request)
    assert result.candidate.facets["entity"] == facets["entity"]
    assert all(len(item) <= 512 for item in facets["entity"])
    assert request.canonical_entities.http.url == value
    assert profile.project_run_facets(_run(3, service_url=value + "changed"))["behavior_fingerprint"] == facets["behavior_fingerprint"]


def test_raw_object_bindings_remain_replay_stable_but_review_and_matching_use_bounded_values():
    value = "workstation-" + "a" * 700
    run = with_entity(value, kind=EntityKind.HOST)
    run.llm_analysis_request.canonical_entities.host.host_name = value
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)
    observation = _observe(service, run, "long-host:1").observation
    raw_binding = observation.signature.scope_bindings[0]
    assert raw_binding.facets["entity"] == [f"host:{value}"]
    candidate = SimpleNamespace(
        tenant_id=observation.tenant_id,
        applicability=None,
        metadata={"observation_ids": [observation.observation_id]},
        source=SimpleNamespace(metadata={}, alert_id=run.alert_id),
    )
    page = option_page(candidate, repository, facet_key="entity", prefix="host")
    assert len(page["items"]) == 1
    selected = page["items"][0]["value"]
    assert len(selected) <= 512
    assert selected == memory_facets_from_analysis_run(run)["entity"][0]
    spec = scope(reuse_conditions=[SocMemoryReuseCondition(facet_key="entity", value_prefix="host", values=[selected])])
    current = query("tool:p", "network_service:tcp/80")
    current.facets["entity"] = [selected]
    current.scope_bindings = [raw_binding]
    assert evaluate(spec, current).status.value == "applicable"
    current.scope_bindings = [SocMemoryScopeBinding(source_ref="entities.endpoint", facets={"entity": [f"host:{value}other"]})]
    assert evaluate(spec, current).status.value == "partial"
    assert observation.signature.scope_bindings[0] == raw_binding
    assert scope_samples(candidate, repository)[0]["scope_bindings"][0] == raw_binding


def test_historical_binding_only_long_values_replay_without_rewriting_observations():
    run = _run(1)
    run.llm_analysis_request.canonical_entities.host.host_name = "host-" + "a" * 700
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)
    first = _observe(service, run, "legacy-binding:1").observation
    frozen = first.model_dump(mode="json")
    second = _observe(service, run, "legacy-binding:1").observation
    assert second.model_dump(mode="json") == frozen
    assert len(first.signature.scope_bindings[0].facets["entity"][0]) > 512


def test_contract_validation_errors_are_not_treated_as_normal_pattern_ineligibility():
    run = _run(1)
    # Environment is operator-owned, not an arbitrary exact entity to fingerprint.
    with pytest.raises(ValidationError, match="1-512"):
        memory_pattern_command_from_run(
            run,
            source_type=MemoryPatternSourceType.BATCH_ALERT,
            transport_ref="invalid-contract:1",
            environment="x" * 513,
            data_class=MemoryPatternDataClass.OPERATIONAL,
            policy_fingerprint="test-policy",
        )
