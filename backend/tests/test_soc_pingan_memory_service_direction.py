"""Synthetic service directions stay object-bound and historical profiles stay frozen."""

import pytest

from soc_agent.contracts import AnalysisRun, LLMAnalysisRequest
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.utils.hashing import stable_hash


def _request(*, direction="to_client", src_port=11999, dst_port=2461, protocol="TCP", observations=(), refs=None):
    return LLMAnalysisRequest.model_validate(
        {
            "alert_id": "synthetic-direction",
            "environment": "dev",
            "source": {"source_system": "zeus", "product": "ids", "integration_name": "pingan_legacy_alert_platform"},
            "detection": {"detection_key": "synthetic-detector", "rule_name": "Synthetic network detection"},
            "canonical_entities": {
                "network": {
                    "source_ip": "192.0.2.1",
                    "destination_ip": "192.0.2.2",
                    "src_port": src_port,
                    "dst_port": dst_port,
                    "protocol": protocol,
                    "direction": direction,
                    "observations": list(observations),
                },
                "detections": [
                    {
                        "observation_id": "synthetic-detection",
                        "evidence_path": "synthetic",
                        "event_scope_id": "synthetic",
                        "kind": "network_access",
                        "name": "Synthetic detection",
                        "detector_id": "event-42",
                        "subject_refs": refs or ["entities.network"],
                    }
                ],
            },
        }
    )


def _observation(**fields):
    return {"observation_id": "synthetic-observation", "evidence_path": "synthetic", **fields}


def _current():
    return PingAnSocMemoryProfile(semantic_features=True)


@pytest.mark.parametrize(("direction", "expected"), [("to_client", "tcp/11999"), ("to_server", "tcp/2461")])
def test_service_and_strong_anchor_follow_the_same_explicit_direction(direction, expected):
    facets = _current().project_query_facets(_request(direction=direction))
    assert facets["network_service"] == [expected]
    assert facets["behavior_component_strong"] == ["detected_behavior:network:zeus:ids:id:event-42@service:" + expected]
    assert "network_service:" + expected in facets["behavior_component_core"]


@pytest.mark.parametrize("direction", [None, "unknown", "in", "out", "inbound", "outbound"])
def test_unknown_direction_does_not_guess_a_service(direction):
    request = _request(direction=direction)
    facets = _current().project_query_facets(request)
    assert not facets.get("network_service")
    assert not facets.get("behavior_component_strong")
    assert "entities.detections[0]:subject_not_projected:entities.network" in _current().projection_gaps(request)


@pytest.mark.parametrize("port", [0, -1, 65536, None])
def test_invalid_service_port_does_not_fall_back_to_other_endpoint_or_http(port):
    request = _request(src_port=port)
    request.canonical_entities.http.port = 443
    request.canonical_entities.http.protocol = "https"
    facets = _current().project_query_facets(request)
    assert not facets.get("network_service")
    assert not facets.get("behavior_component_strong")


@pytest.mark.parametrize("protocol", [None, "", "unknown", "http", "icmp"])
def test_unknown_transport_does_not_create_service_anchor(protocol):
    facets = _current().project_query_facets(_request(protocol=protocol))
    assert not facets.get("network_service")
    assert not facets.get("behavior_component_strong")


def test_bound_observation_uses_only_its_own_direction_transport_and_ports():
    observations = [
        _observation(direction="to_server", protocol="UDP", src_port=60000, dst_port=53),
        _observation(observation_id="incomplete", direction=None, protocol=None, src_port=2222, dst_port=443),
    ]
    request = _request(observations=observations, refs=["entities.network.observations[0]"])
    facets = _current().project_query_facets(request)
    assert facets["behavior_component_strong"] == ["detected_behavior:network:zeus:ids:id:event-42@service:udp/53"]
    assert "tcp/443" not in facets["network_service"]
    assert "tcp/53" not in facets["network_service"]
    request.canonical_entities.detections[0].subject_refs = ["entities.network.observations[1]"]
    assert not _current().project_query_facets(request).get("behavior_component_strong")


