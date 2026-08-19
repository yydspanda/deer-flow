from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.cli import main as soc_cli_main
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
    SocDaemonMessage,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateStatus,
    SocMemoryCandidateType,
    TriageActivityStage,
    TriageScenarioAssessment,
    TriageScenarioOrigin,
    Verdict,
)
from soc_agent.core import (
    SocDaemonService,
    SocMemoryPatternService,
    SocServiceConflictError,
)
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.memory import (
    GenericSocMemoryProfile,
    InMemoryMemoryPatternRepository,
    SocMemoryProfileIdentity,
    SocMemoryProfileRegistry,
    memory_pattern_command_from_run,
)

_START = datetime(2026, 8, 6, 1, 0, tzinfo=UTC)


def _context(role: str = "soc_batch_runner") -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id="memory-pattern-test",
            actor_type=ActorType.SERVICE,
            surface=EntrySurface.TEST,
            roles=[role],
        )
    )


def _run(
    index: int,
    *,
    tenant_id: str = "tenant-a",
    detection_key: str | None = "generic:detector:credential-access",
    category: str | None = "credential_access",
    scenario_key: str | None = None,
    verdict: Verdict = Verdict.SUSPICIOUS,
) -> AnalysisRun:
    scenario_assessments = []
    if scenario_key is not None:
        scenario_assessments.append(
            TriageScenarioAssessment(
                scenario_name="Credential access behavior",
                scenario_key=scenario_key,
                is_primary=True,
                origin=TriageScenarioOrigin.INFERRED,
                confidence=0.7,
                activity_stage=TriageActivityStage.ATTEMPT_OBSERVED,
                evidence_refs=["E-000000000001"],
                reasoning_refs=["R-01"],
                rationale="The bounded process evidence supports this hypothesis.",
            )
        )
    analysis = AnalysisResult(
        verdict=verdict,
        confidence=0.7,
        summary="Repeated endpoint behavior requires review.",
        evidence=[
            EvidenceItem(
                evidence_ref="E-000000000001",
                source="canonical",
                description="Observed process",
                value="cmd.exe",
            )
        ],
        reasoning=[
            AnalysisReasoningItem(
                reasoning_id="R-01",
                statement="The bounded process evidence supports this hypothesis.",
                basis=[AnalysisReasoningBasis.CURRENT_EVIDENCE],
                evidence_refs=["E-000000000001"],
                confidence=0.7,
            )
        ],
        scenario_assessments=scenario_assessments,
        reason="Bounded test reasoning.",
        recommended_action="review",
    )
    return AnalysisRun(
        run_id=f"RUN-PATTERN-{index:03d}",
        alert_id=f"ALERT-PATTERN-{index:03d}",
        status=AnalysisRunStatus.NEEDS_REVIEW,
        input_payload={
            "alert_id": f"ALERT-PATTERN-{index:03d}",
            "event_time": (_START + timedelta(minutes=index)).isoformat(),
        },
        input_hash=f"{index:064x}",
        started_at=_START + timedelta(minutes=index),
        llm_analysis_request=LLMAnalysisRequest(
            alert_id=f"ALERT-PATTERN-{index:03d}",
            tenant_id=tenant_id,
            source=AlertSourceRef(
                source_type=AlertSourceType.EDR,
                source_system="generic-edr",
            ),
            detection=DetectionRuleRef(
                detection_key=detection_key,
                rule_name="Credential behavior",
            ),
            classification=AlertClassification(category=category),
        ),
        analysis=analysis,
    )


def _service(
    repository: InMemoryMemoryPatternRepository | SqlAlchemyAlertRepository,
    *,
    threshold: int = 3,
) -> SocMemoryPatternService:
    return SocMemoryPatternService(
        repository=repository,
        candidate_repository=repository,
        policy=MemoryPatternAggregationPolicy(
            minimum_support=threshold,
            minimum_distinct_sources=threshold,
            minimum_conclusive_support=threshold,
        ),
    )


