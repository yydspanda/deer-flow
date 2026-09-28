"""Synthetic application services require source-bound session and HTTP evidence."""

from copy import deepcopy

import pytest

from soc_agent.contracts import LLMAnalysisRequest, SocMemoryCandidateType
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile
from soc_agent.integrations.pingan.memory.service_identity import proven_application_services
from soc_agent.memory.retrieval import memory_query_from_analysis_request
from soc_agent.memory.scoring import evaluate_memory_scope
from soc_agent.utils.hashing import stable_hash

_SOURCE = "sources.events[0]"
_NETWORK = "entities.network.observations[0]"
_HTTP = "entities.http.observations[0]"
# Frozen synthetic regression snapshots; tests must never regenerate these.
# The separately shipped Profile 7 release baseline remains authoritative.
_FROZEN_FACET_HASHES = {
    "7": "6c4f9f6183d6074496eb429aa078a6856afa35c9059eca14c9c9ab94d2b3759f",
    "8": "f8dbfea5b322e34edb6349c20de3178316e8b6c19a394f69e790cd17de1df9e7",
    "9": "1333c682358a04587f32d4818e963bbd553b95da6e56bb26ce51638c6182141e",
    "10": "a01754bd87bb075f6bc66fb49f94f1508e9a63fc1585edabf2001bd24ffbc6ab",
    "11": "cab9c67940b87a36dac837b972666f7b5d2d798b73fffd81e2c98812e7e1710c",
}


def _payload(*, protocol="http", responder="destination", aggregate=False):
    network = {
        "observation_id": "synthetic-network",
        "evidence_path": _SOURCE + "#parsed",
        "source_ip": "192.0.2.10",
        "destination_ip": "192.0.2.20",
        "src_port": 51000,
        "dst_port": 443 if protocol == "https" else 80,
        "protocol": protocol,
    }
    if responder == "source":
        network["src_port"], network["dst_port"] = network["dst_port"], network["src_port"]
    http = {
        "observation_id": "synthetic-http",
        "evidence_path": _SOURCE + "#parsed",
        "method": "POST",
        "status_code": 500,
    }
    # Deliberately opaque source names: projection must use adapter semantics,
    # never vendor aliases or a smaller-port heuristic.
    fields = {
        "source_ip": "endpoint_a",
        "destination_ip": "endpoint_b",
        "src_port": "socket_a",
        "dst_port": "socket_b",
        "protocol": "application",
    }
    provenance = [
        {
            "canonical_path": _NETWORK + "." + key,
            "selected_value": str(network[key]),
            "selected_from": _SOURCE + "#parsed." + source,
            "source_layer": "raw_message",
            "trust_level": "high",
            "selection_reason": "synthetic reviewed adapter",
        }
        for key, source in fields.items()
    ]
    provenance.extend(
        [
            {
                "canonical_path": _HTTP + ".method",
                "selected_value": "POST",
                "selected_from": _SOURCE + "#decoded.request.start_line",
                "source_layer": "raw_message",
                "trust_level": "high",
                "selection_reason": "synthetic HTTP request parsing",
            },
            {
                "canonical_path": _HTTP + ".status_code",
                "selected_value": "500",
                "selected_from": _SOURCE + "#parsed.response_status",
                "source_layer": "raw_message",
                "trust_level": "high",
                "selection_reason": "synthetic HTTP response mapping",
            },
        ]
    )
    roles = ("responder", "initiator") if responder == "source" else ("initiator", "responder")
    semantics = [
        {
            "field_path": _SOURCE + "#parsed." + field,
            "semantic_type": "provider_reported_session_" + role,
            "meaning": "Synthetic reviewed session role, independent of security role.",
            "participates_in_entities": True,
            "participates_in_reasoning": True,
        }
        for field, role in zip(("endpoint_a", "endpoint_b"), roles, strict=True)
    ]
    semantics.extend(
        [
            {
                "field_path": _SOURCE + "#parsed.request",
                "semantic_type": "observed_HTTP_request_headers",
                "meaning": "Synthetic request headers.",
                "participates_in_reasoning": True,
            },
            {
                "field_path": _SOURCE + "#parsed.response_status",
                "semantic_type": "observed_HTTP_response_status",
                "meaning": "Synthetic response status, independent of success.",
                "participates_in_reasoning": True,
            },
        ]
    )
    entity = {"observations": [network]}
    if aggregate:
        entity.update({key: network[key] for key in fields})
        provenance.extend({**item, "canonical_path": item["canonical_path"].replace(_NETWORK, "entities.network")} for item in provenance[:5])
    return {
        "alert_id": "synthetic-application-service",
        "tenant_id": "pingan",
        "environment": "dev",
        "source": {"source_system": "zeus", "product": "synthetic-sensor", "integration_name": "pingan_legacy_alert_platform"},
        "detection": {"detection_key": "synthetic-http-rule", "rule_name": "Synthetic HTTP rule"},
        "canonical_entities": {"network": entity, "http": {"observations": [http]}},
        "source_field_semantics": semantics,
        "fact_reconstruction": {"canonical_field_provenance": provenance},
    }


