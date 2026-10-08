"""Deterministic process facts remain bound, stable and historically frozen."""

from copy import deepcopy

import pytest

from soc_agent.contracts import AnalysisRun, LLMAnalysisRequest, SocMemoryCandidateType
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.memory.retrieval import memory_query_from_analysis_request
from soc_agent.memory.scoring import evaluate_memory_scope


def _payload(*, port=31443, source="message", ip="192.0.2.20"):
    node_ref = "entities.process.observations[0].nodes[2]"
    return {
        "alert_id": "synthetic-process",
        "tenant_id": "pingan",
        "environment": "dev",
        "source": {"source_type": "hids", "source_system": "sensor", "product": "sensor", "integration_name": "pingan_legacy_alert_platform"},
        "detection": {"detection_key": "synthetic-rule", "rule_name": "Synthetic process detection"},
        "canonical_entities": {
            "process": {
                "process_name": "wrong-ancestor",
                "process_id": 10,
                "process_path": "/wrong/ancestor",
                "command_line": "wrong summary",
                "primary_ref": node_ref,
                "observations": [
                    {
                        "observation_id": "process-0",
                        "event_scope_id": source,
                        "evidence_path": source + "#parsed",
                        "nodes": [
                            {"process_name": "bash", "process_id": 10, "process_path": "/usr/bin/bash", "command_line": "bash"},
                            {"process_name": "timeout", "process_id": 11, "process_path": "/usr/bin/timeout", "command_line": "timeout 2 bash -c ..."},
                            {"process_name": "bash", "process_id": 12, "process_path": "/usr/bin/bash", "command_line": f"bash -c exec 3<>/dev/tcp/{ip}/{port}", "username": "root"},
                        ],
                        "subject_binding": {"status": "resolved", "node_index": 2, "parent_node_index": 1, "reason": "same-source command", "evidence_paths": [source + "#parsed.command"], "gaps": []},
                        "field_sources": {"nodes[2].process_path": source + "#parsed.process_path", "nodes[2].command_line": source + "#parsed.command"},
                    }
                ],
            },
            "supplementary_facts": [
                {
                    "observation_id": "target-0",
                    "evidence_path": source + "#parsed.command",
                    "event_scope_id": source,
                    "name": "command target",
                    "value": f"bash -c exec 3<>/dev/tcp/{ip}/{port}",
                    "meaning": "Command target, not a successful connection",
                    "subject_ref": node_ref,
                    "process_behavior": {"kind": "shell_tcp_connection_target", "protocol": "tcp", "destination_ip": ip, "dst_port": port},
                    "process_behavior_verification": "source_bound_v1",
                }
            ],
        },
    }


def _request(**kwargs):
    return LLMAnalysisRequest.model_validate(_payload(**kwargs))


def _selected(request, *, enabled=True):
    return SocMemoryProfileRegistry([PingAnSocMemoryProfile(semantic_features=enabled)]).resolve_request(request)


def _core(request):
    return _selected(request).project_query_facets(request)["behavior_component_core"]


@pytest.mark.parametrize("enabled", [False, True])
def test_deterministic_binding_selects_new_profile_independently_of_model_mode(enabled):
    request = _request()
    profile = _selected(request, enabled=enabled)
    facets = profile.project_query_facets(request)
    assert profile.identity.profile_version == "13"
    assert profile.identity.feature_schema_version == "pingan.soc.memory_features.v11"
    assert "process_image:bash" in facets["behavior_component_core"]
    assert "process_path:usr/bin/bash" in facets["behavior_component_core"]
    assert not any("wrong" in component for component in facets["behavior_component_core"])
    assert any("command_target=tcp/31443" in component for component in facets["behavior_component_core"])
    assert not facets.get("network_service")
    assert not profile.projection_gaps(request)


def test_summary_llm_wording_ips_ids_and_fact_order_do_not_change_process_scope():
    original = _request()
    changed = _request(source="other", ip="192.0.2.90")
    changed.canonical_entities.process.process_path = None
    changed.canonical_entities.process.command_line = "arbitrary model wording"
    changed.canonical_entities.process.observations[0].nodes[2].process_id = 42
    changed.canonical_entities.supplementary_facts[0].meaning = "another explanation"
    changed.canonical_entities.supplementary_facts.append(changed.canonical_entities.supplementary_facts[0].model_copy(update={"observation_id": "duplicate"}))
    assert _core(original) == _core(changed)
    assert _selected(original).project_query_facets(original)["behavior_fingerprint"] == _selected(changed).project_query_facets(changed)["behavior_fingerprint"]
    assert _core(_request(port=18443)) != _core(original)