def _observe(
    service: SocMemoryPatternService,
    run: AnalysisRun,
    *,
    transport_ref: str,
    environment: str = "dev",
    data_class: MemoryPatternDataClass = MemoryPatternDataClass.SIMULATION,
    source_type: MemoryPatternSourceType = MemoryPatternSourceType.BATCH_ALERT,
):
    return service.observe_run(
        run,
        source_type=source_type,
        transport_ref=transport_ref,
        environment=environment,
        data_class=data_class,
        context=_context("soc_daemon" if source_type is MemoryPatternSourceType.KAFKA_ALERT else "soc_batch_runner"),
    )


def test_distinct_sources_create_one_frozen_pending_candidate() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)

    first = _observe(service, _run(1), transport_ref="batch:1")
    second = _observe(service, _run(2), transport_ref="batch:2")
    third = _observe(service, _run(3), transport_ref="batch:3")

    assert first.threshold_met is False
    assert second.threshold_met is False
    assert third.candidate_created is True
    assert third.candidate_frozen is True
    assert third.candidate is not None
    assert third.candidate.status is SocMemoryCandidateStatus.PENDING_REVIEW
    assert third.candidate.source.source_type is SocMemoryCandidateSourceType.REPEATED_PATTERN
    assert third.candidate.runtime_decision_allowed is False
    assert third.cohort_quality.quality_gate_passed is True
    assert third.cohort_quality.dominant_risk_class.value == "risk"
    assert third.candidate.facets["detection_key"] == ["generic:detector:credential-access"]
    assert "经验结论" in third.candidate.content
    assert "风险判断：有风险" in third.candidate.content
    assert "代表性研判" in third.candidate.content
    assert third.candidate.metadata["candidate_snapshot_frozen"] is True
    assert third.candidate.metadata["support_count_at_creation"] == 3
    assert third.candidate.validity.valid_from == third.candidate.created_at
    assert third.candidate.validity.valid_until == third.candidate.created_at + timedelta(days=90)
    assert (
        third.candidate.source.metadata["window_start"]
        == _START.replace(
            hour=0,
            minute=0,
        ).isoformat()
    )
    assert third.candidate.source.metadata["window_end"] == (_START.replace(hour=0, minute=0) + timedelta(days=1)).isoformat()

    fourth = _observe(service, _run(4), transport_ref="batch:4")
    assert fourth.candidate_created is False
    assert fourth.candidate_frozen is True
    assert fourth.candidate == third.candidate
    assert len(repository.list_memory_candidates()) == 1

    replay = service.replay(third.observation.aggregation_key)
    assert replay.changed is True
    assert replay.added_observation_ids == [fourth.observation.observation_id]
    assert replay.source_integrity_passed is True
    assert replay.candidate_mutation_performed is False
    assert replay.supersession_mode == "manual_only"


def test_primary_scenario_generalizes_without_rule_code() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository, threshold=2)

    result = _observe(
        service,
        _run(1, detection_key=None, scenario_key="credential_access"),
        transport_ref="batch:scenario:1",
    )
    final = _observe(
        service,
        _run(2, detection_key=None, scenario_key="credential_access"),
        transport_ref="batch:scenario:2",
    )

    assert result.observation.signature.dimension is MemoryPatternDimension.SCENARIO
    assert result.observation.signature.value == "credential_access"
    assert final.candidate_created is True
    assert final.candidate is not None
    assert final.candidate.facets["pattern_dimension"] == ["scenario"]


def test_conflicting_outcomes_do_not_create_expert_review_candidate() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository, threshold=4)

    results = [
        _observe(
            service,
            _run(index, verdict=verdict),
            transport_ref=f"batch:conflict:{index}",
        )
        for index, verdict in enumerate(
            (
                Verdict.TRUE_POSITIVE,
                Verdict.FALSE_POSITIVE,
                Verdict.SUSPICIOUS,
                Verdict.FALSE_POSITIVE,
            ),
            start=1,
        )
    ]

    final = results[-1]
    assert final.threshold_met is True
    assert final.cohort_quality.quality_gate_passed is False
    assert final.cohort_quality.consistency_ratio == 0.5
    assert "inconsistent_risk_outcomes" in final.cohort_quality.reason_codes
    assert final.candidate is None
    assert repository.list_memory_candidates() == []


