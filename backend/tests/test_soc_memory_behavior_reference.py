"""Saved semantic scopes may cross known identities only as reviewed context."""

from datetime import timedelta

import pytest
from test_soc_memory_reference_retrieval import reference_fixture

from soc_agent.contracts import SocMemoryDecisionDirective, SocMemoryDecisionImpact
from soc_agent.contracts.schemas import SocMemoryReuseCondition, SocMemoryScopeBinding
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile
from soc_agent.memory.retrieval import memory_context_item
from soc_agent.utils.hashing import stable_hash

HTTP_ANCHORS = ["detected_behavior:http:sensor:product:id:sql@http:post", "detected_behavior:http:sensor:product:id:command@http:post"]
SERVICE_COMPONENTS = ["network_service:http/80", "detected_behavior:network:sensor:product:id:sql@service:http/80", "detected_behavior:network:sensor:product:id:command@service:http/80"]
COMMON_COMPONENTS = ["attack_family:sql_injection", *HTTP_ANCHORS, "http_method:post", "protocol:http", "scenario:web_attack", "technique:t1190", "web_detection_target"]
SCHEMAS = {"9": "v7", "10": "v8", "11": "v9", "12": "v10"}


def test_source_proven_service_restores_exact_directive_while_missing_proof_only_recalls_context():
    from test_soc_memory_service_identity import _profile_payload

    from soc_agent.contracts import LLMAnalysisRequest
    from soc_agent.core import SocMemoryService
    from soc_agent.memory import ConfirmedMemoryAnalysisRequestEnricher, memory_query_from_analysis_request
    from soc_agent.memory.profiles import SocMemoryProfileRegistry

    _, _, record, repository, _, _, now = reference_fixture()
    request = LLMAnalysisRequest.model_validate(_profile_payload())
    original = PingAnSocMemoryProfile(semantic_features=True, directional_services=False)
    facets = original.project_query_facets(request)
    spec = original.build_applicability(consensus_facets=facets, strong_anchor_facets=facets)
    record.facets = facets
    record.facets_hash = stable_hash(facets)
    record.applicability = spec
    record.decision_impact = SocMemoryDecisionImpact.DETECTION_DECISION
    record.decision_directive = SocMemoryDecisionDirective(effect="override", target_verdict="false_positive", required_facet_keys=list(spec.required_facets), rationale="Synthetic reviewed HTTP service behavior.")
    repository.save_memory_record(record)
    frozen = record.model_dump_json()
    registry = SocMemoryProfileRegistry([PingAnSocMemoryProfile(semantic_features=True)])
    service = SocMemoryService(record_repository=repository, profile_registry=registry, now_provider=lambda: now)
    query = memory_query_from_analysis_request(request, profile=registry.resolve_request(request))
    direct = service.find_directive_records(query)
    assert len(direct.matches) == 1
    assert direct.matches[0].applicability_report.status.value == "applicable"
    enriched = ConfirmedMemoryAnalysisRequestEnricher(service, profile_registry=registry)(request)
    assert enriched.context_catalog[0].metadata["decision_directive_applicable"]

    unproven = request.model_copy(update={"source_field_semantics": []})
    unproven_query = memory_query_from_analysis_request(unproven, profile=registry.resolve_request(unproven))
    assert service.find_directive_records(unproven_query).matches == []
    references = service.find_relevant_records(unproven_query)
    assert len(references.matches) == 1
    item = memory_context_item(references.matches[0], query_facets=unproven_query.facets, retrieval_policy_version=references.policy_version)
    assert item.memory_comparison.use_mode.value == "context_only"
    assert not item.metadata["decision_directive_applicable"]
    assert repository.get_memory_record(record.memory_id).model_dump_json() == frozen


