"""Accumulation policy changes preserve recorded matching and replay identity."""

from dataclasses import asdict
from datetime import timedelta

import pytest
from test_soc_pingan_memory_profile import _observe, _run

from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.contracts import LLMAnalysisRequest, MemoryPatternAggregationPolicy
from soc_agent.contracts.memory_patterns import MemoryPatternAccumulationScope
from soc_agent.core import SocMemoryPatternService
from soc_agent.integrations.pingan.memory import PingAnSocMemoryProfile
from soc_agent.memory import InMemoryMemoryPatternRepository, memory_query_from_analysis_request
from soc_agent.memory.profiles import GenericSocMemoryProfile, SocMemoryProfileIdentity
from soc_agent.memory.projection_audit import bind_memory_profile
from soc_agent.pipeline.analysis_context import project_analysis_context

_DAY_SECONDS = 24 * 60 * 60


def test_aggregation_policy_supports_a_bounded_180_day_window():
    policy = MemoryPatternAggregationPolicy(window_seconds=180 * _DAY_SECONDS)
    assert policy.window_seconds == 180 * _DAY_SECONDS
    assert policy.minimum_support == policy.minimum_distinct_sources == policy.minimum_conclusive_support == 5


def test_profile_window_accepts_the_same_180_day_bound():
    identity = SocMemoryProfileIdentity(profile_id="test.profile", profile_version="1", feature_schema_version="test.v1", aggregation_window_seconds=180 * _DAY_SECONDS)
    assert identity.aggregation_window_seconds == 180 * _DAY_SECONDS


@pytest.mark.parametrize("window_seconds", [299, 180 * _DAY_SECONDS + 1])
def test_window_bounds_reject_short_or_unbounded_accumulation(window_seconds):
    with pytest.raises(ValueError):
        MemoryPatternAggregationPolicy(window_seconds=window_seconds)
    with pytest.raises(ValueError):
        SocMemoryProfileIdentity(profile_id="test.profile", profile_version="1", feature_schema_version="test.v1", aggregation_window_seconds=window_seconds)


def test_fresh_pingan_run_freezes_180_days_without_changing_matching_or_index_identity():
    profile = PingAnSocMemoryProfile()
    request = _run(1).llm_analysis_request
    before_query = memory_query_from_analysis_request(request, profile=profile)
    frozen = bind_memory_profile(request, profile)

    assert frozen.memory_aggregation_window_seconds == 180 * _DAY_SECONDS
    assert memory_query_from_analysis_request(frozen, profile=profile) == before_query
    assert "memory_aggregation_window_seconds" not in project_analysis_context(frozen)
    assert LLMAnalysisRequest.model_validate_json(frozen.model_dump_json()).memory_aggregation_window_seconds == 180 * _DAY_SECONDS
    assert asdict(PingAnSocMemoryProfile.identity) == {
        "profile_id": "pingan.soc",
        "profile_version": "7",
        "feature_schema_version": "pingan.soc.memory_features.v5",
        "aggregation_window_seconds": 30 * _DAY_SECONDS,
    }


@pytest.mark.parametrize("profile_version", ["7", "8", "9", "10", "11", "12", "13"])
def test_saved_profile_requests_do_not_acquire_a_new_window(profile_version):
    profile = PingAnSocMemoryProfile().for_identity({"profile_id": "pingan.soc", "profile_version": profile_version, "feature_schema_version": f"pingan.soc.memory_features.v{int(profile_version) - 2}"})
    request = _run(1).llm_analysis_request
    request.memory_profile = {key: getattr(profile.identity, key) for key in ("profile_id", "profile_version", "feature_schema_version")}
    frozen = bind_memory_profile(request, profile)

    assert frozen.memory_aggregation_window_seconds is None
    assert "memory_aggregation_window_seconds" not in frozen.model_dump(mode="json")
    assert frozen.memory_profile == request.memory_profile


def test_rebinding_a_frozen_window_preserves_it_and_fresh_requests_use_server_default():
    profile = PingAnSocMemoryProfile()
    first = bind_memory_profile(_run(1).llm_analysis_request, profile)
    first.memory_aggregation_window_seconds = 60 * _DAY_SECONDS
    assert bind_memory_profile(first, profile).memory_aggregation_window_seconds == 60 * _DAY_SECONDS

    fresh = _run(2).llm_analysis_request
    fresh.memory_aggregation_window_seconds = 60 * _DAY_SECONDS
    assert bind_memory_profile(fresh, profile).memory_aggregation_window_seconds == 180 * _DAY_SECONDS