def test_consistent_false_positive_cohort_creates_benign_pattern_lesson() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository, threshold=3)

    results = [
        _observe(
            service,
            _run(index, verdict=Verdict.FALSE_POSITIVE),
            transport_ref=f"batch:benign:{index}",
        )
        for index in range(1, 4)
    ]

    candidate = results[-1].candidate
    assert candidate is not None
    assert candidate.candidate_type is SocMemoryCandidateType.BENIGN_PATTERN
    assert "风险判断：无风险/误报模式" in candidate.content
    assert candidate.decision_impact.value == "review_hint"


def test_equivalent_lesson_in_later_window_does_not_create_another_candidate() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository, threshold=2)

    first_results = [
        _observe(
            service,
            _run(index),
            transport_ref=f"batch:first-window:{index}",
        )
        for index in range(1, 3)
    ]
    first_candidate = first_results[-1].candidate
    assert first_candidate is not None

    second_results = []
    for index in range(3, 5):
        run = _run(index)
        run.input_payload["event_time"] = (_START + timedelta(days=1, minutes=index)).isoformat()
        second_results.append(
            _observe(
                service,
                run,
                transport_ref=f"batch:second-window:{index}",
            )
        )

    final = second_results[-1]
    assert final.threshold_met is True
    assert final.candidate_created is False
    assert final.candidate_coverage == "equivalent_lesson"
    assert final.candidate == first_candidate
    assert "reinforcement observations" in final.note
    assert len(repository.list_memory_candidates()) == 1

    replay = service.replay(final.observation.aggregation_key)
    assert replay.candidate_id == first_candidate.candidate_id
    assert replay.candidate_coverage == "equivalent_lesson"
    assert replay.candidate_origin_aggregation_key == first_results[-1].observation.aggregation_key
    assert replay.source_integrity_checked is False
    assert replay.changed is False


def test_completed_run_without_analysis_is_not_a_memory_observation() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)
    run = _run(1)
    run.analysis = None

    from soc_agent.memory import MemoryPatternIneligibleError

    with pytest.raises(
        MemoryPatternIneligibleError,
        match="requires a completed analysis conclusion",
    ):
        _observe(service, run, transport_ref="batch:no-analysis")

    assert repository.list_memory_pattern_observations() == []


def test_duplicate_alert_across_batch_and_kafka_counts_once() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository, threshold=2)
    run = _run(1)

    first = _observe(service, run, transport_ref="batch:1")
    duplicate = _observe(
        service,
        run,
        transport_ref="kafka:soc.alerts.raw.v1:0:1",
        source_type=MemoryPatternSourceType.KAFKA_ALERT,
    )

    assert first.support_count == 1
    assert duplicate.duplicate_source is True
    assert duplicate.support_count == 1
    assert duplicate.distinct_source_count == 1
    assert duplicate.candidate is None
    assert len(repository.list_memory_pattern_observations()) == 1


def test_fixed_window_uses_source_event_time_not_runtime_start_time() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository, threshold=2)
    first_run = _run(1)
    second_run = _run(2)
    second_run.started_at = first_run.started_at + timedelta(days=30)

    first = _observe(service, first_run, transport_ref="batch:event-time:1")
    second = _observe(service, second_run, transport_ref="batch:event-time:2")

    assert first.observation.window_start == second.observation.window_start
    assert second.candidate_created is True
    assert second.observation.metadata["window_time_source"] == "canonical_alert.event.event_time"


def test_missing_source_event_time_is_ineligible() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)
    run = _run(1)
    run.input_payload = {"alert_id": run.alert_id}

    from soc_agent.memory import MemoryPatternIneligibleError

    with pytest.raises(MemoryPatternIneligibleError, match="requires canonical alert event_time"):
        _observe(service, run, transport_ref="batch:no-event-time")


