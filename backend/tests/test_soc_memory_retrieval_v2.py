from __future__ import annotations

from datetime import UTC, datetime, timedelta

from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.contracts import (
    ActorContext,
    AlertClassification,
    AlertEntitySet,
    AlertSourceRef,
    AlertSourceType,
    DetectionRuleRef,
    FactReconstructionResult,
    LLMAnalysisRequest,
    NetworkEntityRef,
    ProcessEntityRef,
    RoleResolution,
    RoleResolutionStatus,
    ScenarioHypothesis,
    SocMemoryApplicabilitySpec,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryRecord,
    SocMemoryTargetArtifact,
)
from soc_agent.core.service import SocMemoryService
from soc_agent.memory import (
    MEMORY_RETRIEVAL_POLICY_V1,
    MEMORY_RETRIEVAL_POLICY_V2,
    InMemoryMemoryCandidateRepository,
    build_memory_retrieval_diff,
    memory_query_from_analysis_request,
)
from soc_agent.utils.hashing import stable_hash


def _request() -> LLMAnalysisRequest:
    return LLMAnalysisRequest(
        alert_id="ALT-RETRIEVAL-V2",
        tenant_id="pingan",
        environment="prd",
        source=AlertSourceRef(
            source_type=AlertSourceType.NIDS,
            source_system="sample-ndr",
            product="network-sensor",
        ),
        detection=DetectionRuleRef(
            rule_name="Reverse connection behavior",
            rule_category="command_and_control",
        ),
        classification=AlertClassification(
            severity="high",
            category="command_and_control",
            technique=["T1059"],
        ),
        canonical_entities=AlertEntitySet(
            network=NetworkEntityRef(protocol="tcp"),
            process=ProcessEntityRef(process_name="bash"),
        ),
        fact_reconstruction=FactReconstructionResult(
            scenario_hypotheses=[
                ScenarioHypothesis(
                    scenario_type="reverse_shell",
                    status="confirmed",
                    confidence=0.86,
                    rationale="Observed reverse-connection behavior.",
                )
            ],
            role_resolutions=[
                RoleResolution(
                    role="attacker",
                    status=RoleResolutionStatus.CONFIRMED,
                    selected_value="30.174.29.44",
                    semantic_confidence=0.9,
                    rationale="Confirmed from reviewed role evidence.",
                )
            ],
        ),
    )


def _record(
    memory_id: str,
    *,
    facets: dict[str, list[str]],
) -> SocMemoryRecord:
    now = datetime.now(UTC)
    return SocMemoryRecord(
        memory_id=memory_id,
        memory_type=SocMemoryCandidateType.DETECTION_LESSON,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        tenant_scope="pingan",
        tenant_id="pingan",
        source_candidate_id=f"MC-{memory_id}",
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.CORRECTION,
            source_id=f"COR-{memory_id}",
        ),
        summary=f"Reviewed lesson {memory_id}",
        content="A reviewed reusable detection lesson.",
        facets=facets,
        evidence_refs=[f"correction:{memory_id}"],
        validity=SocMemoryCandidateValidity(
            valid_from=now - timedelta(days=1),
            notes="Reviewed retrieval test fixture.",
        ),
        confidence=0.8,
        content_hash=stable_hash({"memory_id": memory_id, "kind": "content"}),
        facets_hash=stable_hash(facets),
        retrieval_enabled=True,
        retrieval_policy_version="soc.memory_retrieval_activation_policy.v1",
        retrieval_valid_until=now + timedelta(days=30),
        retrieval_review_due_at=now + timedelta(days=7),
        retrieval_updated_by=ActorContext(actor_id="memory-governor"),
        retrieval_updated_at=now,
        retrieval_reason="Approved for bounded Runtime retrieval.",
        created_by=ActorContext(actor_id="memory-reviewer"),
    )


def test_runtime_query_v2_uses_optional_vendor_neutral_anchors() -> None:
    query = memory_query_from_analysis_request(_request())

    assert query.policy_version == MEMORY_RETRIEVAL_POLICY_V2
    assert "rule_code" not in query.facets
    assert "detection_key" not in query.facets
    assert query.facets["environment"] == ["prd"]
    assert query.facets["scenario_key"] == ["reverse_shell"]
    assert query.facets["role_entity"] == ["attacker:30.174.29.44"]
    assert query.facets["behavior_component"] == [
        "process:bash",
        "protocol:tcp",
        "scenario:reverse_shell",
        "technique:t1059",
    ]
    assert len(query.facets["behavior_fingerprint"]) == 1


