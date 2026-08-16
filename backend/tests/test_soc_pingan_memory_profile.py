from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AlertClassification,
    AlertSourceRef,
    AlertSourceType,
    AnalysisReasoningBasis,
    AnalysisReasoningItem,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunStatus,
    DetectionRuleRef,
    EntrySurface,
    EvidenceItem,
    LLMAnalysisRequest,
    MemoryPatternAggregationPolicy,
    MemoryPatternDataClass,
    MemoryPatternDimension,
    MemoryPatternSourceType,
    ServiceRequestContext,
    SocMemoryApplicabilitySpec,
    SocMemoryCandidateReviewCommand,
    SocMemoryCandidateReviewDecision,
    SocMemoryDecisionImpact,
    Verdict,
)
from soc_agent.core import SocMemoryPatternService, SocMemoryService, SocServiceError
from soc_agent.memory import (
    InMemoryMemoryPatternRepository,
    MemoryPatternIneligibleError,
    memory_query_from_analysis_request,
)

_START = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)


def _context() -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id="pingan-memory-test",
            actor_type=ActorType.SERVICE,
            surface=EntrySurface.TEST,
            roles=["soc_batch_runner"],
        )
    )


def _run(
    index: int,
    *,
    detection_key: str | None = "pingan:ndr:reverse-shell",
    rule_name: str | None = "Reverse shell detector",
    techniques: list[str] | None = None,
    source_event_id: str | None = None,
    verdict: Verdict = Verdict.FALSE_POSITIVE,
) -> AnalysisRun:
    evidence_ref = "E-000000000001"
    analysis = AnalysisResult(
        verdict=verdict,
        confidence=0.86,
        summary="Reviewed recurring reverse-connection alert.",
        evidence=[
            EvidenceItem(
                evidence_ref=evidence_ref,
                source="canonical",
                description="Reviewed detector hit",
                value=detection_key or "network_anomaly",
            )
        ],
        reasoning=[
            AnalysisReasoningItem(
                reasoning_id="R-01",
                statement="The reviewed event belongs to the same stable detector class.",
                basis=[AnalysisReasoningBasis.CURRENT_EVIDENCE],
                evidence_refs=[evidence_ref],
                confidence=0.86,
            )
        ],
        reason=("Analysts confirmed this recurring class as expected internal activity." if verdict is Verdict.FALSE_POSITIVE else "Analysts confirmed this recurring class as a real security risk."),
        recommended_action=("ignore" if verdict is Verdict.FALSE_POSITIVE else "contain"),
    )
    alert_id = f"PA-ALERT-{index:03d}"
    return AnalysisRun(
        run_id=f"PA-RUN-{index:03d}",
        alert_id=alert_id,
        status=AnalysisRunStatus.SUCCESS,
        input_payload={
            "alert_id": source_event_id or alert_id,
            "event_time": (_START + timedelta(minutes=index)).isoformat(),
        },
        input_hash=(f"event:{source_event_id}" if source_event_id is not None else f"{index:064x}"),
        started_at=_START + timedelta(minutes=index),
        llm_analysis_request=LLMAnalysisRequest(
            alert_id=alert_id,
            tenant_id="pingan",
            environment="prd",
            source=AlertSourceRef(
                source_type=AlertSourceType.NIDS,
                source_system="zeus",
                vendor="pingan",
                product="ndr",
                integration_name="pingan_legacy_alert_platform",
            ),
            detection=DetectionRuleRef(
                detection_key=detection_key,
                rule_name=rule_name,
            ),
            classification=AlertClassification(
                category="command_and_control",
                severity="high",
                technique=(["T1059", "T1071"] if techniques is None else techniques),
            ),
        ),
        analysis=analysis,
    )


def _service(repository: InMemoryMemoryPatternRepository) -> SocMemoryPatternService:
    return SocMemoryPatternService(
        repository=repository,
        candidate_repository=repository,
        policy=MemoryPatternAggregationPolicy(
            minimum_support=2,
            minimum_distinct_sources=2,
            minimum_conclusive_support=2,
        ),
        profile_registry=build_soc_memory_profile_registry(),
    )