def test_generic_profile_keeps_legacy_one_day_policy_and_serialization():
    frozen = bind_memory_profile(LLMAnalysisRequest(alert_id="generic"), GenericSocMemoryProfile())
    assert frozen.memory_aggregation_window_seconds is None
    assert "memory_aggregation_window_seconds" not in frozen.model_dump(mode="json")
    assert MemoryPatternAggregationPolicy().window_seconds == _DAY_SECONDS


@pytest.mark.parametrize("window_seconds", [299, 180 * _DAY_SECONDS + 1])
def test_frozen_run_window_uses_the_policy_bound(window_seconds):
    with pytest.raises(ValueError):
        LLMAnalysisRequest(alert_id="invalid", memory_aggregation_window_seconds=window_seconds)


def _new_bound_run(index):
    run = _run(index)
    profile = build_soc_memory_profile_registry().resolve_request(run.llm_analysis_request)
    run.llm_analysis_request = bind_memory_profile(run.llm_analysis_request, profile)
    return run


def _pattern_service(repository, **kwargs):
    return SocMemoryPatternService(repository=repository, candidate_repository=repository, profile_registry=build_soc_memory_profile_registry(), **kwargs)


def test_new_run_accumulates_in_180_day_fixed_window_and_retries_idempotently():
    repository = InMemoryMemoryPatternRepository()
    service = _pattern_service(repository)
    run = _new_bound_run(1)
    first = _observe(service, run, "new:window")
    retry = _observe(service, run, "new:window")

    assert first.observation.aggregation_policy.window_seconds == 180 * _DAY_SECONDS
    assert first.observation.window_end - first.observation.window_start == timedelta(days=180)
    assert retry.observation.observation_id == first.observation.observation_id
    assert retry.support_count == 1


def test_five_independent_alerts_across_months_can_qualify_in_the_same_new_window():
    repository = InMemoryMemoryPatternRepository()
    service = _pattern_service(repository)
    for index, source_time in enumerate(["2026-03-30T00:00:00+00:00", "2026-04-30T00:00:00+00:00", "2026-05-30T00:00:00+00:00", "2026-06-30T00:00:00+00:00", "2026-08-30T00:00:00+00:00"], start=1):
        run = _new_bound_run(index)
        run.input_payload["event_time"] = source_time
        result = _observe(service, run, f"across-months:{index}")
        assert result.support_count == index
        assert result.candidate_created is (index == 5)
    assert result.candidate is not None
    assert result.observation.aggregation_policy.minimum_support == 5


def test_old_run_remains_thirty_days_and_does_not_rewrite_existing_observation():
    repository = InMemoryMemoryPatternRepository()
    run = _run(1)
    service = _pattern_service(repository)
    first = _observe(service, run, "old:window")
    snapshot = first.observation.model_dump(mode="json")
    retry = _observe(_pattern_service(repository), run, "old:window")

    assert retry.observation.aggregation_policy.window_seconds == 30 * _DAY_SECONDS
    assert retry.observation.model_dump(mode="json") == snapshot
    assert retry.support_count == 1


def test_explicit_operator_policy_still_overrides_frozen_run_default():
    repository = InMemoryMemoryPatternRepository()
    service = _pattern_service(repository, policy=MemoryPatternAggregationPolicy(window_seconds=7 * _DAY_SECONDS))
    result = _observe(service, _new_bound_run(1), "explicit:window")
    assert result.observation.window_end - result.observation.window_start == timedelta(days=7)


def test_existing_experiment_retains_its_full_event_range_and_policy_identity():
    repository = InMemoryMemoryPatternRepository()
    run = _new_bound_run(1)
    source_time = run.started_at
    # The experiment range, rather than an ordinary UTC bucket, owns this cohort.
    scope = MemoryPatternAccumulationScope(experiment_id="EXP-WINDOW", configuration_hash="a" * 64, event_start=source_time - timedelta(days=7), event_end=source_time + timedelta(days=200))
    result = _observe(_pattern_service(repository, accumulation_scope=scope), run, "experiment:window")

    assert result.observation.window_start == scope.event_start
    assert result.observation.window_end == scope.event_end
    assert result.observation.aggregation_policy.window_seconds == 30 * _DAY_SECONDS
