from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.contracts import (
    ActorContext,
    MemoryPatternAggregationPolicy,
    MemoryPatternDataClass,
    MemoryPatternDimension,
    MemoryPatternLessonObservation,
    MemoryPatternObservation,
    MemoryPatternRiskClass,
    MemoryPatternSignature,
    MemoryPatternSourceRef,
    MemoryPatternSourceType,
    SocMemoryApplicabilitySpec,
    SocMemoryCandidate,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateStatus,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryDecisionImpact,
    SocMemoryProfileState,
    SocMemoryRecord,
    SocMemoryTargetArtifact,
    Verdict,
)
from soc_agent.core import SocMemoryCenterService
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.memory import GenericSocMemoryProfile, InMemoryMemoryPatternRepository
from soc_agent.utils.hashing import stable_hash

_START = datetime(2026, 8, 18, 1, 0, tzinfo=UTC)
_AGGREGATION_KEY = "a" * 64
_LATER_AGGREGATION_KEY = "c" * 64
_LINEAGE_KEY = "b" * 64
_TERMINAL_AGGREGATION_KEY = "d" * 64
_TERMINAL_LINEAGE_KEY = "e" * 64
_PROFILE = GenericSocMemoryProfile().identity


def _observation(index: int) -> MemoryPatternObservation:
    later_window = index == 3
    observed_at = _START + (timedelta(days=1, minutes=index) if later_window else timedelta(minutes=index))
    window_start = _START.replace(hour=0) + timedelta(days=1) if later_window else _START.replace(hour=0)
    return MemoryPatternObservation(
        idempotency_key=f"memory-center:{index}",
        aggregation_key=(_LATER_AGGREGATION_KEY if later_window else _AGGREGATION_KEY),
        lineage_key=_LINEAGE_KEY,
        content_hash=f"{index + 10:064x}",
        tenant_id="pingan",
        environment="dev",
        data_class=MemoryPatternDataClass.SIMULATION,
        profile_id=_PROFILE.profile_id,
        profile_version=_PROFILE.profile_version,
        feature_schema_version=_PROFILE.feature_schema_version,
        occurrence_key=f"{index + 20:064x}",
        source=MemoryPatternSourceRef(
            source_type=MemoryPatternSourceType.BATCH_ALERT,
            source_id=f"alert:{index}",
            transport_ref=f"batch:{index}",
            run_id=f"RUN-{index}",
            alert_id=f"ALERT-{index}",
            observed_at=observed_at,
        ),
        signature=MemoryPatternSignature(
            dimension=MemoryPatternDimension.DETECTION,
            value="generic:detector:test",
            label="Repeated test detector",
            origin="canonical_detection",
            facets={"detection_key": ["generic:detector:test"]},
        ),
        lesson=MemoryPatternLessonObservation(
            verdict=Verdict.FALSE_POSITIVE,
            risk_class=MemoryPatternRiskClass.BENIGN,
            needs_review=False,
            summary="Known benign repeated behavior.",
            reason="The reviewed detector pattern is expected in this scope.",
            recommended_action="ignore",
        ),
        window_start=window_start,
        window_end=window_start + timedelta(days=1),
        aggregation_policy=MemoryPatternAggregationPolicy(),
        evidence_refs=[f"alert:{index}"],
        mocked=True,
    )


def _candidate(observations: list[MemoryPatternObservation]) -> SocMemoryCandidate:
    return SocMemoryCandidate(
        candidate_id="MC-MEMORYCENTER",
        candidate_type=SocMemoryCandidateType.BENIGN_PATTERN,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        summary="Reviewed repeated detector candidate",
        content="Candidate content",
        tenant_scope="pingan",
        tenant_id="pingan",
        status=SocMemoryCandidateStatus.PENDING_REVIEW,
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.REPEATED_PATTERN,
            source_id=f"memory_pattern:{_AGGREGATION_KEY}",
            run_id=observations[0].source.run_id,
            alert_id=observations[0].source.alert_id,
            metadata={
                "environment": "dev",
                "data_class": "simulation",
                "memory_profile_id": _PROFILE.profile_id,
                "memory_profile_version": _PROFILE.profile_version,
                "memory_feature_schema_version": _PROFILE.feature_schema_version,
                "lineage_key": _LINEAGE_KEY,
            },
        ),
        evidence_refs=[item.evidence_refs[0] for item in observations],
        validity=SocMemoryCandidateValidity(
            valid_from=_START,
            valid_until=_START + timedelta(days=90),
            review_after_days=30,
            notes="test",
        ),
        confidence=1.0,
        facets={"detection_key": ["generic:detector:test"]},
        applicability=SocMemoryApplicabilitySpec(
            profile_id=_PROFILE.profile_id,
            profile_version=_PROFILE.profile_version,
            feature_schema_version=_PROFILE.feature_schema_version,
            required_facets={"detection_key": ["generic:detector:test"]},
        ),
        decision_impact=SocMemoryDecisionImpact.DETECTION_DECISION,
        metadata={
            "observation_ids": [item.observation_id for item in observations],
            "support_count_at_creation": len(observations),
            "distinct_source_count_at_creation": len(observations),
            "memory_profile_id": _PROFILE.profile_id,
            "memory_profile_version": _PROFILE.profile_version,
            "memory_feature_schema_version": _PROFILE.feature_schema_version,
            "lineage_key": _LINEAGE_KEY,
        },
    )


