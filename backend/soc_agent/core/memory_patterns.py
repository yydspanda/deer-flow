"""PI-03F3 repeated-pattern aggregation and pending memory candidate service."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any

from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AnalysisRun,
    EntrySurface,
    MemoryPatternAggregationPolicy,
    MemoryPatternAggregationResult,
    MemoryPatternCohortQuality,
    MemoryPatternDataClass,
    MemoryPatternDimension,
    MemoryPatternObservation,
    MemoryPatternObservationCreateCommand,
    MemoryPatternReplayReport,
    MemoryPatternRiskClass,
    MemoryPatternSourceType,
    ServiceRequestContext,
    SocEvent,
    SocEventType,
    SocMemoryCandidate,
    SocMemoryCandidateCreateCommand,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryDecisionImpact,
    SocMemoryTargetArtifact,
    SocMutationOperation,
)
from soc_agent.memory.patterns import memory_pattern_command_from_run
from soc_agent.memory.profiles import SocMemoryProfile, SocMemoryProfileRegistry
from soc_agent.memory.scoring import memory_strong_anchor_keys
from soc_agent.protocols import (
    MemoryCandidateRepository,
    MemoryPatternObservationRepository,
    SocEventSink,
    SocMutationAuditRepository,
    SocMutationUnitOfWork,
)
from soc_agent.utils.hashing import stable_hash

from .access_control import require_actor_roles
from .mutation_audit import (
    BufferedSocEventSink,
    build_mutation_audit,
    mutation_audit_repository_from,
    mutation_uow_from,
)
from .service import (
    NoopEventSink,
    SocMemoryService,
    SocServiceConflictError,
    SocServiceNotFoundError,
    SocServiceNotImplementedError,
)

_INGEST_ROLES = frozenset({"soc_daemon", "soc_batch_runner", "soc_engineer", "soc_admin"})


class SocMemoryPatternService:
    """Persist recurrence observations and propose one frozen review candidate."""

    def __init__(
        self,
        *,
        repository: MemoryPatternObservationRepository | None = None,
        candidate_repository: MemoryCandidateRepository | None = None,
        policy: MemoryPatternAggregationPolicy | None = None,
        mutation_audit_repository: SocMutationAuditRepository | None = None,
        mutation_uow: SocMutationUnitOfWork | None = None,
        event_sink: SocEventSink | None = None,
        now_provider: Callable[[], datetime] | None = None,
        profile_registry: SocMemoryProfileRegistry | None = None,
        _transaction_active: bool = False,
    ) -> None:
        self._repository = repository
        self._candidate_repository = candidate_repository
        self._policy = policy or MemoryPatternAggregationPolicy()
        self._mutation_audit_repository = mutation_audit_repository or mutation_audit_repository_from(repository, candidate_repository)
        self._mutation_uow = mutation_uow or mutation_uow_from(
            repository,
            candidate_repository,
        )
        self._event_sink = event_sink or NoopEventSink()
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._profile_registry = profile_registry or SocMemoryProfileRegistry()
        self._transaction_active = _transaction_active

    @property
    def policy(self) -> MemoryPatternAggregationPolicy:
        return self._policy

    def observe_run(
        self,
        run: AnalysisRun,
        *,
        source_type: MemoryPatternSourceType,
        transport_ref: str,
        environment: str,
        data_class: MemoryPatternDataClass,
        context: ServiceRequestContext,
    ) -> MemoryPatternAggregationResult:
        """Project and admit one completed Runtime result through the same service."""

        policy_fingerprint = stable_hash(self._policy.model_dump(mode="json"))
        profile = self._profile_registry.resolve_run(run)
        command = memory_pattern_command_from_run(
            run,
            source_type=source_type,
            transport_ref=transport_ref,
            environment=environment,
            data_class=data_class,
            policy_fingerprint=policy_fingerprint,
            profile=profile,
        )
        return self.ingest_observation(command, context=context)

    def ingest_observation(
        self,
        command: MemoryPatternObservationCreateCommand,
        *,
        context: ServiceRequestContext,
    ) -> MemoryPatternAggregationResult:
        """Persist one source observation and check both frozen thresholds."""

        require_actor_roles(
            context,
            _INGEST_ROLES,
            operation="ingesting a repeated memory pattern observation",
        )
        repository = self._require_repository()
        content_hash = _observation_content_hash(command, self._policy)
        window_start, window_end = _fixed_window(
            command.source.observed_at,
            self._policy.window_seconds,
        )
        lineage_key = _lineage_key(command, self._policy)
        aggregation_key = _aggregation_key(
            lineage_key=lineage_key,
            window_start=window_start,
            window_end=window_end,
            policy=self._policy,
        )

        existing = repository.find_memory_pattern_observation_by_idempotency_key(command.idempotency_key)
        if existing is not None:
            if existing.content_hash != content_hash:
                raise SocServiceConflictError(f"memory pattern idempotency key {command.idempotency_key} was reused for different content")
            if existing.aggregation_key != aggregation_key:
                raise SocServiceConflictError(f"memory pattern idempotency key {command.idempotency_key} was reused with a different aggregation policy")
            return self._aggregation_result(
                existing,
                idempotent=True,
                duplicate_source=False,
                duplicate_occurrence=False,
            )

        current_cohort = repository.list_memory_pattern_observations(
            aggregation_key=aggregation_key,
            limit=10_000,
        )
        same_occurrence = next(
            (item for item in current_cohort if item.occurrence_key == command.occurrence_key),
            None,
        )
        if same_occurrence is not None:
            return self._aggregation_result(
                same_occurrence,
                idempotent=True,
                duplicate_source=(same_occurrence.source.source_id == command.source.source_id),
                duplicate_occurrence=True,
            )

        same_source = next(
            (item for item in current_cohort if item.source.source_id == command.source.source_id),
            None,
        )
        if same_source is not None:
            return self._aggregation_result(
                same_source,
                idempotent=True,
                duplicate_source=True,
                duplicate_occurrence=False,
            )

        if self._mutation_uow is not None and not self._transaction_active:
            buffered_events = BufferedSocEventSink(self._event_sink)
            with self._mutation_uow.mutation_transaction() as transaction_repository:
                result = SocMemoryPatternService(
                    repository=transaction_repository,
                    candidate_repository=transaction_repository,
                    policy=self._policy,
                    mutation_audit_repository=transaction_repository,
                    mutation_uow=self._mutation_uow,
                    event_sink=buffered_events,
                    now_provider=self._now_provider,
                    profile_registry=self._profile_registry,
                    _transaction_active=True,
                ).ingest_observation(command, context=context)
            buffered_events.flush()
            return result

        observation = MemoryPatternObservation(
            idempotency_key=command.idempotency_key,
            aggregation_key=aggregation_key,
            lineage_key=lineage_key,
            content_hash=content_hash,
            tenant_id=command.tenant_id,
            environment=command.environment,
            data_class=command.data_class,
            profile_id=command.profile_id,
            profile_version=command.profile_version,
            feature_schema_version=command.feature_schema_version,
            occurrence_key=command.occurrence_key,
            source=command.source,
            signature=command.signature,
            lesson=command.lesson,
            window_start=window_start,
            window_end=window_end,
            aggregation_policy=self._policy,
            evidence_refs=command.evidence_refs,
            metadata=command.metadata,
            mocked=command.data_class is MemoryPatternDataClass.SIMULATION,
            created_at=self._now_provider(),
        )
        repository.save_memory_pattern_observation(observation)
        result = self._aggregation_result(
            observation,
            idempotent=False,
            duplicate_source=False,
            duplicate_occurrence=False,
        )
        self._append_audit(command, result=result, context=context)
        self._event_sink.emit(
            SocEvent(
                event_type=SocEventType.MEMORY_PATTERN_OBSERVED,
                request_id=context.request_id,
                run_id=observation.source.run_id,
                alert_id=observation.source.alert_id,
                actor=context.actor,
                payload={
                    "observation_id": observation.observation_id,
                    "aggregation_key": observation.aggregation_key,
                    "pattern_dimension": observation.signature.dimension.value,
                    "data_class": observation.data_class.value,
                    "memory_profile_id": observation.profile_id,
                    "memory_profile_version": observation.profile_version,
                    "occurrence_key": observation.occurrence_key,
                    "duplicate_occurrence": result.duplicate_occurrence,
                    "mocked": observation.mocked,
                    "support_count": result.support_count,
                    "distinct_source_count": result.distinct_source_count,
                    "threshold_met": result.threshold_met,
                    "quality_gate_passed": result.cohort_quality.quality_gate_passed,
                    "quality_reason_codes": result.cohort_quality.reason_codes,
                    "conclusive_count": result.cohort_quality.conclusive_count,
                    "consistency_ratio": result.cohort_quality.consistency_ratio,
                    "candidate_coverage": result.candidate_coverage,
                    "candidate_id": (result.candidate.candidate_id if result.candidate is not None else None),
                },
            )
        )
        return result

    def list_observations(
        self,
        *,
        aggregation_key: str | None = None,
        lineage_key: str | None = None,
        tenant_id: str | None = None,
        environment: str | None = None,
        data_class: MemoryPatternDataClass | None = None,
        source_type: MemoryPatternSourceType | None = None,
        limit: int = 500,
    ) -> list[MemoryPatternObservation]:
        return self._require_repository().list_memory_pattern_observations(
            aggregation_key=aggregation_key,
            lineage_key=lineage_key,
            tenant_id=tenant_id,
            environment=environment,
            data_class=data_class,
            source_type=source_type,
            limit=limit,
        )

    def replay(self, aggregation_key: str) -> MemoryPatternReplayReport:
        """Recompute an aggregate without creating, updating, or superseding memory."""

        observations = self._cohort(aggregation_key)
        if not observations:
            raise SocServiceNotFoundError(f"memory pattern aggregation {aggregation_key} not found")
        policy = observations[0].aggregation_policy
        if any(item.aggregation_policy != policy for item in observations):
            raise SocServiceConflictError("memory pattern cohort contains more than one aggregation policy")
        distinct_count = len({item.source.source_id for item in observations})
        threshold_met = len(observations) >= policy.minimum_support and distinct_count >= policy.minimum_distinct_sources
        cohort_quality = _cohort_quality(
            observations,
            policy=policy,
            recurrence_threshold_met=threshold_met,
        )
        current_candidate = self._find_candidate(aggregation_key)
        equivalent_candidate = (
            self._find_equivalent_lesson_candidate(
                observations[0],
                cohort_quality,
            )
            if current_candidate is None and cohort_quality.quality_gate_passed
            else None
        )
        candidate = current_candidate or equivalent_candidate
        candidate_coverage = "current_cohort" if current_candidate is not None else "equivalent_lesson" if equivalent_candidate is not None else "none"
        current_ids = [item.observation_id for item in observations]
        snapshot_ids = _candidate_observation_ids(candidate)
        snapshot_items = [item for item in observations if item.observation_id in set(snapshot_ids)]
        baseline_hash = _candidate_evidence_set_hash(candidate)
        source_integrity_checked = candidate_coverage == "current_cohort"
        if source_integrity_checked:
            recomputed_hash = _evidence_set_hash(snapshot_items) if snapshot_ids and snapshot_items else None
            missing = sorted(set(snapshot_ids) - set(current_ids))
            added = sorted(set(current_ids) - set(snapshot_ids))
            source_integrity = not missing and len(snapshot_items) == len(snapshot_ids) and baseline_hash == recomputed_hash
            changed = bool(added) or bool(missing) or not source_integrity
        else:
            recomputed_hash = None
            missing = []
            added = []
            source_integrity = True
            changed = candidate is None and cohort_quality.quality_gate_passed
        return MemoryPatternReplayReport(
            aggregation_key=aggregation_key,
            lineage_key=observations[0].lineage_key,
            policy_version=policy.policy_version,
            support_count=len(observations),
            distinct_source_count=distinct_count,
            threshold_met=threshold_met,
            cohort_quality=cohort_quality,
            candidate_id=candidate.candidate_id if candidate is not None else None,
            candidate_status=(candidate.status.value if candidate is not None else None),
            candidate_coverage=candidate_coverage,
            candidate_origin_aggregation_key=_candidate_aggregation_key(candidate),
            candidate_snapshot_observation_ids=snapshot_ids,
            current_observation_ids=current_ids,
            added_observation_ids=added,
            missing_observation_ids=missing,
            baseline_evidence_set_hash=baseline_hash,
            recomputed_snapshot_hash=recomputed_hash,
            source_integrity_passed=source_integrity,
            source_integrity_checked=source_integrity_checked,
            changed=changed,
        )

    def _aggregation_result(
        self,
        observation: MemoryPatternObservation,
        *,
        idempotent: bool,
        duplicate_source: bool,
        duplicate_occurrence: bool,
    ) -> MemoryPatternAggregationResult:
        observations = self._cohort(observation.aggregation_key)
        support_count = len(observations)
        distinct_count = len({item.source.source_id for item in observations})
        threshold_met = support_count >= self._policy.minimum_support and distinct_count >= self._policy.minimum_distinct_sources
        cohort_quality = _cohort_quality(
            observations,
            policy=self._policy,
            recurrence_threshold_met=threshold_met,
        )
        existing = self._find_candidate(observation.aggregation_key)
        if not threshold_met:
            return MemoryPatternAggregationResult(
                observation=observation,
                support_count=support_count,
                distinct_source_count=distinct_count,
                minimum_support=self._policy.minimum_support,
                minimum_distinct_sources=self._policy.minimum_distinct_sources,
                threshold_met=False,
                cohort_quality=cohort_quality,
                candidate=existing,
                candidate_coverage=("current_cohort" if existing is not None else "none"),
                idempotent=idempotent,
                duplicate_source=duplicate_source,
                duplicate_occurrence=duplicate_occurrence,
                note=(
                    "duplicate operational occurrence retained as one observation"
                    if duplicate_occurrence
                    else "duplicate alert source retained as one observation"
                    if duplicate_source
                    else "observation retained; repeated-pattern thresholds not reached"
                ),
            )
        if not cohort_quality.quality_gate_passed:
            return MemoryPatternAggregationResult(
                observation=observation,
                support_count=support_count,
                distinct_source_count=distinct_count,
                minimum_support=self._policy.minimum_support,
                minimum_distinct_sources=self._policy.minimum_distinct_sources,
                threshold_met=True,
                cohort_quality=cohort_quality,
                candidate=existing,
                candidate_coverage=("current_cohort" if existing is not None else "none"),
                candidate_frozen=existing is not None,
                idempotent=idempotent,
                duplicate_source=duplicate_source,
                duplicate_occurrence=duplicate_occurrence,
                note=("recurrence threshold reached, but lesson quality gate withheld expert review: " + ", ".join(cohort_quality.reason_codes)),
            )
        if existing is not None:
            return MemoryPatternAggregationResult(
                observation=observation,
                support_count=support_count,
                distinct_source_count=distinct_count,
                minimum_support=self._policy.minimum_support,
                minimum_distinct_sources=self._policy.minimum_distinct_sources,
                threshold_met=True,
                cohort_quality=cohort_quality,
                candidate=existing,
                candidate_coverage="current_cohort",
                candidate_frozen=True,
                idempotent=idempotent,
                duplicate_source=duplicate_source,
                duplicate_occurrence=duplicate_occurrence,
                note="candidate snapshot is frozen; later observations remain replay-only",
            )
        equivalent = self._find_equivalent_lesson_candidate(
            observation,
            cohort_quality,
        )
        if equivalent is not None:
            return MemoryPatternAggregationResult(
                observation=observation,
                support_count=support_count,
                distinct_source_count=distinct_count,
                minimum_support=self._policy.minimum_support,
                minimum_distinct_sources=self._policy.minimum_distinct_sources,
                threshold_met=True,
                cohort_quality=cohort_quality,
                candidate=equivalent,
                candidate_coverage="equivalent_lesson",
                candidate_frozen=True,
                idempotent=idempotent,
                duplicate_source=duplicate_source,
                duplicate_occurrence=duplicate_occurrence,
                note=("equivalent pattern lesson is already under governance; current cohort retained as reinforcement observations"),
            )
        candidate = self._propose_candidate(
            observations,
            cohort_quality=cohort_quality,
        )
        return MemoryPatternAggregationResult(
            observation=observation,
            support_count=support_count,
            distinct_source_count=distinct_count,
            minimum_support=self._policy.minimum_support,
            minimum_distinct_sources=self._policy.minimum_distinct_sources,
            threshold_met=True,
            cohort_quality=cohort_quality,
            candidate=candidate,
            candidate_coverage="current_cohort",
            candidate_created=True,
            candidate_frozen=True,
            idempotent=idempotent,
            duplicate_source=duplicate_source,
            duplicate_occurrence=duplicate_occurrence,
            note="one frozen pending repeated-pattern candidate created for human review",
        )

    def _propose_candidate(
        self,
        observations: list[MemoryPatternObservation],
        *,
        cohort_quality: MemoryPatternCohortQuality,
    ) -> SocMemoryCandidate:
        if self._candidate_repository is None:
            raise SocServiceNotImplementedError("repeated-pattern threshold requires a MemoryCandidateRepository")
        snapshot = observations
        profile = self._profile_for_observation(snapshot[0])
        command = _candidate_command(
            snapshot,
            policy=self._policy,
            cohort_quality=cohort_quality,
            profile=profile,
        )
        first = snapshot[0]
        context = ServiceRequestContext(
            actor=_candidate_actor_from_observation(first),
            trace_id=f"memory-pattern:{first.aggregation_key[:16]}",
            idempotency_key=command.idempotency_key,
        )
        return SocMemoryService(
            candidate_repository=self._candidate_repository,
            event_sink=self._event_sink,
        ).propose_candidate(command, context=context)

    def _profile_for_observation(
        self,
        observation: MemoryPatternObservation,
    ) -> SocMemoryProfile:
        profile = self._profile_registry.get(observation.profile_id)
        if profile is None:
            raise SocServiceConflictError(f"memory profile {observation.profile_id!r} is unavailable for replay")
        if profile.identity.profile_version != observation.profile_version or profile.identity.feature_schema_version != observation.feature_schema_version:
            raise SocServiceConflictError("memory observation profile version does not match the registered profile")
        return profile

    def _find_candidate(self, aggregation_key: str) -> SocMemoryCandidate | None:
        if self._candidate_repository is None:
            return None
        return self._candidate_repository.find_memory_candidate_by_source_id(f"memory_pattern:{aggregation_key}")

    def _find_equivalent_lesson_candidate(
        self,
        observation: MemoryPatternObservation,
        cohort_quality: MemoryPatternCohortQuality,
    ) -> SocMemoryCandidate | None:
        if self._candidate_repository is None:
            return None
        fingerprint = _lesson_fingerprint(observation, cohort_quality)
        return self._candidate_repository.find_memory_candidate_by_idempotency_key(_candidate_idempotency_key(fingerprint))

    def _cohort(self, aggregation_key: str) -> list[MemoryPatternObservation]:
        observations = self._require_repository().list_memory_pattern_observations(
            aggregation_key=aggregation_key,
            limit=10_000,
        )
        unique: dict[str, MemoryPatternObservation] = {}
        for item in observations:
            unique.setdefault(item.source.source_id, item)
        return sorted(
            unique.values(),
            key=lambda item: (item.source.observed_at, item.observation_id),
        )

    def _append_audit(
        self,
        command: MemoryPatternObservationCreateCommand,
        *,
        result: MemoryPatternAggregationResult,
        context: ServiceRequestContext,
    ) -> None:
        if self._mutation_audit_repository is None:
            return
        audit_context = context.model_copy(update={"idempotency_key": (f"memory-pattern-observation:{stable_hash(command.idempotency_key)}")})
        observation = result.observation
        self._mutation_audit_repository.append_mutation_audit(
            build_mutation_audit(
                operation=SocMutationOperation.MEMORY_PATTERN_OBSERVATION_INGEST,
                target_type="memory_pattern_observation",
                target_id=observation.observation_id,
                run_id=observation.source.run_id,
                alert_id=observation.source.alert_id,
                context=audit_context,
                reason="typed repeated-pattern source observation admitted",
                command=command.model_dump(mode="json"),
                result_ref=(result.candidate.candidate_id if result.candidate is not None else observation.observation_id),
                payload={
                    "aggregation_key": observation.aggregation_key,
                    "lineage_key": observation.lineage_key,
                    "data_class": observation.data_class.value,
                    "mocked": observation.mocked,
                    "pattern_dimension": observation.signature.dimension.value,
                    "support_count": result.support_count,
                    "distinct_source_count": result.distinct_source_count,
                    "threshold_met": result.threshold_met,
                    "quality_gate_passed": result.cohort_quality.quality_gate_passed,
                    "quality_reason_codes": result.cohort_quality.reason_codes,
                    "conclusive_count": result.cohort_quality.conclusive_count,
                    "consistency_ratio": result.cohort_quality.consistency_ratio,
                    "candidate_created": result.candidate_created,
                    "candidate_coverage": result.candidate_coverage,
                    "candidate_id": (result.candidate.candidate_id if result.candidate is not None else None),
                },
            )
        )

    def _require_repository(self) -> MemoryPatternObservationRepository:
        if self._repository is None:
            raise SocServiceNotImplementedError("memory pattern workflow requires a MemoryPatternObservationRepository")
        return self._repository


def _fixed_window(observed_at: datetime, window_seconds: int) -> tuple[datetime, datetime]:
    observed_utc = observed_at.astimezone(UTC)
    epoch = int(observed_utc.timestamp())
    window_epoch = epoch - (epoch % window_seconds)
    window_start = datetime.fromtimestamp(window_epoch, UTC)
    return window_start, window_start + timedelta(seconds=window_seconds)


def _observation_content_hash(
    command: MemoryPatternObservationCreateCommand,
    policy: MemoryPatternAggregationPolicy,
) -> str:
    return stable_hash(
        {
            "command": command.model_dump(mode="json", exclude={"idempotency_key"}),
            "policy": policy.model_dump(mode="json"),
        }
    )


def _lineage_key(
    command: MemoryPatternObservationCreateCommand,
    policy: MemoryPatternAggregationPolicy,
) -> str:
    return stable_hash(
        {
            "policy_version": policy.policy_version,
            "profile_id": command.profile_id,
            "profile_version": command.profile_version,
            "feature_schema_version": command.feature_schema_version,
            "tenant_id": command.tenant_id,
            "environment": command.environment,
            "data_class": command.data_class.value,
            "signature": {
                "dimension": command.signature.dimension.value,
                "value": command.signature.value,
            },
        }
    )


def _aggregation_key(
    *,
    lineage_key: str,
    window_start: datetime,
    window_end: datetime,
    policy: MemoryPatternAggregationPolicy,
) -> str:
    return stable_hash(
        {
            "lineage_key": lineage_key,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "policy": policy.model_dump(mode="json"),
        }
    )


def _cohort_quality(
    observations: list[MemoryPatternObservation],
    *,
    policy: MemoryPatternAggregationPolicy,
    recurrence_threshold_met: bool,
) -> MemoryPatternCohortQuality:
    lessons = [item.lesson for item in observations if item.lesson is not None]
    verdict_counts = Counter(item.verdict.value for item in lessons)
    risk_counts = Counter(item.risk_class.value for item in lessons)
    conclusive_count = sum(risk_counts.get(item.value, 0) for item in (MemoryPatternRiskClass.RISK, MemoryPatternRiskClass.BENIGN))
    unresolved_count = risk_counts.get(MemoryPatternRiskClass.UNRESOLVED.value, 0)
    dominant: MemoryPatternRiskClass | None = None
    consistency_ratio = 0.0
    if conclusive_count:
        risk_count = risk_counts.get(MemoryPatternRiskClass.RISK.value, 0)
        benign_count = risk_counts.get(MemoryPatternRiskClass.BENIGN.value, 0)
        if risk_count != benign_count:
            dominant = MemoryPatternRiskClass.RISK if risk_count > benign_count else MemoryPatternRiskClass.BENIGN
        consistency_ratio = max(risk_count, benign_count) / conclusive_count

    applicability_facets = _consensus_facets(
        observations,
        minimum_ratio=policy.minimum_consistency_ratio,
    )
    candidate_type = SocMemoryCandidateType.BENIGN_PATTERN if dominant is MemoryPatternRiskClass.BENIGN else SocMemoryCandidateType.DETECTION_LESSON
    strong_keys = memory_strong_anchor_keys(candidate_type)
    strong_anchor_facets = {key: values for key, values in applicability_facets.items() if key in strong_keys and values}
    reason_codes: list[str] = []
    if not recurrence_threshold_met:
        reason_codes.append("recurrence_threshold_not_met")
    if conclusive_count < policy.minimum_conclusive_support:
        reason_codes.append("insufficient_conclusive_support")
    if dominant is None or consistency_ratio < policy.minimum_consistency_ratio:
        reason_codes.append("inconsistent_risk_outcomes")
    if not strong_anchor_facets:
        reason_codes.append("missing_reusable_strong_anchor")

    representatives = _representative_observations(
        observations,
        dominant=dominant,
        limit=policy.maximum_representative_conclusions,
    )
    return MemoryPatternCohortQuality(
        support_count=len(observations),
        distinct_source_count=len({item.source.source_id for item in observations}),
        conclusive_count=conclusive_count,
        unresolved_count=unresolved_count,
        verdict_counts=dict(sorted(verdict_counts.items())),
        risk_class_counts=dict(sorted(risk_counts.items())),
        dominant_risk_class=dominant,
        consistency_ratio=round(consistency_ratio, 4),
        applicability_facets=applicability_facets,
        strong_anchor_facets=strong_anchor_facets,
        quality_gate_passed=not reason_codes,
        reason_codes=reason_codes,
        representative_observation_ids=[item.observation_id for item in representatives],
    )


def _consensus_facets(
    observations: list[MemoryPatternObservation],
    *,
    minimum_ratio: float,
) -> dict[str, list[str]]:
    occurrences: dict[str, Counter[str]] = defaultdict(Counter)
    display_values: dict[tuple[str, str], str] = {}
    for observation in observations:
        for key, values in observation.signature.facets.items():
            for value in {item.strip() for item in values if item.strip()}:
                normalized = value.casefold()
                occurrences[key][normalized] += 1
                display_values.setdefault((key, normalized), value)

    minimum_count = ceil(len(observations) * minimum_ratio)
    facets: dict[str, list[str]] = {}
    for key, value_counts in sorted(occurrences.items()):
        accepted = [display_values[(key, value)] for value, count in sorted(value_counts.items()) if count >= minimum_count]
        if accepted:
            facets[key] = accepted[:20]

    first = observations[0]
    signature_key = {
        MemoryPatternDimension.SCENARIO: "scenario_key",
        MemoryPatternDimension.DETECTION: "detection_key",
        MemoryPatternDimension.BEHAVIOR: "behavior_fingerprint",
        MemoryPatternDimension.COMPOUND: "pattern_signature",
        MemoryPatternDimension.CATEGORY: "category",
    }[first.signature.dimension]
    values = facets.setdefault(signature_key, [])
    if first.signature.value not in values:
        values.insert(0, first.signature.value)
    return facets


def _representative_observations(
    observations: list[MemoryPatternObservation],
    *,
    dominant: MemoryPatternRiskClass | None,
    limit: int,
) -> list[MemoryPatternObservation]:
    ranked = sorted(
        observations,
        key=lambda item: (
            item.lesson is not None and dominant is not None and item.lesson.risk_class is dominant,
            item.lesson is not None and item.lesson.risk_class is not MemoryPatternRiskClass.UNRESOLVED,
            item.source.observed_at,
            item.observation_id,
        ),
        reverse=True,
    )
    selected: list[MemoryPatternObservation] = []
    seen_conclusions: set[tuple[str, str]] = set()
    for item in ranked:
        if item.lesson is None:
            continue
        conclusion_key = (
            " ".join(item.lesson.summary.split()).casefold(),
            " ".join(item.lesson.reason.split()).casefold(),
        )
        if conclusion_key in seen_conclusions:
            continue
        selected.append(item)
        seen_conclusions.add(conclusion_key)
        if len(selected) >= limit:
            break
    return selected


def _candidate_command(
    observations: list[MemoryPatternObservation],
    *,
    policy: MemoryPatternAggregationPolicy,
    cohort_quality: MemoryPatternCohortQuality,
    profile: SocMemoryProfile,
) -> SocMemoryCandidateCreateCommand:
    first = observations[0]
    representatives = [item for observation_id in cohort_quality.representative_observation_ids for item in observations if item.observation_id == observation_id][: policy.maximum_representative_sources]
    evidence_refs = list(dict.fromkeys(ref for observation in representatives for ref in observation.evidence_refs))[: policy.maximum_evidence_refs]
    evidence_set_hash = _evidence_set_hash(observations)
    observation_ids = [item.observation_id for item in observations]
    source_ids = [item.source.source_id for item in observations]
    source_types = sorted({item.source.source_type.value for item in observations})
    window_start = first.window_start.isoformat()
    window_end = first.window_end.isoformat()
    dominant = cohort_quality.dominant_risk_class
    if dominant is None:
        raise ValueError("memory lesson candidate requires a dominant risk class")
    lesson_fingerprint = _lesson_fingerprint(first, cohort_quality)
    candidate_type = SocMemoryCandidateType.BENIGN_PATTERN if dominant is MemoryPatternRiskClass.BENIGN else SocMemoryCandidateType.DETECTION_LESSON
    applicability = profile.build_applicability(
        consensus_facets=cohort_quality.applicability_facets,
        strong_anchor_facets=cohort_quality.strong_anchor_facets,
    )
    if applicability is None:
        raise ValueError("memory lesson candidate requires a profile applicability contract")
    decision_scope = "pattern_decision_eligible" if "behavior_fingerprint" in applicability.required_facets else "rule_context_only"
    return SocMemoryCandidateCreateCommand(
        candidate_type=candidate_type,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        summary=(f"[{_risk_class_label(dominant)}经验候选] {first.signature.label}：{cohort_quality.conclusive_count} 条有效结论，一致率 {cohort_quality.consistency_ratio:.0%}"),
        content=_candidate_content(
            observations,
            representatives=representatives,
            quality=cohort_quality,
            decision_scope=decision_scope,
        ),
        tenant_scope=first.tenant_id,
        tenant_id=first.tenant_id,
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.REPEATED_PATTERN,
            source_surface=(EntrySurface.DAEMON if first.source.source_type is MemoryPatternSourceType.KAFKA_ALERT else EntrySurface.CLI),
            source_id=f"memory_pattern:{first.aggregation_key}",
            run_id=first.source.run_id,
            alert_id=first.source.alert_id,
            metadata={
                "aggregation_key": first.aggregation_key,
                "lineage_key": first.lineage_key,
                "policy_version": policy.policy_version,
                "window_start": window_start,
                "window_end": window_end,
                "environment": first.environment,
                "data_class": first.data_class.value,
                "memory_profile_id": first.profile_id,
                "memory_profile_version": first.profile_version,
                "memory_feature_schema_version": first.feature_schema_version,
                "source_types": source_types,
                "observation_ids": observation_ids,
                "representative_observation_ids": [item.observation_id for item in representatives],
                "source_ids": source_ids,
                "evidence_set_hash": evidence_set_hash,
                "cohort_quality": cohort_quality.model_dump(mode="json"),
                "lesson_fingerprint": lesson_fingerprint,
                "candidate_snapshot_frozen": True,
                "supersession_mode": policy.supersession_mode,
            },
        ),
        evidence_refs=evidence_refs,
        validity=SocMemoryCandidateValidity(
            valid_from=first.window_start,
            valid_until=first.window_end + timedelta(days=90),
            review_after_days=30,
            notes=("Repeated occurrence is candidate-only evidence; an analyst must review the cohort before confirmation or retrieval activation."),
        ),
        idempotency_key=_candidate_idempotency_key(lesson_fingerprint),
        confidence=cohort_quality.consistency_ratio,
        facets={
            "candidate_source": ["repeated_pattern"],
            "pattern_dimension": [first.signature.dimension.value],
            "pattern_value": [first.signature.value],
            "pattern_origin": [first.signature.origin],
            "environment": [first.environment],
            "data_class": [first.data_class.value],
            **cohort_quality.applicability_facets,
        },
        applicability=applicability,
        decision_impact=(SocMemoryDecisionImpact.DETECTION_DECISION if decision_scope == "pattern_decision_eligible" else SocMemoryDecisionImpact.REVIEW_HINT),
        review_owner="soc_memory_reviewer",
        labels=[
            "repeated-pattern",
            "quality-gated",
            f"cohort-{dominant.value}",
            decision_scope.replace("_", "-"),
            "candidate-only",
            *(["simulation"] if first.mocked else ["operational"]),
        ],
        metadata={
            "runtime_decision_allowed": False,
            "direct_alert_memory_write": False,
            "aggregation_key": first.aggregation_key,
            "lineage_key": first.lineage_key,
            "aggregation_policy": policy.model_dump(mode="json"),
            "memory_profile_id": first.profile_id,
            "memory_profile_version": first.profile_version,
            "memory_feature_schema_version": first.feature_schema_version,
            "support_count_at_creation": len(observations),
            "distinct_source_count_at_creation": len(set(source_ids)),
            "observation_ids": observation_ids,
            "representative_observation_ids": [item.observation_id for item in representatives],
            "source_ids": source_ids,
            "evidence_set_hash": evidence_set_hash,
            "cohort_quality": cohort_quality.model_dump(mode="json"),
            "lesson_fingerprint": lesson_fingerprint,
            "confidence_basis": "cohort_consistency_not_probability",
            "decision_scope": decision_scope,
            "candidate_snapshot_frozen": True,
            "later_observations_are_replay_only": True,
            "supersession_mode": policy.supersession_mode,
            "mocked": first.mocked,
        },
    )


def _candidate_content(
    observations: list[MemoryPatternObservation],
    *,
    representatives: list[MemoryPatternObservation],
    quality: MemoryPatternCohortQuality,
    decision_scope: str,
) -> str:
    first = observations[0]
    dominant = quality.dominant_risk_class
    if dominant is None:
        raise ValueError("memory lesson content requires a dominant risk class")
    applicability = _display_applicability_facets(quality.applicability_facets)
    verdicts = ", ".join(f"{key}={value}" for key, value in quality.verdict_counts.items())
    lines = [
        "经验结论：",
        f"- 风险判断：{_risk_class_label(dominant)}。",
        (f"- 统计依据：{quality.conclusive_count} 条有效结论，一致率 {quality.consistency_ratio:.0%}；原始 verdict 分布：{verdicts or '无'}。"),
        "适用范围：",
        f"- 租户/环境：{first.tenant_id} / {first.environment}。",
        (f"- 模式：{first.signature.dimension.value}={first.signature.label}。"),
        f"- 稳定特征：{applicability}。",
        "代表性研判：",
    ]
    for item in representatives:
        lesson = item.lesson
        if lesson is None:
            continue
        lines.append(f"- [{item.source.alert_id}] {lesson.summary}；理由：{lesson.reason}")
    minority_count = quality.conclusive_count - max(
        quality.risk_class_counts.get(MemoryPatternRiskClass.RISK.value, 0),
        quality.risk_class_counts.get(MemoryPatternRiskClass.BENIGN.value, 0),
    )
    lines.extend(
        [
            "边界与复核重点：",
            (f"- 少数相反结论 {minority_count} 条，未决结论 {quality.unresolved_count} 条；审核时应检查其是否属于例外。"),
            ("- 仅当新告警命中上述受治理强锚点且当前证据没有明确反证时，该经验才可作为 M-* 参考。"),
            (
                "- 当前候选包含稳定 behavior fingerprint；审核者可在精确适用范围内显式授予未来决策指令。"
                if decision_scope == "pattern_decision_eligible"
                else "- 当前候选只有 detection/rule 级稳定锚点，只能作为同规则背景参考，不能生成未来自动改判指令。"
            ),
            ("- 这是模式级待审候选，不是单条告警复述；人工确认前不得改判、抑制、关单或授权动作。"),
        ]
    )
    return "\n".join(lines)


def _display_applicability_facets(facets: dict[str, list[str]]) -> str:
    preferred = (
        "source_type",
        "source_system",
        "product",
        "detection_key",
        "rule_code",
        "scenario_key",
        "behavior_component",
        "behavior_fingerprint",
    )
    parts = [f"{key}={','.join(facets[key][:3])}" for key in preferred if facets.get(key)]
    return "; ".join(parts) if parts else "无稳定可复用特征"


def _risk_class_label(risk_class: MemoryPatternRiskClass) -> str:
    return {
        MemoryPatternRiskClass.RISK: "有风险",
        MemoryPatternRiskClass.BENIGN: "无风险/误报模式",
        MemoryPatternRiskClass.UNRESOLVED: "未决",
    }[risk_class]


def _candidate_actor_from_observation(
    observation: MemoryPatternObservation,
) -> ActorContext:
    return ActorContext(
        actor_id="soc-memory-pattern-aggregator",
        actor_type=ActorType.SERVICE,
        surface=(EntrySurface.DAEMON if observation.source.source_type is MemoryPatternSourceType.KAFKA_ALERT else EntrySurface.CLI),
        roles=["soc_memory_pattern_aggregator"],
    )


def _lesson_fingerprint(
    observation: MemoryPatternObservation,
    quality: MemoryPatternCohortQuality,
) -> str:
    dominant = quality.dominant_risk_class
    if dominant is None:
        raise ValueError("memory lesson fingerprint requires a dominant risk class")
    return stable_hash(
        {
            "policy_version": observation.aggregation_policy.policy_version,
            "lineage_key": observation.lineage_key,
            "risk_class": dominant.value,
            "strong_anchor_facets": quality.strong_anchor_facets,
            "memory_profile_id": observation.profile_id,
            "memory_profile_version": observation.profile_version,
            "memory_feature_schema_version": observation.feature_schema_version,
        }
    )


def _candidate_idempotency_key(lesson_fingerprint: str) -> str:
    return f"memory_candidate:repeated_pattern_lesson:{lesson_fingerprint}"


def _evidence_set_hash(observations: list[MemoryPatternObservation]) -> str:
    return stable_hash(
        [
            {
                "observation_id": item.observation_id,
                "content_hash": item.content_hash,
                "source_id": item.source.source_id,
                "evidence_refs": item.evidence_refs,
            }
            for item in observations
        ]
    )


def _candidate_observation_ids(candidate: SocMemoryCandidate | None) -> list[str]:
    if candidate is None:
        return []
    values = candidate.metadata.get("observation_ids")
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        return []
    return list(values)


def _candidate_evidence_set_hash(candidate: SocMemoryCandidate | None) -> str | None:
    if candidate is None:
        return None
    value: Any = candidate.metadata.get("evidence_set_hash")
    return value if isinstance(value, str) and len(value) == 64 else None


def _candidate_aggregation_key(candidate: SocMemoryCandidate | None) -> str | None:
    if candidate is None:
        return None
    value: Any = candidate.metadata.get("aggregation_key")
    return value if isinstance(value, str) and len(value) == 64 else None


__all__ = ["SocMemoryPatternService"]
