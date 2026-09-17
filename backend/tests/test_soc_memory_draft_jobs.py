from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_soc_memory_working_drafts import command, context, setup

from soc_agent.contracts import ProcessingJobStatus, SocMemoryBusinessLessonDraft, Verdict
from soc_agent.contracts.memory_drafts import MemoryDraftGenerateCommand
from soc_agent.core.errors import SocServiceConflictError
from soc_agent.core.memory_draft_jobs import SocMemoryDraftJobService


def generated(candidate_id):
    return SocMemoryBusinessLessonDraft.model_validate(
        {
            "candidate_id": candidate_id,
            "reviewer_verdict": "false_positive",
            "lesson": {
                "schema_version": "soc.memory_business_lesson.v2",
                "detection_scenario": "模拟规则报告了网络连接",
                "observed_event": "模拟来源包含业务访问线索",
                "conclusion": "模拟审核选择误报，业务事实仍由运营确认。",
                "business_rationale": ["模拟审核人已选择最终判断"],
                "applicability_conditions": ["沿用候选的适用条件"],
                "generalization_boundaries": ["仅适用相同的业务模式"],
                "invalidation_conditions": ["出现新的攻击行为时失效"],
                "handling_guidance": ["请审核草稿后再决定启用"],
            },
            "supporting_source_refs": ["D-001"],
            "rationale_sources": [{"statement": "模拟审核人已选择最终判断", "source_refs": ["D-001"]}],
            "source_catalog": [{"source_ref": "D-001", "source_kind": "reviewer_verdict", "label": "模拟人工判断", "value": "false_positive"}],
            "provenance": {"generator_id": "fake", "model_name": "mock", "prompt_version": "mock", "prompt_hash": "a" * 64, "response_hash": "b" * 64, "usage": {"input_tokens": 100, "output_tokens": 50}},
        }
    )


def fixture(tmp_path):
    repo, candidate, drafts = setup(tmp_path)
    calls = []

    def generate(candidate_id, **kwargs):
        calls.append(kwargs)
        return generated(candidate_id)

    service = SocMemoryDraftJobService(repository=repo, drafter_factory=lambda: SimpleNamespace(draft_business_lesson=generate))
    view = drafts.get(candidate.candidate_id, context=context())
    saved = drafts.save(candidate.candidate_id, command(view), context=context())
    request = MemoryDraftGenerateCommand(candidate_id=candidate.candidate_id, expected_version=saved.version, candidate_revision=saved.candidate_revision, regenerate=True)
    return repo, candidate, drafts, service, request, calls


def test_durable_draft_job_reuses_review_selection_never_approves_memory(tmp_path):
    repo, candidate, drafts, service, request, calls = fixture(tmp_path)
    before = repo.get_memory_candidate(candidate.candidate_id)
    job = service.submit_many([request], context=context("generate"), external_ref="experiment")[0]
    assert service.submit_many([request], context=context("generate"), external_ref="experiment")[0].job_id == job.job_id
    assert calls == []
    assert service.execute_one().status is ProcessingJobStatus.COMPLETED
    result = drafts.get(candidate.candidate_id, context=context()).draft
    assert result.version == 3 and result.last_generation.provenance.model_name == "mock"
    assert result.content.reviewer_context == "" and result.content.reviewer_verdict is Verdict.FALSE_POSITIVE
    assert "业务事实仍由运营确认" in result.content.conclusion
    assert len(calls) == 1 and calls[0]["reviewer_context"] is None
    assert repo.get_memory_candidate(candidate.candidate_id) == before
    assert repo.list_memory_records() == [] and repo.processing_jobs().list_callbacks(job.job_id) == []
    assert service.execute_one() is None


def test_no_verdict_or_silent_regeneration_does_not_enqueue(tmp_path):
    repo, candidate, drafts, service, request, calls = fixture(tmp_path)
    with pytest.raises(SocServiceConflictError, match="重新生成"):
        service.submit_many([request.model_copy(update={"regenerate": False})], context=context("implicit"))
    view = drafts.get(candidate.candidate_id, context=context())
    update = command(view, expected_version=1)
    update.content.reviewer_verdict = None
    drafts.save(candidate.candidate_id, update, context=context("clear-verdict"))
    with pytest.raises(SocServiceConflictError, match="最终判断"):
        service.submit_many([request.model_copy(update={"expected_version": 2})], context=context("missing-verdict"))
    assert service.execute_one() is None and not calls


def test_edit_during_generation_preserves_operator_text_and_saved_model_result(tmp_path):
    repo, candidate, drafts, service, request, calls = fixture(tmp_path)
    service.submit_many([request], context=context("generate"))

    def generate(candidate_id, **kwargs):
        current = drafts.get(candidate_id, context=context())
        drafts.save(candidate_id, command(current, expected_version=2, conclusion="人工新修改"), context=context("edit-during"))
        return generated(candidate_id)

    service._drafter_factory = lambda: SimpleNamespace(draft_business_lesson=generate)
    result = service.execute_one()
    assert result.status is ProcessingJobStatus.FAILED
    assert result.error_code == "draft_changed"
    assert result.result_payload["generated_draft"]["lesson"]["conclusion"]
    assert drafts.get(candidate.candidate_id, context=context()).draft.content.conclusion == "人工新修改"