def _fixture(*, source_version="9", query_version="10", directive=True):
    _, query, record, repository, _, service, now = reference_fixture()
    profile = PingAnSocMemoryProfile().for_identity({"profile_id": "pingan.soc", "profile_version": source_version, "feature_schema_version": f"pingan.soc.memory_features.{SCHEMAS[source_version]}"})
    components = sorted(COMMON_COMPONENTS + SERVICE_COMPONENTS)
    facets = {
        **{key: query.facets[key] for key in ("detection_key", "detection_signature", "environment")},
        "behavior_component": components,
        "behavior_component_core": components,
        "behavior_component_strong": [*HTTP_ANCHORS, *SERVICE_COMPONENTS[1:], "technique:t1190"],
        "behavior_fingerprint": [stable_hash({"schema_version": f"pingan.soc.memory_behavior_fingerprint.{SCHEMAS[source_version]}", "components": components})],
        "behavior_strength": ["strong"],
        "network_service": ["http/80"],
        "attack_behavior_family": ["sql_injection"],
    }
    spec = profile.build_applicability(consensus_facets=facets, strong_anchor_facets=facets)
    record = record.model_copy(
        update={
            "facets": facets,
            "facets_hash": stable_hash(facets),
            "applicability": spec,
            "decision_impact": SocMemoryDecisionImpact.DETECTION_DECISION if directive else SocMemoryDecisionImpact.REVIEW_HINT,
            "decision_directive": SocMemoryDecisionDirective(
                effect="override", target_verdict="false_positive", required_facet_keys=["detection_key", "detection_signature", "environment", "behavior_component"], rationale="Synthetic reviewed behavior scope."
            )
            if directive
            else None,
        }
    )
    repository.save_memory_record(record)
    current = {
        **facets,
        "behavior_component": sorted(COMMON_COMPONENTS),
        "behavior_component_core": sorted(COMMON_COMPONENTS),
        "behavior_component_strong": [*HTTP_ANCHORS, "technique:t1190"],
        "behavior_fingerprint": ["different-current-fingerprint"],
    }
    current.pop("network_service")
    query = query.model_copy(
        update={
            "facets": current,
            "scope_bindings": [],
            "projection_gaps": ["entities.detections[0]:subject_not_projected:entities.network"],
            "metadata": {**query.metadata, "memory_profile_version": query_version, "memory_feature_schema_version": f"pingan.soc.memory_features.{SCHEMAS[query_version]}"},
        }
    )
    return service, repository, record, query, now


@pytest.mark.parametrize("directive", [False, True])
@pytest.mark.parametrize(("source_version", "query_version"), [("9", "10"), ("9", "11"), ("10", "11"), ("11", "10"), ("9", "12"), ("10", "12"), ("11", "12")])
def test_saved_behavior_scope_with_missing_service_is_reference_only(source_version, query_version, directive):
    service, repository, record, query, _ = _fixture(source_version=source_version, query_version=query_version, directive=directive)
    frozen_record, frozen_query = record.model_dump_json(), query.model_dump_json()
    result = service.find_relevant_records(query)
    assert len(result.matches) == 1
    report = result.matches[0].applicability_report
    assert report.status.value == "partial"
    assert report.context_only_allowed
    assert report.missing_behavior_components == sorted(SERVICE_COMPONENTS)
    assert {"compatible_semantic_scope_reference_only", "missing_service_reference_only", "behavior_projection_incomplete"} <= set(report.reason_codes)
    item = memory_context_item(result.matches[0], query_facets=query.facets, retrieval_policy_version=result.policy_version)
    assert item.memory_comparison.use_mode.value == "context_only"
    assert "部分服务条件尚未确认" in item.memory_comparison.applicability_explanation
    assert "仅供参考" in item.memory_comparison.applicability_explanation
    assert not item.memory_comparison.decision_directive_applicable
    assert not item.metadata["decision_directive_applicable"]
    assert service.find_directive_records(query).matches == []
    assert repository.get_memory_record(record.memory_id).model_dump_json() == frozen_record
    assert query.model_dump_json() == frozen_query


def test_new_http_behavior_stays_visible_without_granting_direct_reuse():
    service, _, _, query, _ = _fixture(query_version="11")
    added = "http_observation:response=directory_listing;server=simplehttp"
    query.facets["behavior_component_core"].append(added)
    query.facets["behavior_component"].append(added)
    query.facets["behavior_component_strong"].append(added)
    result = service.find_relevant_records(query)
    assert len(result.matches) == 1
    report = result.matches[0].applicability_report
    assert report.uncovered_behavior_components == [added]
    assert "uncovered_core_behavior" in report.reason_codes
    assert service.find_directive_records(query).matches == []


