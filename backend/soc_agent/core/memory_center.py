"""Operational read model for repeated-pattern Memory governance."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime

from soc_agent.contracts import (
    MemoryPatternDataClass,
    MemoryPatternLineageStats,
    SocMemoryCandidate,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateStatus,
    SocMemoryCenterCandidateRef,
    SocMemoryCenterMetrics,
    SocMemoryCenterOverview,
    SocMemoryCenterPatternDetail,
    SocMemoryCenterPatternSummary,
    SocMemoryCenterRecordRef,
    SocMemoryFutureUseState,
    SocMemoryPatternLifecycleState,
    SocMemoryPatternStageFilter,
    SocMemoryProfileState,
    SocMemoryRecord,
    SocMemoryRecordStatus,
)
from soc_agent.memory import SocMemoryProfileRegistry, memory_candidate_lineage_key
from soc_agent.protocols import (
    MemoryCandidateRepository,
    MemoryCenterRepository,
    MemoryPatternObservationRepository,
    MemoryRecordRepository,
)

from .errors import SocServiceNotFoundError, SocServiceNotImplementedError


class SocMemoryCenterService:
    """Compose persisted observations and governance objects by stable lineage."""

    def __init__(
        self,
        *,
        center_repository: MemoryCenterRepository | None = None,
        observation_repository: MemoryPatternObservationRepository | None = None,
        candidate_repository: MemoryCandidateRepository | None = None,
        record_repository: MemoryRecordRepository | None = None,
        profile_registry: SocMemoryProfileRegistry | None = None,
    ) -> None:
        self._center_repository = center_repository
        self._observation_repository = observation_repository
        self._candidate_repository = candidate_repository
        self._record_repository = record_repository
        self._profile_registry = profile_registry or SocMemoryProfileRegistry()

    def overview(
        self,
        *,
        tenant_id: str | None = None,
        environment: str | None = None,
        data_class: MemoryPatternDataClass | None = None,
        profile_id: str | None = None,
        search: str | None = None,
        include_terminal_history: bool = False,
        stage: SocMemoryPatternStageFilter | None = None,
        future_use: SocMemoryFutureUseState | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> SocMemoryCenterOverview:
        center_repository = self._require_center_repository()
        active_count_page = center_repository.list_memory_pattern_lineage_stats(
            tenant_id=tenant_id,
            environment=environment,
            data_class=data_class,
            profile_id=profile_id,
            search=search,
            include_terminal_history=False,
            limit=1,
            offset=0,
        )
        all_count_page = center_repository.list_memory_pattern_lineage_stats(
            tenant_id=tenant_id,
            environment=environment,
            data_class=data_class,
            profile_id=profile_id,
            search=search,
            include_terminal_history=True,
            limit=1,
            offset=0,
        )
        if stage is not None or future_use is not None:
            filtered = self._filtered_pattern_summaries(
                tenant_id=tenant_id,
                environment=environment,
                data_class=data_class,
                profile_id=profile_id,
                search=search,
                include_terminal_history=include_terminal_history,
                stage=stage,
                future_use=future_use,
            )
            total = len(filtered)
            items = filtered[offset : offset + limit]
        else:
            page = center_repository.list_memory_pattern_lineage_stats(
                tenant_id=tenant_id,
                environment=environment,
                data_class=data_class,
                profile_id=profile_id,
                search=search,
                include_terminal_history=include_terminal_history,
                limit=limit,
                offset=offset,
            )
            total = page.total
            items = self._pattern_summaries(page.items)

        inventory = center_repository.get_memory_center_inventory()
        legacy_patterns = 0
        unregistered_patterns = 0
        for profile in inventory.profile_inventory:
            registered = self._profile_registry.get(profile.profile_id)
            if registered is None:
                unregistered_patterns += profile.pattern_count
            elif registered.identity.profile_version != profile.profile_version or registered.identity.feature_schema_version != profile.feature_schema_version:
                legacy_patterns += profile.pattern_count
        return SocMemoryCenterOverview(
            metrics=SocMemoryCenterMetrics(
                pattern_count=inventory.pattern_count,
                aggregation_window_count=inventory.aggregation_window_count,
                observation_count=inventory.observation_count,
                pending_candidate_count=inventory.candidate_status_counts.get(
                    SocMemoryCandidateStatus.PENDING_REVIEW.value,
                    0,
                ),
                confirmed_memory_count=inventory.record_status_counts.get(
                    SocMemoryRecordStatus.CONFIRMED.value,
                    0,
                ),
                retrieval_enabled_memory_count=(inventory.retrieval_enabled_record_count),
                superseded_candidate_count=(
                    inventory.candidate_status_counts.get(
                        SocMemoryCandidateStatus.SUPERSEDED.value,
                        0,
                    )
                ),
                legacy_profile_pattern_count=legacy_patterns,
                unregistered_profile_pattern_count=unregistered_patterns,
            ),
            items=items,
            terminal_history_count=max(
                0,
                all_count_page.total - active_count_page.total,
            ),
            total=total,
            limit=limit,
            offset=offset,
        )

    def _filtered_pattern_summaries(
        self,
        *,
        tenant_id: str | None,
        environment: str | None,
        data_class: MemoryPatternDataClass | None,
        profile_id: str | None,
        search: str | None,
        include_terminal_history: bool,
        stage: SocMemoryPatternStageFilter | None,
        future_use: SocMemoryFutureUseState | None,
    ) -> list[SocMemoryCenterPatternSummary]:
        repository = self._require_center_repository()
        batch_size = 500
        cursor = 0
        filtered: list[SocMemoryCenterPatternSummary] = []
        while True:
            page = repository.list_memory_pattern_lineage_stats(
                tenant_id=tenant_id,
                environment=environment,
                data_class=data_class,
                profile_id=profile_id,
                search=search,
                include_terminal_history=include_terminal_history,
                limit=batch_size,
                offset=cursor,
            )
            for summary in self._pattern_summaries(page.items):
                if stage is not None and _stage_filter(summary.lifecycle_state) is not stage:
                    continue
                if future_use is not None and summary.future_use_state is not future_use:
                    continue
                filtered.append(summary)
            cursor += len(page.items)
            if not page.items or cursor >= page.total:
                break
        return filtered

    def _pattern_summaries(
        self,
        stats_items: Sequence[MemoryPatternLineageStats],
    ) -> list[SocMemoryCenterPatternSummary]:
        candidates = self._require_center_repository().find_memory_candidates_by_lineage_keys([item.lineage_key for item in stats_items])
        records = self._require_record_repository().find_memory_records_by_candidate_ids([item.candidate_id for item in candidates])
        candidates_by_lineage = _candidates_by_lineage(candidates)
        records_by_candidate = {item.source_candidate_id: item for item in records}
        summaries: list[SocMemoryCenterPatternSummary] = []
        for stats in stats_items:
            candidate, record = _select_governance_objects(
                candidates_by_lineage.get(stats.lineage_key, []),
                records_by_candidate,
            )
            summaries.append(
                self._pattern_summary(
                    stats,
                    candidate=candidate,
                    record=record,
                )
            )
        return summaries

    def pattern_detail(
        self,
        lineage_key: str,
        *,
        include_observations: bool = True,
        observation_limit: int = 100,
        observation_offset: int = 0,
    ) -> SocMemoryCenterPatternDetail:
        page = self._require_center_repository().list_memory_pattern_lineage_stats(
            search=lineage_key,
            include_terminal_history=True,
            limit=10,
            offset=0,
        )
        stats = next(
            (item for item in page.items if item.lineage_key == lineage_key),
            None,
        )
        if stats is None:
            raise SocServiceNotFoundError(f"memory pattern {lineage_key} not found")
        candidates = self._require_center_repository().find_memory_candidates_by_lineage_keys([lineage_key])
        records = self._require_record_repository().find_memory_records_by_candidate_ids([item.candidate_id for item in candidates])
        records_by_candidate = {item.source_candidate_id: item for item in records}
        candidate, record = _select_governance_objects(
            candidates,
            records_by_candidate,
        )
        observations = (
            self._require_observation_repository().list_memory_pattern_observations(
                lineage_key=lineage_key,
                limit=observation_limit,
                offset=observation_offset,
            )
            if include_observations
            else []
        )
        return SocMemoryCenterPatternDetail(
            pattern=self._pattern_summary(
                stats,
                candidate=candidate,
                record=record,
            ),
            candidates=candidates,
            memory_records=records,
            observations=observations,
            observation_total=stats.support_count,
            observation_limit=observation_limit,
            observation_offset=observation_offset,
            suggested_successor_candidate_id=(self._suggested_successor(candidate) if candidate is not None else None),
        )

    def _pattern_summary(
        self,
        stats: MemoryPatternLineageStats,
        *,
        candidate: SocMemoryCandidate | None,
        record: SocMemoryRecord | None,
    ) -> SocMemoryCenterPatternSummary:
        registered_profile = self._profile_registry.get(stats.profile_id)
        if registered_profile is None:
            profile_state = SocMemoryProfileState.UNREGISTERED
            current_profile_version = None
            current_feature_schema_version = None
        else:
            current_profile_version = registered_profile.identity.profile_version
            current_feature_schema_version = registered_profile.identity.feature_schema_version
            profile_state = SocMemoryProfileState.CURRENT if current_profile_version == stats.profile_version and current_feature_schema_version == stats.feature_schema_version else SocMemoryProfileState.LEGACY

        snapshot_count = _candidate_snapshot_count(candidate)
        reinforcement_count = max(0, stats.support_count - snapshot_count) if candidate is not None else 0
        attention_reasons: list[str] = []
        if profile_state is SocMemoryProfileState.UNREGISTERED:
            attention_reasons.append("unregistered_memory_profile")
        elif profile_state is SocMemoryProfileState.LEGACY:
            attention_reasons.append("legacy_memory_profile")
            if candidate is not None and candidate.status in {
                SocMemoryCandidateStatus.PENDING_REVIEW,
                SocMemoryCandidateStatus.CONFIRMED_CANDIDATE,
            }:
                attention_reasons.append("legacy_candidate_requires_reconciliation")
            if record is not None:
                attention_reasons.append("legacy_memory_requires_revalidation")
        if candidate is not None:
            if candidate.status is SocMemoryCandidateStatus.PENDING_REVIEW:
                attention_reasons.append("candidate_review_required")
            elif candidate.status is SocMemoryCandidateStatus.SUPERSEDED:
                attention_reasons.append("superseded_history")
        if record is not None and not record.retrieval_enabled:
            attention_reasons.append("memory_retrieval_disabled")

        lifecycle_state = _pattern_lifecycle(candidate, record)
        return SocMemoryCenterPatternSummary(
            lineage_key=stats.lineage_key,
            tenant_id=stats.tenant_id,
            environment=stats.environment,
            data_class=stats.data_class,
            pattern_dimension=stats.pattern_dimension,
            pattern_value=stats.pattern_value,
            pattern_label=stats.pattern_label,
            profile_id=stats.profile_id,
            profile_version=stats.profile_version,
            feature_schema_version=stats.feature_schema_version,
            current_profile_version=current_profile_version,
            current_feature_schema_version=current_feature_schema_version,
            profile_state=profile_state,
            lifecycle_state=lifecycle_state,
            future_use_state=_future_use_state(record, profile_state),
            attention_reasons=attention_reasons,
            support_count=stats.support_count,
            distinct_source_count=stats.distinct_source_count,
            aggregation_window_count=stats.aggregation_window_count,
            candidate_snapshot_count=snapshot_count,
            reinforcement_count=reinforcement_count,
            first_observed_at=stats.first_observed_at,
            last_observed_at=stats.last_observed_at,
            first_window_start=stats.first_window_start,
            last_window_end=stats.last_window_end,
            candidate=_candidate_ref(candidate),
            memory_record=_record_ref(record),
        )

    def _suggested_successor(
        self,
        candidate: SocMemoryCandidate,
    ) -> str | None:
        if candidate.superseded_by_candidate_id:
            return candidate.superseded_by_candidate_id
        if candidate.status not in {
            SocMemoryCandidateStatus.PENDING_REVIEW,
            SocMemoryCandidateStatus.CONFIRMED_CANDIDATE,
        }:
            return None
        if not candidate.source.alert_id:
            return None
        profile = _candidate_profile(candidate)
        if profile is None:
            return None
        current = self._profile_registry.get(profile[0])
        if current is None:
            return None
        candidates = self._require_candidate_repository().list_memory_candidates(
            alert_id=candidate.source.alert_id,
            limit=200,
        )
        eligible = [
            item
            for item in candidates
            if item.candidate_id != candidate.candidate_id
            and item.tenant_scope == candidate.tenant_scope
            and item.tenant_id == candidate.tenant_id
            and item.source.source_type is SocMemoryCandidateSourceType.REPEATED_PATTERN
            and item.status
            in {
                SocMemoryCandidateStatus.PENDING_REVIEW,
                SocMemoryCandidateStatus.CONFIRMED_CANDIDATE,
                SocMemoryCandidateStatus.CONFIRMED,
            }
            and _candidate_profile(item)
            == (
                current.identity.profile_id,
                current.identity.profile_version,
                current.identity.feature_schema_version,
            )
        ]
        return eligible[0].candidate_id if eligible else None

    def _require_center_repository(self) -> MemoryCenterRepository:
        if self._center_repository is None:
            raise SocServiceNotImplementedError("Memory Center requires a MemoryCenterRepository")
        return self._center_repository

    def _require_observation_repository(
        self,
    ) -> MemoryPatternObservationRepository:
        if self._observation_repository is None:
            raise SocServiceNotImplementedError("Memory Center detail requires a MemoryPatternObservationRepository")
        return self._observation_repository

    def _require_candidate_repository(self) -> MemoryCandidateRepository:
        if self._candidate_repository is None:
            raise SocServiceNotImplementedError("Memory Center requires a MemoryCandidateRepository")
        return self._candidate_repository

    def _require_record_repository(self) -> MemoryRecordRepository:
        if self._record_repository is None:
            raise SocServiceNotImplementedError("Memory Center requires a MemoryRecordRepository")
        return self._record_repository


def _candidates_by_lineage(
    candidates: Sequence[SocMemoryCandidate],
) -> dict[str, list[SocMemoryCandidate]]:
    grouped: dict[str, list[SocMemoryCandidate]] = defaultdict(list)
    for candidate in candidates:
        lineage_key = memory_candidate_lineage_key(candidate)
        if lineage_key is not None:
            grouped[lineage_key].append(candidate)
    return grouped


def _select_governance_objects(
    candidates: Sequence[SocMemoryCandidate],
    records_by_candidate: dict[str, SocMemoryRecord],
) -> tuple[SocMemoryCandidate | None, SocMemoryRecord | None]:
    if not candidates:
        return None, None

    active_candidate_statuses = {
        SocMemoryCandidateStatus.PENDING_REVIEW,
        SocMemoryCandidateStatus.CONFIRMED_CANDIDATE,
        SocMemoryCandidateStatus.CONFIRMED,
    }

    def rank(candidate: SocMemoryCandidate) -> tuple[int, int, int, object, str]:
        record = records_by_candidate.get(candidate.candidate_id)
        return (
            1 if record is not None and record.retrieval_enabled else 0,
            1 if record is not None else 0,
            1 if candidate.status in active_candidate_statuses else 0,
            candidate.created_at,
            candidate.candidate_id,
        )

    candidate = max(candidates, key=rank)
    return candidate, records_by_candidate.get(candidate.candidate_id)


def _candidate_snapshot_count(candidate: SocMemoryCandidate | None) -> int:
    if candidate is None:
        return 0
    values = candidate.metadata.get("observation_ids")
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        return len([item for item in values if isinstance(item, str)])
    value = candidate.metadata.get("support_count_at_creation")
    return int(value) if isinstance(value, int) and value >= 0 else 0


def _candidate_ref(
    candidate: SocMemoryCandidate | None,
) -> SocMemoryCenterCandidateRef | None:
    if candidate is None:
        return None
    distinct = candidate.metadata.get("distinct_source_count_at_creation")
    return SocMemoryCenterCandidateRef(
        candidate_id=candidate.candidate_id,
        status=candidate.status,
        summary=candidate.summary,
        support_count_at_creation=_candidate_snapshot_count(candidate),
        distinct_source_count_at_creation=(int(distinct) if isinstance(distinct, int) and distinct >= 0 else 0),
        superseded_by_candidate_id=candidate.superseded_by_candidate_id,
    )


def _record_ref(record: SocMemoryRecord | None) -> SocMemoryCenterRecordRef | None:
    if record is None:
        return None
    return SocMemoryCenterRecordRef(
        memory_id=record.memory_id,
        version=record.version,
        status=record.status,
        summary=record.summary,
        retrieval_enabled=record.retrieval_enabled,
        decision_directive_ready=record.decision_directive is not None,
        retrieval_valid_until=record.retrieval_valid_until,
        retrieval_review_due_at=record.retrieval_review_due_at,
    )


def _pattern_lifecycle(
    candidate: SocMemoryCandidate | None,
    record: SocMemoryRecord | None,
) -> SocMemoryPatternLifecycleState:
    if record is not None:
        return SocMemoryPatternLifecycleState.MEMORY_ACTIVE if record.retrieval_enabled else SocMemoryPatternLifecycleState.MEMORY_INACTIVE
    if candidate is None:
        return SocMemoryPatternLifecycleState.COLLECTING
    if candidate.status is SocMemoryCandidateStatus.PENDING_REVIEW:
        return SocMemoryPatternLifecycleState.CANDIDATE_PENDING
    if candidate.status in {
        SocMemoryCandidateStatus.CONFIRMED_CANDIDATE,
        SocMemoryCandidateStatus.CONFIRMED,
    }:
        return SocMemoryPatternLifecycleState.CANDIDATE_INTERMEDIATE
    return SocMemoryPatternLifecycleState.TERMINAL_HISTORY


def _stage_filter(
    lifecycle: SocMemoryPatternLifecycleState,
) -> SocMemoryPatternStageFilter:
    if lifecycle is SocMemoryPatternLifecycleState.COLLECTING:
        return SocMemoryPatternStageFilter.COLLECTING
    if lifecycle is SocMemoryPatternLifecycleState.CANDIDATE_PENDING:
        return SocMemoryPatternStageFilter.AWAITING_REVIEW
    if lifecycle is SocMemoryPatternLifecycleState.CANDIDATE_INTERMEDIATE:
        return SocMemoryPatternStageFilter.MATERIALIZING
    if lifecycle in {
        SocMemoryPatternLifecycleState.MEMORY_INACTIVE,
        SocMemoryPatternLifecycleState.MEMORY_ACTIVE,
    }:
        return SocMemoryPatternStageFilter.PERSISTED
    return SocMemoryPatternStageFilter.TERMINAL


def _future_use_state(
    record: SocMemoryRecord | None,
    profile_state: SocMemoryProfileState,
) -> SocMemoryFutureUseState:
    if record is None:
        return SocMemoryFutureUseState.NOT_READY
    if not record.retrieval_enabled:
        return SocMemoryFutureUseState.PAUSED
    now = datetime.now(UTC)
    if (
        profile_state is not SocMemoryProfileState.CURRENT
        or record.status is not SocMemoryRecordStatus.CONFIRMED
        or (record.retrieval_valid_until is not None and record.retrieval_valid_until <= now)
        or (record.retrieval_review_due_at is not None and record.retrieval_review_due_at <= now)
    ):
        return SocMemoryFutureUseState.BLOCKED
    if record.decision_directive is not None:
        return SocMemoryFutureUseState.EXACT_MATCH_DECISION
    return SocMemoryFutureUseState.REFERENCE_ONLY


def _candidate_profile(
    candidate: SocMemoryCandidate,
) -> tuple[str, str, str] | None:
    if candidate.applicability is not None:
        return (
            candidate.applicability.profile_id,
            candidate.applicability.profile_version,
            candidate.applicability.feature_schema_version,
        )
    values = (
        candidate.metadata.get("memory_profile_id"),
        candidate.metadata.get("memory_profile_version"),
        candidate.metadata.get("memory_feature_schema_version"),
    )
    if all(isinstance(item, str) and item for item in values):
        return values  # type: ignore[return-value]
    return None


__all__ = ["SocMemoryCenterService"]
