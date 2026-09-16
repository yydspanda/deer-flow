"""One candidate/record resolution policy for automatic and explicit learning."""

from datetime import UTC, datetime

from soc_agent.contracts import SocMemoryApplicabilityStatus, SocMemoryCandidate, SocMemoryCandidateCreateCommand, SocMemoryCandidateStatus, SocMemoryQuery, SocMemoryRecord, SocMemoryRecordStatus
from soc_agent.contracts.memory_learning import SocMemoryLearningView
from soc_agent.memory.governance import scope_identity
from soc_agent.memory.lineage import memory_candidate_lineage_key
from soc_agent.memory.scoring import evaluate_memory_scope
from soc_agent.protocols import MemoryCandidateRepository, MemoryRecordRepository

LearningInput = SocMemoryCandidate | SocMemoryCandidateCreateCommand

_PENDING = {SocMemoryCandidateStatus.PENDING_REVIEW, SocMemoryCandidateStatus.CONFIRMED_CANDIDATE}
_CLOSED = {SocMemoryCandidateStatus.REJECTED, SocMemoryCandidateStatus.EXPIRED, SocMemoryCandidateStatus.DEPRECATED}


def aggregation_key(item: LearningInput) -> str | None:
    return item.metadata.get("aggregation_key") or item.source.metadata.get("aggregation_key")


def data_scope(item: LearningInput) -> str | None:
    return item.metadata.get("data_class") or item.source.metadata.get("data_class") or next(iter(item.facets.get("data_class", [])), None)


def _record(repository: MemoryCandidateRepository | MemoryRecordRepository, candidate: SocMemoryCandidate) -> SocMemoryRecord | None:
    getter = getattr(repository, "get_memory_record_by_candidate_id", None)
    return getter(candidate.candidate_id) if callable(getter) else None


def _expired(record: SocMemoryRecord, now: datetime) -> bool:
    return any(value is not None and value <= now for value in (record.validity.valid_until, record.retrieval_valid_until, record.retrieval_review_due_at))


def _covers(candidate: SocMemoryCandidate, incoming: LearningInput, record: SocMemoryRecord | None) -> bool:
    if candidate.tenant_id != incoming.tenant_id or candidate.tenant_scope != incoming.tenant_scope or data_scope(candidate) != data_scope(incoming):
        return False
    spec = record.applicability if record is not None else candidate.applicability
    if spec is None:
        return memory_candidate_lineage_key(candidate) == memory_candidate_lineage_key(incoming) and memory_candidate_lineage_key(incoming) is not None
    # Pending review is one exact scope; an approved broader scope can cover a
    # new occurrence, but a mere context-only match cannot suppress new learning.
    if record is None and incoming.applicability is not None:
        key = scope_identity(candidate)
        if key is not None:
            return key == scope_identity(incoming)
        return memory_candidate_lineage_key(candidate) == memory_candidate_lineage_key(incoming) and memory_candidate_lineage_key(incoming) is not None and spec == incoming.applicability
    identity = incoming.applicability
    query = SocMemoryQuery(
        facets=incoming.facets,
        metadata={
            "memory_profile_id": identity.profile_id,
            "memory_profile_version": identity.profile_version,
            "memory_feature_schema_version": identity.feature_schema_version,
        }
        if identity is not None
        else incoming.metadata,
    )
    report = evaluate_memory_scope(spec, record.memory_type if record is not None else candidate.candidate_type, query, {})
    if report.status is not SocMemoryApplicabilityStatus.APPLICABLE:
        return False
    # An aggregate with several IPs is not covered by an IP-restricted lesson.
    for condition in spec.reuse_conditions:
        values = {v.casefold() for v in incoming.facets.get(condition.facet_key, [])}
        allowed = {v.casefold() for v in condition.values}
        prefixes = {v.split(":", 1)[0] for v in allowed if ":" in v}
        relevant = {v for v in values if not prefixes or v.split(":", 1)[0] in prefixes}
        if not relevant or not relevant <= allowed:
            return False
    return True