def test_aggregate_with_distinct_connections_needs_an_explicit_observation_binding():
    observations = [
        _observation(source_ip="192.0.2.1", destination_ip="192.0.2.2", direction="to_client", protocol="tcp", src_port=11999, dst_port=2461),
        _observation(observation_id="other", source_ip="192.0.2.3", destination_ip="192.0.2.4", direction="to_server", protocol="tcp", src_port=55000, dst_port=443),
    ]
    request = _request(observations=observations)
    assert not _current().project_query_facets(request).get("behavior_component_strong")
    assert "entities.detections[0]:network_subject_ambiguous:entities.network" in _current().projection_gaps(request)
    request.canonical_entities.detections[0].subject_refs = ["entities.network.observations[1]"]
    assert _current().project_query_facets(request)["behavior_component_strong"] == ["detected_behavior:network:zeus:ids:id:event-42@service:tcp/443"]


def test_reversed_request_response_observations_do_not_invent_two_connections():
    request = _request(observations=[_observation(source_ip="192.0.2.2", destination_ip="192.0.2.1", direction="to_server", protocol="6", src_port=2461, dst_port=11999)])
    facets = _current().project_query_facets(request)
    assert facets["network_service"] == ["tcp/11999"]
    assert facets["behavior_component_strong"]


def test_current_identity_and_run_projection_use_directional_features():
    profile = _current()
    assert profile.identity.profile_version == "10"
    assert profile.identity.feature_schema_version == "pingan.soc.memory_features.v8"
    request = _request()
    request.memory_profile = {"profile_id": "pingan.soc", "profile_version": "10", "feature_schema_version": "pingan.soc.memory_features.v8"}
    run = AnalysisRun(alert_id=request.alert_id, status="success", llm_analysis_request=request)
    restored = PingAnSocMemoryProfile.for_run(run)
    assert restored.project_run_facets(run) == profile.project_query_facets(request)
    assert PingAnSocMemoryProfile().identity.profile_version == "7"


def test_current_scope_expands_only_its_verified_directional_fingerprint():
    profile = _current()
    facets = profile.project_query_facets(_request())
    spec = profile.build_applicability(consensus_facets=facets, strong_anchor_facets=facets)
    assert spec is not None
    assert spec.policy_version == "soc.memory_applicability_policy.v4"
    assert spec.required_facets["behavior_fingerprint"] == facets["behavior_fingerprint"]
    assert spec.covered_behavior_components == facets["behavior_component_core"]
    view = profile.explain_scope_facets(spec, facets)
    assert view["behavior_fingerprint"]["behavior_component"] == facets["behavior_component_core"]
    assert view["behavior_fingerprint"]["network_service"] == ["tcp/11999"]


@pytest.mark.parametrize(("direction", "version"), [("to_server", "9"), ("to_client", "10"), (None, "10")])
def test_fresh_requests_select_new_identity_only_when_canonical_features_change(direction, version):
    request = _request(direction=direction)
    registry = SocMemoryProfileRegistry([_current()])
    assert registry.resolve_request(request).identity.profile_version == version
    assert request.memory_profile == {}


def test_new_projection_gap_is_not_hidden_when_duplicate_service_anchors_collapse():
    request = _request(
        direction="to_server",
        observations=[_observation(source_ip="192.0.2.3", destination_ip="192.0.2.4", direction="to_server", protocol="tcp", src_port=11999, dst_port=2461)],
        refs=["entities.network", "entities.network.observations[0]"],
    )
    previous = PingAnSocMemoryProfile(semantic_features=True, directional_services=False)
    old_facets, new_facets = previous.project_query_facets(request), _current().project_query_facets(request)
    old_facets.pop("behavior_fingerprint")
    new_facets.pop("behavior_fingerprint")
    assert old_facets == new_facets
    assert previous.projection_gaps(request) == []
    registry = SocMemoryProfileRegistry([_current()])
    selected = registry.resolve_request(request)
    assert selected.identity.profile_version == "10"
    assert "entities.detections[0]:network_subject_ambiguous:entities.network" in selected.projection_gaps(request)


@pytest.mark.parametrize("version", ["7", "8", "9", "10"])
def test_saved_identity_never_runs_fresh_feature_selection(version):
    request = _request(direction="to_server" if version == "10" else "to_client")
    request.memory_profile = {"profile_id": "pingan.soc", "profile_version": version, "feature_schema_version": f"pingan.soc.memory_features.v{int(version) - 2}"}
    registry = SocMemoryProfileRegistry([_current()])
    assert registry.resolve_request(request).identity.profile_version == version