def _terminal_observation() -> MemoryPatternObservation:
    payload = _observation(1).model_dump()
    payload.update(
        observation_id="MPO-TERMINAL",
        idempotency_key="memory-center:terminal",
        aggregation_key=_TERMINAL_AGGREGATION_KEY,
        lineage_key=_TERMINAL_LINEAGE_KEY,
        content_hash="f" * 64,
        occurrence_key="9" * 64,
    )
    payload["source"].update(
        source_id="alert:terminal",
        transport_ref="batch:terminal",
        run_id="RUN-TERMINAL",
        alert_id="ALERT-TERMINAL",
    )
    payload["signature"].update(
        value="generic:detector:terminal",
        label="Terminal historical detector",
        facets={"detection_key": ["generic:detector:terminal"]},
    )
    payload["evidence_refs"] = ["alert:terminal"]
    return MemoryPatternObservation.model_validate(payload)


def _terminal_candidate(observation: MemoryPatternObservation) -> SocMemoryCandidate:
    payload = _candidate([observation]).model_dump()
    payload.update(
        candidate_id="MC-MEMORYCENTER-TERMINAL",
        status=SocMemoryCandidateStatus.SUPERSEDED,
        superseded_by_candidate_id="MC-MEMORYCENTER-SUCCESSOR",
        superseded_at=_START + timedelta(days=2),
        supersession_reason="Profile contract upgraded.",
    )
    payload["source"].update(
        source_id=f"memory_pattern:{_TERMINAL_AGGREGATION_KEY}",
        run_id=observation.source.run_id,
        alert_id=observation.source.alert_id,
    )
    payload["source"]["metadata"]["lineage_key"] = _TERMINAL_LINEAGE_KEY
    payload["metadata"].update(
        observation_ids=[observation.observation_id],
        support_count_at_creation=1,
        distinct_source_count_at_creation=1,
        lineage_key=_TERMINAL_LINEAGE_KEY,
    )
    return SocMemoryCandidate.model_validate(payload)


def _manual_candidate(
    observation: MemoryPatternObservation,
) -> SocMemoryCandidate:
    payload = _candidate([observation]).model_dump()
    payload.update(
        candidate_id="MC-MANUAL-PROMOTION",
        status=SocMemoryCandidateStatus.CONFIRMED,
        summary="Analyst-promoted reviewed lesson",
        content="The analyst promoted this exact run before recurrence threshold.",
    )
    payload["source"].update(
        source_type=SocMemoryCandidateSourceType.MANUAL_NOTE,
        source_id="manual_run_promotion:test",
        run_id=observation.source.run_id,
        alert_id=observation.source.alert_id,
        metadata={
            "promote_to_memory": True,
            "environment": observation.environment,
            "memory_profile_id": observation.profile_id,
            "memory_profile_version": observation.profile_version,
            "memory_feature_schema_version": observation.feature_schema_version,
        },
    )
    payload["metadata"].pop("lineage_key", None)
    payload["metadata"].pop("observation_ids", None)
    payload["metadata"].pop("support_count_at_creation", None)
    payload["metadata"].pop("distinct_source_count_at_creation", None)
    payload["metadata"].update(source="manual_run_promotion")
    return SocMemoryCandidate.model_validate(payload)


def _manual_memory_record(candidate: SocMemoryCandidate) -> SocMemoryRecord:
    return SocMemoryRecord(
        memory_id="MEM-MANUAL-PROMOTION",
        memory_type=candidate.candidate_type,
        target_artifact=candidate.target_artifact,
        tenant_scope=candidate.tenant_scope,
        tenant_id=candidate.tenant_id,
        source_candidate_id=candidate.candidate_id,
        source=candidate.source,
        summary="Reviewed analyst-promoted lesson",
        content="This exact run established a reusable reviewed lesson.",
        facets=candidate.facets,
        applicability=candidate.applicability,
        evidence_refs=candidate.evidence_refs,
        validity=candidate.validity,
        confidence=0.9,
        decision_impact=candidate.decision_impact,
        content_hash=stable_hash({"candidate_id": candidate.candidate_id}),
        facets_hash=stable_hash(candidate.facets),
        retrieval_enabled=True,
        retrieval_policy_version="soc.memory_retrieval_activation_policy.v1",
        retrieval_valid_until=_START + timedelta(days=90),
        retrieval_review_due_at=_START + timedelta(days=30),
        retrieval_updated_by=ActorContext(actor_id="memory-reviewer"),
        retrieval_updated_at=_START,
        retrieval_reason="Approved for exact Pattern retrieval.",
        created_by=ActorContext(actor_id="memory-reviewer"),
    )


