"""Reviewed rule-context lessons remain reference-only across optional differences."""

from datetime import UTC, datetime, timedelta

from soc_agent.contracts import (
    ActorContext,
    AlertClassification,
    AlertEntitySet,
    AlertSourceRef,
    AlertSourceType,
    AnalysisRun,
    AnalysisRunStatus,
    DetectionRuleRef,
    LLMAnalysisRequest,
    NetworkEntityRef,
    SocMemoryApplicabilitySpec,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryDecisionImpact,
    SocMemoryRecord,
    SocMemoryRecordMatchTestCommand,
    SocMemoryTargetArtifact,
)
from soc_agent.core import SocMemoryService
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile
from soc_agent.memory import InMemoryMemoryCandidateRepository
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.memory.retrieval import memory_context_item, memory_query_from_analysis_request
from soc_agent.utils.hashing import stable_hash


def reference_fixture():
    now = datetime(2026, 9, 24, tzinfo=UTC)
    profile = PingAnSocMemoryProfile()
    request = LLMAnalysisRequest(
        alert_id="ALERT-REFERENCE-NETWORK",
        tenant_id="pingan",
        environment="dev-corpus-eval",
        source=AlertSourceRef(source_type=AlertSourceType.NIDS, source_system="test-nids", product="NIDS", integration_name="pingan_legacy_alert_platform"),
        detection=DetectionRuleRef(detection_key="test-nids:rule:directory", rule_name="Directory listing detector"),
        classification=AlertClassification(category="information_exposure"),
        canonical_entities=AlertEntitySet(network=NetworkEntityRef(protocol="TCP", src_port=7709, dst_port=61765, direction="to_client")),
        memory_profile={"profile_id": "pingan.soc", "profile_version": "7", "feature_schema_version": "pingan.soc.memory_features.v5"},
    )
    query = memory_query_from_analysis_request(request, profile=profile)
    facets = {
        **query.facets,
        "network_service": ["tcp/2461"],
        "behavior_component": ["network_service:tcp/2461", "protocol:tcp"],
        "behavior_component_core": ["network_service:tcp/2461", "protocol:tcp"],
        "behavior_component_weak": ["network_service:tcp/2461", "protocol:tcp"],
        "behavior_strength": ["weak_only"],
    }
    spec = SocMemoryApplicabilitySpec(
        profile_id="pingan.soc",
        profile_version="7",
        feature_schema_version="pingan.soc.memory_features.v5",
        required_facets={key: query.facets[key] for key in ("detection_key", "detection_signature", "environment")},
        optional_facets={"network_service": ["tcp/2461"]},
        minimum_optional_matches=0,
        minimum_strong_anchor_matches=2,
    )
    record = SocMemoryRecord(
        memory_id="MEM-NETWORK-REFERENCE",
        memory_type=SocMemoryCandidateType.DETECTION_LESSON,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        tenant_scope="pingan",
        tenant_id="pingan",
        source_candidate_id="MC-NETWORK-REFERENCE",
        source=SocMemoryCandidateSource(source_type=SocMemoryCandidateSourceType.MANUAL_NOTE, source_id="reviewed-source"),
        summary="Reviewed directory-listing lesson",
        content="A directory listing alone does not establish malicious file transfer. Compare current evidence and business context.",
        evidence_refs=["reviewed-source"],
        facets=facets,
        applicability=spec,
        decision_impact=SocMemoryDecisionImpact.REVIEW_HINT,
        validity=SocMemoryCandidateValidity(valid_from=now - timedelta(days=1), notes="Synthetic approved reference fixture."),
        confidence=0.8,
        content_hash=stable_hash("reviewed-content"),
        facets_hash=stable_hash(facets),
        retrieval_enabled=True,
        retrieval_policy_version="soc.memory_retrieval_activation_policy.v1",
        retrieval_valid_until=now + timedelta(days=30),
        retrieval_review_due_at=now + timedelta(days=7),
        retrieval_updated_by=ActorContext(actor_id="reviewer"),
        retrieval_updated_at=now,
        retrieval_reason="Reviewer approved this lesson for reference.",
        created_by=ActorContext(actor_id="reviewer"),
    )
    repository = InMemoryMemoryCandidateRepository()
    repository.save_memory_record(record)
    registry = SocMemoryProfileRegistry([profile])
    service = SocMemoryService(record_repository=repository, profile_registry=registry, now_provider=lambda: now)
    return request, query, record, repository, registry, service, now