def test_model_duplicate_process_detections_do_not_change_adapter_owned_scope():
    payload = _payload()
    adapter = {
        "observation_id": "adapter",
        "evidence_path": "message#parsed",
        "event_scope_id": "message",
        "kind": "process_execution",
        "subject_refs": ["entities.process.observations[0].nodes[2]"],
        "name": "same detector",
    }
    original = LLMAnalysisRequest.model_validate({**payload, "canonical_entities": {**payload["canonical_entities"], "detections": [adapter]}})
    payload["canonical_entities"]["network"] = {"observations": [{"observation_id": "command-network", "evidence_path": "message#semantic", "destination_ip": "192.0.2.20", "dst_port": 31443}]}
    payload["canonical_entities"]["detections"] = [
        adapter,
        *[
            {
                "observation_id": f"model-{index}",
                "evidence_path": "message#semantic",
                "event_scope_id": "message",
                "kind": "process_execution",
                "subject_refs": ["entities.process.observations[0].nodes[2]", "entities.network.observations[0]"],
                "name": "same detector",
                "category": "random model classification",
            }
            for index in range(2)
        ],
    ]
    request = LLMAnalysisRequest.model_validate(payload)
    assert _core(request) == _core(original)
    assert not _selected(request).projection_gaps(request)


def test_secondary_source_detector_is_not_lost_when_process_and_target_match():
    import json

    from test_soc_hids_command_targets import payload

    from soc_agent.normalizers import normalize_alert_payload

    raw = payload("bash -c exec 3<>/dev/tcp/10.0.0.1/31443", "bash -c exec 3<>/dev/tcp/10.0.0.2/31443")

    def request_from_raw(value):
        alert = normalize_alert_payload(value)
        return LLMAnalysisRequest(alert_id=alert.alert_id, tenant_id=alert.tenant_id, environment="dev", source=alert.source, detection=alert.detection, classification=alert.classification, canonical_entities=alert.entities)

    original = request_from_raw(raw)
    second = json.loads(raw["alert"]["hitLog"][0]["zeusRawLogs"][1]["message"])
    second["detail.hit_rule_name"] = "independent second detector"
    raw["alert"]["hitLog"][0]["zeusRawLogs"][1]["message"] = json.dumps(second)
    changed = request_from_raw(raw)
    assert changed.detection == original.detection
    assert _core(changed) != _core(original)
    assert _selected(changed).project_query_facets(changed)["behavior_fingerprint"] != _selected(original).project_query_facets(original)["behavior_fingerprint"]
    assert any("independent_second_detector" in value for value in _core(changed))
    usage = _selected(changed).projection_usage(changed)
    assert any(item["canonical_path"] == "entities.detections[1]" and item["status"] == "used" and any("independent_second_detector" in value for value in item["components"]) for item in usage)


def test_independent_model_detection_is_not_misclassified_as_an_adapter_duplicate():
    payload = _payload()
    payload["canonical_entities"]["detections"] = [
        {"observation_id": "adapter", "evidence_path": "message#parsed", "event_scope_id": "message", "kind": "process_execution", "subject_refs": ["entities.process.observations[0].nodes[2]"], "name": "adapter detector"},
        {"observation_id": "model", "evidence_path": "message#semantic", "event_scope_id": "message", "kind": "process_execution", "subject_refs": ["entities.process.observations[0].nodes[2]"], "name": "different detector"},
    ]
    request = LLMAnalysisRequest.model_validate(payload)
    assert any("adapter_detector" in component for component in _core(request))
    assert any("different_detector" in component for component in _core(request))


def test_independent_file_detection_survives_process_source_deduplication():
    payload = _payload()
    payload["canonical_entities"]["file"] = {"observations": [{"observation_id": "file-1", "evidence_path": "message#semantic", "file_path": "/opt/tool.bin", "relation": "observed_artifact"}]}
    payload["canonical_entities"]["detections"] = [
        {"observation_id": "file-detector", "evidence_path": "message#semantic", "event_scope_id": "message", "kind": "file_detection", "subject_refs": ["entities.file.observations[0]"], "name": "file detector"}
    ]
    assert "detected_file:tool.bin" in _core(LLMAnalysisRequest.model_validate(payload))


def test_selected_process_is_not_assumed_to_be_the_last_tree_node():
    payload = _payload()
    payload["canonical_entities"]["process"]["observations"][0]["nodes"].append({"process_name": "unselected", "process_id": 99, "process_path": "/usr/bin/unselected"})
    assert _core(LLMAnalysisRequest.model_validate(payload)) == _core(_request())