def _project(payload):
    request = LLMAnalysisRequest.model_validate(payload)
    before = request.model_dump(mode="json")
    result = proven_application_services(request)
    assert request.model_dump(mode="json") == before
    return result


def _provenance(payload, field, *, ref=_NETWORK):
    return next(item for item in payload["fact_reconstruction"]["canonical_field_provenance"] if item["canonical_path"] == ref + "." + field)


@pytest.mark.parametrize(("protocol", "service"), [("http", "http/80"), ("https", "https/443"), ("HTTP", "http/80")])
@pytest.mark.parametrize("responder", ["source", "destination"])
def test_direct_source_proof_identifies_application_service_and_responder_port(protocol, service, responder):
    assert _project(_payload(protocol=protocol, responder=responder)) == {_NETWORK: service}


def test_service_does_not_require_a_low_port_or_invent_transport():
    payload = _payload()
    payload["canonical_entities"]["network"]["observations"][0]["dst_port"] = 61000
    _provenance(payload, "dst_port")["selected_value"] = "61000"
    assert _project(payload) == {_NETWORK: "http/61000"}


@pytest.mark.parametrize("protocol", [None, "", "tcp", "6", "udp", "unknown", "ftp"])
def test_non_http_or_missing_protocol_never_borrows_http_summary_protocol(protocol):
    payload = _payload()
    payload["canonical_entities"]["network"]["observations"][0]["protocol"] = protocol
    _provenance(payload, "protocol")["selected_value"] = str(protocol) or "missing"
    payload["canonical_entities"]["http"].update({"protocol": "http", "port": 80})
    payload["canonical_entities"]["http"]["observations"][0]["protocol"] = "http"
    assert _project(payload) == {}


@pytest.mark.parametrize("field", ["source_ip", "destination_ip", "src_port", "dst_port", "protocol"])
@pytest.mark.parametrize("failure", ["missing", "value_mismatch", "other_source", "semantic_only", "untrusted", "alternatives"])
def test_each_network_field_must_have_its_own_verified_source_provenance(field, failure):
    payload = _payload()
    proof = _provenance(payload, field)
    if failure == "missing":
        payload["fact_reconstruction"]["canonical_field_provenance"].remove(proof)
    elif failure == "value_mismatch":
        proof["selected_value"] = "different"
    elif failure == "other_source":
        proof["selected_from"] = proof["selected_from"].replace(_SOURCE, "sources.events[1]")
    elif failure == "semantic_only":
        proof["selected_from"] = _SOURCE + "#semantic-unverified"
    elif failure == "untrusted":
        proof["trust_level"] = "medium"
    else:
        proof["alternative_values"] = ["different"]
    assert _project(payload) == {}


@pytest.mark.parametrize("port", [None, 0, -1, 65536])
def test_invalid_port_is_not_a_service(port):
    payload = _payload()
    payload["canonical_entities"]["network"]["observations"][0]["dst_port"] = port
    _provenance(payload, "dst_port")["selected_value"] = str(port)
    assert _project(payload) == {}


