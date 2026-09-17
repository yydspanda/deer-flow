"""Shared draft editing; only the existing review command can publish Memory."""

from datetime import UTC, datetime

from soc_agent.contracts import ServiceRequestContext, SocMemoryCandidateStatus, SocMutationOperation
from soc_agent.contracts.memory_drafts import MemoryDraftSaveCommand, MemoryWorkingDraft, MemoryWorkingDraftView
from soc_agent.core.access_control import SOC_MEMORY_REVIEWER_ROLES, require_actor_roles
from soc_agent.core.errors import SocServiceConflictError, SocServiceNotFoundError
from soc_agent.core.mutation_audit import build_mutation_audit, mutation_idempotency_key, validate_mutation_retry
from soc_agent.protocols import SocMutationRepository, SocMutationUnitOfWork
from soc_agent.utils.hashing import stable_hash

EDITABLE = {SocMemoryCandidateStatus.PENDING_REVIEW, SocMemoryCandidateStatus.CONFIRMED_CANDIDATE}


def candidate_draft_revision(candidate) -> str:
    return stable_hash(candidate.model_dump(mode="json"))


class SocMemoryWorkingDraftService:
    def __init__(self, *, repository: SocMutationRepository, mutation_uow: SocMutationUnitOfWork):
        self._repository = repository
        self._uow = mutation_uow

    def get(self, candidate_id: str, *, context: ServiceRequestContext) -> MemoryWorkingDraftView:
        require_actor_roles(context, SOC_MEMORY_REVIEWER_ROLES, operation="reading shared Memory drafts")
        candidate = self._repository.get_memory_candidate(candidate_id)
        if candidate is None:
            raise SocServiceNotFoundError("经验候选不存在")
        revision = candidate_draft_revision(candidate)
        draft = self._repository.get_memory_working_draft(candidate_id)
        return MemoryWorkingDraftView(candidate_id=candidate_id, candidate_revision=revision, editable=candidate.status in EDITABLE, stale=bool(draft and draft.candidate_revision != revision), draft=draft)

    def save(self, candidate_id: str, command: MemoryDraftSaveCommand, *, context: ServiceRequestContext) -> MemoryWorkingDraft:
        require_actor_roles(context, SOC_MEMORY_REVIEWER_ROLES, operation="saving shared Memory drafts")
        payload = {**command.model_dump(mode="json"), "actor_id": context.actor.actor_id}
        operation = SocMutationOperation.MEMORY_DRAFT_SAVE
        with self._uow.mutation_transaction() as tx:
            tx.lock_memory_governance()
            prior = tx.find_mutation_audit_by_idempotency_key(operation, mutation_idempotency_key(context))
            if prior:
                validate_mutation_retry(prior, command=payload, target_type="memory_working_draft", target_id=candidate_id)
                saved = tx.get_memory_working_draft(candidate_id, version=int(prior.payload["draft_version"]))
                if saved is None:
                    raise SocServiceConflictError("草稿保存记录不完整，请检查数据库")
                return saved
            candidate = tx.get_memory_candidate(candidate_id)
            if candidate is None:
                raise SocServiceNotFoundError("经验候选不存在")
            if candidate.status not in EDITABLE:
                raise SocServiceConflictError("该候选已结束审核，不能修改待审草稿")
            if candidate_draft_revision(candidate) != command.candidate_revision:
                raise SocServiceConflictError("候选来源已变化，请刷新并核对来源后保存；原草稿仍然保留")
            previous = tx.get_memory_working_draft(candidate_id)
            if (previous.version if previous else 0) != command.expected_version:
                raise SocServiceConflictError("草稿已被其他人修改，请先读取最新草稿；本次未覆盖任何内容")
            result = MemoryWorkingDraft(
                candidate_id=candidate_id,
                candidate_revision=command.candidate_revision,
                version=command.expected_version + 1,
                content=command.content,
                updated_by=context.actor.actor_id,
                updated_at=datetime.now(UTC),
                last_generation=previous.last_generation if previous else None,
            )
            if not tx.append_memory_working_draft(result, expected_version=command.expected_version):
                raise SocServiceConflictError("草稿版本冲突，请刷新后重试")
            tx.append_mutation_audit(
                build_mutation_audit(
                    operation=operation,
                    target_type="memory_working_draft",
                    target_id=candidate_id,
                    context=context,
                    command=payload,
                    reason="保存待编辑经验草稿，未审核或启用经验",
                    run_id=candidate.source.run_id,
                    alert_id=candidate.source.alert_id,
                    result_ref=f"{candidate_id}:draft:v{result.version}",
                    payload={"draft_version": result.version, "candidate_revision": result.candidate_revision, "authority": "draft_only"},
                )
            )
            return result
