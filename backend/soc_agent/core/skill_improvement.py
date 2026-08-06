"""Governed PI-03C feedback aggregation and Skill improvement backlog."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from soc_agent.contracts import (
    ServiceRequestContext,
    SkillFeedbackObservation,
    SkillFeedbackObservationCreateCommand,
    SkillImprovementAggregationPolicy,
    SkillImprovementAggregationResult,
    SkillImprovementCandidate,
    SkillImprovementCandidateStatus,
    SkillImprovementReplayDiff,
    SkillImprovementReplayReport,
    SkillImprovementRepresentativeSample,
    SkillImprovementReviewCommand,
    SkillImprovementReviewDecision,
    SkillImprovementReviewResult,
    SocEvaluationDataClass,
    SocEvent,
    SocEventType,
    SocMutationOperation,
)
from soc_agent.protocols import (
    SkillImprovementRepository,
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
    validate_mutation_retry,
)
from .service import (
    NoopEventSink,
    SocServiceConflictError,
    SocServiceNotFoundError,
    SocServiceNotImplementedError,
)

_INGEST_ROLES = frozenset(
    {
        "external_disposition_adapter",
        "soc_admin",
        "soc_analyst",
        "soc_engineer",
    }
)
_SIMULATION_INGEST_ROLES = frozenset({"soc_admin", "soc_engineer"})
_REVIEW_ROLES = frozenset({"soc_admin", "soc_engineer", "soc_skill_reviewer"})


class SocSkillImprovementService:
    """Aggregate typed feedback without editing, activating, or publishing Skills."""

    def __init__(
        self,
        *,
        repository: SkillImprovementRepository | None = None,
        mutation_audit_repository: SocMutationAuditRepository | None = None,
        mutation_uow: SocMutationUnitOfWork | None = None,
        event_sink: SocEventSink | None = None,
        now_provider: Callable[[], datetime] | None = None,
        _transaction_active: bool = False,
    ) -> None:
        self._repository = repository
        self._mutation_audit_repository = mutation_audit_repository or mutation_audit_repository_from(repository)
        self._mutation_uow = mutation_uow or mutation_uow_from(repository)
        self._event_sink = event_sink or NoopEventSink()
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._transaction_active = _transaction_active

    def ingest_feedback(
        self,
        command: SkillFeedbackObservationCreateCommand,
        *,
        context: ServiceRequestContext,
        policy: SkillImprovementAggregationPolicy | None = None,
    ) -> SkillImprovementAggregationResult:
        """Persist one typed observation and deterministically refresh its cohort."""

        require_actor_roles(context, _INGEST_ROLES, operation="ingesting Skill feedback")
        if command.data_class.value == "simulation":
            require_actor_roles(
                context,
                _SIMULATION_INGEST_ROLES,
                operation="ingesting simulated Skill feedback",
            )
        repository = self._require_repository()
        aggregation_policy = policy or SkillImprovementAggregationPolicy()
        content_hash = _feedback_content_hash(command)
        aggregation_key = _aggregation_key(command, aggregation_policy)
        existing = repository.find_skill_feedback_observation_by_idempotency_key(command.idempotency_key)
        if existing is not None:
            if existing.content_hash != content_hash:
                raise SocServiceConflictError(f"skill feedback idempotency key {command.idempotency_key} was reused for different content")
            if existing.aggregation_key != aggregation_key:
                raise SocServiceConflictError(f"skill feedback idempotency key {command.idempotency_key} was reused with a different aggregation policy")
            return self._aggregation_result_for_observation(
                existing,
                policy=aggregation_policy,
                idempotent=True,
            )

        same_source = next(
            (
                item
                for item in repository.list_skill_feedback_observations(
                    aggregation_key=aggregation_key,
                    limit=10_000,
                )
                if item.source.source_id == command.source.source_id
            ),
            None,
        )
        if same_source is not None:
            if same_source.content_hash != content_hash:
                raise SocServiceConflictError(f"feedback source {command.source.source_id} already has different content in this cohort")
            return self._aggregation_result_for_observation(
                same_source,
                policy=aggregation_policy,
                idempotent=True,
            )

        if self._mutation_uow is not None and not self._transaction_active:
            buffered_events = BufferedSocEventSink(self._event_sink)
            with self._mutation_uow.mutation_transaction() as transaction_repository:
                result = SocSkillImprovementService(
                    repository=transaction_repository,
                    mutation_audit_repository=transaction_repository,
                    mutation_uow=self._mutation_uow,
                    event_sink=buffered_events,
                    now_provider=self._now_provider,
                    _transaction_active=True,
                ).ingest_feedback(command, context=context, policy=aggregation_policy)
            buffered_events.flush()
            return result

        observation = SkillFeedbackObservation(
            idempotency_key=command.idempotency_key,
            aggregation_key=aggregation_key,
            content_hash=content_hash,
            tenant_id=command.tenant_id,
            data_class=command.data_class,
            source=command.source,
            target_skill=command.target_skill,
            scenario_key=command.scenario_key,
            failure_facet=command.failure_facet,
            feedback_summary=command.feedback_summary,
            suggested_change=command.suggested_change,
            representative_sample_ref=command.representative_sample_ref,
            replay_set_refs=command.replay_set_refs,
            metadata=command.metadata,
            mocked=command.data_class.value == "simulation",
            created_at=self._now_provider(),
        )
        repository.save_skill_feedback_observation(observation)
        result = self._aggregation_result_for_observation(
            observation,
            policy=aggregation_policy,
            idempotent=False,
        )
        if self._mutation_audit_repository is not None:
            audit_context = context.model_copy(update={"idempotency_key": f"skill-feedback:{command.idempotency_key}"})
            self._mutation_audit_repository.append_mutation_audit(
                build_mutation_audit(
                    operation=SocMutationOperation.SKILL_FEEDBACK_INGEST,
                    target_type="skill_feedback_observation",
                    target_id=observation.observation_id,
                    run_id=observation.source.run_id,
                    alert_id=observation.source.alert_id,
                    queue_id=observation.source.queue_id,
                    context=audit_context,
                    reason="typed Skill feedback admitted",
                    command=command.model_dump(mode="json"),
                    result_ref=(result.candidate.candidate_id if result.candidate is not None else observation.observation_id),
                    payload={
                        "aggregation_key": observation.aggregation_key,
                        "data_class": observation.data_class.value,
                        "mocked": observation.mocked,
                        "skill_name": observation.target_skill.skill_name,
                        "failure_facet": observation.failure_facet.value,
                        "distinct_source_count": result.distinct_source_count,
                        "threshold_met": result.threshold_met,
                        "candidate_id": (result.candidate.candidate_id if result.candidate is not None else None),
                    },
                )
            )
        self._event_sink.emit(
            SocEvent(
                event_type=SocEventType.SKILL_FEEDBACK_INGESTED,
                request_id=context.request_id,
                run_id=observation.source.run_id,
                alert_id=observation.source.alert_id,
                actor=context.actor,
                payload={
                    "observation_id": observation.observation_id,
                    "aggregation_key": observation.aggregation_key,
                    "data_class": observation.data_class.value,
                    "mocked": observation.mocked,
                    "threshold_met": result.threshold_met,
                    "candidate_id": (result.candidate.candidate_id if result.candidate is not None else None),
                },
            )
        )
        return result

    def get_candidate(self, candidate_id: str) -> SkillImprovementCandidate:
        candidate = self._require_repository().get_skill_improvement_candidate(candidate_id)
        if candidate is None:
            raise SocServiceNotFoundError(f"Skill improvement candidate {candidate_id} not found")
        return candidate

    def list_candidates(
        self,
        *,
        status: SkillImprovementCandidateStatus | None = None,
        tenant_id: str | None = None,
        data_class: SocEvaluationDataClass | None = None,
        skill_name: str | None = None,
        limit: int = 100,
    ) -> list[SkillImprovementCandidate]:
        return self._require_repository().list_skill_improvement_candidates(
            status=status,
            tenant_id=tenant_id,
            data_class=data_class,
            skill_name=skill_name,
            limit=limit,
        )

    def review_candidate(
        self,
        command: SkillImprovementReviewCommand,
        *,
        context: ServiceRequestContext,
    ) -> SkillImprovementReviewResult:
        """Transition backlog state without touching the target Skill package."""

        require_actor_roles(context, _REVIEW_ROLES, operation="reviewing a Skill improvement candidate")
        repository = self._require_repository()
        command_payload = command.model_dump(mode="json")
        existing_audit = (
            self._mutation_audit_repository.find_mutation_audit_by_idempotency_key(
                SocMutationOperation.SKILL_IMPROVEMENT_REVIEW,
                context.idempotency_key or context.request_id,
            )
            if self._mutation_audit_repository is not None
            else None
        )
        if existing_audit is not None:
            validate_mutation_retry(
                existing_audit,
                command=command_payload,
                target_type="skill_improvement_candidate",
                target_id=command.candidate_id,
            )
            current = self.get_candidate(command.candidate_id)
            previous_status = SkillImprovementCandidateStatus(existing_audit.payload["previous_status"])
            return SkillImprovementReviewResult(
                candidate=current,
                previous_status=previous_status,
                decision=command.decision,
                idempotent=True,
            )

        if self._mutation_uow is not None and not self._transaction_active:
            buffered_events = BufferedSocEventSink(self._event_sink)
            with self._mutation_uow.mutation_transaction() as transaction_repository:
                result = SocSkillImprovementService(
                    repository=transaction_repository,
                    mutation_audit_repository=transaction_repository,
                    mutation_uow=self._mutation_uow,
                    event_sink=buffered_events,
                    now_provider=self._now_provider,
                    _transaction_active=True,
                ).review_candidate(command, context=context)
            buffered_events.flush()
            return result

        candidate = self.get_candidate(command.candidate_id)
        if candidate.version != command.expected_version:
            raise SocServiceConflictError(f"Skill improvement candidate {candidate.candidate_id} changed; expected version {command.expected_version}, found {candidate.version}")
        previous_status = candidate.status
        next_status = _review_transition(previous_status, command.decision)
        replacement = None
        if command.decision is SkillImprovementReviewDecision.SUPERSEDE:
            assert command.superseded_by_candidate_id is not None
            replacement = self.get_candidate(command.superseded_by_candidate_id)
            _validate_supersession(candidate, replacement)
        reviewed_at = self._now_provider()
        updated = candidate.model_copy(
            update={
                "version": candidate.version + 1,
                "status": next_status,
                "reviewed_by": context.actor,
                "reviewed_at": reviewed_at,
                "review_reason": command.reason,
                "superseded_by_candidate_id": (replacement.candidate_id if replacement is not None else None),
                "updated_at": reviewed_at,
            }
        )
        if not repository.compare_and_set_skill_improvement_candidate(
            updated,
            expected_version=candidate.version,
        ):
            raise SocServiceConflictError(f"Skill improvement candidate {candidate.candidate_id} changed during review")
        if self._mutation_audit_repository is not None:
            self._mutation_audit_repository.append_mutation_audit(
                build_mutation_audit(
                    operation=SocMutationOperation.SKILL_IMPROVEMENT_REVIEW,
                    target_type="skill_improvement_candidate",
                    target_id=updated.candidate_id,
                    context=context,
                    reason=command.reason,
                    command=command_payload,
                    result_ref=updated.candidate_id,
                    payload={
                        "previous_status": previous_status.value,
                        "status": updated.status.value,
                        "decision": command.decision.value,
                        "version": updated.version,
                        "superseded_by_candidate_id": updated.superseded_by_candidate_id,
                        "skill_mutation_allowed": updated.skill_mutation_allowed,
                        "skill_activation_allowed": updated.skill_activation_allowed,
                    },
                )
            )
        self._event_sink.emit(
            SocEvent(
                event_type=SocEventType.SKILL_IMPROVEMENT_CANDIDATE_UPDATED,
                request_id=context.request_id,
                actor=context.actor,
                payload={
                    "candidate_id": updated.candidate_id,
                    "previous_status": previous_status.value,
                    "status": updated.status.value,
                    "version": updated.version,
                    "skill_mutation_allowed": updated.skill_mutation_allowed,
                    "skill_activation_allowed": updated.skill_activation_allowed,
                },
            )
        )
        return SkillImprovementReviewResult(
            candidate=updated,
            previous_status=previous_status,
            decision=command.decision,
        )

    def replay_candidate(
        self,
        candidate_id: str,
        *,
        baseline: SkillImprovementCandidate | None = None,
        policy: SkillImprovementAggregationPolicy | None = None,
    ) -> SkillImprovementReplayReport:
        """Recompute the deterministic cohort and report source/replay-set drift."""

        candidate = self.get_candidate(candidate_id)
        if baseline is not None and baseline.candidate_id != candidate_id:
            raise SocServiceConflictError("replay baseline candidate_id does not match")
        baseline_candidate = baseline or candidate
        aggregation_policy = policy or SkillImprovementAggregationPolicy(
            minimum_distinct_sources=candidate.threshold,
        )
        observations = self._cohort(candidate.aggregation_key)
        projection = _candidate_projection(observations, aggregation_policy)
        recomputed_hash = _candidate_projection_hash(projection)
        current_observation_ids = set(projection["observation_ids"])
        baseline_observation_ids = set(baseline_candidate.observation_ids)
        current_replay_refs = set(projection["replay_set_refs"])
        baseline_replay_refs = set(baseline_candidate.replay_set_refs)
        diff = SkillImprovementReplayDiff(
            added_observation_ids=sorted(current_observation_ids - baseline_observation_ids),
            removed_observation_ids=sorted(baseline_observation_ids - current_observation_ids),
            added_replay_set_refs=sorted(current_replay_refs - baseline_replay_refs),
            removed_replay_set_refs=sorted(baseline_replay_refs - current_replay_refs),
            candidate_content_changed=(baseline_candidate.candidate_content_hash != recomputed_hash),
        )
        source_integrity = len(observations) == len({item.source.source_id for item in observations}) and all(item.aggregation_key == candidate.aggregation_key for item in observations)
        changed = any(
            (
                diff.added_observation_ids,
                diff.removed_observation_ids,
                diff.added_replay_set_refs,
                diff.removed_replay_set_refs,
                diff.candidate_content_changed,
            )
        )
        return SkillImprovementReplayReport(
            candidate_id=candidate.candidate_id,
            aggregation_key=candidate.aggregation_key,
            baseline_candidate_content_hash=baseline_candidate.candidate_content_hash,
            recomputed_candidate_content_hash=recomputed_hash,
            changed=changed,
            diff=diff,
            observation_count=len(observations),
            source_integrity_passed=source_integrity,
        )

    def _aggregation_result_for_observation(
        self,
        observation: SkillFeedbackObservation,
        *,
        policy: SkillImprovementAggregationPolicy,
        idempotent: bool,
    ) -> SkillImprovementAggregationResult:
        repository = self._require_repository()
        observations = self._cohort(observation.aggregation_key)
        distinct_count = len(observations)
        existing = repository.find_skill_improvement_candidate_by_aggregation_key(observation.aggregation_key)
        if distinct_count < policy.minimum_distinct_sources:
            return SkillImprovementAggregationResult(
                observation=observation,
                distinct_source_count=distinct_count,
                threshold=policy.minimum_distinct_sources,
                candidate=existing,
                idempotent=idempotent,
                threshold_met=False,
                note="feedback retained; distinct-source threshold not reached",
            )

        projection = _candidate_projection(observations, policy)
        candidate_hash = _candidate_projection_hash(projection)
        if existing is None:
            candidate = SkillImprovementCandidate(
                aggregation_key=observation.aggregation_key,
                aggregation_policy_version=policy.policy_version,
                candidate_content_hash=candidate_hash,
                tenant_id=observation.tenant_id,
                data_class=observation.data_class,
                target_skill=observation.target_skill,
                scenario_key=observation.scenario_key,
                failure_facet=observation.failure_facet,
                threshold=policy.minimum_distinct_sources,
                occurrence_count=len(observations),
                observation_ids=projection["observation_ids"],
                source_refs=projection["source_refs"],
                representative_samples=projection["representative_samples"],
                suggested_changes=projection["suggested_changes"],
                replay_set_refs=projection["replay_set_refs"],
                mocked=observation.mocked,
                created_at=self._now_provider(),
                updated_at=self._now_provider(),
            )
            repository.save_skill_improvement_candidate(candidate)
            return SkillImprovementAggregationResult(
                observation=observation,
                distinct_source_count=distinct_count,
                threshold=policy.minimum_distinct_sources,
                candidate=candidate,
                candidate_created=True,
                idempotent=idempotent,
                threshold_met=True,
                note="pending Skill improvement candidate created for human review",
            )

        if existing.status is not SkillImprovementCandidateStatus.PENDING_REVIEW:
            return SkillImprovementAggregationResult(
                observation=observation,
                distinct_source_count=distinct_count,
                threshold=policy.minimum_distinct_sources,
                candidate=existing,
                candidate_frozen=True,
                idempotent=idempotent,
                threshold_met=True,
                note="reviewed candidate is frozen; feedback retained for replay and a later package version",
            )
        if existing.candidate_content_hash == candidate_hash:
            return SkillImprovementAggregationResult(
                observation=observation,
                distinct_source_count=distinct_count,
                threshold=policy.minimum_distinct_sources,
                candidate=existing,
                idempotent=idempotent,
                threshold_met=True,
                note="pending candidate already reflects the complete cohort",
            )
        updated = existing.model_copy(
            update={
                "version": existing.version + 1,
                "candidate_content_hash": candidate_hash,
                "occurrence_count": len(observations),
                "observation_ids": projection["observation_ids"],
                "source_refs": projection["source_refs"],
                "representative_samples": projection["representative_samples"],
                "suggested_changes": projection["suggested_changes"],
                "replay_set_refs": projection["replay_set_refs"],
                "updated_at": self._now_provider(),
            }
        )
        if not repository.compare_and_set_skill_improvement_candidate(
            updated,
            expected_version=existing.version,
        ):
            raise SocServiceConflictError(f"Skill improvement candidate {existing.candidate_id} changed during aggregation")
        return SkillImprovementAggregationResult(
            observation=observation,
            distinct_source_count=distinct_count,
            threshold=policy.minimum_distinct_sources,
            candidate=updated,
            candidate_updated=True,
            idempotent=idempotent,
            threshold_met=True,
            note="pending Skill improvement candidate refreshed with a distinct source",
        )

    def _cohort(self, aggregation_key: str) -> list[SkillFeedbackObservation]:
        observations = self._require_repository().list_skill_feedback_observations(
            aggregation_key=aggregation_key,
            limit=10_000,
        )
        unique: dict[str, SkillFeedbackObservation] = {}
        for observation in observations:
            existing = unique.get(observation.source.source_id)
            if existing is not None and existing.content_hash != observation.content_hash:
                raise SocServiceConflictError(f"feedback source {observation.source.source_id} has conflicting cohort content")
            unique.setdefault(observation.source.source_id, observation)
        return sorted(
            unique.values(),
            key=lambda item: (item.source.observed_at, item.observation_id),
        )

    def _require_repository(self) -> SkillImprovementRepository:
        if self._repository is None:
            raise SocServiceNotImplementedError("Skill improvement workflow requires a SkillImprovementRepository")
        return self._repository


def _feedback_content_hash(command: SkillFeedbackObservationCreateCommand) -> str:
    payload = command.model_dump(mode="json", exclude={"idempotency_key"})
    return stable_hash(payload)


def _aggregation_key(
    command: SkillFeedbackObservationCreateCommand,
    policy: SkillImprovementAggregationPolicy,
) -> str:
    return stable_hash(
        {
            "policy": policy.model_dump(mode="json"),
            "tenant_id": command.tenant_id,
            "data_class": command.data_class.value,
            "target_skill": command.target_skill.model_dump(mode="json"),
            "scenario_key": command.scenario_key.strip().casefold(),
            "failure_facet": command.failure_facet.value,
        }
    )


def _candidate_projection(
    observations: list[SkillFeedbackObservation],
    policy: SkillImprovementAggregationPolicy,
) -> dict[str, Any]:
    ordered = sorted(
        observations,
        key=lambda item: (item.source.observed_at, item.observation_id),
    )
    representative = [
        SkillImprovementRepresentativeSample(
            observation_id=item.observation_id,
            source=item.source,
            sample_ref=item.representative_sample_ref,
            feedback_summary=item.feedback_summary,
        )
        for item in ordered[: policy.maximum_representative_samples]
    ]
    replay_refs = sorted({ref for item in ordered for ref in item.replay_set_refs})[: policy.maximum_replay_set_refs]
    return {
        "observation_ids": [item.observation_id for item in ordered],
        "source_refs": [item.source for item in ordered],
        "representative_samples": representative,
        "suggested_changes": sorted({item.suggested_change for item in ordered}),
        "replay_set_refs": replay_refs,
    }


def _candidate_projection_hash(projection: dict[str, Any]) -> str:
    return stable_hash(
        {
            **projection,
            "source_refs": [item.model_dump(mode="json") for item in projection["source_refs"]],
            "representative_samples": [item.model_dump(mode="json") for item in projection["representative_samples"]],
        }
    )


def _review_transition(
    current: SkillImprovementCandidateStatus,
    decision: SkillImprovementReviewDecision,
) -> SkillImprovementCandidateStatus:
    transitions = {
        SkillImprovementCandidateStatus.PENDING_REVIEW: {
            SkillImprovementReviewDecision.APPROVE_FOR_CHANGE: SkillImprovementCandidateStatus.APPROVED_FOR_CHANGE,
            SkillImprovementReviewDecision.REJECT: SkillImprovementCandidateStatus.REJECTED,
            SkillImprovementReviewDecision.SUPERSEDE: SkillImprovementCandidateStatus.SUPERSEDED,
            SkillImprovementReviewDecision.EXPIRE: SkillImprovementCandidateStatus.EXPIRED,
        },
        SkillImprovementCandidateStatus.APPROVED_FOR_CHANGE: {
            SkillImprovementReviewDecision.SUPERSEDE: SkillImprovementCandidateStatus.SUPERSEDED,
            SkillImprovementReviewDecision.EXPIRE: SkillImprovementCandidateStatus.EXPIRED,
        },
        SkillImprovementCandidateStatus.REJECTED: {
            SkillImprovementReviewDecision.SUPERSEDE: SkillImprovementCandidateStatus.SUPERSEDED,
        },
    }
    target = transitions.get(current, {}).get(decision)
    if target is None:
        raise SocServiceConflictError(f"cannot apply {decision.value} to Skill improvement candidate in {current.value}")
    return target


def _validate_supersession(
    candidate: SkillImprovementCandidate,
    replacement: SkillImprovementCandidate,
) -> None:
    if candidate.tenant_id != replacement.tenant_id:
        raise SocServiceConflictError("superseding candidate must have the same tenant")
    if candidate.data_class is not replacement.data_class:
        raise SocServiceConflictError("simulation and real candidate lineages cannot mix")
    if candidate.target_skill.skill_name != replacement.target_skill.skill_name:
        raise SocServiceConflictError("superseding candidate must target the same Skill")
    if candidate.scenario_key.strip().casefold() != replacement.scenario_key.strip().casefold():
        raise SocServiceConflictError("superseding candidate must target the same scenario")
    if candidate.failure_facet is not replacement.failure_facet:
        raise SocServiceConflictError("superseding candidate must target the same failure facet")


__all__ = ["SocSkillImprovementService"]
