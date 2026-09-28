"""Observed HTTP behavior is stable, transaction-bound and replay-compatible."""

import pytest

from soc_agent.contracts import AnalysisRun, LLMAnalysisRequest, SocMemoryCandidateType
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile
from soc_agent.memory.behavior_scope import select_memory_behavior_components
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.memory.retrieval import memory_query_from_analysis_request
from soc_agent.memory.scoring import evaluate_memory_scope


def _fact(kind, *, subject=0, source="message", product=None, value="original excerpt", wording="observed"):
    descriptor = {"kind": kind}
    if product:
        descriptor["server_product"] = product
    return {
        "observation_id": f"fact-{kind}-{subject}",
        "evidence_path": source + "#semantic",
        "event_scope_id": source,
        "name": wording,
        "value": value,
        "meaning": wording,
        "subject_ref": f"entities.http.observations[{subject}]",
        "network_behavior": descriptor,
        "network_behavior_verification": "source_bound_v1",
    }


def _request(facts=None, *, observations=None):
    return LLMAnalysisRequest.model_validate(
        {
            "alert_id": "synthetic-http",
            "tenant_id": "pingan",
            "environment": "dev",
            "source": {"source_system": "nids", "product": "sensor", "integration_name": "pingan_legacy_alert_platform"},
            "detection": {"detection_key": "synthetic-rule", "rule_name": "Synthetic HTTP detection"},
            "canonical_entities": {
                "http": {"observations": observations or [{"observation_id": "http-1", "evidence_path": "message#parsed", "status_code": 200}]},
                "supplementary_facts": facts if facts is not None else [_fact("http_response_directory_listing"), _fact("http_response_server_banner", product="simplehttp")],
            },
        }
    )


def _selected(request):
    return SocMemoryProfileRegistry([PingAnSocMemoryProfile(semantic_features=True)]).resolve_request(request)


def _http_components(profile, request):
    return [c for c in profile.project_query_facets(request).get("behavior_component_core", []) if c.startswith("http_observation:")]


def test_observed_response_produces_one_selectable_behavior_condition():
    request = _request()
    profile = _selected(request)
    assert profile.identity.profile_version == "11"
    facets = profile.project_query_facets(request)
    assert _http_components(profile, request) == ["http_observation:response=directory_listing;server=simplehttp"]
    assert facets["behavior_strength"] == ["strong"]
    spec = profile.build_applicability(consensus_facets=facets, strong_anchor_facets=facets)
    assert spec.required_facets["behavior_fingerprint"] == facets["behavior_fingerprint"]
    assert spec.covered_behavior_components == facets["behavior_component_core"]
    view = profile.explain_scope_facets(spec, facets)
    assert view["behavior_fingerprint"]["behavior_component"] == facets["behavior_component_core"]


def test_paraphrase_ids_order_and_source_paths_do_not_change_matching_values():
    original = _request()
    changed = _request(
        [_fact("http_response_server_banner", source="different", product="simplehttp", value="SimpleHTTP/1.0", wording="服务名称"), _fact("http_response_directory_listing", source="different", value="文件列表页面", wording="目录索引")],
        observations=[{"observation_id": "different-id", "evidence_path": "different#parsed", "event_time": "2026-09-28T01:00:00Z", "host": "192.0.2.99", "path": "/different/", "status_code": 200}],
    )
    assert _http_components(_selected(original), original) == _http_components(_selected(changed), changed)
    assert _selected(original).project_query_facets(original)["behavior_fingerprint"] == _selected(changed).project_query_facets(changed)["behavior_fingerprint"]


def test_two_responses_do_not_create_a_combination_that_neither_response_contains():
    request = _request(
        [_fact("http_response_directory_listing", source="first"), _fact("http_response_server_banner", subject=1, source="second", product="simplehttp")],
        observations=[{"observation_id": "a", "evidence_path": "first#parsed"}, {"observation_id": "b", "evidence_path": "second#parsed"}],
    )
    components = _http_components(_selected(request), request)
    assert "http_observation:response=directory_listing;server=simplehttp" not in components
    assert components == ["http_observation:response=directory_listing", "http_observation:server=simplehttp"]