def test_optional_service_difference_retains_reviewed_reference_without_directive():
    _, query, record, repository, _, service, _ = reference_fixture()
    frozen = record.model_dump(mode="json")
    result = service.find_relevant_records(query)
    assert len(result.matches) == 1
    match = result.matches[0]
    assert match.applicability_report.status == "partial"
    assert match.applicability_report.context_only_allowed
    assert "optional_network_service_difference" in match.applicability_report.reason_codes
    item = memory_context_item(match, query_facets=query.facets, retrieval_policy_version=result.policy_version)
    assert item.memory_comparison.use_mode == "context_only"
    assert not item.memory_comparison.decision_directive_applicable
    assert item.memory_comparison.current_only_facets["network_service"] == ["tcp/61765"]
    assert item.memory_comparison.memory_only_facets["network_service"] == ["tcp/2461"]
    assert service.find_directive_records(query).matches == []
    assert repository.get_memory_record(record.memory_id).model_dump(mode="json") == frozen


def test_known_profile_change_can_only_recall_rule_context_as_reference():
    _, query, _, _, _, service, _ = reference_fixture()
    query = query.model_copy(update={"metadata": {**query.metadata, "memory_profile_version": "9", "memory_feature_schema_version": "pingan.soc.memory_features.v7"}})
    result = service.find_relevant_records(query)
    assert len(result.matches) == 1
    report = result.matches[0].applicability_report
    assert report.status == "partial"
    assert report.context_only_allowed
    assert "compatible_profile_reference_only" in report.reason_codes
    assert service.find_directive_records(query).matches == []


def test_current_response_projection_recalls_old_reference_with_explicit_port_comparison():
    request, _, record, repository, _, _, now = reference_fixture()
    profile = PingAnSocMemoryProfile(semantic_features=True)
    request = request.model_copy(update={"memory_profile": {}})
    request.canonical_entities.network = NetworkEntityRef.model_validate(
        {
            "protocol": "TCP",
            "direction": "to_client",
            "observations": [{"observation_id": f"response-{port}", "evidence_path": "synthetic", "protocol": "TCP", "direction": "to_client", "src_port": 7709, "dst_port": port} for port in (61764, 61765)],
        }
    )
    query = memory_query_from_analysis_request(request, profile=profile)
    assert query.facets["network_service"] == ["tcp/7709"]
    service = SocMemoryService(record_repository=repository, profile_registry=SocMemoryProfileRegistry([profile]), now_provider=lambda: now)
    frozen = record.model_dump_json()
    result = service.find_relevant_records(query)
    assert len(result.matches) == 1
    item = memory_context_item(result.matches[0], query_facets=query.facets, retrieval_policy_version=result.policy_version)
    assert item.memory_comparison.use_mode == "context_only"
    assert item.memory_comparison.current_only_facets["network_service"] == ["tcp/7709"]
    assert item.memory_comparison.memory_only_facets["network_service"] == ["tcp/2461"]
    assert not item.memory_comparison.decision_directive_applicable
    assert service.find_directive_records(query).matches == []
    assert repository.get_memory_record(record.memory_id).model_dump_json() == frozen


def test_match_preview_uses_same_profile_conflicts_as_runtime():
    request, query, record, repository, registry, _, now = reference_fixture()
    request.classification.labels["vulnerability_id"] = "CVE-2026-22222"
    record.facets["vulnerability_id"] = ["CVE-2026-11111"]
    repository.save_memory_record(record)
    run = AnalysisRun(alert_id=request.alert_id, status=AnalysisRunStatus.SUCCESS, llm_analysis_request=request)

    class Runs:
        def get_run(self, run_id):
            return run if run_id == run.run_id else None

    service = SocMemoryService(record_repository=repository, analysis_run_repository=Runs(), profile_registry=registry, now_provider=lambda: now)
    result = service.test_record_match(SocMemoryRecordMatchTestCommand(memory_id=record.memory_id, run_id=run.run_id))
    assert result.matched is False
    assert result.retrieval.skipped_not_applicable == 1
