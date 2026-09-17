from datetime import UTC, datetime, timedelta

import pytest
from test_soc_memory_patterns import _observe, _run, _service

from soc_agent.contracts import MemoryPatternAggregationPolicy, Verdict
from soc_agent.contracts.memory_patterns import MemoryPatternAccumulationScope
from soc_agent.core import SocMemoryPatternService, SocServiceConflictError
from soc_agent.memory import InMemoryMemoryPatternRepository


def _scope(experiment_id="EXP-test"):
    return MemoryPatternAccumulationScope(
        experiment_id=experiment_id,
        event_start=datetime(2026, 1, 1, tzinfo=UTC),
        event_end=datetime(2027, 1, 1, tzinfo=UTC),
    )


def _experimental(repository, experiment_id="EXP-test", threshold=5):
    return SocMemoryPatternService(
        repository=repository,
        candidate_repository=repository,
        policy=MemoryPatternAggregationPolicy(minimum_support=threshold, minimum_distinct_sources=threshold, minimum_conclusive_support=threshold),
        accumulation_scope=_scope(experiment_id),
    )


def test_experiment_accumulates_across_dates_without_changing_event_time_or_normal_window():
    repository = InMemoryMemoryPatternRepository()
    experiment = _experimental(repository)
    ordinary = _service(repository, threshold=5)
    results = []
    normal_results = []
    for index in range(1, 6):
        run = _run(index)
        observed = datetime(2026, index, 15, tzinfo=UTC)
        run.input_payload["event_time"] = observed.isoformat()
        run.started_at = observed
        results.append(_observe(experiment, run, transport_ref=f"exp:{index}"))
        normal_results.append(_observe(ordinary, run, transport_ref=f"normal:{index}"))
        assert results[-1].observation.source.observed_at == observed
    assert [r.support_count for r in results] == [1, 2, 3, 4, 5]
    assert all(r.support_count == 1 for r in normal_results)
    final = results[-1]
    assert final.candidate_created
    assert final.candidate.source.metadata["experiment_id"] == "EXP-test"
    assert "experiment_id" not in final.candidate.applicability.required_facets
    assert final.observation.accumulation_scope == _scope()
    replay = _observe(experiment, run, transport_ref="exp:retry")
    assert replay.support_count == 5
    assert replay.duplicate_occurrence
    assert experiment.replay(final.observation.aggregation_key).support_count == 5
    other = _observe(_experimental(repository, "EXP-other"), run, transport_ref="other")
    assert other.support_count == 1
    assert other.observation.lineage_key != final.observation.lineage_key


def test_experiment_mixed_valid_conclusions_create_an_unapproved_neutral_candidate():
    repository = InMemoryMemoryPatternRepository()
    service = _experimental(repository, threshold=4)
    for index, verdict in enumerate([Verdict.TRUE_POSITIVE, Verdict.FALSE_POSITIVE] * 2):
        result = _observe(service, _run(index, verdict=verdict), transport_ref=f"mixed:{index}")
    assert result.cohort_quality.quality_gate_passed
    assert result.cohort_quality.review_kind == "mixed_conclusions"
    assert result.cohort_quality.dominant_risk_class is None
    assert result.cohort_quality.consistency_ratio == 0.5
    assert result.candidate.status.value == "pending_review"
    assert "待确认结论" in result.candidate.summary
    assert "100%" not in result.candidate.summary
    assert not result.candidate.runtime_decision_allowed
    assert service.replay(result.observation.aggregation_key).cohort_quality.review_kind == "mixed_conclusions"


def test_experiment_never_bypasses_unknown_outcomes_or_accepts_out_of_scope_events():
    repository = InMemoryMemoryPatternRepository()
    service = _experimental(repository)
    for index in range(1, 6):
        result = _observe(service, _run(index, verdict=Verdict.UNKNOWN), transport_ref=f"unknown:{index}")
    assert result.candidate is None
    assert "insufficient_conclusive_support" in result.cohort_quality.reason_codes
    run = _run(7)
    run.input_payload["event_time"] = (_scope().event_end + timedelta(days=1)).isoformat()
    run.started_at = _scope().event_end + timedelta(days=1)
    with pytest.raises(SocServiceConflictError, match="experiment event range"):
        _observe(service, run, transport_ref="out-of-range")


def test_changed_learning_configuration_uses_another_accumulation_scope():
    repository = InMemoryMemoryPatternRepository()
    left = SocMemoryPatternService(repository=repository, candidate_repository=repository, accumulation_scope=_scope().model_copy(update={"configuration_hash": "a" * 64}))
    right = SocMemoryPatternService(repository=repository, candidate_repository=repository, accumulation_scope=_scope().model_copy(update={"configuration_hash": "b" * 64}))
    first = _observe(left, _run(1), transport_ref="left")
    second = _observe(right, _run(2), transport_ref="right")
    assert first.observation.aggregation_key != second.observation.aggregation_key
    assert second.support_count == 1