def _observe(service: SocMemoryPatternService, run: AnalysisRun, ref: str):
    return service.observe_run(
        run,
        source_type=MemoryPatternSourceType.BATCH_ALERT,
        transport_ref=ref,
        environment="prd",
        data_class=MemoryPatternDataClass.OPERATIONAL,
        context=_context(),
    )


def test_pingan_profile_creates_one_typed_same_class_candidate() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)

    _observe(service, _run(1), "batch:1")
    result = _observe(service, _run(2), "batch:2")

    assert result.candidate is not None
    assert result.observation.profile_id == "pingan.soc"
    assert result.observation.signature.dimension is MemoryPatternDimension.COMPOUND
    assert result.candidate.applicability is not None
    assert result.candidate.applicability.profile_id == "pingan.soc"
    assert set(result.candidate.applicability.required_facets) == {
        "behavior_fingerprint",
        "detection_key",
        "environment",
    }
    assert result.candidate.decision_impact is SocMemoryDecisionImpact.DETECTION_DECISION
    assert "经验结论" in result.candidate.content
    assert len(repository.list_memory_candidates()) == 1


def test_pingan_profile_rejects_category_only_cohorts() -> None:
    with pytest.raises(
        MemoryPatternIneligibleError,
        match="canonical detection key or behavior fingerprint",
    ):
        _observe(
            _service(InMemoryMemoryPatternRepository()),
            _run(1, detection_key=None, techniques=[]),
            "batch:1",
        )


def test_pingan_profile_uses_deterministic_behavior_when_rule_identity_is_absent() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)
    first = _run(
        1,
        detection_key=None,
        rule_name=None,
        techniques=["T1059", "T1071"],
    )
    second = _run(
        2,
        detection_key=None,
        rule_name=None,
        techniques=["T1059", "T1071"],
    )

    first_result = _observe(service, first, "batch:1")
    second_result = _observe(service, second, "batch:2")

    assert first_result.observation.signature.dimension is MemoryPatternDimension.BEHAVIOR
    assert second_result.candidate is not None
    assert second_result.candidate.applicability is not None
    assert set(second_result.candidate.applicability.required_facets) == {
        "behavior_fingerprint",
        "environment",
    }
    assert second_result.candidate.decision_impact is SocMemoryDecisionImpact.DETECTION_DECISION


def test_pingan_profile_deduplicates_one_upstream_occurrence() -> None:
    service = _service(InMemoryMemoryPatternRepository())
    first = _observe(service, _run(1, source_event_id="ZEUS-EVENT-001"), "batch:1")
    duplicate = _observe(service, _run(2, source_event_id="ZEUS-EVENT-001"), "batch:2")

    assert duplicate.duplicate_occurrence is True
    assert duplicate.observation.observation_id == first.observation.observation_id
    assert duplicate.support_count == 1
    assert duplicate.candidate is None


def test_pingan_profile_is_server_selected_for_runtime_query() -> None:
    request = _run(1).llm_analysis_request
    assert request is not None
    registry = build_soc_memory_profile_registry()
    query = memory_query_from_analysis_request(
        request,
        profile=registry.resolve_request(request),
    )

    assert query.metadata["memory_profile_id"] == "pingan.soc"
    assert query.metadata["memory_feature_schema_version"] == ("pingan.soc.memory_features.v2")
    assert query.facets["detection_key"] == ["pingan:ndr:reverse-shell"]