@pytest.mark.parametrize("change", ["unverified", "unbound", "cross_source", "wrong_object", "ambiguous_source"])
def test_unproven_fact_binding_cannot_become_a_core_condition(change):
    fact = _fact("http_response_directory_listing")
    observations = [{"observation_id": "a", "evidence_path": "message#parsed"}]
    if change == "unverified":
        fact.pop("network_behavior_verification")
    elif change == "unbound":
        fact["subject_ref"] = None
    elif change == "cross_source":
        fact["event_scope_id"] = "other"
    elif change == "wrong_object":
        fact["subject_ref"] = "entities.network"
    else:
        observations.append({"observation_id": "b", "evidence_path": "message#semantic"})
    request = _request([fact], observations=observations)
    assert not _http_components(_selected(request), request)
    if change == "unverified":
        assert _selected(request).identity.profile_version in {"9", "10"}
    else:
        assert _selected(request).identity.profile_version == "11"
        assert _selected(request).projection_gaps(request)


def test_server_banner_alone_does_not_grant_strong_behavior():
    request = _request([_fact("http_response_server_banner", product="nginx")])
    facets = _selected(request).project_query_facets(request)
    assert facets["behavior_strength"] == ["weak_only"]
    assert not facets.get("behavior_component_strong")


def test_conflicting_products_in_one_response_do_not_produce_an_arbitrary_product():
    request = _request([_fact("http_response_directory_listing"), _fact("http_response_server_banner", product="nginx"), _fact("http_response_server_banner", product="simplehttp")])
    assert not _http_components(_selected(request), request)
    assert "http_behavior_server_product_ambiguous" in _selected(request).projection_gaps(request)


def test_failed_http_projection_cannot_fall_back_to_exact_legacy_scope():
    old = _request([])
    old.canonical_entities.process.process_name = "cmd.exe"
    old.canonical_entities.http.method = "GET"
    profile = _selected(old)
    facets = profile.project_query_facets(old)
    spec = profile.build_applicability(consensus_facets=facets, strong_anchor_facets=facets)
    assert _evaluate(spec, old).status == "applicable"
    current = old.model_copy(deep=True)
    current.canonical_entities.supplementary_facts = _request(
        [_fact("http_response_directory_listing"), _fact("http_response_server_banner", product="nginx"), _fact("http_response_server_banner", product="simplehttp")]
    ).canonical_entities.supplementary_facts
    assert _selected(current).identity.profile_version == "11"
    assert "http_behavior_server_product_ambiguous" in _selected(current).projection_gaps(current)
    assert _evaluate(spec, current).status != "applicable"


@pytest.mark.parametrize("version", ["7", "8", "9", "10"])
def test_frozen_profiles_do_not_reinterpret_new_metadata(version):
    identity = {"profile_id": "pingan.soc", "profile_version": version, "feature_schema_version": f"pingan.soc.memory_features.v{int(version) - 2}"}
    request = _request()
    request.memory_profile = identity
    profile = _selected(request)
    assert profile.identity.profile_version == version
    assert profile.project_query_facets(request) == profile.project_query_facets(_request([]))


def test_saved_http_identity_restores_scope_without_fresh_selection():
    request = _request()
    profile = _selected(request)
    request.memory_profile = {"profile_id": profile.identity.profile_id, "profile_version": profile.identity.profile_version, "feature_schema_version": profile.identity.feature_schema_version}
    run = AnalysisRun(alert_id=request.alert_id, status="success", llm_analysis_request=request)
    assert PingAnSocMemoryProfile.for_run(run).project_run_facets(run) == profile.project_query_facets(request)


def test_plain_supplementary_prose_never_becomes_a_matching_condition():
    request = _request([{k: v for k, v in _fact("http_response_directory_listing", value="Directory listing for /").items() if not k.startswith("network_behavior")}])
    assert _selected(request).identity.profile_version in {"9", "10"}
    assert not _http_components(_selected(request), request)


