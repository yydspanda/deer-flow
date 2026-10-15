from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event, local

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from test_soc_memory_patterns import _context, _observe, _run

from soc_agent.contracts import AnalysisContextCatalogItem, MemoryPatternAggregationPolicy, SocMemoryCandidateStatus, SocMemoryRecord, Verdict
from soc_agent.contracts.memory_patterns import MemoryPatternAccumulationScope
from soc_agent.core import SocMemoryPatternService, SocServiceConflictError
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.memory import InMemoryMemoryPatternRepository


@pytest.fixture(params=["memory", "sqlite"])
def repository(request):
    if request.param == "memory":
        yield InMemoryMemoryPatternRepository()
        return
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    yield SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    engine.dispose()


def _scope(*, continuous=True, generation="generation-a", configuration=None):
    return MemoryPatternAccumulationScope(
        experiment_id="EXP-CONTINUOUS",
        learning_generation=generation,
        configuration_hash=configuration,
        event_start=datetime(2026, 1, 1, tzinfo=UTC),
        event_end=datetime(2027, 1, 1, tzinfo=UTC),
        **({"accumulation_mode": "continuous"} if continuous else {}),
    )


def _service(repository, scope=None):
    return SocMemoryPatternService(repository=repository, candidate_repository=repository, accumulation_scope=scope or _scope(), policy=MemoryPatternAggregationPolicy())


def _retry(run, *, verdict=None, detection_key=None):
    run = run.model_copy(deep=True)
    run.run_id += "-RETRY"
    run.started_at += timedelta(hours=1)
    if verdict:
        run.analysis.verdict = verdict
    if detection_key:
        run.llm_analysis_request.detection.detection_key = detection_key
    return run


def test_learning_four_plus_validation_one_share_one_continuous_cohort(repository):
    for index in range(1, 5):
        result = _observe(_service(repository), _run(index), transport_ref=f"learning:{index}")
        assert result.support_count == index
        assert result.candidate is None
    result = _observe(_service(repository), _run(5), transport_ref="validation:5")
    assert result.support_count == 5
    assert result.candidate.status is SocMemoryCandidateStatus.PENDING_REVIEW


def test_explicit_continuous_projection_carries_legacy_learning_support_once(repository):
    legacy = _service(repository, _scope(continuous=False, configuration="a" * 64))
    old = [_observe(legacy, _run(index), transport_ref=f"legacy:{index}").observation for index in range(1, 5)]
    for index in range(1, 5):
        _observe(_service(repository), _run(index), transport_ref=f"continuous:{index}")
    result = _observe(_service(repository), _run(5), transport_ref="validation:5")
    assert result.support_count == 5
    assert result.candidate_created
    assert len(repository.list_memory_pattern_observations()) == 5
    assert all(repository.get_memory_pattern_observation(item.observation_id) == item for item in old)
    assert all(not repository.is_memory_pattern_observation_current(item.observation_id) for item in old)


def test_replacing_source_changes_current_behavior_without_duplicate_support(repository):
    service = _service(repository)
    original = _run(1)
    old = _observe(service, original, transport_ref="first").observation
    result = _observe(service, _retry(original, detection_key="generic:detector:different"), transport_ref="retry")
    assert result.observation.aggregation_key != old.aggregation_key
    assert result.support_count == 1
    assert len(repository.list_memory_pattern_observations()) == 1
    assert repository.get_memory_pattern_observation(old.observation_id) == old
    with pytest.raises(SocServiceConflictError, match="superseded"):
        _observe(service, original, transport_ref="first")


def test_same_behavior_retry_replaces_conclusion_and_invalidates_pending_snapshot(repository):
    service = _service(repository)
    for index in range(1, 6):
        old = _observe(service, _run(index), transport_ref=f"initial:{index}")
    old_candidate = old.candidate
    replacement = _observe(service, _retry(_run(5), verdict=Verdict.FALSE_POSITIVE), transport_ref="corrected")
    assert replacement.support_count == 5
    assert replacement.cohort_quality.verdict_counts["false_positive"] == 1
    assert repository.get_memory_candidate(old_candidate.candidate_id).status is SocMemoryCandidateStatus.SUPERSEDED
    assert replacement.candidate is not None
    assert replacement.candidate.candidate_id != old_candidate.candidate_id


def test_memory_reference_is_recurrence_without_independent_conclusion(repository):
    service = _service(repository)
    for index in range(1, 5):
        _observe(service, _run(index), transport_ref=f"independent:{index}")
    run = _run(5)
    run.llm_analysis_request.context_catalog = [AnalysisContextCatalogItem(context_ref="M-000000000001", kind="confirmed_memory", label="Reviewed", source_id="MEM-OLD@v1", summary="Existing lesson")]
    result = _observe(service, run, transport_ref="reference")
    assert result.support_count == 5
    assert result.observation.lesson is None
    assert result.observation.metadata["conclusion_origin"] == "memory_reference"
    assert result.cohort_quality.conclusive_count == 4
    assert result.candidate is None