def test_reviewer_can_confirm_activate_and_authorize_exact_future_matches() -> None:
    repository = InMemoryMemoryPatternRepository()
    pattern_service = _service(repository)
    _observe(pattern_service, _run(1), "batch:1")
    aggregated = _observe(pattern_service, _run(2), "batch:2")
    assert aggregated.candidate is not None

    reviewed_at = _START + timedelta(hours=1)
    result = SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
        mutation_audit_repository=repository,
        now_provider=lambda: reviewed_at,
    ).review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=aggregated.candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="Reviewed cohort is reusable for the exact PingAn detector class.",
            confirmed_verdict=Verdict.FALSE_POSITIVE,
            apply_to_future_matches=True,
            clear_review_on_match=True,
            activate_retrieval=True,
            activation_valid_until=reviewed_at + timedelta(days=30),
            activation_review_after_days=7,
        ),
        context=ServiceRequestContext(
            idempotency_key="confirm-pingan-memory-001",
            actor=ActorContext(
                actor_id="memory-reviewer",
                actor_type=ActorType.USER,
                surface=EntrySurface.TEST,
                roles=["soc_memory_reviewer"],
            ),
        ),
    )

    assert result.memory_record is not None
    assert result.memory_record.retrieval_enabled is True
    assert result.memory_record.decision_directive is not None
    assert result.memory_record.decision_directive.target_verdict is Verdict.FALSE_POSITIVE
    assert result.memory_record.applicability is not None
    assert result.memory_record.applicability.profile_id == "pingan.soc"


def test_pingan_profile_requires_the_reviewed_environment_for_retrieval() -> None:
    repository = InMemoryMemoryPatternRepository()
    pattern_service = _service(repository)
    _observe(pattern_service, _run(1), "batch:1")
    aggregated = _observe(pattern_service, _run(2), "batch:2")
    assert aggregated.candidate is not None

    reviewed_at = _START + timedelta(hours=1)
    record = (
        SocMemoryService(
            candidate_repository=repository,
            record_repository=repository,
            mutation_audit_repository=repository,
            now_provider=lambda: reviewed_at,
        )
        .review_candidate(
            SocMemoryCandidateReviewCommand(
                candidate_id=aggregated.candidate.candidate_id,
                decision=SocMemoryCandidateReviewDecision.CONFIRM,
                reason="The exact detector class is reusable only in the reviewed environment.",
                confirmed_verdict=Verdict.FALSE_POSITIVE,
                apply_to_future_matches=True,
                activate_retrieval=True,
                activation_valid_until=reviewed_at + timedelta(days=30),
                activation_review_after_days=7,
            ),
            context=ServiceRequestContext(
                idempotency_key="confirm-pingan-memory-environment",
                actor=ActorContext(
                    actor_id="memory-reviewer",
                    actor_type=ActorType.USER,
                    surface=EntrySurface.TEST,
                    roles=["soc_memory_reviewer"],
                ),
            ),
        )
        .memory_record
    )
    assert record is not None

    registry = build_soc_memory_profile_registry()
    prd_request = _run(3).llm_analysis_request
    assert prd_request is not None
    prd_query = memory_query_from_analysis_request(
        prd_request,
        profile=registry.resolve_request(prd_request),
    )
    stg_request = prd_request.model_copy(update={"environment": "stg"})
    stg_query = memory_query_from_analysis_request(
        stg_request,
        profile=registry.resolve_request(stg_request),
    )
    memory_service = SocMemoryService(
        record_repository=repository,
        now_provider=lambda: reviewed_at,
    )

    assert [item.memory_id for item in memory_service.find_relevant_records(prd_query).matches] == [record.memory_id]
    stg_result = memory_service.find_relevant_records(stg_query)
    assert stg_result.matches == []
    assert stg_result.skipped_not_applicable == 1