def _reviewed_scope(request):
    profile = _selected(request)
    facets = profile.project_query_facets(request)
    spec = profile.build_applicability(consensus_facets=facets, strong_anchor_facets=facets)
    return select_memory_behavior_components(spec, facets, _http_components(profile, request), registry=SocMemoryProfileRegistry([profile]))


def _evaluate(spec, request):
    query = memory_query_from_analysis_request(request, profile=_selected(request))
    return evaluate_memory_scope(spec, SocMemoryCandidateType.DETECTION_LESSON, query, {})


def test_reviewed_scope_matches_paraphrases_but_not_another_observed_behavior():
    spec = _reviewed_scope(_request())
    request = _request([_fact("http_response_directory_listing", value="目录索引", wording="另一种说法"), _fact("http_response_server_banner", product="simplehttp", value="SimpleHTTP/9.9")])
    assert _evaluate(spec, request).status == "applicable"
    request = _request([_fact("http_response_command_output"), _fact("http_response_server_banner", product="simplehttp")])
    report = _evaluate(spec, request)
    assert report.status != "applicable"
    assert "http_observation:response=directory_listing;server=simplehttp" in report.missing_behavior_components


def test_reviewed_scope_rejects_cross_response_combination_and_new_unreviewed_behavior():
    spec = _reviewed_scope(_request())
    observations = [{"observation_id": "a", "evidence_path": "first#parsed"}, {"observation_id": "b", "evidence_path": "second#parsed"}]
    cross_response = _request([_fact("http_response_directory_listing", source="first"), _fact("http_response_server_banner", subject=1, source="second", product="simplehttp")], observations=observations)
    assert _evaluate(spec, cross_response).status != "applicable"
    extra_response = _request(
        [_fact("http_response_directory_listing", source="first"), _fact("http_response_server_banner", source="first", product="simplehttp"), _fact("http_response_command_output", subject=1, source="second")], observations=observations
    )
    report = _evaluate(spec, extra_response)
    assert report.status == "partial"
    assert report.context_only_allowed
    assert "uncovered_core_behavior" in report.reason_codes


def test_observed_http_does_not_hide_existing_detector_ambiguity():
    request = _request()
    from soc_agent.contracts.normalization import DetectionObservationRef

    request.canonical_entities.detections = [
        DetectionObservationRef(observation_id="ambiguous", evidence_path="message#semantic", event_scope_id="message", kind="web_detection", subject_refs=["entities.http.observations[0]"], name="detector", identity_basis="ambiguous")
    ]
    spec = _reviewed_scope(request)
    report = _evaluate(spec, request)
    assert report.status == "partial"
    assert "behavior_projection_incomplete" in report.reason_codes


def test_old_reviewed_reference_is_recalled_without_rewriting_its_scope():
    from test_soc_memory_reference_retrieval import reference_fixture

    request, _, record, repository, _, service, _ = reference_fixture()
    frozen = record.model_dump(mode="json")
    request.memory_profile = {}
    request.canonical_entities.http = _request().canonical_entities.http
    request.canonical_entities.supplementary_facts = _request().canonical_entities.supplementary_facts
    query = memory_query_from_analysis_request(request, profile=_selected(request))
    assert query.metadata["memory_profile_version"] == "11"
    result = service.find_relevant_records(query)
    assert len(result.matches) == 1
    report = result.matches[0].applicability_report
    assert report.status == "partial"
    assert report.context_only_allowed
    assert service.find_directive_records(query).matches == []
    assert repository.get_memory_record(record.memory_id).model_dump(mode="json") == frozen


