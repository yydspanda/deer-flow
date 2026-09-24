"""Independent rejection boundaries for the narrow PingAn reference-only lane."""

from datetime import UTC, datetime, timedelta

import pytest
from test_soc_memory_revision_workflow import RevisionRepository, _lesson, _reviewer_context
from test_soc_pingan_memory_profile import _run

from soc_agent.contracts import (
    AlertEntitySet,
    NetworkEntityRef,
    SocMemoryApplicabilitySpec,
    SocMemoryCandidateCreateCommand,
    SocMemoryCandidateReviewCommand,
    SocMemoryCandidateSource,
    SocMemoryCandidateValidity,
    SocMemoryDecisionImpact,
    SocMemoryRecordMatchTestCommand,
    SocMemoryRetrievalActivationCommand,
    Verdict,
)
from soc_agent.core import SocMemoryService
from soc_agent.integrations.pingan.memory import PingAnSocMemoryProfile
from soc_agent.memory import ConfirmedMemoryAnalysisRequestEnricher, memory_query_from_analysis_request
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.memory.retrieval import memory_context_item

NOW = datetime(2026, 9, 24, tzinfo=UTC)


def _profile(version="7"):
    return PingAnSocMemoryProfile().for_identity(
        {
            "profile_id": "pingan.soc",
            "profile_version": version,
            "feature_schema_version": {"7": "pingan.soc.memory_features.v5", "9": "pingan.soc.memory_features.v7", "10": "pingan.soc.memory_features.v8"}[version],
        }
    )


def _request(port=49152, *, category="network_anomaly"):
    request = _run(
        81,
        techniques=[],
        detection_key="pingan:ndr:reviewed-network",
        rule_name="Reviewed network detector",
        category=category,
        canonical_entities=AlertEntitySet(network=NetworkEntityRef(protocol="tcp", src_port=80, dst_port=port, direction="to_client")),
    ).llm_analysis_request
    assert request is not None
    return request


def _fixture(*, spec_changes=None, facet_changes=None, impact=SocMemoryDecisionImpact.REVIEW_HINT, directive=False, registered=True, query_version="9", source_category="network_anomaly"):
    source_profile = _profile()
    current_profile = _profile(query_version)
    registry = SocMemoryProfileRegistry([current_profile]) if registered else SocMemoryProfileRegistry()
    repository = RevisionRepository()
    service = SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
        mutation_audit_repository=repository,
        analysis_run_repository=repository,
        profile_registry=registry,
        now_provider=lambda: NOW,
    )
    facets = source_profile.project_query_facets(_request(category=source_category))
    facets.update(facet_changes or {})
    required = {key: facets[key] for key in ("detection_key", "detection_signature", "environment")}
    optional = {"network_service": facets["network_service"]}
    if directive:
        required["behavior_fingerprint"] = facets["behavior_fingerprint"]
    spec = {
        "profile_id": "pingan.soc",
        "profile_version": "7",
        "feature_schema_version": "pingan.soc.memory_features.v5",
        "required_facets": required,
        "optional_facets": optional,
        "minimum_strong_anchor_matches": 2,
    }
    spec.update(spec_changes or {})
    candidate = service.propose_candidate(
        SocMemoryCandidateCreateCommand(
            candidate_type="detection_lesson",
            target_artifact="tenant_memory",
            summary="人工审核的检测器参考经验",
            content="当前行为仍需模型独立研判，未授予自动忽略权限。",
            tenant_scope="pingan",
            tenant_id="pingan",
            source=SocMemoryCandidateSource(source_type="repeated_pattern", source_id="synthetic-pattern", run_id="synthetic-source", alert_id="synthetic-source-alert"),
            evidence_refs=["synthetic-reviewed-cohort"],
            validity=SocMemoryCandidateValidity(valid_from=NOW - timedelta(days=1), valid_until=NOW + timedelta(days=60), review_after_days=30, notes="Synthetic reviewed detector context."),
            confidence=0.9,
            decision_impact=impact,
            facets=facets,
            applicability=SocMemoryApplicabilitySpec(**spec),
        )
    )
    result = service.review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=candidate.candidate_id,
            decision="confirm",
            reason="人工审核后的只读兼容测试经验。",
            record_lesson=_lesson("这是一条已审核的历史经验，新的告警仍必须满足其保存边界。"),
            confirmed_verdict=Verdict.FALSE_POSITIVE,
            apply_to_future_matches=directive,
        ),
        context=_reviewer_context(key="reference-safety-confirm"),
    )
    assert result.memory_record is not None
    record = service.set_retrieval_activation(
        SocMemoryRetrievalActivationCommand(
            memory_id=result.memory_record.memory_id, action="enable", expected_record_version=result.memory_record.version, reason="显式启用已审核经验用于测试。", activation_valid_until=NOW + timedelta(days=30), review_after_days=7
        ),
        context=_reviewer_context(key="reference-safety-enable"),
    ).record
    query = memory_query_from_analysis_request(_request(50001), profile=current_profile)
    return service, repository, record, query, registry