def test_invalid_saved_identity_is_not_replaced_by_an_unchanged_projection():
    request = _request(direction="to_server")
    request.memory_profile = {"profile_id": "pingan.soc", "profile_version": "10", "feature_schema_version": "pingan.soc.memory_features.v7"}
    with pytest.raises(ValueError, match="saved PingAn Memory"):
        SocMemoryProfileRegistry([_current()]).resolve_request(request)


@pytest.mark.parametrize("network_present", [True, False], ids=["to-server", "file-without-network"])
def test_unchanged_features_keep_reviewed_v9_directive_on_its_exact_path(network_present):
    from test_soc_memory_reference_retrieval import reference_fixture

    from soc_agent.contracts import NetworkEntityRef, SocMemoryDecisionDirective, SocMemoryDecisionImpact
    from soc_agent.core import SocMemoryService
    from soc_agent.memory import ConfirmedMemoryAnalysisRequestEnricher, memory_query_from_analysis_request

    _, _, record, repository, _, _, now = reference_fixture()
    request = _request(direction="to_server")
    request.tenant_id = "pingan"
    if not network_present:
        request.canonical_entities.network = NetworkEntityRef()
        request.canonical_entities.file.file_path = "C:/ProgramData/synthetic.dll"
        request.canonical_entities.detections[0].kind = "file_detection"
        request.canonical_entities.detections[0].subject_refs = ["entities.file"]
    old_profile = PingAnSocMemoryProfile().for_identity({"profile_id": "pingan.soc", "profile_version": "9", "feature_schema_version": "pingan.soc.memory_features.v7"})
    facets = old_profile.project_query_facets(request)
    spec = old_profile.build_applicability(consensus_facets=facets, strong_anchor_facets=facets)
    record.facets = facets
    record.facets_hash = stable_hash(facets)
    record.applicability = spec
    record.decision_impact = SocMemoryDecisionImpact.DETECTION_DECISION
    record.decision_directive = SocMemoryDecisionDirective(effect="override", target_verdict="false_positive", required_facet_keys=list(spec.required_facets), rationale="Synthetic exact reviewed behavior.")
    repository.save_memory_record(record)
    before = record.model_dump_json()
    registry = SocMemoryProfileRegistry([_current()])
    service = SocMemoryService(record_repository=repository, profile_registry=registry, now_provider=lambda: now)
    selected = registry.resolve_request(request)
    query = memory_query_from_analysis_request(request, profile=selected)
    result = service.find_directive_records(query)
    assert len(result.matches) == 1
    assert result.matches[0].applicability_report.status == "applicable"
    enriched = ConfirmedMemoryAnalysisRequestEnricher(service, profile_registry=registry)(request)
    assert enriched.memory_profile["profile_version"] == "9"
    assert enriched.context_catalog[0].metadata["decision_directive_applicable"]
    request.canonical_entities.network = _request(direction="to_client").canonical_entities.network
    changed = registry.resolve_request(request)
    assert changed.identity.profile_version == "10"
    assert not service.find_directive_records(memory_query_from_analysis_request(request, profile=changed)).matches
    assert repository.get_memory_record(record.memory_id).model_dump_json() == before


@pytest.mark.parametrize(("version", "expected"), [("7", True), ("8", False), ("9", True), ("10", True)])
def test_current_apply_family_is_only_a_known_display_identity(version, expected):
    identity = {"profile_id": "pingan.soc", "profile_version": version, "feature_schema_version": f"pingan.soc.memory_features.v{int(version) - 2}"}
    assert _current().is_current_identity(identity) is expected
    assert not _current().is_current_identity({**identity, "profile_id": "unknown"})
    assert not _current().is_current_identity({**identity, "feature_schema_version": "unknown"})