def test_old_override_remains_direct_for_unchanged_behavior_but_not_new_http_behavior():
    from test_soc_memory_reference_retrieval import reference_fixture

    from soc_agent.contracts import SocMemoryDecisionDirective, SocMemoryDecisionImpact
    from soc_agent.memory.behavior_scope import required_directive_keys
    from soc_agent.memory.retrieval import memory_context_item
    from soc_agent.utils.hashing import stable_hash

    request = _request([])
    request.canonical_entities.process.process_name = "curl"
    request.canonical_entities.http.method = "GET"
    profile = _selected(request)
    assert profile.identity.profile_version == "9"
    facets = profile.project_query_facets(request)
    spec = profile.build_applicability(consensus_facets=facets, strong_anchor_facets=facets)
    assert spec is not None
    _, _, record, repository, _, service, _ = reference_fixture()
    record = record.model_copy(
        update={
            "facets": facets,
            "facets_hash": stable_hash(facets),
            "applicability": spec,
            "decision_impact": SocMemoryDecisionImpact.DETECTION_DECISION,
            "decision_directive": SocMemoryDecisionDirective(
                effect="override",
                target_verdict="false_positive",
                required_facet_keys=required_directive_keys(spec),
                rationale="Synthetic reviewed override for the original behavior scope.",
            ),
        }
    )
    repository.save_memory_record(record)
    frozen = record.model_dump(mode="json")

    unchanged_query = memory_query_from_analysis_request(request, profile=profile)
    unchanged = service.find_directive_records(unchanged_query)
    assert len(unchanged.matches) == 1
    match = unchanged.matches[0]
    assert match.applicability_report.status == "applicable"
    context = memory_context_item(match, query_facets=unchanged_query.facets, retrieval_policy_version=unchanged.policy_version)
    assert context.metadata["decision_directive_applicable"] is True
    assert match.score >= record.decision_directive.minimum_match_score
    assert all(match.matched_facets.get(key) for key in record.decision_directive.required_facet_keys)

    changed_request = request.model_copy(deep=True)
    changed_request.canonical_entities.supplementary_facts = _request().canonical_entities.supplementary_facts
    changed_query = memory_query_from_analysis_request(changed_request, profile=_selected(changed_request))
    assert changed_query.metadata["memory_profile_version"] == "11"
    changed = service.find_directive_records(changed_query)
    assert changed.matches == []
    assert changed.skipped_not_applicable == 1
    assert service.find_relevant_records(changed_query).matches == []
    assert repository.get_memory_record(record.memory_id).model_dump(mode="json") == frozen


# Entirely synthetic APT-sensor SQL traffic: RFC 5737 addresses, invented
# detector identities, and no copied production payload or persisted run.
_APT_COMMON_CORE = {
    "attack_family:source_category:sql_injection",
    "detected_behavior:http:synthetic-apt:synthetic-sensor:id:sql-alpha@http:post",
    "detected_behavior:http:synthetic-apt:synthetic-sensor:id:sql-beta@http:post",
    "http_method:post",
    "protocol:http/1.1",
    "scenario:web_attack",
    "web_detection_target",
}
_APT_HTTP_SERVICE_CORE = {
    "network_service:http/80",
    "detected_behavior:network:synthetic-apt:synthetic-sensor:id:sql-alpha@service:http/80",
    "detected_behavior:network:synthetic-apt:synthetic-sensor:id:sql-beta@service:http/80",
}
_APT_TCP_SERVICE_CORE = {
    "network_service:tcp/80",
    "detected_behavior:network:synthetic-apt:synthetic-sensor:id:sql-alpha@service:tcp/80",
    "detected_behavior:network:synthetic-apt:synthetic-sensor:id:sql-beta@service:tcp/80",
}
_APT_UNKNOWN_DIRECTION_GAPS = [
    "entities.detections[0]:subject_not_projected:entities.network.observations[0]",
    "entities.detections[1]:subject_not_projected:entities.network.observations[1]",
]


