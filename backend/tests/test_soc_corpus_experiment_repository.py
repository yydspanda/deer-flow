"""Durable experiment membership and round state, without Runtime/model calls."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.contracts import ProcessingJobStatus, SocProcessingJobSubmission
from soc_agent.contracts.corpus_experiments import CorpusExperiment, CorpusExperimentMember, CorpusRound, CorpusRoundSelection
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.db.corpus_experiments import CorpusExperimentConflict


def repository(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'experiments.db'}")
    create_soc_tables(engine)
    return SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))


def experiment():
    return CorpusExperiment(
        experiment_id="EXP-test", plan_id="a" * 64, name="Test experiment", tenant_id="pingan", environment="dev-corpus-eval", source_identity={"sha256": "b" * 64}, created_by="operator", created_at=datetime(2026, 9, 18, tzinfo=UTC)
    )


def members(count=12):
    return [
        CorpusExperimentMember(
            alert_id=str(i),
            payload_hash=f"{i:064x}",
            source_index=i,
            group_id="group-a" if i < 6 else "group-b",
            sequence_number=i,
            position_in_group=i % 6,
            batch="learning" if i % 6 < 5 else "validation",
            validation_tier=None if i % 6 < 5 else "main",
            event_time=datetime(2026, 7, 1, tzinfo=UTC) + timedelta(days=i),
            rule_code="rule-a",
            reason="group_early_samples",
        )
        for i in range(count)
    ]


def test_prepare_idempotency_and_selected_members_are_durable(tmp_path):
    repo = repository(tmp_path)
    store = repo.corpus_experiments()
    assert store.prepare(experiment(), members()) is True
    assert store.prepare(experiment(), members()) is False
    assert store.get_experiment("EXP-test").member_count == 12
    selection = CorpusRoundSelection(batch="validation", scope="reuse")
    page = store.list_members("EXP-test", selection=selection, limit=1, offset=1)
    assert page.total == 2
    assert [row.alert_id for row in page.items] == ["11"]
    assert store.list_members("EXP-test", selection=CorpusRoundSelection(batch="learning", group_ids=["group-b"])).total == 5
    with pytest.raises(CorpusExperimentConflict):
        store.prepare(experiment(), members()[:-1])


def test_round_membership_and_jobs_commit_atomically_and_keep_history(tmp_path):
    repo = repository(tmp_path)
    store = repo.corpus_experiments()
    store.prepare(experiment(), members())
    round_ = CorpusRound(
        round_id="ROUND-1", experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning"), config_hash="c" * 64, config_snapshot={"mock": True}, created_by="operator", created_at=datetime.now(UTC), execution_limit=5
    )
    with pytest.raises(RuntimeError):
        with repo.mutation_transaction() as tx:
            tx.corpus_experiments().create_round(round_)
            raise RuntimeError("crash before members")
    assert store.get_round("ROUND-1") is None
    with repo.mutation_transaction() as tx:
        tx.corpus_experiments().create_round(round_)
        for row in members()[:5]:
            job, _ = tx.processing_jobs().submit(
                SocProcessingJobSubmission(
                    workload_kind="corpus_experiment", queue_name="deepseek-v4-flash", idempotency_key=f"round-1:{row.alert_id}", alert_id=row.alert_id, concurrency_key=f"pingan:dev:{row.alert_id}", input_payload={"alert_id": row.alert_id}
                )
            )
            tx.corpus_experiments().attach_job("ROUND-1", row.alert_id, job.job_id, sequence_number=row.sequence_number)
    assert store.round_progress("ROUND-1").counts == {"queued": 5}
    assert store.round_progress("ROUND-1").selected_count == 5
    changed = store.set_round_state("ROUND-1", expected_version=1, state="running", reason=None)
    assert changed.version == 2
    with pytest.raises(CorpusExperimentConflict):
        store.set_round_state("ROUND-1", expected_version=1, state="paused", reason="stale")
    assert store.eligible_job_ids("ROUND-1") == [store.list_round_items("ROUND-1").items[0].job_id]
    job = repo.processing_jobs().claim_next(queue_name="deepseek-v4-flash", workload_kind="corpus_experiment", worker_id="worker", lease_seconds=60, job_ids=store.eligible_job_ids("ROUND-1"))
    assert job.status == ProcessingJobStatus.CLAIMED
    assert store.eligible_job_ids("ROUND-1") == []
    store.set_round_state("ROUND-1", expected_version=2, state="paused", reason="operator")
    assert store.eligible_job_ids("ROUND-1") == []
    assert store.list_round_items("ROUND-1").items[0].job.status == ProcessingJobStatus.CLAIMED


def test_pagination_and_counts_do_not_truncate_at_ten_thousand(tmp_path):
    repo = repository(tmp_path)
    base = members()[0]
    rows = [base.model_copy(update={"alert_id": str(i), "source_index": i, "sequence_number": i, "payload_hash": f"{i:064x}"}) for i in range(10005)]
    store = repo.corpus_experiments()
    store.prepare(experiment(), rows)
    page = store.list_members("EXP-test", selection=CorpusRoundSelection(batch="learning"), offset=10000, limit=20)
    assert page.total == 10005
    assert [row.alert_id for row in page.items] == [str(i) for i in range(10000, 10005)]