def _assert_center(repository: InMemoryMemoryPatternRepository | SqlAlchemyAlertRepository) -> None:
    observations = [_observation(1), _observation(2), _observation(3)]
    for observation in observations:
        repository.save_memory_pattern_observation(observation)
    repository.save_memory_candidate(_candidate(observations[:2]))
    terminal_observation = _terminal_observation()
    repository.save_memory_pattern_observation(terminal_observation)
    repository.save_memory_candidate(_terminal_candidate(terminal_observation))
    service = SocMemoryCenterService(
        center_repository=repository,
        observation_repository=repository,
        candidate_repository=repository,
        record_repository=repository,
    )

    overview = service.overview()
    assert overview.metrics.pattern_count == 2
    assert overview.metrics.aggregation_window_count == 3
    assert overview.metrics.observation_count == 4
    assert overview.metrics.pending_candidate_count == 1
    assert overview.metrics.superseded_candidate_count == 1
    assert overview.total == 1
    assert overview.terminal_history_count == 1
    with_history = service.overview(include_terminal_history=True)
    assert with_history.total == 2
    assert with_history.terminal_history_count == 1
    assert {item.lineage_key: item.lifecycle_state.value for item in with_history.items}[_TERMINAL_LINEAGE_KEY] == "terminal_history"
    assert service.overview(search="Repeated test detector").total == 1
    assert service.overview(search="ALERT-3").total == 1
    assert service.overview(search="Terminal historical detector").total == 0
    assert (
        service.overview(
            search="Terminal historical detector",
            include_terminal_history=True,
        ).total
        == 1
    )
    pattern = overview.items[0]
    assert pattern.profile_state is SocMemoryProfileState.CURRENT
    assert pattern.support_count == 3
    assert pattern.aggregation_window_count == 2
    assert pattern.candidate_snapshot_count == 2
    assert pattern.reinforcement_count == 1
    assert pattern.candidate is not None

    detail = service.pattern_detail(_LINEAGE_KEY)
    assert detail.observation_total == 3
    assert len(detail.observations) == 3
    assert len(detail.candidates) == 1
    assert detail.candidates[0].candidate_id == "MC-MEMORYCENTER"

    summary_detail = service.pattern_detail(
        _LINEAGE_KEY,
        include_observations=False,
    )
    assert summary_detail.observation_total == 3
    assert summary_detail.observations == []


def _assert_manual_promotion_projection(
    repository: InMemoryMemoryPatternRepository | SqlAlchemyAlertRepository,
) -> None:
    observation = _observation(1)
    candidate = _manual_candidate(observation)
    record = _manual_memory_record(candidate)
    repository.save_memory_pattern_observation(observation)
    repository.save_memory_candidate(candidate)
    repository.save_memory_record(record)
    service = SocMemoryCenterService(
        center_repository=repository,
        observation_repository=repository,
        candidate_repository=repository,
        record_repository=repository,
    )

    overview = service.overview()

    assert overview.total == 1
    pattern = overview.items[0]
    assert pattern.lifecycle_state.value == "memory_active"
    assert pattern.candidate is not None
    assert pattern.candidate.candidate_id == candidate.candidate_id
    assert pattern.memory_record is not None
    assert pattern.memory_record.memory_id == record.memory_id
    assert pattern.support_count == 1
    assert pattern.candidate_snapshot_count == 0

    detail = service.pattern_detail(observation.lineage_key)
    assert [item.candidate_id for item in detail.candidates] == [candidate.candidate_id]
    assert [item.memory_id for item in detail.memory_records] == [record.memory_id]


def test_memory_center_uses_dynamic_in_memory_patterns() -> None:
    _assert_center(InMemoryMemoryPatternRepository())


def test_memory_center_uses_sql_aggregates() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    _assert_center(repository)


def test_memory_center_projects_manual_run_promotion_in_memory() -> None:
    _assert_manual_promotion_projection(InMemoryMemoryPatternRepository())


def test_memory_center_projects_manual_run_promotion_in_sql() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    _assert_manual_promotion_projection(repository)