@pytest.mark.parametrize("failure", ["missing", "security_role", "same_role", "excluded", "ambiguous"])
def test_only_reviewed_opposite_session_roles_prove_endpoint_roles(failure):
    payload = _payload()
    semantics = payload["source_field_semantics"]
    if failure == "missing":
        semantics.pop(1)
    elif failure == "security_role":
        semantics[1]["semantic_type"] = "vendor_security_role_assertion"
    elif failure == "same_role":
        semantics[1]["semantic_type"] = semantics[0]["semantic_type"]
    elif failure == "excluded":
        semantics[1]["participates_in_reasoning"] = False
    else:
        semantics.append({**semantics[1], "semantic_type": semantics[0]["semantic_type"]})
    assert _project(payload) == {}


@pytest.mark.parametrize("failure", ["missing", "other_source", "duplicate", "unverified", "wrong_semantics", "missing_semantics"])
def test_http_transaction_is_unique_same_source_and_grounded(failure):
    payload = _payload()
    http = payload["canonical_entities"]["http"]["observations"]
    if failure == "missing":
        http.clear()
    elif failure == "other_source":
        http[0]["evidence_path"] = "sources.events[1]#parsed"
    elif failure == "duplicate":
        http.append({**http[0], "observation_id": "second-http"})
    elif failure == "unverified":
        for field in ("method", "status_code"):
            _provenance(payload, field, ref=_HTTP)["selected_from"] = _SOURCE + "#semantic-unverified"
    elif failure == "wrong_semantics":
        for semantic in payload["source_field_semantics"][2:]:
            semantic["semantic_type"] = "vendor_detection_assertion"
    else:
        del payload["source_field_semantics"][2:]
    assert _project(payload) == {}


@pytest.mark.parametrize("direction", ["unknown", "in", "out", "proxy", "to_client"])
def test_explicit_unknown_or_conflicting_direction_cannot_be_overridden(direction):
    payload = _payload()
    payload["canonical_entities"]["network"]["observations"][0]["direction"] = direction
    assert _project(payload) == {}


@pytest.mark.parametrize(("responder", "direction"), [("source", "to_client"), ("destination", "to_server")])
def test_consistent_explicit_direction_is_preserved(responder, direction):
    payload = _payload(responder=responder)
    payload["canonical_entities"]["network"]["observations"][0]["direction"] = direction
    assert _project(payload) == {_NETWORK: "http/80"}


@pytest.mark.parametrize("field", ["forwarded_chain", "x_forwarded_for"])
def test_forwarding_evidence_keeps_the_service_unproven(field):
    payload = _payload()
    if field == "forwarded_chain":
        payload["canonical_entities"]["network"]["observations"][0][field] = ["192.0.2.30"]
    else:
        payload["canonical_entities"]["http"]["observations"][0][field] = "192.0.2.30"
    assert _project(payload) == {}


def test_http_summary_forwarding_cannot_be_ignored_by_an_observation():
    payload = _payload(aggregate=True)
    payload["canonical_entities"]["http"]["x_forwarded_for"] = "192.0.2.30"
    assert _project(payload) == {}


@pytest.mark.parametrize("field", [_NETWORK + ".destination_ip", _HTTP + ".method", _SOURCE + "#parsed.endpoint_b", "entities.network"])
def test_relevant_conflict_cannot_be_ignored(field):
    payload = _payload()
    payload["fact_reconstruction"]["conflict_reports"] = [{"conflict_type": "synthetic_conflict", "description": "Different source value.", "involved_fields": [field]}]
    assert _project(payload) == {}


def test_unscoped_conflict_role_keys_are_not_mistaken_for_unrelated_paths():
    payload = _payload()
    payload["fact_reconstruction"]["conflict_reports"] = [{"conflict_type": "network_direction_conflict", "description": "Different source role.", "candidate_values": {"source": ["192.0.2.10", "192.0.2.30"]}}]
    assert _project(payload) == {}


def test_decoded_conflict_is_related_to_the_same_parsed_source_field():
    payload = _payload()
    payload["fact_reconstruction"]["conflict_reports"] = [{"conflict_type": "synthetic_conflict", "description": "Different response endpoint.", "involved_fields": [_SOURCE + "#decoded.endpoint_b"]}]
    assert _project(payload) == {}