def test_v2_removes_broad_same_source_matches_but_keeps_ruleless_behavior_match() -> None:
    request = _request()
    v2_query = memory_query_from_analysis_request(request)
    fingerprint = v2_query.facets["behavior_fingerprint"][0]
    repository = InMemoryMemoryCandidateRepository()
    for record in (
        _record(
            "MEM-EXACT-BEHAVIOR",
            facets={
                "source_type": ["nids"],
                "behavior_fingerprint": [fingerprint],
                "environment": ["prd"],
            },
        ),
        _record(
            "MEM-BROAD-SOURCE",
            facets={"source_type": ["nids"], "environment": ["prd"]},
        ),
        _record(
            "MEM-OTHER-SCENARIO",
            facets={"source_type": ["nids"], "scenario_key": ["lateral_movement"]},
        ),
    ):
        repository.save_memory_record(record)
    service = SocMemoryService(
        record_repository=repository,
        now_provider=lambda: datetime.now(UTC),
    )

    baseline = service.find_relevant_records(
        memory_query_from_analysis_request(
            request,
            policy_version=MEMORY_RETRIEVAL_POLICY_V1,
        ).model_copy(update={"limit": 10, "max_tokens": 5000})
    )
    current = service.find_relevant_records(v2_query.model_copy(update={"limit": 10, "max_tokens": 5000}))

    assert {match.memory_id for match in baseline.matches} == {
        "MEM-BROAD-SOURCE",
        "MEM-EXACT-BEHAVIOR",
        "MEM-OTHER-SCENARIO",
    }
    assert [match.memory_id for match in current.matches] == ["MEM-EXACT-BEHAVIOR"]
    assert current.matches[0].matched_anchor_facets == {"behavior_fingerprint": [fingerprint]}
    assert current.skipped_missing_strong_anchor == 2
    diff = build_memory_retrieval_diff(baseline, current)
    assert diff.removed_memory_ids == [
        "MEM-BROAD-SOURCE",
        "MEM-OTHER-SCENARIO",
    ]


def test_typed_pingan_applicability_requires_exact_profile_and_scope() -> None:
    query = memory_query_from_analysis_request(_request())
    fingerprint = query.facets["behavior_fingerprint"][0]
    applicability = SocMemoryApplicabilitySpec(
        profile_id="pingan.soc",
        profile_version="1",
        feature_schema_version="pingan.soc.memory_features.v1",
        required_facets={"behavior_fingerprint": [fingerprint]},
        optional_facets={"environment": ["prd"]},
        excluded_facets={"source_system": ["untrusted-shadow-source"]},
    )
    record = _record(
        "MEM-PINGAN-TYPED",
        facets={
            "behavior_fingerprint": [fingerprint],
            "environment": ["prd"],
        },
    ).model_copy(update={"applicability": applicability})
    repository = InMemoryMemoryCandidateRepository()
    repository.save_memory_record(record)
    service = SocMemoryService(record_repository=repository)

    pingan_query = query.model_copy(
        update={
            "metadata": {
                **query.metadata,
                "memory_profile_id": "pingan.soc",
                "memory_profile_version": "1",
                "memory_feature_schema_version": "pingan.soc.memory_features.v1",
            }
        }
    )
    applicable = service.find_relevant_records(pingan_query)
    wrong_profile = service.find_relevant_records(query)

    assert [item.memory_id for item in applicable.matches] == ["MEM-PINGAN-TYPED"]
    assert applicable.matches[0].applicability_report is not None
    assert applicable.matches[0].applicability_report.status.value == "applicable"
    assert wrong_profile.matches == []
    assert wrong_profile.skipped_not_applicable == 1