def test_pingan_detection_only_candidate_is_rule_context_not_decision_authority() -> None:
    repository = InMemoryMemoryPatternRepository()
    pattern_service = _service(repository)
    _observe(pattern_service, _run(1, techniques=[]), "batch:1")
    aggregated = _observe(pattern_service, _run(2, techniques=[]), "batch:2")

    assert aggregated.candidate is not None
    assert aggregated.observation.signature.dimension is MemoryPatternDimension.DETECTION
    assert aggregated.candidate.decision_impact is SocMemoryDecisionImpact.REVIEW_HINT
    assert aggregated.candidate.metadata["decision_scope"] == "rule_context_only"

    with pytest.raises(
        SocServiceError,
        match="behavior-scoped decision-eligible candidate",
    ):
        SocMemoryService(
            candidate_repository=repository,
            record_repository=repository,
        ).review_candidate(
            SocMemoryCandidateReviewCommand(
                candidate_id=aggregated.candidate.candidate_id,
                decision=SocMemoryCandidateReviewDecision.CONFIRM,
                reason="Same detector is useful background but not a universal verdict.",
                confirmed_verdict=Verdict.FALSE_POSITIVE,
                apply_to_future_matches=True,
            ),
            context=ServiceRequestContext(
                actor=ActorContext(
                    actor_id="memory-reviewer",
                    actor_type=ActorType.USER,
                    surface=EntrySurface.TEST,
                    roles=["soc_memory_reviewer"],
                )
            ),
        )


def test_reviewer_can_narrow_pattern_decision_scope_with_candidate_facets() -> None:
    repository = InMemoryMemoryPatternRepository()
    pattern_service = _service(repository)
    _observe(pattern_service, _run(1), "batch:1")
    aggregated = _observe(pattern_service, _run(2), "batch:2")
    candidate = aggregated.candidate
    assert candidate is not None
    base = candidate.applicability
    assert base is not None

    required = {**base.required_facets, "source_type": ["nids"]}
    optional = {key: values for key, values in base.optional_facets.items() if key != "source_type"}
    narrowed = SocMemoryApplicabilitySpec.model_validate(
        {
            **base.model_dump(mode="json"),
            "required_facets": required,
            "optional_facets": optional,
            "context_only_required_facet_keys": [
                *base.context_only_required_facet_keys,
                "source_type",
            ],
        }
    )
    reviewed = SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
    ).review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="Only the reviewed NIDS pattern owns this future verdict.",
            record_applicability=narrowed,
            confirmed_verdict=Verdict.FALSE_POSITIVE,
            apply_to_future_matches=True,
        ),
        context=ServiceRequestContext(
            actor=ActorContext(
                actor_id="memory-reviewer",
                actor_type=ActorType.USER,
                surface=EntrySurface.TEST,
                roles=["soc_memory_reviewer"],
            )
        ),
    )

    assert reviewed.memory_record is not None
    assert reviewed.memory_record.applicability == narrowed
    assert reviewed.memory_record.decision_directive is not None
    assert set(reviewed.memory_record.decision_directive.required_facet_keys) == {
        "behavior_fingerprint",
        "detection_key",
        "environment",
        "source_type",
    }


def test_reviewer_cannot_remove_the_compound_behavior_anchor() -> None:
    repository = InMemoryMemoryPatternRepository()
    pattern_service = _service(repository)
    _observe(pattern_service, _run(1), "batch:1")
    aggregated = _observe(pattern_service, _run(2), "batch:2")
    candidate = aggregated.candidate
    assert candidate is not None
    base = candidate.applicability
    assert base is not None
    widened = SocMemoryApplicabilitySpec.model_validate(
        {
            **base.model_dump(mode="json"),
            "required_facets": {key: values for key, values in base.required_facets.items() if key != "behavior_fingerprint"},
            "context_only_required_facet_keys": [],
            "context_only_missing_facet_keys": [],
            "context_only_similarity_facet_keys": [],
        }
    )

    with pytest.raises(
        SocServiceError,
        match="cannot remove candidate required facets: behavior_fingerprint",
    ):
        SocMemoryService(
            candidate_repository=repository,
            record_repository=repository,
        ).review_candidate(
            SocMemoryCandidateReviewCommand(
                candidate_id=candidate.candidate_id,
                decision=SocMemoryCandidateReviewDecision.CONFIRM,
                reason="Attempted broad rule-only scope must fail closed.",
                record_applicability=widened,
            ),
            context=ServiceRequestContext(
                actor=ActorContext(
                    actor_id="memory-reviewer",
                    actor_type=ActorType.USER,
                    surface=EntrySurface.TEST,
                    roles=["soc_memory_reviewer"],
                )
            ),
        )


