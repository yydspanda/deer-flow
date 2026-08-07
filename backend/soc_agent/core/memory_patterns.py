"""PI-03F3 repeated-pattern aggregation and pending memory candidate service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AnalysisRun,
    EntrySurface,
    MemoryPatternAggregationPolicy,
    MemoryPatternAggregationResult,
    MemoryPatternDataClass,
    MemoryPatternObservation,
    MemoryPatternObservationCreateCommand,
    MemoryPatternReplayReport,
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
        command = memory_pattern_command_from_run(
            run,
            source_type=source_type,
            transport_ref=transport_ref,
            environment=environment,
            data_class=data_class,
            policy_fingerprint=policy_fingerprint,
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
            )

        same_source = next(
            (
                item
                for item in repository.list_memory_pattern_observations(
                    aggregation_key=aggregation_key,
                    limit=10_000,
                )
                if item.source.source_id == command.source.source_id
            ),
            None,
        )
        if same_source is not None:
            return self._aggregation_result(
                same_source,
                idempotent=True,
                duplicate_source=True,
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
            source=command.source,
            signature=command.signature,
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
                    "mocked": observation.mocked,
                    "support_count": result.support_count,
                    "distinct_source_count": result.distinct_source_count,
                    "threshold_met": result.threshold_met,
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
        candidate = self._find_candidate(aggregation_key)
        current_ids = [item.observation_id for item in observations]
        snapshot_ids = _candidate_observation_ids(candidate)
        snapshot_items = [item for item in observations if item.observation_id in set(snapshot_ids)]
        baseline_hash = _candidate_evidence_set_hash(candidate)
        recomputed_hash = _evidence_set_hash(snapshot_items) if snapshot_ids and snapshot_items else None
        missing = sorted(set(snapshot_ids) - set(current_ids))
        added = sorted(set(current_ids) - set(snapshot_ids)) if candidate else []
        source_integrity = candidate is None or (not missing and len(snapshot_items) == len(snapshot_ids) and baseline_hash == recomputed_hash)
        distinct_count = len({item.source.source_id for item in observations})
        threshold_met = len(observations) >= policy.minimum_support and distinct_count >= policy.minimum_distinct_sources
        changed = (candidate is None and threshold_met) or bool(added) or bool(missing) or not source_integrity
        return MemoryPatternReplayReport(
            aggregation_key=aggregation_key,
            lineage_key=observations[0].lineage_key,
            policy_version=policy.policy_version,
            support_count=len(observations),
            distinct_source_count=distinct_count,
            threshold_met=threshold_met,
            candidate_id=candidate.candidate_id if candidate is not None else None,
            candidate_status=(candidate.status.value if candidate is not None else None),
            candidate_snapshot_observation_ids=snapshot_ids,
            current_observation_ids=current_ids,
            added_observation_ids=added,
            missing_observation_ids=missing,
            baseline_evidence_set_hash=baseline_hash,
            recomputed_snapshot_hash=recomputed_hash,
            source_integrity_passed=source_integrity,
            changed=changed,
        )

    def _aggregation_result(
        self,
        observation: MemoryPatternObservation,
        *,
        idempotent: bool,
        duplicate_source: bool,
    ) -> MemoryPatternAggregationResult:
        observations = self._cohort(observation.aggregation_key)
        support_count = len(observations)
        distinct_count = len({item.source.source_id for item in observations})
        threshold_met = support_count >= self._policy.minimum_support and distinct_count >= self._policy.minimum_distinct_sources
        existing = self._find_candidate(observation.aggregation_key)
        if not threshold_met:
            return MemoryPatternAggregationResult(
                observation=observation,
                support_count=support_count,
                distinct_source_count=distinct_count,
                minimum_support=self._policy.minimum_support,
                minimum_distinct_sources=self._policy.minimum_distinct_sources,
                threshold_met=False,
                candidate=existing,
                idempotent=idempotent,
                duplicate_source=duplicate_source,
                note=("duplicate alert source retained as one observation" if duplicate_source else "observation retained; repeated-pattern thresholds not reached"),
            )
        if existing is not None:
            return MemoryPatternAggregationResult(
                observation=observation,
                support_count=support_count,
                distinct_source_count=distinct_count,
                minimum_support=self._policy.minimum_support,
                minimum_distinct_sources=self._policy.minimum_distinct_sources,
                threshold_met=True,
                candidate=existing,
                candidate_frozen=True,
                idempotent=idempotent,
                duplicate_source=duplicate_source,
                note="candidate snapshot is frozen; later observations remain replay-only",
            )
        candidate = self._propose_candidate(observations)
        return MemoryPatternAggregationResult(
            observation=observation,
            support_count=support_count,
            distinct_source_count=distinct_count,
            minimum_support=self._policy.minimum_support,
            minimum_distinct_sources=self._policy.minimum_distinct_sources,
            threshold_met=True,
            candidate=candidate,
            candidate_created=True,
            candidate_frozen=True,
            idempotent=idempotent,
            duplicate_source=duplicate_source,
            note="one frozen pending repeated-pattern candidate created for human review",
        )

    def _propose_candidate(
        self,
        observations: list[MemoryPatternObservation],
    ) -> SocMemoryCandidate:
        if self._candidate_repository is None:
            raise SocServiceNotImplementedError("repeated-pattern threshold requires a MemoryCandidateRepository")
        snapshot = observations[: self._policy.minimum_support]
        command = _candidate_command(
            snapshot,
            policy=self._policy,
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

    def _find_candidate(self, aggregation_key: str) -> SocMemoryCandidate | None:
        if self._candidate_repository is None:
            return None
        return self._candidate_repository.find_memory_candidate_by_idempotency_key(_candidate_idempotency_key(aggregation_key))

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
                    "candidate_created": result.candidate_created,
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


def _candidate_command(
    observations: list[MemoryPatternObservation],
    *,
    policy: MemoryPatternAggregationPolicy,
) -> SocMemoryCandidateCreateCommand:
    first = observations[0]
    representatives = observations[: policy.maximum_representative_sources]
    evidence_refs = list(dict.fromkeys(ref for observation in representatives for ref in observation.evidence_refs))[: policy.maximum_evidence_refs]
    evidence_set_hash = _evidence_set_hash(observations)
    observation_ids = [item.observation_id for item in observations]
    source_ids = [item.source.source_id for item in observations]
    source_types = sorted({item.source.source_type.value for item in observations})
    source_facets = sorted({value for item in observations for value in item.signature.facets.get("source_type", [])})
    window_start = first.window_start.isoformat()
    window_end = first.window_end.isoformat()
    return SocMemoryCandidateCreateCommand(
        candidate_type=SocMemoryCandidateType.DETECTION_LESSON,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        summary=(f"Repeated {first.signature.dimension.value} pattern: {first.signature.label}"),
        content=(
            f"The same {first.signature.dimension.value} pattern was observed in "
            f"{len(observations)} distinct alert sources between {window_start} and "
            f"{window_end}. Recurrence alone does not establish benignness, "
            "maliciousness, authorization, impact, or a response action. Review the "
            "referenced runs before confirming reusable memory."
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
                "source_types": source_types,
                "observation_ids": observation_ids,
                "representative_observation_ids": [item.observation_id for item in representatives],
                "source_ids": source_ids,
                "evidence_set_hash": evidence_set_hash,
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
        idempotency_key=_candidate_idempotency_key(first.aggregation_key),
        confidence=0.5,
        facets={
            "candidate_source": ["repeated_pattern"],
            "pattern_dimension": [first.signature.dimension.value],
            "pattern_value": [first.signature.value],
            "pattern_origin": [first.signature.origin],
            "environment": [first.environment],
            "data_class": [first.data_class.value],
            **({"source_type": source_facets} if source_facets else {}),
        },
        decision_impact=SocMemoryDecisionImpact.NONE,
        review_owner="soc_memory_reviewer",
        labels=[
            "repeated-pattern",
            "candidate-only",
            "recurrence-is-not-verdict",
            *(["simulation"] if first.mocked else ["operational"]),
        ],
        metadata={
            "runtime_decision_allowed": False,
            "direct_alert_memory_write": False,
            "aggregation_key": first.aggregation_key,
            "lineage_key": first.lineage_key,
            "aggregation_policy": policy.model_dump(mode="json"),
            "support_count_at_creation": len(observations),
            "distinct_source_count_at_creation": len(set(source_ids)),
            "observation_ids": observation_ids,
            "representative_observation_ids": [item.observation_id for item in representatives],
            "source_ids": source_ids,
            "evidence_set_hash": evidence_set_hash,
            "candidate_snapshot_frozen": True,
            "later_observations_are_replay_only": True,
            "supersession_mode": policy.supersession_mode,
            "mocked": first.mocked,
        },
    )


def _candidate_actor_from_observation(
    observation: MemoryPatternObservation,
) -> ActorContext:
    return ActorContext(
        actor_id="soc-memory-pattern-aggregator",
        actor_type=ActorType.SERVICE,
        surface=(EntrySurface.DAEMON if observation.source.source_type is MemoryPatternSourceType.KAFKA_ALERT else EntrySurface.CLI),
        roles=["soc_memory_pattern_aggregator"],
    )


def _candidate_idempotency_key(aggregation_key: str) -> str:
    return f"memory_candidate:repeated_pattern:{aggregation_key}"


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


__all__ = ["SocMemoryPatternService"]