def test_pingan_profile_filters_legacy_same_rule_cross_behavior_memory() -> None:
    base = _request()
    request = base.model_copy(
        update={
            "environment": "dev-corpus-eval",
            "source": base.source.model_copy(
                update={
                    "integration_name": "pingan_legacy_alert_platform",
                }
            ),
            "detection": base.detection.model_copy(
                update={
                    "detection_key": "sec_guard_apt:rule_code:rpaadm_000558",
                    "rule_code": "RPAADM_000558",
                    "rule_name": "红队IP监控",
                }
            ),
            "classification": base.classification.model_copy(update={"category": "web_attack", "technique": ["T1190"]}),
            "canonical_entities": AlertEntitySet(
                network=NetworkEntityRef(
                    protocol="http",
                    dst_port=80,
                )
            ),
        }
    )
    registry = build_soc_memory_profile_registry()
    profile = registry.resolve_request(request)
    query = memory_query_from_analysis_request(request, profile=profile)
    weak_applicability = SocMemoryApplicabilitySpec(
        profile_id=profile.identity.profile_id,
        profile_version=profile.identity.profile_version,
        feature_schema_version=profile.identity.feature_schema_version,
        required_facets={
            "detection_key": ["sec_guard_apt:rule_code:rpaadm_000558"],
            "environment": ["dev-corpus-eval"],
        },
        optional_facets={
            "network_service": ["udp/44818"],
            "vulnerability_id": ["CVE-2017-7924"],
            "attack_behavior_family": [
                "denial_of_service",
                "vulnerability_exploitation",
            ],
        },
    )
    record = _record(
        "MEM-LEGACY-PLC-SAME-RULE",
        facets={
            "detection_key": ["sec_guard_apt:rule_code:rpaadm_000558"],
            "environment": ["dev-corpus-eval"],
            "network_service": ["udp/44818"],
            "vulnerability_id": ["CVE-2017-7924"],
            "attack_behavior_family": [
                "denial_of_service",
                "vulnerability_exploitation",
            ],
        },
    ).model_copy(update={"applicability": weak_applicability})
    repository = InMemoryMemoryCandidateRepository()
    repository.save_memory_record(record)

    result = SocMemoryService(
        record_repository=repository,
        profile_registry=registry,
    ).find_relevant_records(query)

    assert result.matches == []
    assert result.skipped_not_applicable == 1


def test_pingan_profile_filters_component_only_legacy_network_scope() -> None:
    base = _request()
    request = base.model_copy(
        update={
            "environment": "dev-corpus-eval",
            "source": base.source.model_copy(update={"integration_name": "pingan_legacy_alert_platform"}),
            "detection": base.detection.model_copy(
                update={
                    "detection_key": "sec_guard_apt:rule_code:rpaadm_000558",
                    "rule_code": "RPAADM_000558",
                    "rule_name": "红队IP监控",
                }
            ),
            "classification": base.classification.model_copy(update={"category": "web_attack", "technique": ["T1190"]}),
            "canonical_entities": AlertEntitySet(network=NetworkEntityRef(protocol="http", dst_port=80)),
        }
    )
    registry = build_soc_memory_profile_registry()
    profile = registry.resolve_request(request)
    query = memory_query_from_analysis_request(request, profile=profile)
    detection_signature = query.facets["detection_signature"]
    record_fingerprint = ["openvpn-behavior-fingerprint"]
    applicability = SocMemoryApplicabilitySpec(
        profile_id=profile.identity.profile_id,
        profile_version=profile.identity.profile_version,
        feature_schema_version=profile.identity.feature_schema_version,
        required_facets={
            "detection_key": ["sec_guard_apt:rule_code:rpaadm_000558"],
            "detection_signature": detection_signature,
            "behavior_fingerprint": record_fingerprint,
            "behavior_strength": ["strong"],
            "environment": ["dev-corpus-eval"],
        },
        optional_facets={
            "behavior_component": [
                "attack_family:proxy_tunnel_activity",
                "network_service:udp/1194",
                "protocol:udp",
                "technique:t1190",
            ],
            "behavior_component_strong": ["technique:t1190"],
        },
        context_only_required_facet_keys=[
            "behavior_strength",
            "detection_key",
            "detection_signature",
            "environment",
        ],
        context_only_missing_facet_keys=["behavior_fingerprint"],
        context_only_similarity_facet_keys=["behavior_component_strong"],
    )
    record = _record(
        "MEM-LEGACY-OPENVPN-COMPONENTS",
        facets={
            "detection_key": ["sec_guard_apt:rule_code:rpaadm_000558"],
            "detection_signature": detection_signature,
            "behavior_fingerprint": record_fingerprint,
            "behavior_strength": ["strong"],
            "environment": ["dev-corpus-eval"],
            "behavior_component": applicability.optional_facets["behavior_component"],
            "behavior_component_strong": ["technique:t1190"],
        },
    ).model_copy(update={"applicability": applicability})
    repository = InMemoryMemoryCandidateRepository()
    repository.save_memory_record(record)

    result = SocMemoryService(
        record_repository=repository,
        profile_registry=registry,
    ).find_relevant_records(query)

    assert result.matches == []
    assert result.skipped_not_applicable == 1