def test_tenant_environment_and_data_class_never_share_a_cohort() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository, threshold=2)

    tenant_a = _observe(service, _run(1), transport_ref="batch:a")
    tenant_b = _observe(
        service,
        _run(2, tenant_id="tenant-b"),
        transport_ref="batch:b",
    )
    staging = _observe(
        service,
        _run(3),
        transport_ref="batch:staging",
        environment="staging",
    )
    operational = _observe(
        service,
        _run(4),
        transport_ref="batch:operational",
        data_class=MemoryPatternDataClass.OPERATIONAL,
    )

    keys = {
        tenant_a.observation.aggregation_key,
        tenant_b.observation.aggregation_key,
        staging.observation.aggregation_key,
        operational.observation.aggregation_key,
    }
    assert len(keys) == 4
    assert all(item.support_count == 1 for item in (tenant_a, tenant_b, staging, operational))


def test_idempotency_reuse_with_changed_content_conflicts() -> None:
    repository = InMemoryMemoryPatternRepository()
    service = _service(repository)
    first = _observe(service, _run(1), transport_ref="batch:same")
    command = first.observation.model_dump(mode="python")
    command.pop("schema_version")
    command.pop("observation_id")
    command.pop("aggregation_key")
    command.pop("lineage_key")
    command.pop("content_hash")
    command.pop("window_start")
    command.pop("window_end")
    command.pop("aggregation_policy")
    command.pop("mocked")
    command.pop("direct_memory_candidate_allowed")
    command.pop("runtime_decision_allowed")
    command.pop("created_at")
    command["environment"] = "staging"

    from soc_agent.contracts import MemoryPatternObservationCreateCommand

    with pytest.raises(SocServiceConflictError, match="reused for different content"):
        service.ingest_observation(
            MemoryPatternObservationCreateCommand.model_validate(command),
            context=_context(),
        )


def test_pattern_idempotency_identity_changes_with_profile_contract() -> None:
    class _UpgradedGenericProfile(GenericSocMemoryProfile):
        identity = SocMemoryProfileIdentity(
            profile_id="soc.generic",
            profile_version="2",
            feature_schema_version="soc.memory_features.generic.v2",
        )

    run = _run(1)
    common = {
        "source_type": MemoryPatternSourceType.BATCH_ALERT,
        "transport_ref": "batch:profile-upgrade:1",
        "environment": "dev",
        "data_class": MemoryPatternDataClass.SIMULATION,
        "policy_fingerprint": "a" * 64,
    }

    original = memory_pattern_command_from_run(
        run,
        profile=GenericSocMemoryProfile(),
        **common,
    )
    original_retry = memory_pattern_command_from_run(
        run,
        profile=GenericSocMemoryProfile(),
        **common,
    )
    upgraded = memory_pattern_command_from_run(
        run,
        profile=_UpgradedGenericProfile(),
        **common,
    )

    assert original.idempotency_key == original_retry.idempotency_key
    assert original.idempotency_key != upgraded.idempotency_key
    assert original.feature_schema_version == "soc.memory_features.generic.v1"
    assert upgraded.feature_schema_version == "soc.memory_features.generic.v2"


def test_profile_upgrade_supersedes_same_alert_pending_candidate() -> None:
    class _UpgradedGenericProfile(GenericSocMemoryProfile):
        identity = SocMemoryProfileIdentity(
            profile_id="soc.generic",
            profile_version="2",
            feature_schema_version="soc.memory_features.generic.v2",
        )

    repository = InMemoryMemoryPatternRepository()
    policy = MemoryPatternAggregationPolicy(
        minimum_support=2,
        minimum_distinct_sources=2,
        minimum_conclusive_support=2,
    )
    original_service = SocMemoryPatternService(
        repository=repository,
        candidate_repository=repository,
        policy=policy,
    )
    upgraded_service = SocMemoryPatternService(
        repository=repository,
        candidate_repository=repository,
        policy=policy,
        profile_registry=SocMemoryProfileRegistry(fallback=_UpgradedGenericProfile()),
    )

    _observe(original_service, _run(1), transport_ref="batch:profile-v1:1")
    original = _observe(
        original_service,
        _run(2),
        transport_ref="batch:profile-v1:2",
    ).candidate
    assert original is not None

    _observe(upgraded_service, _run(1), transport_ref="batch:profile-v2:1")
    upgraded = _observe(
        upgraded_service,
        _run(2),
        transport_ref="batch:profile-v2:2",
    ).candidate
    assert upgraded is not None

    superseded = repository.get_memory_candidate(original.candidate_id)
    assert superseded is not None
    assert superseded.status is SocMemoryCandidateStatus.SUPERSEDED
    assert superseded.superseded_by_candidate_id == upgraded.candidate_id
    assert superseded.superseded_at is not None
    persisted_upgraded = repository.get_memory_candidate(upgraded.candidate_id)
    assert persisted_upgraded is not None
    assert persisted_upgraded.metadata["supersedes_candidate_ids"] == [original.candidate_id]
    assert repository.list_memory_candidates(status=SocMemoryCandidateStatus.PENDING_REVIEW) == [persisted_upgraded]


