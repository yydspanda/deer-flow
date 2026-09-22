"""Capacity adjustments stay available across legacy and drafting entry points."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from test_soc_corpus_batch_workbench import workbench as workbench
from test_soc_corpus_experiments import context
from test_soc_memory_draft_jobs import fixture as draft_fixture
from test_soc_memory_draft_jobs import generated
from test_soc_memory_working_drafts import context as draft_context

from soc_agent.contracts import ProcessingJobStatus, SocProcessingJobSubmission
from soc_agent.core.memory_draft_jobs import SocMemoryDraftJobService
from soc_agent.demo.corpus_capacity import CorpusCapacity
from soc_agent.demo.corpus_workbench import SocCorpusWorkbenchCapacityError


def test_legacy_activity_shows_reduction_and_drains_existing_claims(workbench):
    workbench.capacity = capacity = CorpusCapacity(8)
    first = workbench._reserve_execution("0", context=context())
    second = workbench._reserve_execution("1", context=context())
    try:
        capacity.set_limit(1, actor_id="host")
        activity = workbench.get_activity()
        assert (activity.active_count, activity.max_concurrent_executions, activity.available_slots) == (2, 1, 0)
        with pytest.raises(SocCorpusWorkbenchCapacityError):
            workbench._reserve_execution("2", context=context())
        workbench._release_execution(first)
        with pytest.raises(SocCorpusWorkbenchCapacityError):
            workbench._reserve_execution("2", context=context())
        workbench._release_execution(second)
        third = workbench._reserve_execution("2", context=context())
        assert workbench.get_activity().active_count == 1
        workbench._release_execution(third)
        assert workbench.get_activity().available_slots == 1
    finally:
        workbench._release_execution(first)
        workbench._release_execution(second)


def test_slow_legacy_database_read_does_not_block_capacity_save(workbench, monkeypatch):
    reading, release_read, changed = Event(), Event(), Event()

    def blocked_read(**_):
        reading.set()
        assert release_read.wait(3)
        return []

    monkeypatch.setattr(workbench._repository, "corpus_experiments", lambda: SimpleNamespace(list_experiments=blocked_read))

    def change_limit():
        workbench.capacity.set_limit(1, actor_id="host")
        changed.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        claim = pool.submit(workbench._reserve_execution, "0", context=context())
        assert reading.wait(2)
        update = pool.submit(change_limit)
        try:
            assert changed.wait(0.5), "A waiting SQLite read must not hold the capacity settings guard"
        finally:
            release_read.set()
            update.result(timeout=2)
            workbench._release_execution(claim.result(timeout=2))


def test_draft_jobs_use_current_shared_capacity_and_release_guard_before_generation(tmp_path):
    repo, candidate, drafts, original, request, calls = draft_fixture(tmp_path)
    capacity = CorpusCapacity(8)
    jobs = repo.processing_jobs()
    jobs.submit(SocProcessingJobSubmission(workload_kind="corpus_experiment", queue_name="deepseek-v4-flash", idempotency_key="busy-corpus", input_payload={}))
    jobs.claim_next(queue_name="deepseek-v4-flash", worker_id="corpus", lease_seconds=60, workload_kind="corpus_experiment")
    generated_ids = []

    def generate(candidate_id, **_):
        assert jobs.active_workload_count(["corpus_experiment", "memory_lesson_draft"]) == 2
        changed = Event()

        def update():
            capacity.set_limit(1, actor_id="host")
            changed.set()

        thread = Thread(target=update, daemon=True)
        thread.start()
        # A settings save remains independent of an in-flight draft model call.
        assert changed.wait(2)
        thread.join(timeout=2)
        generated_ids.append(candidate_id)
        return generated(candidate_id)

    service = SocMemoryDraftJobService(repository=repo, drafter_factory=lambda: SimpleNamespace(draft_business_lesson=generate), max_concurrency=8, admission=capacity.admission)
    job = service.submit_many([request], context=draft_context("generate"))[0]
    capacity.set_limit(1, actor_id="host")
    assert service.execute_one() is None
    assert jobs.get(job.job_id).status is ProcessingJobStatus.QUEUED
    assert not generated_ids
    capacity.set_limit(2, actor_id="host")
    assert service.execute_one().status is ProcessingJobStatus.COMPLETED
    assert generated_ids == [candidate.candidate_id]
    assert capacity.max_concurrency == 1
