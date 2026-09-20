"""Preserve historical exact matching without changing the 512-character contract."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_soc_memory_coverage import evaluate, query, scope
from test_soc_memory_retrieval_v2 import _record
from test_soc_pingan_memory_profile import _context, _observe, _run, _service

from soc_agent.contracts import AlertInput, EntityKind, EntityMention, MemoryPatternDataClass, MemoryPatternSourceType, SocMemoryCandidateType
from soc_agent.contracts.schemas import RoleResolution, RoleResolutionStatus, SocMemoryReuseCondition
from soc_agent.core import SocMemoryService
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile
from soc_agent.memory import InMemoryMemoryCandidateRepository, InMemoryMemoryPatternRepository, memory_pattern_command_from_run, memory_query_from_analysis_request
from soc_agent.memory.facets import memory_facets_from_analysis_request, memory_facets_from_analysis_run, merge_memory_facets
from soc_agent.memory.lessons import promote_memory_applicability_facets
from soc_agent.memory.scope_bindings import scope_bindings
from soc_agent.memory.scope_options import option_page, scope_samples
from soc_agent.memory.scoring import evaluate_memory_scope
from soc_agent.pipeline.extractor import extract_entities
from soc_agent.utils.hashing import stable_hash


@pytest.fixture(autouse=True)
def fixed_profile(monkeypatch):
    monkeypatch.setenv("SOC_NORMALIZATION_ASSIST_MODE", "off")


def with_entity(value, *, kind=EntityKind.URL):
    run = _run(1)
    run.llm_analysis_request.extracted_entities.mentions = [EntityMention(kind=kind, value=value, key=f"{kind.value}:{value}")]
    return run


class HistoricalRawEntityProfile(PingAnSocMemoryProfile):
    """Fixture for the raw entity facets persisted before large-input handling."""

    def project_run_facets(self, run):
        facets = super().project_run_facets(run)
        facets["entity"] = sorted({mention.key.strip() for mention in run.llm_analysis_request.extracted_entities.mentions})
        return facets


def extracted_url_run(index, entity_key):
    run = _run(index, service_url=entity_key.removeprefix("url:"))
    request = run.llm_analysis_request
    request.extracted_entities = extract_entities(AlertInput(alert_id=request.alert_id, source=request.source, detection=request.detection, classification=request.classification, entities=request.canonical_entities))
    return run


_URL_PREFIX = "url:https://example.test/"
_UNICODE_BOUNDARY = _URL_PREFIX + "a" * (512 - len(_URL_PREFIX) - 1) + "ß"


@pytest.mark.parametrize("semantic", [False, True], ids=["profile-7", "profile-9"])
@pytest.mark.parametrize(
    ("stored_key", "incoming_key"),
    [
        (_URL_PREFIX + "path", _URL_PREFIX + "path"),
        ("url:" + "a" * 508, "url:" + "a" * 508),
        (_UNICODE_BOUNDARY, _UNICODE_BOUNDARY.casefold()),
        ("url:sha256:" + "a" * 64, "url:sha256:" + "a" * 64),
    ],
    ids=["ordinary", "512-ascii", "casefold-crosses-512", "native-sha256-literal"],
)
def test_old_reviewed_exact_conditions_still_match(stored_key, incoming_key, semantic):
    stored = extracted_url_run(1, stored_key)
    current = extracted_url_run(2, incoming_key)
    original = stored.model_dump(mode="json")
    profile = HistoricalRawEntityProfile(semantic_features=semantic)
    facets = profile.project_run_facets(stored)
    signature = profile.build_pattern_signature(stored, facets=facets)
    applicability = profile.build_applicability(consensus_facets=signature.facets, strong_anchor_facets=signature.facets)
    reviewed = promote_memory_applicability_facets(applicability, [], {"entity": [stored_key]})
    frozen_scope = reviewed.model_dump(mode="json")
    assert stored_key in [mention.key for mention in stored.llm_analysis_request.extracted_entities.mentions]
    assert len(stored_key) <= 512

    current_profile = PingAnSocMemoryProfile(semantic_features=semantic)
    current_query = memory_query_from_analysis_request(current.llm_analysis_request, profile=current_profile)
    result = evaluate_memory_scope(reviewed, SocMemoryCandidateType.BENIGN_PATTERN, current_query, {})
    assert result.status.value == "applicable"
    assert not result.missing_reuse_conditions

    different = extracted_url_run(3, incoming_key + "different")
    different_query = memory_query_from_analysis_request(different.llm_analysis_request, profile=current_profile)
    different_result = evaluate_memory_scope(reviewed, SocMemoryCandidateType.BENIGN_PATTERN, different_query, {})
    assert different_result.status.value == "partial"
    assert different_result.missing_reuse_conditions
    assert reviewed.model_dump(mode="json") == frozen_scope
    assert stored.model_dump(mode="json") == original


@pytest.mark.parametrize("kind", [EntityKind.URL, EntityKind.PROCESS, EntityKind.HOST, EntityKind.USER, EntityKind.EMAIL])
def test_long_query_entities_keep_complete_raw_values_and_evidence(kind):
    value = "Example/" + "路径" * 400 + "?tail=one"
    run = with_entity(value, kind=kind)
    original = run.model_dump(mode="json")
    expected = [f"{kind.value}:{value}"]
    stored = memory_facets_from_analysis_run(run)["entity"]
    queried = memory_query_from_analysis_request(run.llm_analysis_request).facets["entity"]
    assert stored == queried == expected
    assert merge_memory_facets({"entity": stored}, {"entity": queried}) == {"entity": expected}
    assert run.model_dump(mode="json") == original


def test_legacy_reference_record_with_long_raw_entity_remains_retrievable():
    value = "https://example.test/query?sql=" + "a" * 700
    record = _record("MEM-LEGACY-LONG-ENTITY", facets={"entity": ["url:" + value]})
    assert record.applicability is None
    frozen = record.model_dump(mode="json")
    repository = InMemoryMemoryCandidateRepository()
    repository.save_memory_record(record)
    service = SocMemoryService(record_repository=repository)
    matched = service.find_relevant_records(memory_query_from_analysis_request(with_entity(value).llm_analysis_request))
    changed = service.find_relevant_records(memory_query_from_analysis_request(with_entity(value + "different").llm_analysis_request))
    assert [match.memory_id for match in matched.matches] == [record.memory_id]
    assert matched.matches[0].applicability_report.status.value == "legacy_anchor_only"
    assert not changed.matches
    assert repository.get_memory_record(record.memory_id).model_dump(mode="json") == frozen


def test_long_role_values_keep_the_existing_casefolded_identity():
    run = _run(1)
    value = "Endpoint/" + "a" * 700
    run.llm_analysis_request.fact_reconstruction.role_resolutions = [
        RoleResolution(role=role, status=RoleResolutionStatus.CONFIRMED, selected_value=value, rationale="Synthetic confirmed role.", confidence=0.9) for role in ("attacker", "victim")
    ]
    original = run.model_dump(mode="json")
    assert memory_facets_from_analysis_request(run.llm_analysis_request)["role_entity"] == [f"{role}:{value}".casefold() for role in ("attacker", "victim")]
    assert run.model_dump(mode="json") == original


@pytest.mark.parametrize("value", ["sha256:" + "a" * 64, "a" * 506 + "ß"], ids=["native-literal", "casefold-crosses-512"])
def test_historical_object_conditions_match_raw_scope_bindings(value):
    run = _run(1)
    run.llm_analysis_request.canonical_entities.host.host_name = value
    condition = SocMemoryReuseCondition(facet_key="entity", value_prefix="host", values=["host:" + value])
    spec = scope(reuse_conditions=[condition])
    current = query("tool:p", "network_service:tcp/80")
    current.scope_bindings = scope_bindings(run.llm_analysis_request)
    current.facets["entity"] = ["host:" + value.casefold()]
    frozen = [binding.model_dump(mode="json") for binding in current.scope_bindings]
    assert evaluate(spec, current).status.value == "applicable"
    assert [binding.model_dump(mode="json") for binding in current.scope_bindings] == frozen
    run.llm_analysis_request.canonical_entities.host.host_name = value + "different"
    current.scope_bindings = scope_bindings(run.llm_analysis_request)
    assert evaluate(spec, current).status.value == "partial"


def test_historical_binding_replay_keeps_raw_values_but_new_options_are_bounded():
    run = _run(1)
    value = "host-" + "a" * 700
    run.llm_analysis_request.canonical_entities.host.host_name = value
    original = run.model_dump(mode="json")
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)
    observation = _observe(service, run, "legacy-binding:1").observation
    frozen = observation.model_dump(mode="json")
    candidate = SimpleNamespace(
        tenant_id=observation.tenant_id,
        applicability=None,
        metadata={"observation_ids": [observation.observation_id]},
        source=SimpleNamespace(metadata={}, alert_id=run.alert_id),
    )
    page = option_page(candidate, repository, facet_key="entity", prefix="host")
    assert page["items"] == []
    assert "host:" + value in scope_samples(candidate, repository)[0]["facets"]["entity"]
    assert _observe(service, run, "legacy-binding:1").observation.model_dump(mode="json") == frozen
    assert repository.get_memory_pattern_observation(observation.observation_id).model_dump(mode="json") == frozen
    assert run.model_dump(mode="json") == original


def test_old_observation_with_native_hash_literal_replays_without_rewriting():
    run = with_entity("sha256:" + "a" * 64)
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)
    command = memory_pattern_command_from_run(
        run,
        source_type=MemoryPatternSourceType.BATCH_ALERT,
        transport_ref="legacy-literal:1",
        environment="prd",
        data_class=MemoryPatternDataClass.OPERATIONAL,
        policy_fingerprint=stable_hash(service.policy.model_dump(mode="json")),
        profile=HistoricalRawEntityProfile(),
    )
    old = service.ingest_observation(command, context=_context()).observation
    frozen = old.model_dump(mode="json")
    replay = _observe(service, run, "legacy-literal:1").observation
    assert replay.model_dump(mode="json") == frozen
    assert repository.get_memory_pattern_observation(old.observation_id).model_dump(mode="json") == frozen


def test_legacy_whitespace_normalization_stays_at_pattern_contract_boundary():
    value = "a" + " " * 700 + "z"
    run = with_entity(value)
    original = run.model_dump(mode="json")
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)
    observation = _observe(service, run, "legacy-whitespace:1").observation
    frozen = observation.model_dump(mode="json")
    assert observation.signature.facets["entity"] == ["url:a z"]
    assert memory_facets_from_analysis_run(run)["entity"] == ["url:" + value]
    assert memory_query_from_analysis_request(run.llm_analysis_request).facets["entity"] == ["url:" + value]
    assert _observe(service, run, "legacy-whitespace:1").observation.model_dump(mode="json") == frozen
    assert run.model_dump(mode="json") == original


def test_oversized_learning_entities_are_filtered_without_changing_query_or_evidence():
    run = with_entity("a" * 509)
    original = run.model_dump(mode="json")
    profile = PingAnSocMemoryProfile()
    before = memory_query_from_analysis_request(run.llm_analysis_request, profile=profile).model_dump(mode="json")
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)

    result = _observe(service, run, "filtered-entity:1")

    assert not result.observation.signature.facets.get("entity")
    assert result.observation.signature.facets["detection_key"]
    assert result.observation.signature.facets["behavior_fingerprint"]
    assert result.candidate is None  # The ordinary recurrence threshold still applies.
    assert memory_query_from_analysis_request(run.llm_analysis_request, profile=profile).model_dump(mode="json") == before
    assert memory_facets_from_analysis_run(run)["entity"] == ["url:" + "a" * 509]
    assert _observe(service, run, "filtered-entity:1").observation == result.observation
    assert run.model_dump(mode="json") == original


def test_other_oversized_pattern_contract_values_still_fail_without_mutating_evidence():
    run = _run(1)
    original = run.model_dump(mode="json")
    with pytest.raises(ValidationError, match="512"):
        memory_pattern_command_from_run(
            run,
            source_type=MemoryPatternSourceType.BATCH_ALERT,
            transport_ref="invalid-contract:1",
            environment="x" * 513,
            data_class=MemoryPatternDataClass.OPERATIONAL,
            policy_fingerprint="test-policy",
        )
    assert run.model_dump(mode="json") == original