@pytest.mark.parametrize("projection", ["parsed", "decoded", "repaired"])
def test_conflict_on_a_whole_source_projection_blocks_its_fields(projection):
    payload = _payload()
    payload["fact_reconstruction"]["conflict_reports"] = [{"conflict_type": "synthetic_conflict", "description": "Conflicting source projection.", "involved_fields": [_SOURCE + "#" + projection]}]
    assert _project(payload) == {}


def test_unrelated_conflict_does_not_block_an_independent_service():
    payload = _payload()
    payload["fact_reconstruction"]["conflict_reports"] = [{"conflict_type": "synthetic_conflict", "description": "Different process value.", "involved_fields": ["entities.process.process_name"]}]
    assert _project(payload) == {_NETWORK: "http/80"}


def test_aggregate_requires_a_single_observation_and_its_own_identical_proof():
    payload = _payload(aggregate=True)
    assert _project(payload) == {_NETWORK: "http/80", "entities.network": "http/80"}
    _provenance(payload, "dst_port", ref="entities.network")["selected_from"] = "other#parsed.socket_b"
    assert _project(payload) == {_NETWORK: "http/80"}


def test_aggregate_cannot_join_multiple_objects_even_when_the_service_matches():
    payload = _payload(aggregate=True)
    network = payload["canonical_entities"]["network"]
    network["observations"].append({**network["observations"][0], "observation_id": "another-network", "source_ip": "192.0.2.30"})
    assert "entities.network" not in _project(payload)


def test_two_verified_network_objects_cannot_share_one_unbound_http_transaction():
    payload = _payload()
    network = payload["canonical_entities"]["network"]
    network["observations"].append({**network["observations"][0], "observation_id": "another-network", "src_port": 52000})
    for item in list(payload["fact_reconstruction"]["canonical_field_provenance"][:5]):
        proof = {**item, "canonical_path": item["canonical_path"].replace("observations[0]", "observations[1]")}
        if proof["canonical_path"].endswith(".src_port"):
            proof["selected_value"] = "52000"
        payload["fact_reconstruction"]["canonical_field_provenance"].append(proof)
    assert _project(payload) == {}


def test_no_observation_cannot_inherit_an_aggregate_or_http_port():
    payload = _payload(aggregate=True)
    payload["canonical_entities"]["network"]["observations"].clear()
    payload["canonical_entities"]["http"]["port"] = 80
    assert _project(payload) == {}


def test_conflicting_duplicate_provenance_is_not_arbitrarily_selected():
    payload = _payload()
    payload["fact_reconstruction"]["canonical_field_provenance"].append({**_provenance(payload, "dst_port"), "selected_value": "443"})
    assert _project(payload) == {}


def _profile_payload():
    payload = _payload(aggregate=True)
    payload["classification"] = {"category": "Synthetic SQL injection", "technique": ["T1190"]}
    payload["fact_reconstruction"]["scenario_hypotheses"] = [{"scenario_type": "web_attack", "rationale": "Synthetic adapter scenario."}]
    entities = payload["canonical_entities"]
    entities["http"].update({"method": "POST", "host": "synthetic.example"})
    entities["http"]["observations"][0]["host"] = "synthetic.example"
    entities["detections"] = [
        {
            "observation_id": "synthetic-detector-" + str(index),
            "evidence_path": _SOURCE + "#semantic",
            "event_scope_id": _SOURCE,
            "kind": "web_detection",
            "subject_refs": [_HTTP, _NETWORK],
            "name": "Synthetic HTTP detection " + str(index),
            "detector_id": "synthetic-" + str(index),
            "identity_basis": "adapter_declared",
        }
        for index in range(2)
    ]
    return payload


