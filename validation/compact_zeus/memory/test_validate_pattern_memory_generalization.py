from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from soc_agent.contracts import (
    ActorContext,
    AlertEntitySet,
    FactReconstructionResult,
    NetworkEntityRef,
    RoleResolution,
    RoleResolutionStatus,
    ScenarioHypothesis,
    SocMemoryApplicabilitySpec,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryDecisionDirective,
    SocMemoryDecisionEffect,
    SocMemoryDecisionImpact,
    SocMemoryRecord,
    SocMemoryReviewEffect,
    SocMemoryTargetArtifact,
    Verdict,
)
from soc_agent.application import build_soc_memory_profile_registry
from soc_agent.utils.hashing import stable_hash
from validation.compact_zeus.memory.test_simulate_pattern_memory_lifecycle import (
    _base_run,
)
from validation.compact_zeus.memory.validate_pattern_memory_generalization import (
    validate_pattern_memory_generalization,
)

NOW = datetime(2026, 8, 16, 1, 0, tzinfo=UTC)


def _network_run():
    run = _base_run()
    request = run.llm_analysis_request
    assert request is not None
    request = request.model_copy(
        update={
            "canonical_entities": AlertEntitySet(
                network=NetworkEntityRef(
                    source_ip="30.116.114.150",
                    destination_ip="30.174.29.44",
                    protocol="tcp",
                )
            ),
            "fact_reconstruction": FactReconstructionResult(
                scenario_hypotheses=[
                    ScenarioHypothesis(
                        scenario_type="reverse_connection",
                        status="confirmed",
                        confidence=0.9,
                        rationale="Controlled reverse-connection fixture.",
                    )
                ],
                role_resolutions=[
                    RoleResolution(
                        role="source",
                        status=RoleResolutionStatus.OBSERVED,
                        selected_value="30.116.114.150",
                        semantic_confidence=0.9,
                        rationale="Observed source endpoint.",
                    ),
                    RoleResolution(
                        role="destination",
                        status=RoleResolutionStatus.OBSERVED,
                        selected_value="30.174.29.44",
                        semantic_confidence=0.9,
                        rationale="Observed destination endpoint.",
                    ),
                ],
            ),
        },
        deep=True,
    )
    return run.model_copy(update={"llm_analysis_request": request}, deep=True)


def _memory() -> SocMemoryRecord:
    run = _network_run()
    request = run.llm_analysis_request
    assert request is not None
    profile = build_soc_memory_profile_registry().resolve_request(request)
    facets = profile.project_query_facets(request)
    required = {
        key: facets[key]
        for key in (
            "detection_key",
            "detection_signature",
            "behavior_fingerprint",
            "behavior_strength",
            "environment",
        )
    }
    applicability = SocMemoryApplicabilitySpec(
        profile_id="pingan.soc",
        profile_version="3",
        feature_schema_version="pingan.soc.memory_features.v3",
        required_facets=required,
        optional_facets={
            key: facets[key]
            for key in (
                "behavior_component",
                "behavior_component_strong",
                "behavior_component_weak",
                "role_entity",
                "entity",
            )
            if facets.get(key)
        },
        minimum_strong_anchor_matches=3,
        context_only_required_facet_keys=[
            "behavior_strength",
            "detection_key",
            "detection_signature",
            "environment",
        ],
        context_only_missing_facet_keys=["behavior_fingerprint"],
        context_only_similarity_facet_keys=["behavior_component_strong"],
    )
    content = "Reviewed reverse-shell pattern."
    return SocMemoryRecord(
        memory_id="MEM-CROSS-IP-TEST",
        memory_type=SocMemoryCandidateType.DETECTION_LESSON,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        decision_impact=SocMemoryDecisionImpact.DETECTION_DECISION,
        tenant_scope="pingan",
        tenant_id="pingan",
        source_candidate_id="MC-CROSS-IP-TEST",
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.MANUAL_NOTE,
            source_id="cross-ip-test",
        ),
        summary="Reviewed reverse-shell pattern",
        content=content,
        facets=facets,
        applicability=applicability,
        evidence_refs=["simulation:cross-ip"],
        validity=SocMemoryCandidateValidity(
            valid_from=NOW - timedelta(days=1),
            valid_until=NOW + timedelta(days=30),
            notes="Cross-IP test fixture.",
        ),
        confidence=0.9,
        content_hash=stable_hash(content),
        facets_hash=stable_hash(facets),
        retrieval_enabled=True,
        retrieval_policy_version="soc.memory_retrieval_activation_policy.v1",
        retrieval_valid_until=NOW + timedelta(days=30),
        retrieval_review_due_at=NOW + timedelta(days=7),
        retrieval_updated_at=NOW,
        retrieval_updated_by=ActorContext(actor_id="test-reviewer"),
        retrieval_reason="Approved for cross-IP regression validation.",
        decision_directive=SocMemoryDecisionDirective(
            effect=SocMemoryDecisionEffect.OVERRIDE,
            target_verdict=Verdict.SUSPICIOUS,
            review_effect=SocMemoryReviewEffect.CLEAR,
            required_facet_keys=sorted(required),
            rationale="Reviewed detection and behavior must match exactly.",
        ),
        created_by=ActorContext(actor_id="test-reviewer"),
        created_at=NOW,
        updated_at=NOW,
    )


def test_cross_ip_generalization_keeps_semantics_not_entity_identity(tmp_path) -> None:
    output_dir = tmp_path / "cross-ip"
    report = validate_pattern_memory_generalization(
        _network_run(),
        _memory(),
        output_dir=output_dir,
    )

    assert report["status"] == "passed"
    assert all(report["checks"].values())
    by_id = {item["case_id"]: item for item in report["cases"]}
    assert by_id["both_ips_changed"]["actual"] == "decision_applicable"
    assert not by_id["both_ips_changed"]["retrieval"]["matched_facets"].get(
        "role_entity"
    )
    assert by_id["same_ips_partial_behavior"]["actual"] == "context_only"
    assert (
        by_id["same_ips_partial_behavior"]["retrieval"]["decision_directive_applicable"]
        is False
    )
    for case_id in (
        "same_ips_different_behavior",
        "same_ips_different_rule",
        "same_ips_different_environment",
    ):
        assert by_id[case_id]["actual"] == "not_retrieved"

    saved = json.loads(
        (output_dir / "cross-ip-generalization.json").read_text(encoding="utf-8")
    )
    assert saved["status"] == "passed"
    assert len(saved["cases"]) == 8