def test_continuous_scope_never_replaces_another_generation(repository):
    old = _observe(_service(repository), _run(1), transport_ref="old")
    new = _observe(_service(repository, _scope(generation="generation-b")), _retry(_run(1)), transport_ref="new")
    assert old.observation.aggregation_key != new.observation.aggregation_key
    assert repository.is_memory_pattern_observation_current(old.observation.observation_id)
    assert len(repository.list_memory_pattern_observations()) == 2


def test_legacy_scope_serialization_keeps_original_identity():
    assert "accumulation_mode" not in _scope(continuous=False).model_dump(mode="json")


def test_continuous_scope_rejects_configuration_partitioning():
    with pytest.raises(ValueError, match="shares one scope"):
        _scope(configuration="a" * 64)


def test_replacement_preserves_confirmed_memory_and_marks_candidate_for_review(repository):
    service = _service(repository)
    for index in range(1, 6):
        old = _observe(service, _run(index), transport_ref=f"initial:{index}")
    candidate = old.candidate.model_copy(update={"status": SocMemoryCandidateStatus.CONFIRMED})
    repository.save_memory_candidate(candidate)
    record = SocMemoryRecord(
        memory_type=candidate.candidate_type,
        target_artifact=candidate.target_artifact,
        tenant_id=candidate.tenant_id,
        tenant_scope=candidate.tenant_scope,
        source_candidate_id=candidate.candidate_id,
        source=candidate.source,
        summary=candidate.summary,
        content=candidate.content,
        evidence_refs=candidate.evidence_refs,
        validity=candidate.validity,
        content_hash="reviewed-content",
        facets_hash="reviewed-facets",
        created_by=_context().actor,
    )
    repository.save_memory_record(record)
    _observe(service, _retry(_run(5)), transport_ref="corrected")
    assert repository.get_memory_record(record.memory_id) == record
    assert repository.get_memory_candidate(candidate.candidate_id).status is SocMemoryCandidateStatus.CONFIRMED
    assert repository.get_memory_candidate(candidate.candidate_id).metadata["source_replacement_review_required"]


def test_old_run_with_new_transport_cannot_replace_a_newer_current_run(repository):
    service = _service(repository)
    original = _run(1)
    _observe(service, original, transport_ref="original")
    current = _observe(service, _retry(original), transport_ref="newer")
    with pytest.raises(SocServiceConflictError, match="newer completed Run"):
        _observe(service, original, transport_ref="late-old-submit")
    assert repository.list_memory_pattern_observations()[0].observation_id == current.observation.observation_id


@pytest.mark.parametrize("newer_first", [False, True])
def test_concurrent_first_writes_keep_one_current_source_and_never_regress(tmp_path, monkeypatch, newer_first):
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "concurrent-patterns.db"), connect_args={"check_same_thread": False, "timeout": 10})
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    first_read, second_reached_boundary = Event(), Event()
    worker = local()
    find_predecessors = SocMemoryPatternService._continuous_predecessors
    lock_governance = SqlAlchemyAlertRepository.lock_memory_governance

    def coordinated_lock(self):
        if worker.index == 1:
            second_reached_boundary.set()
        lock_governance(self)

    def coordinated_predecessors(self, command):
        predecessors = find_predecessors(self, command)
        if worker.index == 0:
            first_read.set()
            assert second_reached_boundary.wait(10), "second writer never reached its admission boundary"
        else:
            # Without a pre-read lock, this writer also sees an empty scope.
            second_reached_boundary.set()
        return predecessors

    monkeypatch.setattr(SqlAlchemyAlertRepository, "lock_memory_governance", coordinated_lock)
    monkeypatch.setattr(SocMemoryPatternService, "_continuous_predecessors", coordinated_predecessors)
    older = _run(1)
    newer = _retry(older, detection_key="generic:detector:corrected")
    runs = (newer, older) if newer_first else (older, newer)

    def write(index):
        worker.index = index
        try:
            return _observe(_service(repository), runs[index], transport_ref=f"concurrent:{index}")
        except SocServiceConflictError as exc:
            return exc

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(write, 0)
            assert first_read.wait(10), "first writer never read its empty scope"
            second = pool.submit(write, 1)
            results = [first.result(timeout=15), second.result(timeout=15)]
        assert not isinstance(results[0], Exception)
        if newer_first:
            assert isinstance(results[1], SocServiceConflictError)
            assert "newer completed Run" in str(results[1])
        else:
            assert not isinstance(results[1], Exception)
        current = repository.list_memory_pattern_observations()
        assert len(current) == 1
        assert current[0].source.run_id == newer.run_id
        history = repository.list_memory_pattern_observations(include_superseded=True)
        assert len(history) == (1 if newer_first else 2)
        assert all(repository.is_memory_pattern_observation_current(item.observation_id) == (item.source.run_id == newer.run_id) for item in history)
    finally:
        engine.dispose()