def test_process_deduplication_does_not_hide_detector_identity_conflict():
    payload = _payload()
    payload["canonical_entities"]["detections"] = [
        {
            "observation_id": "conflicting-detector",
            "evidence_path": "message#semantic",
            "event_scope_id": "message",
            "kind": "process_execution",
            "subject_refs": ["entities.process.observations[0].nodes[2]"],
            "name": "ambiguous",
            "identity_basis": "ambiguous",
        }
    ]
    request = LLMAnalysisRequest.model_validate(payload)
    assert any("detector_identity_ambiguous" in value for value in _selected(request).projection_gaps(request))


@pytest.mark.parametrize("change", ["ambiguous", "cross_source", "unbound_target", "missing_declared_path", "declared_gap", "wrong_node_target", "wrong_same_source_field", "stale_command_descriptor"])
def test_incomplete_or_conflicting_binding_cannot_silently_become_an_exact_scope(change):
    payload = _payload()
    observation = payload["canonical_entities"]["process"]["observations"][0]
    fact = payload["canonical_entities"]["supplementary_facts"][0]
    if change == "ambiguous":
        observation["subject_binding"].update(status="ambiguous", node_index=None, parent_node_index=None)
    elif change == "cross_source":
        fact["event_scope_id"] = "unrelated"
    elif change == "unbound_target":
        fact["subject_ref"] = None
    elif change == "missing_declared_path":
        observation["nodes"][2]["process_path"] = None
    elif change == "declared_gap":
        observation["subject_binding"]["gaps"] = ["command_conflict"]
    elif change == "wrong_node_target":
        fact["subject_ref"] = "entities.process.observations[0].nodes[0]"
    elif change == "wrong_same_source_field":
        fact["evidence_path"] = "message#parsed.unrelated"
    else:
        observation["nodes"][2]["command_line"] = "bash -c printf changed"
    request = LLMAnalysisRequest.model_validate(payload)
    profile = _selected(request)
    assert profile.identity.profile_version == "13"
    assert profile.projection_gaps(request)
    original = _request()
    original_profile = _selected(original)
    facets = original_profile.project_query_facets(original)
    spec = original_profile.build_applicability(consensus_facets=facets, strong_anchor_facets=facets)
    query = memory_query_from_analysis_request(request, profile=profile)
    assert evaluate_memory_scope(spec, SocMemoryCandidateType.DETECTION_LESSON, query, {}).status != "applicable"


def test_absent_unreported_path_is_not_a_false_extraction_gap():
    payload = _payload()
    observation = payload["canonical_entities"]["process"]["observations"][0]
    observation["nodes"][2]["process_path"] = None
    del observation["field_sources"]["nodes[2].process_path"]
    request = LLMAnalysisRequest.model_validate(payload)
    assert not _selected(request).projection_gaps(request)
    assert "process_image:bash" not in _core(request)


def test_targets_are_not_joined_across_sources_or_processes():
    payload = _payload()
    second = _payload(port=18443, source="second")["canonical_entities"]
    second["process"]["observations"][0]["nodes"][2].update(process_name="other-shell", process_path="/usr/bin/other-shell")
    second["supplementary_facts"][0]["subject_ref"] = "entities.process.observations[1].nodes[2]"
    payload["canonical_entities"]["process"]["observations"] += second["process"]["observations"]
    payload["canonical_entities"]["supplementary_facts"] += second["supplementary_facts"]
    components = [value for value in _core(LLMAnalysisRequest.model_validate(payload)) if value.startswith("process_command_target:")]
    assert len(components) == 2
    assert not any("image=bash;" in value and "tcp/18443" in value for value in components)
    assert not any("image=other-shell;" in value and "tcp/31443" in value for value in components)


def test_plain_supplementary_prose_without_new_binding_keeps_existing_profile():
    payload = _payload()
    observation = payload["canonical_entities"]["process"]["observations"][0]
    observation.pop("subject_binding")
    observation.pop("field_sources")
    fact = payload["canonical_entities"]["supplementary_facts"][0]
    fact.pop("process_behavior")
    fact.pop("process_behavior_verification")
    request = LLMAnalysisRequest.model_validate(payload)
    assert _selected(request).identity.profile_version in {"9", "10"}