@pytest.mark.parametrize("query_version", ["7", "9", "10"])
def test_optional_port_compatibility_is_context_only_and_preserves_frozen_record(query_version):
    service, repository, record, query, _registry = _fixture(query_version=query_version)
    before = record.model_dump_json()
    assert record.applicability.profile_version == "7"
    assert record.facets["network_service"] == ["tcp/49152"]
    assert _profile().project_query_facets(_request())["network_service"] == ["tcp/49152"]
    result = service.find_relevant_records(query)
    assert len(result.matches) == 1
    match = result.matches[0]
    assert match.applicability_report.status.value == "partial"
    assert match.applicability_report.context_only_allowed is True
    assert match.record.decision_directive is None
    context = memory_context_item(match, query_facets=query.facets, retrieval_policy_version=result.policy_version)
    assert context.metadata["decision_directive_applicable"] is False
    assert context.memory_comparison.use_mode.value == "context_only"
    for direct in service.find_directive_records(query).matches:
        assert direct.record.decision_directive is None
        assert direct.applicability_report.status.value != "applicable"
    assert repository.get_memory_record(record.memory_id).model_dump_json() == before
    assert service.get_record(record.memory_id).content_hash == record.content_hash
    assert service.get_record(record.memory_id).facets_hash == record.facets_hash


def test_no_registered_tenant_profile_cannot_authorize_cross_version_compatibility():
    service, _repository, _record, query, _registry = _fixture(registered=False)
    assert service.find_relevant_records(query).matches == []


@pytest.mark.parametrize(
    "patch",
    [
        {"memory_profile_version": "unknown"},
        {"memory_profile_id": "foreign.profile"},
        {"memory_feature_schema_version": "pingan.soc.memory_features.unknown"},
    ],
)
def test_unknown_query_identity_is_not_a_known_compatibility_family(patch):
    service, _repository, _record, query, _registry = _fixture()
    query = query.model_copy(update={"metadata": {**query.metadata, **patch}})
    assert service.find_relevant_records(query).matches == []


def test_unknown_saved_profile_identity_is_not_reinterpreted():
    service, _repository, _record, query, _registry = _fixture(spec_changes={"profile_version": "unknown"})
    assert service.find_relevant_records(query).matches == []


def test_cross_tenant_query_never_sees_reference_compatibility_record():
    service, _repository, _record, query, _registry = _fixture()
    query = query.model_copy(update={"tenant_scope": "foreign", "tenant_id": "foreign"})
    assert service.find_relevant_records(query).matches == []


@pytest.mark.parametrize("guard", ["required_service", "excluded_service", "reuse_service", "excluded_fingerprint", "optional_threshold", "required_environment", "required_detector"])
def test_compatibility_never_drops_reviewed_conditions(guard):
    facets = _profile().project_query_facets(_request())
    required = {key: facets[key] for key in ("detection_key", "detection_signature", "environment")}
    if guard == "required_service":
        required["network_service"] = ["tcp/49152"]
        changes = {"required_facets": required, "optional_facets": {}}
    elif guard == "excluded_service":
        changes = {"optional_facets": {}, "excluded_facets": {"network_service": ["tcp/50001"]}}
    elif guard == "reuse_service":
        changes = {"policy_version": "soc.memory_applicability_policy.v2", "reuse_conditions": [{"facet_key": "network_service", "values": ["tcp/49152"]}]}
    elif guard == "excluded_fingerprint":
        changes = {"excluded_facets": {"behavior_fingerprint": facets["behavior_fingerprint"]}}
    elif guard == "optional_threshold":
        changes = {"minimum_optional_matches": 1}
    else:
        required["environment" if guard == "required_environment" else "detection_key"] = ["another-scope"]
        changes = {"required_facets": required}
    service, _repository, _record, query, _registry = _fixture(spec_changes=changes)
    assert service.find_relevant_records(query).matches == []


@pytest.mark.parametrize("kind", ["vulnerability", "behavior_family"])
def test_non_port_semantic_conflicts_still_reject_reference(kind):
    changes = {"facet_changes": {"vulnerability_id": ["cve-2026-1234"]}} if kind == "vulnerability" else {"source_category": "industrial_control_exploit"}
    service, _repository, _record, query, _registry = _fixture(**changes)
    assert service.find_relevant_records(query).matches == []


@pytest.mark.parametrize("directive", [False, True])
def test_decision_bearing_records_never_enter_reference_compatibility(directive):
    service, _repository, _record, query, _registry = _fixture(impact=SocMemoryDecisionImpact.DETECTION_DECISION, directive=directive)
    assert service.find_relevant_records(query).matches == []
    assert service.find_directive_records(query).matches == []


def test_match_test_uses_same_tenant_policy_as_runtime_retrieval():
    service, repository, record, _query, registry = _fixture()
    run = _run(
        82,
        techniques=[],
        detection_key="pingan:ndr:reviewed-network",
        rule_name="Reviewed network detector",
        category="network_anomaly",
        canonical_entities=AlertEntitySet(network=NetworkEntityRef(protocol="tcp", src_port=80, dst_port=50001, direction="to_client")),
    )
    request = run.llm_analysis_request
    profile = _profile("9")
    request = request.model_copy(update={"memory_profile": {"profile_id": profile.identity.profile_id, "profile_version": profile.identity.profile_version, "feature_schema_version": profile.identity.feature_schema_version}})
    run = run.model_copy(update={"llm_analysis_request": request})
    repository.save_run(run)
    before = record.model_dump_json()
    result = service.test_record_match(SocMemoryRecordMatchTestCommand(memory_id=record.memory_id, run_id=run.run_id))
    assert result.matched is True
    assert result.match.applicability_report.status.value == "partial"
    enriched = ConfirmedMemoryAnalysisRequestEnricher(service, profile_registry=registry)(request)
    contexts = [item for item in enriched.context_catalog if item.kind.value == "confirmed_memory"]
    assert len(contexts) == 1
    assert contexts[0].memory_comparison.use_mode.value == "context_only"
    assert repository.get_memory_record(record.memory_id).model_dump_json() == before