def resolve_learning_candidate(repository: MemoryCandidateRepository, incoming: LearningInput, *, record_repository: MemoryRecordRepository | None = None, now: datetime | None = None) -> SocMemoryCandidate | None:
    """Read only: reuse open work, then approved scope, then same-window history."""
    now = now or datetime.now(UTC)
    pool = {}
    lineage = memory_candidate_lineage_key(incoming)
    if lineage:
        for item in repository.find_memory_candidates_by_lineage_keys([lineage]):
            pool[item.candidate_id] = item
    key = scope_identity(incoming)
    if key:
        pending = repository.find_pending_memory_candidate_by_scope(key)
        if pending is not None:
            pool[pending.candidate_id] = pending
    for item in (
        repository.find_memory_candidate_by_idempotency_key(incoming.idempotency_key) if incoming.idempotency_key else None,
        repository.find_memory_candidate_by_source_id(incoming.source.source_id) if incoming.source.source_id else None,
    ):
        if item is not None:
            pool[item.candidate_id] = item
    resolved = []
    for candidate in pool.values():
        seen = set()
        while candidate.status is SocMemoryCandidateStatus.SUPERSEDED and candidate.superseded_by_candidate_id and candidate.candidate_id not in seen:
            seen.add(candidate.candidate_id)
            successor = repository.get_memory_candidate(candidate.superseded_by_candidate_id)
            if successor is None:
                break
            candidate = successor
        record = _record(record_repository or repository, candidate)
        if record is not None and record.superseded_by_memory_id:
            getter = getattr(record_repository or repository, "get_memory_record", None)
            successor = getter(record.superseded_by_memory_id) if callable(getter) else None
            if successor is not None:
                replacement = repository.get_memory_candidate(successor.source_candidate_id)
                if replacement is not None:
                    candidate, record = replacement, successor
        if not _covers(candidate, incoming, record):
            continue
        if record is not None and record.metadata.get("revision_pending"):
            revisions = repository.list_memory_candidates(revision_of_memory_id=record.memory_id, limit=200)
            pending = [item for item in revisions if item.status in _PENDING]
            if len(pending) == 1:
                resolved.append((0, pending[0]))
                continue
        if candidate.status in _PENDING and (candidate.validity.valid_until is None or candidate.validity.valid_until > now):
            resolved.append((1, candidate))
        elif candidate.status is SocMemoryCandidateStatus.CONFIRMED and (record is None or record.status is SocMemoryRecordStatus.CONFIRMED):
            resolved.append((2, candidate))
        elif (
            candidate.status in _CLOSED
            or (record is not None and record.status is not SocMemoryRecordStatus.CONFIRMED)
            or (candidate.status in _PENDING and candidate.validity.valid_until is not None and candidate.validity.valid_until <= now)
        ):
            if aggregation_key(incoming) and aggregation_key(candidate) == aggregation_key(incoming):
                resolved.append((3, candidate))
            elif incoming.idempotency_key and candidate.idempotency_key == incoming.idempotency_key and not aggregation_key(incoming):
                resolved.append((3, candidate))
    # Stable choice for historical duplicates; no records are merged or activated.
    resolved.sort(key=lambda pair: (pair[0], pair[1].created_at, pair[1].candidate_id))
    return resolved[0][1] if resolved else None


def learning_view(repository: MemoryCandidateRepository, candidate: SocMemoryCandidate | None, *, record_repository: MemoryRecordRepository | None = None, now: datetime | None = None) -> SocMemoryLearningView:
    now = now or datetime.now(UTC)
    if candidate is None:
        return SocMemoryLearningView(state="accumulating", label="正在积累同类样本", detail="尚未形成适用经验，可以主动提炼。", action="promote", action_label="提炼经验")
    common = {"candidate_id": candidate.candidate_id}
    record = _record(record_repository or repository, candidate)
    if record is not None and record.metadata.get("revision_pending"):
        revisions = repository.list_memory_candidates(revision_of_memory_id=record.memory_id, limit=200)
        pending = [item for item in revisions if item.status in _PENDING]
        if len(pending) == 1:
            return learning_view(repository, pending[0], record_repository=record_repository, now=now)
    if candidate.status in _PENDING and (candidate.validity.valid_until is None or candidate.validity.valid_until > now):
        revision = candidate.revision_lineage is not None
        automatic = candidate.source.source_type.value == "repeated_pattern"
        return SocMemoryLearningView(
            **common,
            state="revision_pending" if revision else "pending_review",
            label="经验正在修订" if revision else "同类经验待审核" if automatic else "人工提炼经验待审核",
            detail=(
                "正在修订已有经验，审核通过后更新其内容与适用条件。"
                if revision
                else "由同类样本自动提炼。符合适用条件的告警共用这条经验，审核一次即可。"
                if automatic
                else (f"由告警 {candidate.source.alert_id} 人工发起提炼。" if candidate.source.alert_id else "由运营人员主动发起提炼。") + "审核并开放使用后，符合适用条件的新告警也可使用。"
            ),
            action="review",
            action_label="继续审核修订" if revision else "审核同类经验" if automatic else "审核人工提炼经验",
        )
    if record is not None:
        use = "retired" if record.status is not SocMemoryRecordStatus.CONFIRMED else "expired" if _expired(record, now) else "paused" if not record.retrieval_enabled else "exact" if record.decision_directive is not None else "reference"
        labels = {"retired": "经验已废止", "expired": "经验需要复查", "paused": "经验已沉淀，尚未开放使用", "exact": "经验已沉淀，精确匹配可复用结论", "reference": "经验已沉淀，仅供研判参考"}
        return SocMemoryLearningView(**common, memory_id=record.memory_id, state="confirmed", label=labels[use], detail="查看已有业务结论；需要调整时修订原经验。", action="view_memory", action_label="查看 / 修订经验", use_mode=use)
    labels = {"rejected": "本轮已放弃沉淀", "expired": "候选已过期", "deprecated": "候选已废止", "superseded": "候选已被替代", "confirmed": "候选已确认"}
    return SocMemoryLearningView(**common, state="closed", label=labels.get(candidate.status.value, "候选已过期"), detail="保留历史记录，不重复发起本轮自动审核。", action="view_history", action_label="查看记录")