def _apt_sql_request(*, protocol="http", direction=None):
    connections = [{"source_ip": source, "destination_ip": "203.0.113.80", "src_port": 51000 + index, "dst_port": 80, "protocol": protocol, "direction": direction} for index, source in enumerate(("192.0.2.10", "198.51.100.10"))]
    http = {"host": "203.0.113.80", "method": "POST", "path": "/synthetic/query", "protocol": "HTTP/1.1", "port": 80, "status_code": 200}
    return LLMAnalysisRequest.model_validate(
        {
            "alert_id": "synthetic-apt-sql",
            "tenant_id": "pingan",
            "environment": "dev",
            "source": {"source_type": "nids", "source_system": "synthetic-apt", "product": "synthetic-sensor", "integration_name": "pingan_legacy_alert_platform"},
            "detection": {"detection_key": "synthetic-apt-sql-rule", "rule_name": "Synthetic SQL request detection"},
            "classification": {"category": "SQL injection"},
            "fact_reconstruction": {"scenario_hypotheses": [{"scenario_type": "web_attack", "status": "confirmed", "confidence": 0.9, "rationale": "Synthetic SQL request fixture."}]},
            "canonical_entities": {
                "network": {
                    **connections[0],
                    "observations": [{"observation_id": f"synthetic-network-{index}", "evidence_path": f"synthetic.messages[{index}]#network", **connection} for index, connection in enumerate(connections)],
                },
                "http": {
                    **http,
                    "observations": [{"observation_id": f"synthetic-http-{index}", "evidence_path": f"synthetic.messages[{index}]#http", **http} for index in range(2)],
                },
                "detections": [
                    {
                        "observation_id": f"synthetic-detection-{index}",
                        "evidence_path": f"synthetic.messages[{index}]#detection",
                        "event_scope_id": f"synthetic.messages[{index}]",
                        "kind": "web_detection",
                        "name": f"Synthetic SQL detector {detector}",
                        "detector_id": detector,
                        "identity_basis": "adapter_declared",
                        "subject_refs": [f"entities.http.observations[{index}]", f"entities.network.observations[{index}]"],
                    }
                    for index, detector in enumerate(("sql-alpha", "sql-beta"))
                ],
                "supplementary_facts": [
                    {
                        "observation_id": "synthetic-form-body",
                        "evidence_path": "synthetic.messages[0]#body",
                        "event_scope_id": "synthetic.messages[0]",
                        "subject_ref": "entities.http.observations[0]",
                        "name": "Synthetic SQL form field",
                        "value": "query=SELECT+1",
                        "meaning": "Synthetic request body, without a verified HTTP behavior descriptor.",
                    }
                ],
            },
        }
    )


@pytest.mark.parametrize(
    ("protocol", "direction", "version", "expected_core", "expected_gaps"),
    [
        ("http", None, "10", _APT_COMMON_CORE | {"protocol:http"}, _APT_UNKNOWN_DIRECTION_GAPS),
        ("tcp", "to_server", "9", _APT_COMMON_CORE | {"protocol:tcp"} | _APT_TCP_SERVICE_CORE, []),
    ],
    ids=["unknown-direction-http", "known-direction-tcp"],
)
@pytest.mark.parametrize("unverified_descriptor", [False, True], ids=["untyped-fact", "unverified-descriptor"])
def test_apt_core_without_http_descriptors_is_unchanged_by_http_extension(protocol, direction, version, expected_core, expected_gaps, unverified_descriptor):
    request = _apt_sql_request(protocol=protocol, direction=direction)
    if unverified_descriptor:
        fact = _fact("http_response_file_content", source="synthetic.messages[0]")
        fact.pop("network_behavior_verification")
        request.canonical_entities.supplementary_facts.extend(_request([fact]).canonical_entities.supplementary_facts)
    before = request.model_dump_json()
    # Freeze the pre-HTTP service-policy identity independently of fresh selection.
    identity = {"profile_id": "pingan.soc", "profile_version": version, "feature_schema_version": f"pingan.soc.memory_features.v{int(version) - 2}"}
    previous = PingAnSocMemoryProfile().for_identity(identity)
    current = _selected(request)
    previous_facets = previous.project_query_facets(request)
    current_facets = current.project_query_facets(request)

    assert set(previous_facets["behavior_component_core"]) == expected_core
    assert set(current_facets["behavior_component_core"]) == expected_core
    assert current_facets == previous_facets
    assert current.identity == previous.identity
    assert current.identity.profile_version == version
    assert previous.projection_gaps(request) == current.projection_gaps(request) == expected_gaps
    assert not _http_components(current, request)
    assert request.model_dump_json() == before