def test_matching_selected_behavior_across_identity_still_cannot_reuse_directive():
    service, repository, record, query, _ = _fixture()
    record.applicability.selected_behavior_components = sorted(COMMON_COMPONENTS)
    repository.save_memory_record(record)
    result = service.find_relevant_records(query)
    assert len(result.matches) == 1
    match = result.matches[0]
    assert not match.applicability_report.missing_behavior_components
    assert "missing_service_reference_only" not in match.applicability_report.reason_codes
    item = memory_context_item(match, query_facets=query.facets, retrieval_policy_version=result.policy_version)
    assert "仅供参考" in item.memory_comparison.applicability_explanation
    assert item.memory_comparison.use_mode.value == "context_only"
    assert not item.memory_comparison.decision_directive_applicable
    assert service.find_directive_records(query).matches == []


def test_proven_business_limits_preserve_reference_when_all_objects_match():
    service, repository, record, query, _ = _fixture()
    conditions = [
        SocMemoryReuseCondition(facet_key="role_entity", value_prefix="source", values=["source:192.0.2.1"]),
        SocMemoryReuseCondition(facet_key="role_entity", value_prefix="destination", values=["destination:192.0.2.2"]),
    ]
    record.applicability.reuse_conditions = conditions
    record.applicability.required_facets["entity"] = ["domain:business.test"]
    record.applicability.context_only_required_facet_keys.append("entity")
    query.facets["entity"] = ["domain:business.test"]
    query.facets["role_entity"] = ["source:192.0.2.1", "destination:192.0.2.2"]
    query.scope_bindings = [SocMemoryScopeBinding(source_ref="entities.network.observations[0]", facets={"role_entity": list(query.facets["role_entity"])})]
    repository.save_memory_record(record)
    result = service.find_relevant_records(query)
    assert len(result.matches) == 1
    assert set(result.matches[0].applicability_report.matched_reuse_conditions) == {condition.condition_key for condition in conditions}
    assert not result.matches[0].applicability_report.missing_reuse_conditions
    assert service.find_directive_records(query).matches == []