@pytest.mark.parametrize("semantic_features", [False, True], ids=["process-default-off", "process-default-apply"])
def test_memory_center_counts_current_execution_identities_without_replacing_v9_candidates(semantic_features):
    from datetime import UTC, datetime

    from test_soc_memory_reference_retrieval import reference_fixture

    from soc_agent.contracts import MemoryCenterInventory, MemoryPatternLineageStats, MemoryPatternLineageStatsPage, SocMemoryCandidate, SocMemoryCandidateSourceType
    from soc_agent.core import SocMemoryCenterService

    now = datetime.now(UTC)
    rows = [
        MemoryPatternLineageStats(
            lineage_key=version.zfill(64),
            tenant_id="pingan",
            environment="dev",
            data_class="simulation",
            profile_id="pingan.soc",
            profile_version=version,
            feature_schema_version=f"pingan.soc.memory_features.v{int(version) - 2}",
            pattern_dimension="detection",
            pattern_value="synthetic",
            pattern_label="Synthetic",
            support_count=1,
            distinct_source_count=1,
            aggregation_window_count=1,
            first_observed_at=now,
            last_observed_at=now,
            first_window_start=now,
            last_window_end=now,
        )
        for version in ("7", "8", "9", "10")
    ]

    class Inventory:
        def list_memory_pattern_lineage_stats(self, **kwargs):
            return MemoryPatternLineageStatsPage(items=rows[kwargs["offset"] : kwargs["offset"] + kwargs["limit"]], total=4, limit=kwargs["limit"], offset=kwargs["offset"])

        def find_memory_candidates_by_lineage_keys(self, keys):
            return []

        def get_memory_center_inventory(self):
            return MemoryCenterInventory(
                pattern_count=4,
                aggregation_window_count=4,
                observation_count=4,
                retrieval_enabled_record_count=0,
                profile_inventory=[{**{key: getattr(row, key) for key in ("profile_id", "profile_version", "feature_schema_version")}, "pattern_count": 1, "aggregation_window_count": 1, "observation_count": 1} for row in rows],
            )

    _, _, record, repository, _, _, _ = reference_fixture()
    profile = _current().for_request(_request(direction="to_server"))
    facets = profile.project_query_facets(_request(direction="to_server"))
    spec = profile.build_applicability(consensus_facets=facets, strong_anchor_facets=facets)
    candidate = SocMemoryCandidate(
        candidate_id="MC-CURRENT-V9",
        candidate_type=record.memory_type,
        target_artifact=record.target_artifact,
        summary="Synthetic current candidate",
        content="Synthetic review body",
        tenant_id="pingan",
        tenant_scope="pingan",
        source=record.source.model_copy(update={"source_type": SocMemoryCandidateSourceType.REPEATED_PATTERN, "alert_id": "synthetic"}),
        evidence_refs=record.evidence_refs,
        validity=record.validity,
        confidence=record.confidence,
        facets=facets,
        applicability=spec,
    )
    repository.save_memory_candidate(candidate)
    repository.save_memory_candidate(candidate.model_copy(update={"candidate_id": "MC-CURRENT-V10", "applicability": spec.model_copy(update={"profile_version": "10", "feature_schema_version": "pingan.soc.memory_features.v8"})}))
    before = candidate.model_dump_json()
    center = SocMemoryCenterService(center_repository=Inventory(), candidate_repository=repository, record_repository=repository, profile_registry=SocMemoryProfileRegistry([PingAnSocMemoryProfile(semantic_features=semantic_features)]))
    overview = center.overview()
    assert overview.metrics.legacy_profile_pattern_count == 1
    assert {row.profile_version: row.profile_state.value for row in overview.items} == {"7": "current", "8": "legacy", "9": "current", "10": "current"}
    assert all("legacy_memory_profile" not in row.attention_reasons for row in overview.items if row.profile_version in {"7", "9", "10"})
    assert center._suggested_successor(candidate) is None
    assert repository.get_memory_candidate(candidate.candidate_id).model_dump_json() == before


# Filled from the original implementation before changing its projections.
_LEGACY_FACET_HASHES = {
    "7": "d7155007918ae5ac1250c12aba357b097dc772410457d193264f9ba9da5de78b",
    "8": "04f0db9a1f46b52cc9e6cedf18749ab57fe0f41a2f86fed65547be6cff7e6809",
    "9": "262affe3173b0d02aa903c19b90d5c831d2b290b3261a3af068f36516df83d6c",
}


@pytest.mark.parametrize("version", ["7", "8", "9"])
def test_historical_profile_features_remain_byte_equivalent(version):
    identity = {"profile_id": "pingan.soc", "profile_version": version, "feature_schema_version": f"pingan.soc.memory_features.v{int(version) - 2}"}
    request = _request()
    request.memory_profile = identity
    run = AnalysisRun(alert_id=request.alert_id, status="success", llm_analysis_request=request)
    before = run.model_dump_json()
    profile = PingAnSocMemoryProfile.for_run(run)
    assert stable_hash(profile.project_query_facets(request)) == _LEGACY_FACET_HASHES[version]
    assert profile.project_run_facets(run) == profile.project_query_facets(request)
    assert run.model_dump_json() == before
