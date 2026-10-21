"""Weak PingAn observations accumulate without creating unusable review tasks."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from test_soc_agent_service import InMemoryAlertRepository, InMemorySummaryRepository
from test_soc_pingan_memory_profile import _observe, _run, _service

from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.contracts import (
    ActorContext,
    AlertSummary,
    Decision,
    MemoryPatternAggregationPolicy,
    ServiceRequestContext,
    SocMemoryRunPromotionCommand,
)
from soc_agent.core import SocMemoryPatternService, SocReviewService, SocServiceConflictError
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile
from soc_agent.memory import InMemoryMemoryPatternRepository


@pytest.fixture(autouse=True)
def legacy_profile(monkeypatch):
    monkeypatch.setenv("SOC_NORMALIZATION_ASSIST_MODE", "off")


@pytest.mark.parametrize("weak", [{"techniques": []}, {"techniques": [], "network_protocol": "tcp", "scenario_type": "web_attack"}])
@pytest.mark.parametrize("persisted", [False, True])
def test_recurrence_keeps_weak_observations_without_candidate(weak, persisted):
    if persisted:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        create_soc_tables(engine)
        repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    else:
        repository = InMemoryMemoryPatternRepository()
    service = SocMemoryPatternService(
        repository=repository,
        candidate_repository=repository,
        policy=MemoryPatternAggregationPolicy(),
        profile_registry=build_soc_memory_profile_registry(),
    )
    for index in range(1, 7):
        result = _observe(service, _run(index, **weak), f"batch:{index}")

    assert result.threshold_met
    assert result.support_count == 6
    assert result.candidate is None
    assert not result.cohort_quality.quality_gate_passed
    assert result.cohort_quality.reason_codes == ["missing_reusable_core_behavior"]
    assert "缺少可复用的核心行为" in result.note
    assert len(repository.list_memory_pattern_observations(aggregation_key=result.observation.aggregation_key)) == 6
    assert repository.list_memory_candidates() == []
    replay = service.replay(result.observation.aggregation_key)
    assert replay.cohort_quality.reason_codes == ["missing_reusable_core_behavior"]
    assert replay.candidate_id is None


def test_core_behavior_still_needs_normal_recurrence_threshold():
    repository = InMemoryMemoryPatternRepository()
    service = SocMemoryPatternService(
        repository=repository,
        candidate_repository=repository,
        policy=MemoryPatternAggregationPolicy(),
        profile_registry=build_soc_memory_profile_registry(),
    )
    for index in range(1, 5):
        result = _observe(service, _run(index), f"batch:{index}")
        assert result.candidate is None
        assert "missing_reusable_core_behavior" not in result.cohort_quality.reason_codes
    result = _observe(service, _run(5), "batch:5")
    assert result.candidate_created
    assert result.candidate.applicability.required_facets["behavior_fingerprint"]


def test_manual_promotion_explains_missing_core_without_candidate_or_run_mutation():
    run = _run(1, techniques=[])
    run.decision = Decision(verdict=run.analysis.verdict, confidence=run.analysis.confidence, reason=run.analysis.reason, suggested_action="ignore", needs_review=False)
    saved = run.model_dump(mode="json")
    repository = InMemoryAlertRepository()
    repository.save_run(run)
    summaries = InMemorySummaryRepository()
    summaries.save_alert_summary(AlertSummary(run_id=run.run_id, alert_id=run.alert_id, status=run.status, verdict=run.analysis.verdict))
    candidates = InMemoryMemoryPatternRepository()
    service = SocReviewService(
        repository=repository,
        summary_repository=summaries,
        memory_candidate_repository=candidates,
        memory_profile_registry=build_soc_memory_profile_registry(),
    )
    with pytest.raises(SocServiceConflictError, match="缺少可复用的核心行为"):
        service.promote_run_to_memory(
            SocMemoryRunPromotionCommand(run_id=run.run_id),
            context=ServiceRequestContext(actor=ActorContext(actor_id="reviewer", roles=["soc_admin"])),
        )
    view = service.get_alert_investigation_context(run.run_id)
    assert view.learning.action == "view_history"
    assert "缺少可复用的核心行为" in view.learning.detail
    assert candidates.list_memory_candidates() == []
    assert repository.get_run(run.run_id).model_dump(mode="json") == saved


def test_existing_reference_only_candidate_stays_readable(monkeypatch):
    repository = InMemoryMemoryPatternRepository()
    # Model a candidate persisted before the new admission policy.
    with monkeypatch.context() as legacy:
        legacy.setattr(PingAnSocMemoryProfile, "candidate_review_ineligibility", lambda self, applicability: None, raising=False)
        service = _service(repository)
        _observe(service, _run(1, techniques=[]), "batch:1")
        existing = _observe(service, _run(2, techniques=[]), "batch:2")
    candidate = existing.candidate
    assert candidate is not None
    before = candidate.model_dump(mode="json")
    result = _observe(_service(repository), _run(3, techniques=[]), "batch:3")
    assert result.candidate.candidate_id == candidate.candidate_id
    assert not result.candidate_created
    assert repository.get_memory_candidate(candidate.candidate_id).model_dump(mode="json") == before


def test_existing_manual_reference_candidate_keeps_history_navigation(monkeypatch):
    repository = InMemoryMemoryPatternRepository()
    run = _run(3, techniques=[])
    run.decision = Decision(verdict=run.analysis.verdict, confidence=run.analysis.confidence, reason=run.analysis.reason, suggested_action="ignore", needs_review=False)
    alerts = InMemoryAlertRepository()
    alerts.save_run(run)
    summaries = InMemorySummaryRepository()
    summaries.save_alert_summary(AlertSummary(run_id=run.run_id, alert_id=run.alert_id, status=run.status, verdict=run.analysis.verdict))
    review = SocReviewService(
        repository=alerts,
        summary_repository=summaries,
        memory_candidate_repository=repository,
        memory_pattern_observation_repository=repository,
        memory_profile_registry=build_soc_memory_profile_registry(),
    )
    with monkeypatch.context() as legacy:
        legacy.setattr(PingAnSocMemoryProfile, "candidate_review_ineligibility", lambda self, applicability: None)
        candidate = review.promote_run_to_memory(
            SocMemoryRunPromotionCommand(run_id=run.run_id),
            context=ServiceRequestContext(actor=ActorContext(actor_id="reviewer", roles=["soc_admin"])),
        ).memory_candidate
    assert review.get_alert_investigation_context(run.run_id).learning.candidate_id == candidate.candidate_id