def test_source_changes_before_claim_prevent_model_call(tmp_path):
    repo, candidate, drafts, service, request, calls = fixture(tmp_path)
    service.submit_many([request], context=context("generate"))
    candidate.content += "新增来源事实"
    repo.save_memory_candidate(candidate)
    result = service.execute_one()
    assert result.status is ProcessingJobStatus.FAILED and result.error_code == "draft_changed"
    assert not calls


def test_resume_persisted_generation_without_repeating_model(tmp_path):
    repo, candidate, drafts, service, request, calls = fixture(tmp_path)
    service.submit_many([request], context=context("generate"))
    jobs = repo.processing_jobs()
    job = jobs.claim_next(queue_name="deepseek-v4-flash", workload_kind="memory_lesson_draft", worker_id="old", lease_seconds=1)
    jobs.transition(job.job_id, worker_id="old", expected_status=ProcessingJobStatus.CLAIMED, target_status=ProcessingJobStatus.PRECHECKING, event_type="check")
    jobs.transition(job.job_id, worker_id="old", expected_status=ProcessingJobStatus.PRECHECKING, target_status=ProcessingJobStatus.ANALYZING, event_type="draft")
    jobs.transition(
        job.job_id,
        worker_id="old",
        expected_status=ProcessingJobStatus.ANALYZING,
        target_status=ProcessingJobStatus.PROJECTING,
        event_type="generated",
        result_payload={"generated_draft": generated(candidate.candidate_id).model_dump(mode="json")},
    )
    service.recover(now=datetime.now(UTC) + timedelta(seconds=2))
    # Recovery availability uses the observed clock; bring it to the same test clock.
    result = service.execute_one(now=datetime.now(UTC) + timedelta(seconds=3))
    assert result.status is ProcessingJobStatus.COMPLETED and result.attempt_count == 2
    assert not calls


def test_batch_reservations_are_atomic_on_one_stale_entry(tmp_path):
    repo, candidate, drafts, service, request, calls = fixture(tmp_path)
    with pytest.raises(SocServiceConflictError):
        service.submit_many([request, request.model_copy(update={"candidate_id": "missing"})], context=context("batch"))
    assert drafts.get(candidate.candidate_id, context=context()).draft.version == 1
    assert service.execute_one() is None


def test_drafting_observes_the_shared_corpus_worker_budget(tmp_path):
    from soc_agent.contracts import SocProcessingJobSubmission

    repo, candidate, drafts, service, request, calls = fixture(tmp_path)
    service._max_concurrency = 1
    jobs = repo.processing_jobs()
    job, _ = jobs.submit(SocProcessingJobSubmission(workload_kind="corpus_experiment", queue_name="deepseek-v4-flash", idempotency_key="busy-corpus", input_payload={}))
    jobs.claim_next(queue_name="deepseek-v4-flash", worker_id="corpus", lease_seconds=60, workload_kind="corpus_experiment")
    service.submit_many([request], context=context("generate"))
    assert service.execute_one() is None
    jobs.transition(job.job_id, worker_id="corpus", expected_status=ProcessingJobStatus.CLAIMED, target_status=ProcessingJobStatus.FAILED, event_type="test_finished")
    assert service.execute_one().status is ProcessingJobStatus.COMPLETED
    assert len(calls) == 1


def test_ambiguous_remote_call_requires_explicit_retry_not_automatic_rebilling(tmp_path):
    repo, candidate, drafts, service, request, calls = fixture(tmp_path)
    service.submit_many([request], context=context("generate"))
    jobs = repo.processing_jobs()
    now = datetime.now(UTC)
    job = jobs.claim_next(queue_name="deepseek-v4-flash", workload_kind="memory_lesson_draft", worker_id="old", lease_seconds=1, now=now - timedelta(seconds=10))
    # claim_next ignores a not-yet-available job; use the real claim then expire only recovery's clock.
    assert job is None
    job = jobs.claim_next(queue_name="deepseek-v4-flash", workload_kind="memory_lesson_draft", worker_id="old", lease_seconds=1)
    jobs.transition(job.job_id, worker_id="old", expected_status=ProcessingJobStatus.CLAIMED, target_status=ProcessingJobStatus.PRECHECKING, event_type="check")
    jobs.transition(job.job_id, worker_id="old", expected_status=ProcessingJobStatus.PRECHECKING, target_status=ProcessingJobStatus.ANALYZING, event_type="remote_started")
    service.recover(now=now + timedelta(seconds=3))
    failed = service.execute_one(now=now + timedelta(seconds=4))
    assert failed.error_code == "generation_uncertain" and calls == []
    jobs.retry_failed(job.job_id, expected_version=failed.version, actor_id="reviewer")
    completed = service.execute_one()
    assert completed.status is ProcessingJobStatus.COMPLETED and len(calls) == 1