def test_proven_fresh_request_retains_complete_legacy_identity_and_eleven_core_conditions():
    request = LLMAnalysisRequest.model_validate(_profile_payload())
    profile10 = PingAnSocMemoryProfile(semantic_features=True)
    profile9 = PingAnSocMemoryProfile(semantic_features=True, directional_services=False)
    fresh = profile10.for_request(request)
    assert fresh.identity.profile_version == "9"
    assert fresh.project_query_facets(request) == profile9.project_query_facets(request)
    old_core = set(profile10.project_query_facets(request)["behavior_component_core"])
    new_core = set(fresh.project_query_facets(request)["behavior_component_core"])
    assert len(old_core) == 8
    assert len(new_core) == 11
    assert new_core - old_core == {
        "network_service:http/80",
        "detected_behavior:network:zeus:synthetic-sensor:id:synthetic-0@service:http/80",
        "detected_behavior:network:zeus:synthetic-sensor:id:synthetic-1@service:http/80",
    }
    assert fresh.projection_gaps(request) == []
    original_facets = profile9.project_query_facets(request)
    spec = profile9.build_applicability(consensus_facets=original_facets, strong_anchor_facets=original_facets)
    query = memory_query_from_analysis_request(request, profile=fresh)
    assert evaluate_memory_scope(spec, SocMemoryCandidateType.DETECTION_LESSON, query, {}).status == "applicable"


def test_fresh_request_without_proof_keeps_the_existing_directional_gap():
    payload = _profile_payload()
    payload["source_field_semantics"] = []
    request = LLMAnalysisRequest.model_validate(payload)
    profile10 = PingAnSocMemoryProfile(semantic_features=True)
    fresh = profile10.for_request(request)
    assert fresh.identity.profile_version == "10"
    assert fresh.project_query_facets(request) == profile10.project_query_facets(request)
    assert len(fresh.project_query_facets(request)["behavior_component_core"]) == 8
    assert fresh.projection_gaps(request)


@pytest.mark.parametrize("version", ["7", "8", "9", "10", "11"])
def test_saved_profiles_never_apply_new_service_proof_to_their_frozen_projection(version):
    payload = _profile_payload()
    payload["memory_profile"] = {"profile_id": "pingan.soc", "profile_version": version, "feature_schema_version": f"pingan.soc.memory_features.v{int(version) - 2}"}
    without_proof = deepcopy(payload)
    without_proof["source_field_semantics"] = []
    request = LLMAnalysisRequest.model_validate(payload)
    profile = PingAnSocMemoryProfile(semantic_features=True).for_request(request)
    assert profile.identity.profile_version == version
    assert stable_hash(profile.project_query_facets(request)) == _FROZEN_FACET_HASHES[version]
    assert profile.project_query_facets(request) == profile.project_query_facets(LLMAnalysisRequest.model_validate(without_proof))
    assert profile.projection_gaps(request) == profile.projection_gaps(LLMAnalysisRequest.model_validate(without_proof))


def test_verified_http_behavior_and_new_service_proof_require_profile_twelve():
    payload = _profile_payload()
    payload["canonical_entities"]["supplementary_facts"] = [
        {
            "observation_id": "synthetic-response-content",
            "evidence_path": _SOURCE + "#semantic",
            "event_scope_id": _SOURCE,
            "name": "Synthetic command response",
            "value": "uid=1000(synthetic)",
            "meaning": "Synthetic command output.",
            "subject_ref": _HTTP,
            "network_behavior": {"kind": "http_response_command_output"},
            "network_behavior_verification": "source_bound_v1",
        }
    ]
    request = LLMAnalysisRequest.model_validate(payload)
    profile = PingAnSocMemoryProfile(semantic_features=True).for_request(request)
    assert profile.identity.profile_version == "12"
    core = profile.project_query_facets(request)["behavior_component_core"]
    assert "http_observation:response=command_output" in core
    assert "network_service:http/80" in core


def test_a_proven_service_does_not_clear_another_subjects_projection_gap():
    payload = _profile_payload()
    entities = payload["canonical_entities"]
    entities["network"]["observations"].append({"observation_id": "unproven-network", "evidence_path": "sources.events[1]#parsed", "protocol": "http", "dst_port": 80})
    entities["detections"].append(
        {
            "observation_id": "unproven-detection",
            "evidence_path": "sources.events[1]#semantic",
            "event_scope_id": "sources.events[1]",
            "kind": "network_access",
            "name": "Synthetic unproven service",
            "subject_refs": ["entities.network.observations[1]"],
            "detector_id": "synthetic-unproven",
        }
    )
    request = LLMAnalysisRequest.model_validate(payload)
    profile = PingAnSocMemoryProfile(semantic_features=True).for_request(request)
    assert profile.identity.profile_version == "12"
    assert profile.project_query_facets(request)["network_service"] == ["http/80"]
    assert "entities.detections[2]:subject_not_projected:entities.network.observations[1]" in profile.projection_gaps(request)
    assert not any("synthetic-unproven@service" in component for component in profile.project_query_facets(request)["behavior_component_core"])


