"""Durable reviewer-requested drafting on the existing SOC job queue."""

from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Event, Thread
from uuid import uuid4

from soc_agent.contracts import ProcessingJobStatus, ServiceRequestContext, SocMemoryBusinessLessonDraft, SocMutationOperation, SocProcessingJobSubmission
from soc_agent.contracts.memory_drafts import MemoryDraftGenerateCommand
from soc_agent.core.access_control import SOC_MEMORY_REVIEWER_ROLES, require_actor_roles
from soc_agent.core.errors import SocServiceConflictError
from soc_agent.core.memory_working_drafts import EDITABLE, candidate_draft_revision
from soc_agent.core.mutation_audit import build_mutation_audit, validate_mutation_retry
from soc_agent.db.jobs import ProcessingJobConflictError
from soc_agent.utils.hashing import stable_hash

DRAFT_WORKLOAD = "memory_lesson_draft"
DRAFT_QUEUE = "deepseek-v4-flash"


class SocMemoryDraftJobService:
    def __init__(self, *, repository, drafter_factory, max_concurrency=3, lease_seconds=120):
        self.repository = repository
        self.jobs = repository.processing_jobs()
        self._drafter_factory = drafter_factory
        self._max_concurrency = max_concurrency
        self._lease_seconds = lease_seconds

    def submit_many(self, commands: list[MemoryDraftGenerateCommand], *, context: ServiceRequestContext, external_ref: str | None = None):
        require_actor_roles(context, SOC_MEMORY_REVIEWER_ROLES, operation="requesting Memory drafts")
        if not context.idempotency_key or not 1 <= len(commands) <= 50 or len({c.candidate_id for c in commands}) != len(commands):
            raise ValueError("批量起草需要 Idempotency-Key 和 1..50 个不同的候选")
        batch_hash = stable_hash([c.model_dump(mode="json") for c in commands])
        results = []
        with self.repository.mutation_transaction() as tx:
            tx.lock_memory_governance()
            for command in commands:
                key = "draft:" + stable_hash([context.actor.actor_id, context.idempotency_key, command.candidate_id])
                item_context = context.model_copy(update={"idempotency_key": key})
                payload = {"command": command.model_dump(mode="json"), "batch_hash": batch_hash, "external_ref": external_ref, "actor_id": context.actor.actor_id}
                operation = SocMutationOperation.MEMORY_DRAFT_GENERATE
                prior = tx.find_mutation_audit_by_idempotency_key(operation, key)
                if prior:
                    validate_mutation_retry(prior, command=payload, target_type="memory_working_draft", target_id=command.candidate_id)
                    job = tx.processing_jobs().get(prior.result_ref)
                    if job is None:
                        raise SocServiceConflictError("起草任务记录不完整")
                    results.append(job)
                    continue
                candidate = tx.get_memory_candidate(command.candidate_id)
                previous = tx.get_memory_working_draft(command.candidate_id)
                if candidate is None or candidate.status not in EDITABLE or candidate_draft_revision(candidate) != command.candidate_revision:
                    raise SocServiceConflictError("候选来源或审核状态已变化，请重新核对")
                if previous is None or previous.version != command.expected_version or previous.candidate_revision != command.candidate_revision:
                    raise SocServiceConflictError("草稿版本已变化，请先保存并读取最新草稿")
                if previous.content.reviewer_verdict is None:
                    raise SocServiceConflictError("请先选择并保存运营最终判断，模型不会代替审核人选择")
                if previous.generation_job_id:
                    pending = tx.processing_jobs().get(previous.generation_job_id)
                    if pending and not pending.status.is_terminal:
                        raise SocServiceConflictError("该候选已有起草任务，请等待结果或继续编辑草稿")
                text_fields = ("detection_scenario", "observed_event", "conclusion", "business_rationale", "generalization_boundaries", "invalidation_conditions", "handling_guidance")
                if not command.regenerate and (previous.last_generation or any(getattr(previous.content, field).strip() for field in text_fields)):
                    raise SocServiceConflictError("已有经验文字，重新生成需要显式确认")
                job, _ = tx.processing_jobs().submit(
                    SocProcessingJobSubmission(
                        workload_kind=DRAFT_WORKLOAD,
                        queue_name=DRAFT_QUEUE,
                        tenant_id=candidate.tenant_id,
                        concurrency_key=f"memory-draft:{command.candidate_id}",
                        idempotency_key=key,
                        external_ref=external_ref,
                        alert_id=candidate.source.alert_id,
                        input_payload={"candidate_id": command.candidate_id, "candidate_revision": command.candidate_revision, "reserved_version": previous.version + 1, "context": item_context.model_dump(mode="json")},
                        metadata={"authority": "draft_only", "operator_verdict": previous.content.reviewer_verdict.value},
                    )
                )
                reserved = previous.model_copy(update={"version": previous.version + 1, "generation_job_id": job.job_id, "updated_at": datetime.now(UTC), "updated_by": context.actor.actor_id})
                if not tx.append_memory_working_draft(reserved, expected_version=previous.version):
                    raise SocServiceConflictError("草稿版本冲突")
                tx.append_mutation_audit(
                    build_mutation_audit(
                        operation=operation,
                        target_type="memory_working_draft",
                        target_id=command.candidate_id,
                        context=item_context,
                        command=payload,
                        reason="运营选择最终判断后请求起草，未审核或启用经验",
                        result_ref=job.job_id,
                        payload={"draft_version": reserved.version, "authority": "draft_only"},
                    )
                )
                results.append(job)
        return results

    def recover(self, *, now=None):
        with self.repository.mutation_transaction() as tx:
            tx.lock_memory_governance()
            return tx.processing_jobs().recover_expired_leases(queue_name=DRAFT_QUEUE, workload_kind=DRAFT_WORKLOAD, now=now)

    def execute_one(self, *, now=None):
        worker = "draft-worker-" + uuid4().hex
        with self.repository.mutation_transaction() as tx:
            tx.lock_memory_governance()
            if tx.processing_jobs().active_workload_count([DRAFT_WORKLOAD, "corpus_experiment"]) >= self._max_concurrency:
                return None
            job = tx.processing_jobs().claim_next(queue_name=DRAFT_QUEUE, workload_kind=DRAFT_WORKLOAD, worker_id=worker, lease_seconds=self._lease_seconds, now=now)
        if job is None:
            return None
        try:
            job = self.jobs.transition(job.job_id, worker_id=worker, expected_status=job.status, target_status=ProcessingJobStatus.PRECHECKING, event_type="draft_prechecking")
            previous = self._reserved(self.repository, job)
            if job.attempt_count > 3:
                return self._fail(job, worker, "attempt_budget", "起草任务已用完恢复次数")
            checkpoint = (job.result_payload or {}).get("generated_draft")
            if job.attempt_count > 1 and not checkpoint:
                events = self.jobs.list_events(job.job_id)
                acknowledged = any(e.event_type == "operator_retry" and e.attempt == job.attempt_count - 1 for e in events)
                previous_call = any(e.to_status is ProcessingJobStatus.ANALYZING for e in events)
                if previous_call and not acknowledged:
                    return self._fail(job, worker, "generation_uncertain", "上次调用结果未保存，是否再次调用需要人工确认", retryable=True)
            job = self.jobs.transition(job.job_id, worker_id=worker, expected_status=job.status, target_status=ProcessingJobStatus.ANALYZING, event_type="draft_generating" if not checkpoint else "draft_checkpoint_reused")
            if checkpoint:
                generated = SocMemoryBusinessLessonDraft.model_validate(checkpoint)
            else:
                content = previous.content
                with self._heartbeat(job.job_id, worker):
                    generated = self._drafter_factory().draft_business_lesson(
                        previous.candidate_id,
                        reviewer_verdict=content.reviewer_verdict,
                        reviewer_context=content.reviewer_context or None,
                        promoted_facet_values=content.promoted_facet_values or None,
                        selected_behavior_components=content.selected_behavior_components,
                        context=ServiceRequestContext.model_validate(job.input_payload["context"]),
                    )
            if generated.candidate_id != previous.candidate_id or generated.reviewer_verdict != previous.content.reviewer_verdict:
                raise ValueError("生成结果不属于当前候选或审核判断")
            job = self.jobs.transition(
                job.job_id, worker_id=worker, expected_status=job.status, target_status=ProcessingJobStatus.PROJECTING, event_type="draft_generated", result_payload={"generated_draft": generated.model_dump(mode="json")}
            )
            with self.repository.mutation_transaction() as tx:
                tx.lock_memory_governance()
                previous = self._reserved(tx, job)
                lesson = generated.lesson
                content = previous.content.model_copy(
                    update={
                        "detection_scenario": lesson.detection_scenario or "",
                        "observed_event": lesson.observed_event or "",
                        "conclusion": lesson.conclusion,
                        **{field: "\n".join(getattr(lesson, field)) for field in ("business_rationale", "generalization_boundaries", "invalidation_conditions", "handling_guidance")},
                    }
                )
                updated = previous.model_copy(update={"version": previous.version + 1, "content": content, "last_generation": generated, "updated_at": datetime.now(UTC)})
                if not tx.append_memory_working_draft(updated, expected_version=previous.version):
                    raise SocServiceConflictError("草稿已被修改")
                ctx = ServiceRequestContext.model_validate(job.input_payload["context"])
                tx.append_mutation_audit(
                    build_mutation_audit(
                        operation=SocMutationOperation.MEMORY_DRAFT_GENERATED,
                        target_type="memory_working_draft",
                        target_id=previous.candidate_id,
                        context=ctx,
                        command={"job_id": job.job_id},
                        reason="已生成可编辑草稿，仍需人工审核",
                        result_ref=f"{previous.candidate_id}:draft:v{updated.version}",
                        payload={"draft_version": updated.version, "model_name": generated.provenance.model_name, "provider_call_count": generated.provenance.provider_call_count, "authority": "draft_only"},
                    )
                )
                return tx.processing_jobs().transition(
                    job.job_id,
                    worker_id=worker,
                    expected_status=job.status,
                    target_status=ProcessingJobStatus.COMPLETED,
                    event_type="draft_saved",
                    result_payload={**job.result_payload, "draft_version": updated.version, "authority": "draft_only"},
                )
        except SocServiceConflictError as exc:
            return self._fail(job, worker, "draft_changed", str(exc))
        except ProcessingJobConflictError:
            # Another worker owns recovery. Never finish or fail its lease.
            return self.jobs.get(job.job_id)
        except Exception as exc:
            return self._fail(job, worker, type(exc).__name__, "经验起草未完成；已保留原草稿，可显式重试", retryable=True)

    @staticmethod
    def _reserved(repository, job):
        candidate = repository.get_memory_candidate(job.input_payload["candidate_id"])
        draft = repository.get_memory_working_draft(job.input_payload["candidate_id"])
        if candidate is None or candidate.status not in EDITABLE or candidate_draft_revision(candidate) != job.input_payload["candidate_revision"]:
            raise SocServiceConflictError("候选来源或状态已经改变，生成结果未覆盖草稿")
        if draft is None or draft.version != job.input_payload["reserved_version"] or draft.generation_job_id != job.job_id:
            raise SocServiceConflictError("草稿已被人工修改，生成结果未覆盖草稿")
        return draft

    def _fail(self, job, worker, code, message, *, retryable=False):
        return self.jobs.transition(
            job.job_id,
            worker_id=worker,
            expected_status=job.status,
            target_status=ProcessingJobStatus.FAILED,
            event_type="draft_failed",
            error_code=code,
            error_message=message,
            result_payload={**(job.result_payload or {}), "retryable": retryable, "authority": "draft_only"},
        )

    @contextmanager
    def _heartbeat(self, job_id, worker):
        stop, errors = Event(), []

        def renew():
            while not stop.wait(max(1, self._lease_seconds / 3)):
                try:
                    self.jobs.renew_lease(job_id, worker_id=worker, expected_status=ProcessingJobStatus.ANALYZING, lease_seconds=self._lease_seconds)
                except Exception as exc:
                    errors.append(exc)
                    return

        thread = Thread(target=renew, name="soc-draft-lease", daemon=True)
        thread.start()
        try:
            yield
            if errors:
                raise ProcessingJobConflictError("draft lease lost") from errors[0]
        finally:
            stop.set()
            thread.join()