@pytest.mark.parametrize("version", ["7", "8", "9", "10", "11", "12"])
def test_frozen_profiles_ignore_new_binding_and_typed_target_metadata(version):
    payload = _payload()
    payload["memory_profile"] = {"profile_id": "pingan.soc", "profile_version": version, "feature_schema_version": f"pingan.soc.memory_features.v{int(version) - 2}"}
    request = LLMAnalysisRequest.model_validate(payload)
    previous = deepcopy(payload)
    previous["canonical_entities"]["process"].pop("primary_ref")
    for observation in previous["canonical_entities"]["process"]["observations"]:
        observation.pop("subject_binding")
        observation.pop("field_sources")
    previous["canonical_entities"]["supplementary_facts"] = []
    profile = _selected(request)
    assert profile.identity.profile_version == version
    assert profile.project_query_facets(request) == profile.project_query_facets(LLMAnalysisRequest.model_validate(previous))


def test_saved_new_profile_restores_scope_and_exposes_verified_review_conditions():
    request = _request()
    profile = _selected(request)
    request.memory_profile = {"profile_id": profile.identity.profile_id, "profile_version": profile.identity.profile_version, "feature_schema_version": profile.identity.feature_schema_version}
    run = AnalysisRun(alert_id=request.alert_id, status="success", llm_analysis_request=request)
    facets = profile.project_query_facets(request)
    assert PingAnSocMemoryProfile.for_run(run).project_run_facets(run) == facets
    spec = profile.build_applicability(consensus_facets=facets, strong_anchor_facets=facets)
    assert spec.covered_behavior_components == facets["behavior_component_core"]
    assert profile.explain_scope_facets(spec, facets)["behavior_fingerprint"]["behavior_component"] == facets["behavior_component_core"]
    assert profile.is_current_identity(request.memory_profile)


def test_projection_usage_distinguishes_consumed_facts_from_occurrence_context():
    request = _request()
    before = request.model_dump(mode="json")
    profile = _selected(request)
    usage = {item["canonical_path"]: item for item in profile.projection_usage(request)}
    node = "entities.process.observations[0].nodes[2]"
    assert usage[node]["status"] == "used"
    assert "process_path:usr/bin/bash" in usage[node]["components"]
    assert usage[node + ".process_id"]["status"] == "context_only"
    assert usage[node + ".process_id"]["components"] == []
    target = "entities.supplementary_facts[0].process_behavior"
    assert usage[target]["status"] == "used"
    assert any("command_target=tcp/31443" in value for value in usage[target]["components"])
    assert usage[target + ".destination_ip"]["status"] == "context_only"
    assert request.model_dump(mode="json") == before
    assert PingAnSocMemoryProfile(semantic_features=True).projection_usage(request) == []


def test_projection_usage_records_declared_missing_field_separately():
    request = _request()
    request.canonical_entities.process.observations[0].nodes[2].process_path = None
    usage = _selected(request).projection_usage(request)
    assert any(item["canonical_path"] == "entities.process.observations[0].nodes[2].process_path" and item["status"] == "gap" and item["components"] == [] for item in usage)


def test_adapter_to_memory_preserves_two_actual_command_targets_without_gaps():
    from test_soc_hids_command_targets import payload

    from soc_agent.normalizers import normalize_alert_payload

    alert = normalize_alert_payload(payload("bash -c exec 3<>/dev/tcp/10.0.0.1/18443", "bash -c exec 3<>/dev/tcp/10.0.0.2/31443"))
    request = LLMAnalysisRequest(alert_id=alert.alert_id, tenant_id=alert.tenant_id, environment="dev", source=alert.source, detection=alert.detection, classification=alert.classification, canonical_entities=alert.entities)
    profile = _selected(request)
    assert profile.identity.profile_version == "13"
    assert not profile.projection_gaps(request)
    components = profile.project_query_facets(request)["behavior_component_core"]
    assert len([value for value in components if value.startswith("process_command_target:")]) == 2
    assert any("command_target=tcp/18443" in value for value in components)
    assert any("command_target=tcp/31443" in value for value in components)
    assert not profile.project_query_facets(request).get("network_service")
    assert not any(item["status"] == "gap" for item in profile.projection_usage(request))


def test_new_command_scope_does_not_implicitly_translate_reviewed_legacy_scope():
    from test_soc_memory_reference_retrieval import reference_fixture

    request, _, record, repository, _, service, _ = reference_fixture()
    frozen = record.model_dump(mode="json")
    request.memory_profile = {}
    process_request = _request()
    request.canonical_entities.process = process_request.canonical_entities.process
    request.canonical_entities.supplementary_facts = process_request.canonical_entities.supplementary_facts
    profile = _selected(request)
    query = memory_query_from_analysis_request(request, profile=profile)
    assert query.metadata["memory_profile_version"] == "13"
    assert service.find_relevant_records(query).matches == []
    assert service.find_directive_records(query).matches == []
    assert repository.get_memory_record(record.memory_id).model_dump(mode="json") == frozen