def test_same_rule_different_behaviors_and_opposite_outcomes_form_separate_candidates() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)

    _observe(
        service,
        _run(1, techniques=["T1059", "T1071"], verdict=Verdict.FALSE_POSITIVE),
        "batch:1",
    )
    benign = _observe(
        service,
        _run(2, techniques=["T1059", "T1071"], verdict=Verdict.FALSE_POSITIVE),
        "batch:2",
    )
    _observe(
        service,
        _run(3, techniques=["T1059", "T1021"], verdict=Verdict.TRUE_POSITIVE),
        "batch:3",
    )
    risky = _observe(
        service,
        _run(4, techniques=["T1059", "T1021"], verdict=Verdict.TRUE_POSITIVE),
        "batch:4",
    )

    assert benign.candidate is not None
    assert risky.candidate is not None
    assert benign.observation.signature.value != risky.observation.signature.value
    assert benign.candidate.candidate_type.value == "benign_pattern"
    assert risky.candidate.candidate_type.value == "detection_lesson"
    assert len(repository.list_memory_candidates()) == 2


def test_same_rule_similar_behavior_is_context_only_until_exact_fingerprint_matches() -> None:
    repository = InMemoryMemoryPatternRepository()
    pattern_service = _service(repository)
    _observe(pattern_service, _run(1, techniques=["T1059", "T1071"]), "batch:1")
    aggregated = _observe(
        pattern_service,
        _run(2, techniques=["T1059", "T1071"]),
        "batch:2",
    )
    assert aggregated.candidate is not None

    reviewed_at = _START + timedelta(hours=1)
    record = (
        SocMemoryService(
            candidate_repository=repository,
            record_repository=repository,
            mutation_audit_repository=repository,
            now_provider=lambda: reviewed_at,
        )
        .review_candidate(
            SocMemoryCandidateReviewCommand(
                candidate_id=aggregated.candidate.candidate_id,
                decision=SocMemoryCandidateReviewDecision.CONFIRM,
                reason="Exact detector and behavior pair is a reviewed benign pattern.",
                confirmed_verdict=Verdict.FALSE_POSITIVE,
                apply_to_future_matches=True,
                activate_retrieval=True,
                activation_valid_until=reviewed_at + timedelta(days=30),
                activation_review_after_days=7,
            ),
            context=ServiceRequestContext(
                idempotency_key="confirm-pingan-memory-context-only",
                actor=ActorContext(
                    actor_id="memory-reviewer",
                    actor_type=ActorType.USER,
                    surface=EntrySurface.TEST,
                    roles=["soc_memory_reviewer"],
                ),
            ),
        )
        .memory_record
    )
    assert record is not None

    registry = build_soc_memory_profile_registry()
    exact_request = _run(3, techniques=["T1059", "T1071"]).llm_analysis_request
    similar_request = _run(4, techniques=["T1059", "T1021"]).llm_analysis_request
    assert exact_request is not None
    assert similar_request is not None
    exact_query = memory_query_from_analysis_request(
        exact_request,
        profile=registry.resolve_request(exact_request),
    )
    similar_query = memory_query_from_analysis_request(
        similar_request,
        profile=registry.resolve_request(similar_request),
    )
    memory_service = SocMemoryService(
        record_repository=repository,
        now_provider=lambda: reviewed_at,
    )

    exact = memory_service.find_relevant_records(exact_query)
    similar = memory_service.find_relevant_records(similar_query)

    assert exact.matches[0].applicability_report is not None
    assert exact.matches[0].applicability_report.status.value == "applicable"
    assert exact.returned_context_only_count == 0
    assert similar.matches[0].applicability_report is not None
    assert similar.matches[0].applicability_report.status.value == "partial"
    assert similar.matches[0].applicability_report.context_only_allowed is True
    assert similar.returned_context_only_count == 1