def test_frozen_apt_service_conditions_survive_fresh_directional_projection():
    request = _apt_sql_request()
    request.memory_profile = {"profile_id": "pingan.soc", "profile_version": "9", "feature_schema_version": "pingan.soc.memory_features.v7"}
    run = AnalysisRun(alert_id=request.alert_id, status="success", llm_analysis_request=request)
    frozen = run.model_dump_json()
    registry = SocMemoryProfileRegistry([PingAnSocMemoryProfile(semantic_features=True)])
    restored = registry.resolve_run(run)
    old_facets = restored.project_run_facets(run)
    expected_current_core = _APT_COMMON_CORE | {"protocol:http"}

    assert restored.identity.profile_version == "9"
    assert old_facets["network_service"] == ["http/80"]
    assert set(old_facets["behavior_component_core"]) == expected_current_core | _APT_HTTP_SERVICE_CORE
    assert len(old_facets["behavior_component_core"]) == 11
    assert _APT_HTTP_SERVICE_CORE - {"network_service:http/80"} <= set(old_facets["behavior_component_strong"])
    assert restored.projection_gaps(request) == []

    fresh = request.model_copy(deep=True)
    fresh.memory_profile = {}
    fresh_before = fresh.model_dump_json()
    current = registry.resolve_request(fresh)
    current_facets = current.project_query_facets(fresh)
    # This is the existing directional-service rule, not an HTTP compatibility fix.
    assert current.identity.profile_version == "10"
    assert not current_facets.get("network_service")
    assert set(current_facets["behavior_component_core"]) == expected_current_core
    assert len(current_facets["behavior_component_core"]) == 8
    assert set(old_facets["behavior_component_core"]) - set(current_facets["behavior_component_core"]) == _APT_HTTP_SERVICE_CORE
    assert current.projection_gaps(fresh) == _APT_UNKNOWN_DIRECTION_GAPS
    assert not _http_components(current, fresh)
    assert fresh.model_dump(exclude={"memory_profile"}) == request.model_dump(exclude={"memory_profile"})
    assert fresh.model_dump_json() == fresh_before
    assert run.model_dump_json() == frozen
    assert registry.resolve_run(run).project_run_facets(run) == old_facets


def test_verified_http_addition_preserves_existing_apt_core():
    request = _apt_sql_request(protocol="tcp", direction="to_server")
    before = request.model_dump_json()
    previous = _selected(request)
    old_facets = previous.project_query_facets(request)
    expected_old_core = _APT_COMMON_CORE | {"protocol:tcp"} | _APT_TCP_SERVICE_CORE
    assert previous.identity.profile_version == "9"
    assert set(old_facets["behavior_component_core"]) == expected_old_core

    enriched = request.model_copy(deep=True)
    fact = _fact("http_response_file_content", source="synthetic.messages[0]", value="Synthetic response contains a text attachment.")
    enriched.canonical_entities.supplementary_facts.extend(_request([fact]).canonical_entities.supplementary_facts)
    enriched_before = enriched.model_dump_json()
    current = _selected(enriched)
    facets = current.project_query_facets(enriched)
    added = {"http_observation:response=file_content"}

    assert current.identity.profile_version == "11"
    assert current.identity.feature_schema_version == "pingan.soc.memory_features.v9"
    assert set(facets["behavior_component_core"]) == expected_old_core | added
    for key in ("behavior_component", "behavior_component_core", "behavior_component_strong"):
        assert set(facets[key]) == set(old_facets[key]) | added
    assert facets["behavior_fingerprint"] != old_facets["behavior_fingerprint"]
    changed_keys = {"behavior_component", "behavior_component_core", "behavior_component_strong", "behavior_fingerprint"}
    assert {key: value for key, value in facets.items() if key not in changed_keys} == {key: value for key, value in old_facets.items() if key not in changed_keys}
    assert previous.projection_gaps(request) == current.projection_gaps(enriched) == []
    spec = current.build_applicability(consensus_facets=facets, strong_anchor_facets=facets)
    assert spec is not None
    assert set(spec.covered_behavior_components) == expected_old_core | added
    assert enriched.canonical_entities.model_dump(exclude={"supplementary_facts"}) == request.canonical_entities.model_dump(exclude={"supplementary_facts"})
    assert request.model_dump_json() == before
    assert enriched.model_dump_json() == enriched_before