@pytest.mark.parametrize(
    "guard",
    [
        "disabled",
        "activation_expired",
        "review_overdue",
        "expired",
        "unconfirmed",
        "tenant",
        "environment",
        "detector",
        "signature",
        "strong_class",
        "unknown_query",
        "unknown_record",
        "unknown_schema",
        "profile_7",
        "missing_selected",
        "missing_coverage",
        "unverified_coverage",
        "unverified_source",
        "unknown_required",
        "unknown_exclusion",
        "unknown_reuse",
        "required_ip",
        "reuse_ip",
        "excluded_ip",
        "binding_gap",
        "optional_threshold",
    ],
)
def test_semantic_reference_preserves_governance_and_saved_scope(guard):
    service, repository, record, query, now = _fixture()
    spec = record.applicability
    if guard == "disabled":
        record.retrieval_enabled = False
    elif guard == "activation_expired":
        record.retrieval_valid_until = now
    elif guard == "review_overdue":
        record.retrieval_review_due_at = now
    elif guard == "expired":
        record.validity.valid_until = now - timedelta(seconds=1)
    elif guard == "unconfirmed":
        record.status = "deprecated"
    elif guard == "tenant":
        query.tenant_id = "foreign"
    elif guard in {"environment", "detector", "signature", "strong_class"}:
        key = {"environment": "environment", "detector": "detection_key", "signature": "detection_signature", "strong_class": "behavior_strength"}[guard]
        query.facets[key] = ["different"]
    elif guard == "unknown_query":
        query.metadata["memory_profile_version"] = "unknown"
    elif guard == "unknown_record":
        spec.profile_version = "unknown"
    elif guard == "unknown_schema":
        query.metadata["memory_feature_schema_version"] = "unknown"
    elif guard == "profile_7":
        spec.profile_version, spec.feature_schema_version = "7", "pingan.soc.memory_features.v5"
    elif guard == "missing_selected":
        spec.selected_behavior_components = None
    elif guard == "missing_coverage":
        spec.covered_behavior_components = None
    elif guard == "unverified_coverage":
        spec.covered_behavior_components = [*spec.covered_behavior_components, "http_observation:response=directory_listing"]
    elif guard == "unverified_source":
        spec.required_facets["behavior_fingerprint"] = ["unverified"]
    elif guard == "unknown_required":
        spec.required_facets["opaque_business_scope"] = ["x"]
        query.facets["opaque_business_scope"] = ["x"]
    elif guard == "unknown_exclusion":
        spec.excluded_facets["opaque_business_scope"] = ["x"]
    elif guard == "unknown_reuse":
        spec.reuse_conditions = [SocMemoryReuseCondition(facet_key="behavior_fingerprint", values=["old-hash"])]
    elif guard == "required_ip":
        spec.required_facets["entity"] = ["ip:192.0.2.1"]
    elif guard == "reuse_ip":
        spec.reuse_conditions = [SocMemoryReuseCondition(facet_key="role_entity", value_prefix="source", values=["source:192.0.2.1"])]
    elif guard == "excluded_ip":
        spec.excluded_facets["entity"] = ["ip:192.0.2.1"]
        query.facets["entity"] = ["ip:192.0.2.1"]
    elif guard == "binding_gap":
        spec.reuse_conditions = [
            SocMemoryReuseCondition(facet_key="role_entity", value_prefix="source", values=["source:192.0.2.1"]),
            SocMemoryReuseCondition(facet_key="role_entity", value_prefix="destination", values=["destination:192.0.2.2"]),
        ]
        query.facets["role_entity"] = ["source:192.0.2.1", "destination:192.0.2.2"]
        query.scope_bindings = [SocMemoryScopeBinding(source_ref="entities.network.observations[0]", facets={"role_entity": ["source:192.0.2.1", "destination:192.0.2.3"]})]
    elif guard == "optional_threshold":
        spec.minimum_optional_matches = len(spec.optional_facets)
    repository.save_memory_record(record)
    assert service.find_relevant_records(query).matches == []
    assert service.find_directive_records(query).matches == []


@pytest.mark.parametrize("guard", ["service", "bound_service", "other_bound_service", "overlapping_bound_service", "cve", "family", "no_concrete_strong", "outside_selected", "missing_non_service"])
def test_same_detector_does_not_override_known_conflict_or_missing_behavior(guard):
    service, repository, record, query, _ = _fixture()
    if guard == "service":
        query.facets["network_service"] = ["tcp/443"]
    elif guard in {"bound_service", "other_bound_service", "overlapping_bound_service"}:
        changed = SERVICE_COMPONENTS[1].replace("http/80", "tcp/443")
        if guard == "other_bound_service":
            changed = changed.replace("id:sql", "id:other")
        query.facets["behavior_component_core"].append(changed)
        query.facets["behavior_component"].append(changed)
        if guard == "overlapping_bound_service":
            query.facets["network_service"] = ["http/80", "tcp/443"]
    elif guard == "cve":
        record.facets["vulnerability_id"] = ["cve-2026-11111"]
        query.facets["vulnerability_id"] = ["cve-2026-22222"]
    elif guard == "family":
        query.facets["attack_behavior_family"] = ["command_execution"]
        query.facets["behavior_component"] = [value.replace("attack_family:sql_injection", "attack_family:command_execution") for value in query.facets["behavior_component"]]
        query.facets["behavior_component_core"] = list(query.facets["behavior_component"])
    elif guard == "no_concrete_strong":
        query.facets["behavior_component_strong"] = ["technique:t1190"]
    elif guard == "outside_selected":
        record.applicability.selected_behavior_components = ["technique:t1190"]
    elif guard == "missing_non_service":
        query.facets["behavior_component_core"].remove(HTTP_ANCHORS[0])
    repository.save_memory_record(record)
    assert service.find_relevant_records(query).matches == []
