"""Pending analyst learning must not invalidate a frozen validation round."""

from datetime import UTC, datetime, timedelta

import pytest
from test_soc_corpus_experiment_repository import repository
from test_soc_corpus_experiments import context, prepare, reviewed_memory, service
from test_soc_memory_experiment_accumulation import _experimental
from test_soc_memory_patterns import _observe, _run

from soc_agent.contracts import Decision, ProcessingJobStatus, SocMemoryCandidateStatus, SocMemoryRetrievalActivationCommand, SocMemoryRunPromotionCommand
from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.core import SocMemoryService, SocReviewService
from soc_agent.db.corpus_experiments import CorpusExperimentConflict


def _promotion_source(repo):
    run = _run(999, tenant_id="pingan", detection_key="generic:detector:manual-promotion")
    run.decision = Decision(
        verdict=run.analysis.verdict,
        confidence=run.analysis.confidence,
        suggested_action=run.analysis.recommended_action,
        needs_review=True,
        reason=run.analysis.reason,
    )
    repo.save_run(run)
    _observe(_experimental(repo), run, transport_ref="corpus:manual-promotion")
    review = SocReviewService(
        repository=repo,
        memory_candidate_repository=repo,
        memory_record_repository=repo,
        memory_pattern_observation_repository=repo,
    )
    return review, SocMemoryRunPromotionCommand(run_id=run.run_id)


@pytest.mark.parametrize("existing_candidate", [False, True])
def test_manual_promotion_does_not_block_running_validation(tmp_path, existing_candidate):
    repo = repository(tmp_path)
    prepare(repo)
    record = reviewed_memory(repo)
    review, command = _promotion_source(repo)
    prior = review.promote_run_to_memory(command, context=context()) if existing_candidate else None
    record_before = repo.get_memory_record(record.memory_id).model_dump(mode="json")
    run_before = repo.get_run(command.run_id).model_dump(mode="json")
    calls = []
    svc = service(repo, calls)
    round_ = svc.create_round(
        CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="validation")),
        context=context(),
    )
    assert [item.memory_id for item in round_.memory_snapshot] == [record.memory_id]
    svc.start(round_.round_id, context=context())
    assert svc.execute_one(round_.round_id)

    promoted = review.promote_run_to_memory(
        command.model_copy(update={"note": "Analyst supplied an optional business note."}),
        context=context(),
    )

    assert promoted.memory_candidate.status is SocMemoryCandidateStatus.PENDING_REVIEW
    if prior is not None:
        assert promoted.memory_candidate.candidate_id == prior.memory_candidate.candidate_id
    assert repo.get_memory_record_by_candidate_id(promoted.memory_candidate.candidate_id) is None
    assert repo.get_run(command.run_id).model_dump(mode="json") == run_before
    assert repo.get_memory_record(record.memory_id).model_dump(mode="json") == record_before
    assert svc.store.governance_hash("pingan") == round_.governance_hash
    assert svc.snapshot_problem(round_) is None
    assert svc.execute_one(round_.round_id)
    assert svc.store.get_round(round_.round_id).state == "completed"
    assert svc.store.get_round(round_.round_id).memory_snapshot == round_.memory_snapshot
    assert len(calls) == 2
    assert svc.store.round_progress(round_.round_id).completed_count == 2


def test_pending_candidate_rejection_does_not_change_frozen_governance(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    record = reviewed_memory(repo)
    review, command = _promotion_source(repo)
    candidate = review.promote_run_to_memory(command, context=context()).memory_candidate
    svc = service(repo, [])
    round_ = svc.create_round(
        CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="validation")),
        context=context(),
    )
    record_before = repo.get_memory_record(record.memory_id).model_dump(mode="json")
    svc.start(round_.round_id, context=context())
    repo.save_memory_candidate(candidate.model_copy(update={"status": SocMemoryCandidateStatus.REJECTED, "updated_at": datetime.now(UTC)}))

    assert svc.store.governance_hash("pingan") == round_.governance_hash
    assert repo.get_memory_record(record.memory_id).model_dump(mode="json") == record_before
    assert svc.snapshot_problem(round_) is None
    assert svc.execute_one(round_.round_id)
    assert svc.store.get_round(round_.round_id).state == "running"


def test_revoked_then_restored_memory_requires_remaining_only_child_round(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    record = reviewed_memory(repo)
    calls = []
    svc = service(repo, calls)
    parent = svc.create_round(
        CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="validation")),
        context=context(),
    )
    svc.start(parent.round_id, context=context())
    assert svc.execute_one(parent.round_id)
    memory = SocMemoryService(record_repository=repo)
    disabled = memory.set_retrieval_activation(
        SocMemoryRetrievalActivationCommand(
            memory_id=record.memory_id,
            action="disable",
            expected_record_version=record.version,
            reason="Operator temporarily revoked this reviewed memory.",
        ),
        context=context(),
    ).record
    assert svc.execute_one(parent.round_id) is False
    assert svc.store.get_round(parent.round_id).state_reason == "memory_governance_changed"
    restored = memory.set_retrieval_activation(
        SocMemoryRetrievalActivationCommand(
            memory_id=record.memory_id,
            action="enable",
            expected_record_version=disabled.version,
            reason="Operator restored the reviewed memory after checking it.",
            activation_valid_until=datetime.now(UTC) + timedelta(days=30),
            review_after_days=7,
        ),
        context=context(),
    ).record
    assert restored.retrieval_enabled is True
    assert restored.version == record.version + 2
    assert (restored.content_hash, restored.facets_hash) == (record.content_hash, record.facets_hash)
    with pytest.raises(CorpusExperimentConflict, match="memory_governance_changed"):
        svc.start(parent.round_id, context=context())

    original_round = svc.store.get_round(parent.round_id).model_dump(mode="json")
    original_items = svc.store.list_round_items(parent.round_id)
    original_payload = original_items.model_dump(mode="json")
    queued = [item.alert_id for item in original_items.items if item.job.status is ProcessingJobStatus.QUEUED]
    assert len(queued) == 1
    assert sum(item.job.status is ProcessingJobStatus.COMPLETED for item in original_items.items) == 1
    child = svc.create_round(
        CorpusRoundCreateCommand(
            experiment_id=parent.experiment_id,
            selection=parent.selection.model_copy(update={"alert_ids": queued}),
            purpose=parent.purpose,
            options=parent.options,
            memory_mode=parent.memory_mode,
            parent_round_id=parent.round_id,
        ),
        context=context(),
    )
    assert child.parent_round_id == parent.round_id
    assert [entry.version for entry in child.memory_snapshot] == [restored.version]
    assert [entry.version for entry in parent.memory_snapshot] == [record.version]
    svc.start(child.round_id, context=context())
    assert svc.execute_one(child.round_id)

    assert svc.store.get_round(child.round_id).state == "completed"
    assert svc.store.round_progress(child.round_id).selected_count == 1
    assert svc.store.round_progress(child.round_id).completed_count == 1
    assert len(calls) == 2
    assert calls[-1][1] == queued[0]
    assert svc.store.get_round(parent.round_id).model_dump(mode="json") == original_round
    assert svc.store.list_round_items(parent.round_id).model_dump(mode="json") == original_payload