def test_saved_application_service_scope_restores_and_explains_its_own_fingerprint():
    from soc_agent.contracts import AnalysisRun

    request = LLMAnalysisRequest.model_validate(_profile_payload())
    writer = PingAnSocMemoryProfile(semantic_features=True, application_services=True)
    request.memory_profile = {key: getattr(writer.identity, key) for key in ("profile_id", "profile_version", "feature_schema_version")}
    run = AnalysisRun(alert_id=request.alert_id, status="success", llm_analysis_request=request)
    frozen = run.model_dump_json()
    restored = PingAnSocMemoryProfile.for_run(run)
    assert restored.identity == writer.identity
    assert PingAnSocMemoryProfile(semantic_features=True).for_request(request).identity == writer.identity
    facets = restored.project_run_facets(run)
    assert facets == writer.project_query_facets(request)
    spec = restored.build_applicability(consensus_facets=facets, strong_anchor_facets=facets)
    explained = restored.explain_scope_facets(spec, facets)
    assert explained["behavior_fingerprint"]["behavior_component"] == facets["behavior_component_core"]
    assert run.model_dump_json() == frozen


def test_proven_service_preserves_reviewed_v9_directive_but_missing_proof_cannot_reuse_it():
    from test_soc_memory_reference_retrieval import reference_fixture

    from soc_agent.contracts import SocMemoryDecisionDirective, SocMemoryDecisionImpact
    from soc_agent.core import SocMemoryService
    from soc_agent.memory import ConfirmedMemoryAnalysisRequestEnricher
    from soc_agent.memory.profiles import SocMemoryProfileRegistry

    _, _, record, repository, _, _, now = reference_fixture()
    request = LLMAnalysisRequest.model_validate(_profile_payload())
    previous = PingAnSocMemoryProfile(semantic_features=True, directional_services=False)
    facets = previous.project_query_facets(request)
    spec = previous.build_applicability(consensus_facets=facets, strong_anchor_facets=facets)
    record.facets = facets
    record.facets_hash = stable_hash(facets)
    record.applicability = spec
    record.decision_impact = SocMemoryDecisionImpact.DETECTION_DECISION
    record.decision_directive = SocMemoryDecisionDirective(effect="override", target_verdict="false_positive", required_facet_keys=list(spec.required_facets), rationale="Synthetic reviewed exact HTTP service.")
    repository.save_memory_record(record)
    before = record.model_dump_json()
    registry = SocMemoryProfileRegistry([PingAnSocMemoryProfile(semantic_features=True)])
    service = SocMemoryService(record_repository=repository, profile_registry=registry, now_provider=lambda: now)
    selected = registry.resolve_request(request)
    assert selected.identity.profile_version == "9"
    result = service.find_directive_records(memory_query_from_analysis_request(request, profile=selected))
    assert len(result.matches) == 1
    assert result.matches[0].applicability_report.status == "applicable"
    assert result.matches[0].record.decision_directive.effect == "override"
    enriched = ConfirmedMemoryAnalysisRequestEnricher(service, profile_registry=registry)(request)
    assert enriched.memory_profile["profile_version"] == "9"
    assert enriched.context_catalog[0].metadata["decision_directive_applicable"]

    missing_proof = request.model_copy(update={"source_field_semantics": []}, deep=True)
    rejected_profile = registry.resolve_request(missing_proof)
    assert rejected_profile.identity.profile_version == "10"
    assert not service.find_directive_records(memory_query_from_analysis_request(missing_proof, profile=rejected_profile)).matches
    assert repository.get_memory_record(record.memory_id).model_dump_json() == before
