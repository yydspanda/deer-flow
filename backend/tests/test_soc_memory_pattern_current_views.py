from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from test_soc_corpus_list_queries import _row
from test_soc_effectiveness import _summary
from test_soc_memory_pattern_continuous_scope import _retry, _service
from test_soc_memory_patterns import _context, _observe, _run

from soc_agent.contracts import AnalysisContextCatalogItem, SocEffectivenessScope, SocMemoryCandidateReviewCommand, SocMemoryCandidateReviewDecision, SocMemoryCandidateStatus, SocRuleEffectivenessSelector
from soc_agent.core import SocMemoryService, SocServiceConflictError
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.db.corpus_lists import SocCorpusListQueries
from soc_agent.db.effectiveness import SqlAlchemySocEffectivenessRepository
from soc_agent.memory import InMemoryMemoryPatternRepository
from soc_agent.memory.learning import resolve_learning_candidate
from soc_agent.utils.hashing import stable_hash


@pytest.mark.parametrize("decision", [SocMemoryCandidateReviewDecision.CONFIRM, SocMemoryCandidateReviewDecision.CONFIRM_CANDIDATE, SocMemoryCandidateReviewDecision.REOPEN])
def test_invalidated_candidate_cannot_be_confirmed_or_reopened(decision):
    repository = InMemoryMemoryPatternRepository()
    patterns = _service(repository)
    for index in range(1, 6):
        result = _observe(patterns, _run(index), transport_ref=f"source:{index}")
    stale = result.candidate.model_copy(update={"status": SocMemoryCandidateStatus.SUPERSEDED, "metadata": {**result.candidate.metadata, "source_observations_replaced": [result.observation.observation_id]}})
    repository.save_memory_candidate(stale)
    memory = SocMemoryService(candidate_repository=repository, record_repository=repository)
    with pytest.raises(SocServiceConflictError, match="source observations were replaced"):
        memory.review_candidate(SocMemoryCandidateReviewCommand(candidate_id=stale.candidate_id, decision=decision, reason="Attempt to use an outdated snapshot"), context=_context("soc_admin"))
    assert repository.get_memory_candidate(stale.candidate_id) == stale


def test_invalidated_candidate_is_not_resolved_as_current_learning_work():
    repository = InMemoryMemoryPatternRepository()
    patterns = _service(repository)
    for index in range(1, 6):
        result = _observe(patterns, _run(index), transport_ref=f"source:{index}")
    candidate = result.candidate
    stale = candidate.model_copy(update={"status": SocMemoryCandidateStatus.SUPERSEDED, "metadata": {**candidate.metadata, "source_observations_replaced": [result.observation.observation_id]}})
    repository.save_memory_candidate(stale)
    assert resolve_learning_candidate(repository, candidate) is None


def test_current_corpus_counts_and_revision_exclude_replaced_observations():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    repository = SqlAlchemyAlertRepository(sessions)
    service = _service(repository)
    run = _run(1)
    old = _observe(service, run, transport_ref="first").observation
    queries = SocCorpusListQueries(sessions)
    row = {**_row(1), "alert_id": run.alert_id, "input_hash": run.input_hash}
    queries.insert_missing([row])
    before = queries.source_revisions("catalog", tenant_id="tenant-a", environment="dev")
    current = _observe(service, _retry(run, detection_key="generic:changed"), transport_ref="corrected").observation
    assert queries.aggregation_counts() == {current.aggregation_key: 1}
    assert queries.support_count(old.aggregation_key) == 0
    assert queries.support_count(current.aggregation_key) == 1
    after = queries.source_revisions("catalog", tenant_id="tenant-a", environment="dev")
    assert after != before
    assert after[run.alert_id] == stable_hash([["observation", current.observation_id, current.aggregation_key, current.created_at.replace(tzinfo=None).isoformat()]])
    assert repository.get_memory_pattern_observation(old.observation_id) == old
    engine.dispose()


def test_effectiveness_behavior_groups_use_only_current_observations():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    repository = SqlAlchemyAlertRepository(sessions)
    service = _service(repository)
    run = _run(1)
    _observe(service, run, transport_ref="first")
    retry = _retry(run, detection_key="generic:changed")
    current = _observe(service, retry, transport_ref="corrected").observation
    with sessions() as session:
        for source in (run, retry):
            summary = _summary(source.run_id, source.alert_id, source.started_at)
            summary.tenant_id = "tenant-a"
            session.add(summary)
        session.commit()
    groups = SqlAlchemySocEffectivenessRepository(sessions).read_behavior_group_aggregates(
        SocEffectivenessScope(window_start=datetime(2026, 1, 1, tzinfo=UTC), window_end=datetime(2027, 1, 1, tzinfo=UTC)),
        SocRuleEffectivenessSelector(tenant_id="tenant-a", source_type="nids", source_system="zeus", detection_key="pingan:nids:RC-MIXED-1"),
    )
    assert len(groups) == 1
    assert groups[0].lineage_key == current.lineage_key
    assert groups[0].sample_count == 1
    engine.dispose()


def test_effectiveness_does_not_present_invalidated_snapshot_as_current_candidate():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    repository = SqlAlchemyAlertRepository(sessions)
    service = _service(repository)
    runs = [_run(index) for index in range(1, 6)]
    for run in runs:
        result = _observe(service, run, transport_ref=f"initial:{run.alert_id}")
    old_candidate = result.candidate
    retry = _retry(runs[-1])
    retry.llm_analysis_request.context_catalog = [AnalysisContextCatalogItem(context_ref="M-000000000001", kind="confirmed_memory", label="Reviewed", source_id="MEM-OLD@v1", summary="Existing lesson")]
    replacement = _observe(service, retry, transport_ref="memory-supported-retry")
    assert replacement.candidate is None
    assert repository.get_memory_candidate(old_candidate.candidate_id).status is SocMemoryCandidateStatus.SUPERSEDED
    with sessions() as session:
        for run in [*runs, retry]:
            summary = _summary(run.run_id, run.alert_id, run.started_at)
            summary.tenant_id = "tenant-a"
            session.add(summary)
        session.commit()
    groups = SqlAlchemySocEffectivenessRepository(sessions).read_behavior_group_aggregates(
        SocEffectivenessScope(window_start=datetime(2026, 1, 1, tzinfo=UTC), window_end=datetime(2027, 1, 1, tzinfo=UTC)),
        SocRuleEffectivenessSelector(tenant_id="tenant-a", source_type="nids", source_system="zeus", detection_key="pingan:nids:RC-MIXED-1"),
    )
    assert len(groups) == 1
    assert groups[0].sample_count == 5
    assert groups[0].verdict_counts == {"suspicious": 4}
    assert groups[0].candidate_id is None
    engine.dispose()
