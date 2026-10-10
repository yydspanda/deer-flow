"""Rebalanced experiments preserve reviewed lineage without widening authority."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker
from test_soc_corpus_experiment_repository import experiment, members
from test_soc_memory_patterns import _run
from test_soc_memory_retrieval_v2 import _record

from soc_agent.contracts import SocMemoryCandidate, SocMemoryCandidateSource, SocProcessingJobSubmission
from soc_agent.contracts.corpus_experiments import CorpusExperiment, CorpusExperimentRebalance, CorpusRound, CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.db.corpus_experiments import CorpusExperimentConflict
from soc_agent.db.models import SocCorpusExperimentRow, SocMemoryCandidateRow, SocProcessingJobRow
from soc_agent.utils.hashing import stable_hash


def repository(_tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    return SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))


def _child(**changes):
    return experiment().model_copy(update={"experiment_id": "EXP-child", "plan_id": "c" * 64, "rebalance": CorpusExperimentRebalance(parent_experiment_id="EXP-test", plan_hash="d" * 64, **changes)})


def _memory(repo, *, memory_id="MEM-origin", experiment_id="EXP-test", status="confirmed", tenant_id="pingan", alert_id="0"):
    record = _record(memory_id, facets={"environment": ["dev-corpus-eval"]})
    source = SocMemoryCandidateSource(source_type="repeated_pattern", source_id=f"pattern:{memory_id}", alert_id=alert_id, metadata={"experiment_id": experiment_id})
    candidate = SocMemoryCandidate(
        candidate_id=record.source_candidate_id,
        candidate_type=record.memory_type,
        target_artifact=record.target_artifact,
        summary=record.summary,
        content=record.content,
        tenant_scope=tenant_id,
        tenant_id=tenant_id,
        status=status,
        source=source,
        evidence_refs=record.evidence_refs,
        validity=record.validity,
    )
    repo.save_memory_candidate(candidate)
    record = record.model_copy(update={"source": source, "tenant_id": tenant_id, "tenant_scope": tenant_id})
    repo.save_memory_record(record)
    return record


def _round(repo, *, purpose="memory", state="prepared", job_status=None, batch="learning"):
    round_ = CorpusRound(
        round_id="ROUND-origin",
        experiment_id="EXP-test",
        selection=CorpusRoundSelection(batch=batch),
        purpose=purpose,
        config_hash="e" * 64,
        config_snapshot={},
        created_by="operator",
        created_at=datetime.now(UTC),
        state=state,
        memory_mode="none" if purpose == "facts" else "snapshot",
        options={"normalization_review_mode": "apply"} if purpose == "facts" else {},
    )
    repo.corpus_experiments().create_round(round_)
    if job_status:
        job, _ = repo.processing_jobs().submit(SocProcessingJobSubmission(tenant_id="pingan", workload_kind="corpus_experiment", queue_name="test", idempotency_key="source", alert_id="0", input_payload={}))
        repo.corpus_experiments().attach_job(round_.round_id, "0", job.job_id, sequence_number=0)
        with repo._session_factory() as session:
            session.execute(update(SocProcessingJobRow).where(SocProcessingJobRow.job_id == job.job_id).values(status=job_status, run_id="RUN-source"))
            session.commit()
    return round_


def test_legacy_manifest_idempotency_survives_optional_lineage_fields(tmp_path):
    repo = repository(tmp_path)
    store = repo.corpus_experiments()
    original = experiment()
    store.prepare(original, members())
    old_payload = original.model_dump(mode="json", exclude={"rebalance", "fact_source_experiment_id"})
    identity = stable_hash({"experiment": {key: value for key, value in old_payload.items() if key not in {"member_count", "created_at", "created_by"}}, "members": [m.model_dump(mode="json") for m in members()]})
    with repo._session_factory() as session:
        session.execute(update(SocCorpusExperimentRow).values(record_payload=old_payload, manifest_hash=identity))
        session.commit()
    assert store.get_experiment(original.experiment_id).rebalance is None
    assert store.prepare(original, members()) is False


def test_inheritance_preserves_record_and_only_expands_confirmed_candidate_view(tmp_path):
    repo = repository(tmp_path)
    store = repo.corpus_experiments()
    store.prepare(experiment(), members())
    record = _memory(repo)
    _memory(repo, memory_id="MEM-pending", status="pending_review")
    _memory(repo, memory_id="MEM-rejected", status="rejected")
    child = _child(inherited_memory_ids=[record.memory_id])
    assert store.prepare(child, members())
    assert store.learning_memory_records(child.experiment_id) == [record]
    assert store.list_learning_candidates(child.experiment_id).total == 0
    assert store.list_learning_candidates(child.experiment_id, review_stage="all").total == 1
    assert store.learning_candidate_ids(child.experiment_id, [record.source_candidate_id]) == {record.source_candidate_id}
    assert len(repo.list_memory_records()) == 3
    changed = record.model_copy(update={"retrieval_enabled": False, "version": 2})
    repo.save_memory_record(changed)
    assert store.learning_memory_records(child.experiment_id) == [changed]
    with repo._session_factory() as session:
        session.execute(update(SocMemoryCandidateRow).where(SocMemoryCandidateRow.candidate_id == record.source_candidate_id).values(status="rejected"))
        session.commit()
    assert store.list_learning_candidates(child.experiment_id, review_stage="all").total == 0


@pytest.mark.parametrize("defect", ["missing_parent", "tenant", "environment", "source", "drop_member", "change_payload", "learning_to_validation", "foreign_memory", "unconfirmed_memory", "validation_memory"])
def test_prepare_rejects_forged_or_leaking_lineage(tmp_path, defect):
    repo = repository(tmp_path)
    store = repo.corpus_experiments()
    if defect != "missing_parent":
        store.prepare(experiment(), members())
    child, rows = _child(), members()
    if defect in {"tenant", "environment", "source"}:
        key, value = {"tenant": ("tenant_id", "foreign"), "environment": ("environment", "prd"), "source": ("source_identity", {"sha256": "f" * 64})}[defect]
        child = child.model_copy(update={key: value})
    elif defect == "drop_member":
        rows = rows[:-1]
    elif defect == "change_payload":
        rows[0] = rows[0].model_copy(update={"payload_hash": "f" * 64})
    elif defect == "learning_to_validation":
        rows[0] = rows[0].model_copy(update={"batch": "validation", "validation_tier": "main"})
    elif defect.endswith("memory"):
        record = _memory(repo, experiment_id="EXP-foreign" if defect == "foreign_memory" else "EXP-test", status="pending_review" if defect == "unconfirmed_memory" else "confirmed", alert_id="5" if defect == "validation_memory" else "0")
        child = _child(inherited_memory_ids=[record.memory_id])
    with pytest.raises((ValueError, CorpusExperimentConflict)):
        store.prepare(child, rows)
    assert store.get_experiment("EXP-child") is None


@pytest.mark.parametrize("activity", ["running_round", "active_job", "manual_pending"])
def test_rebalance_requires_quiescent_parent(tmp_path, activity):
    repo = repository(tmp_path)
    store = repo.corpus_experiments()
    store.prepare(experiment(), members())
    round_ = _round(repo, state="running" if activity == "running_round" else "prepared", job_status="claimed" if activity == "active_job" else "queued" if activity == "manual_pending" else None)
    if activity == "manual_pending":
        with repo._session_factory() as session:
            session.execute(update(SocProcessingJobRow).values(metadata_payload={"manual_dispatch": True}))
            session.commit()
    with pytest.raises(CorpusExperimentConflict, match="quiescent"):
        store.require_quiescent("EXP-test")
    with pytest.raises(CorpusExperimentConflict):
        store.prepare(_child(), members())
    assert store.get_round(round_.round_id) is not None


def test_facts_completion_is_not_an_importable_analysis(tmp_path):
    repo = repository(tmp_path)
    store = repo.corpus_experiments()
    store.prepare(experiment(), members())
    _round(repo, purpose="facts", job_status="completed")
    assert store.completed_run_refs("EXP-test", ["0"]) == {}
    with pytest.raises(CorpusExperimentConflict):
        store.prepare(_child(learning_run_imports={"0": "RUN-source"}), members())


def test_moved_validation_cannot_import_its_old_decision(tmp_path):
    repo = repository(tmp_path)
    store = repo.corpus_experiments()
    original = members()
    original[0] = original[0].model_copy(update={"batch": "validation", "validation_tier": "main"})
    store.prepare(experiment(), original)
    _round(repo, batch="validation", job_status="completed")
    repo.save_run(_run(0, tenant_id="pingan").model_copy(update={"run_id": "RUN-source", "alert_id": "0"}))
    with pytest.raises(CorpusExperimentConflict, match="original learning"):
        store.prepare(_child(learning_run_imports={"0": "RUN-source"}), members())


def test_completed_import_references_preserve_source_and_do_not_import_old_pending_queue(tmp_path):
    repo = repository(tmp_path)
    store = repo.corpus_experiments()
    store.prepare(experiment(), members())
    _round(repo, job_status="completed")
    source = _run(0, tenant_id="pingan").model_copy(update={"run_id": "RUN-source", "alert_id": "0"})
    repo.save_run(source)
    record = _memory(repo, status="pending_review")
    candidate = repo.get_memory_candidate(record.source_candidate_id)
    candidate.source.run_id = source.run_id
    repo.save_memory_candidate(candidate)
    assert store.completed_run_refs("EXP-test", ["0", "1"]) == {"0": source.run_id}
    child = _child(learning_run_imports={"0": source.run_id})
    assert store.prepare(child, members())
    round_ = CorpusRound(round_id="ROUND-import", experiment_id=child.experiment_id, selection=CorpusRoundSelection(batch="learning"), config_hash="e" * 64, config_snapshot={}, created_by="operator", created_at=datetime.now(UTC))
    store.create_round(round_)
    job, _ = repo.processing_jobs().submit(SocProcessingJobSubmission(tenant_id="pingan", workload_kind="corpus_experiment", queue_name="test", idempotency_key="import", alert_id="0", input_payload={}))
    store.attach_job(round_.round_id, "0", job.job_id, sequence_number=0)
    with repo._session_factory() as session:
        session.execute(update(SocProcessingJobRow).where(SocProcessingJobRow.job_id == job.job_id).values(status="completed", run_id=source.run_id))
        session.commit()
    assert store.list_learning_candidates("EXP-test").total == 1
    assert store.list_learning_candidates(child.experiment_id, review_stage="all").total == 0
    assert repo.get_run(source.run_id) == source


@pytest.mark.parametrize("defect", ["missing", "failed", "other_alert", "foreign_job"])
def test_import_rejects_unusable_saved_analysis(tmp_path, defect):
    repo = repository(tmp_path)
    store = repo.corpus_experiments()
    store.prepare(experiment(), members())
    _round(repo, job_status="completed")
    if defect != "missing":
        from soc_agent.contracts import AnalysisRunStatus

        source = _run(0, tenant_id="pingan").model_copy(update={"run_id": "RUN-source", "alert_id": "other" if defect == "other_alert" else "0", "status": AnalysisRunStatus.FAILED if defect == "failed" else AnalysisRunStatus.SUCCESS})
        repo.save_run(source)
    if defect == "foreign_job":
        with repo._session_factory() as session:
            session.execute(update(SocProcessingJobRow).values(tenant_id="foreign"))
            session.commit()
    with pytest.raises(CorpusExperimentConflict):
        store.prepare(_child(learning_run_imports={"0": "RUN-source"}), members())


def test_facts_experiment_binds_identical_parent_membership(tmp_path):
    store = repository(tmp_path).corpus_experiments()
    store.prepare(experiment(), members())
    child = experiment().model_copy(update={"experiment_id": "EXP-facts", "fact_source_experiment_id": "EXP-test"})
    assert store.prepare(child, members())
    assert store.get_experiment("EXP-facts").fact_source_experiment_id == "EXP-test"
    changed = members()
    changed[-1] = changed[-1].model_copy(update={"batch": "learning", "validation_tier": None})
    with pytest.raises(CorpusExperimentConflict):
        store.prepare(child.model_copy(update={"experiment_id": "EXP-facts-other"}), changed)


def test_rebalanced_members_accept_verified_fact_time_without_changing_source_identity(tmp_path):
    store = repository(tmp_path).corpus_experiments()
    original = members()
    store.prepare(experiment(), original)
    corrected = list(original)
    corrected[0] = original[0].model_copy(update={"event_time": original[0].event_time + timedelta(hours=1)})
    assert store.prepare(_child(), corrected)
    assert store.get_member("EXP-child", "0").event_time == corrected[0].event_time
    assert store.get_member("EXP-test", "0").event_time == original[0].event_time
    facts = experiment().model_copy(update={"experiment_id": "EXP-facts", "fact_source_experiment_id": "EXP-test"})
    with pytest.raises(CorpusExperimentConflict, match="identical source membership"):
        store.prepare(facts, corrected)


def test_facts_command_requires_frozen_non_decision_options():
    kwargs = {"experiment_id": "EXP-facts", "selection": CorpusRoundSelection(batch="learning"), "purpose": "facts"}
    with pytest.raises(ValidationError):
        CorpusRoundCreateCommand(**kwargs)
    command = CorpusRoundCreateCommand(**kwargs, memory_mode="none", options={"normalization_review_mode": "apply"})
    assert command.purpose == "facts"
    with pytest.raises(ValidationError):
        CorpusExperiment.model_validate({**experiment().model_dump(), "rebalance": _child().rebalance.model_dump(), "fact_source_experiment_id": "EXP-test"})