def test_sql_repository_commits_observation_candidate_and_audit_atomically() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    service = _service(repository, threshold=2)

    _observe(service, _run(1), transport_ref="batch:sql:1")
    final = _observe(service, _run(2), transport_ref="batch:sql:2")

    assert final.candidate is not None
    assert len(repository.list_memory_pattern_observations()) == 2
    assert repository.get_memory_candidate(final.candidate.candidate_id) == final.candidate
    audits = repository.list_mutation_audits()
    assert len(audits) == 2
    assert all(item.operation.value == "memory_pattern_observation.ingest" for item in audits)
    assert service.replay(final.observation.aggregation_key).changed is False


def test_cli_lists_and_replays_persisted_pattern_cohort(
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "memory-patterns.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    engine = create_engine(database_url)
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    service = _service(repository, threshold=2)
    _observe(service, _run(1), transport_ref="batch:cli:1")
    final = _observe(service, _run(2), transport_ref="batch:cli:2")

    assert (
        soc_cli_main(
            [
                "memory",
                "patterns",
                "list",
                "--aggregation-key",
                final.observation.aggregation_key,
                "--database-url",
                database_url,
            ]
        )
        == 0
    )
    listed = json.loads(capsys.readouterr().out)
    assert [item["observation_id"] for item in listed] == [
        item.observation_id
        for item in repository.list_memory_pattern_observations(
            aggregation_key=final.observation.aggregation_key,
        )
    ]

    assert (
        soc_cli_main(
            [
                "memory",
                "patterns",
                "replay",
                final.observation.aggregation_key,
                "--database-url",
                database_url,
            ]
        )
        == 0
    )
    replay = json.loads(capsys.readouterr().out)
    assert replay["source_integrity_passed"] is True
    assert replay["candidate_mutation_performed"] is False


class _AnalysisService:
    def __init__(self, run: AnalysisRun) -> None:
        self.run = run

    def analyze(self, _payload: dict, *, context: ServiceRequestContext) -> AnalysisRun:
        assert context.actor.roles == ["soc_daemon"]
        return self.run


def test_daemon_bridge_is_default_off_and_explicitly_non_blocking() -> None:
    run = _run(1)
    message = SocDaemonMessage(
        kind="alert",
        payload={"alert_id": run.alert_id},
        topic="soc.alerts.raw.v1",
        partition=0,
        offset=9,
    )
    plain = SocDaemonService(analysis_service=_AnalysisService(run)).process_message(message)
    assert "memory_pattern_status" not in plain.payload

    repository = InMemoryMemoryPatternRepository()
    enabled = SocDaemonService(
        analysis_service=_AnalysisService(run),
        memory_pattern_observer=_service(repository, threshold=2),
        memory_pattern_environment="dev",
        memory_pattern_data_class=MemoryPatternDataClass.SIMULATION,
    ).process_message(message)
    assert enabled.status == "processed"
    assert enabled.payload["memory_pattern_status"] == "observed"
    assert enabled.payload["memory_pattern_support_count"] == 1
    assert len(repository.list_memory_pattern_observations()) == 1
