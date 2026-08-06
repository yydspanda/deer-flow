"""In-memory PI-03C repository used by tests and explicit simulations."""

from __future__ import annotations

from collections.abc import Iterable

from soc_agent.contracts import (
    SkillFeedbackObservation,
    SkillFeedbackSourceType,
    SkillImprovementCandidate,
    SkillImprovementCandidateStatus,
    SocEvaluationDataClass,
    SocMutationAuditRecord,
    SocMutationOperation,
)


class SkillImprovementRepositoryConflictError(ValueError):
    """Raised when an immutable identity or optimistic version conflicts."""


class InMemorySkillImprovementRepository:
    def __init__(
        self,
        *,
        observations: Iterable[SkillFeedbackObservation] = (),
        candidates: Iterable[SkillImprovementCandidate] = (),
    ) -> None:
        self._observations: dict[str, SkillFeedbackObservation] = {}
        self._observation_idempotency: dict[str, str] = {}
        self._candidates: dict[str, SkillImprovementCandidate] = {}
        self._candidate_aggregation: dict[str, str] = {}
        self._mutation_audits: dict[tuple[SocMutationOperation, str], SocMutationAuditRecord] = {}
        for observation in observations:
            self.save_skill_feedback_observation(observation)
        for candidate in candidates:
            self.save_skill_improvement_candidate(candidate)

    def save_skill_feedback_observation(self, observation: SkillFeedbackObservation) -> None:
        existing_id = self._observation_idempotency.get(observation.idempotency_key)
        if existing_id is not None:
            existing = self._observations[existing_id]
            if existing != observation:
                raise SkillImprovementRepositoryConflictError(f"skill feedback idempotency key {observation.idempotency_key} already exists")
            return
        if observation.observation_id in self._observations:
            raise SkillImprovementRepositoryConflictError(f"skill feedback observation {observation.observation_id} already exists")
        self._observations[observation.observation_id] = observation
        self._observation_idempotency[observation.idempotency_key] = observation.observation_id

    def get_skill_feedback_observation(self, observation_id: str) -> SkillFeedbackObservation | None:
        return self._observations.get(observation_id)

    def find_skill_feedback_observation_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SkillFeedbackObservation | None:
        observation_id = self._observation_idempotency.get(idempotency_key)
        return self._observations.get(observation_id) if observation_id is not None else None

    def list_skill_feedback_observations(
        self,
        *,
        aggregation_key: str | None = None,
        tenant_id: str | None = None,
        data_class: SocEvaluationDataClass | None = None,
        source_type: SkillFeedbackSourceType | None = None,
        limit: int = 500,
    ) -> list[SkillFeedbackObservation]:
        observations = list(self._observations.values())
        if aggregation_key is not None:
            observations = [item for item in observations if item.aggregation_key == aggregation_key]
        if tenant_id is not None:
            observations = [item for item in observations if item.tenant_id == tenant_id]
        if data_class is not None:
            observations = [item for item in observations if item.data_class is data_class]
        if source_type is not None:
            observations = [item for item in observations if item.source.source_type is source_type]
        return sorted(observations, key=lambda item: (item.source.observed_at, item.observation_id))[:limit]

    def save_skill_improvement_candidate(self, candidate: SkillImprovementCandidate) -> None:
        existing_id = self._candidate_aggregation.get(candidate.aggregation_key)
        if existing_id is not None and existing_id != candidate.candidate_id:
            raise SkillImprovementRepositoryConflictError(f"skill improvement aggregation key {candidate.aggregation_key} already exists")
        existing = self._candidates.get(candidate.candidate_id)
        if existing is not None and existing != candidate:
            raise SkillImprovementRepositoryConflictError(f"skill improvement candidate {candidate.candidate_id} already exists")
        self._candidates[candidate.candidate_id] = candidate
        self._candidate_aggregation[candidate.aggregation_key] = candidate.candidate_id

    def compare_and_set_skill_improvement_candidate(
        self,
        candidate: SkillImprovementCandidate,
        *,
        expected_version: int,
    ) -> bool:
        current = self._candidates.get(candidate.candidate_id)
        if current is None or current.version != expected_version:
            return False
        if candidate.version != expected_version + 1:
            raise SkillImprovementRepositoryConflictError("skill improvement candidate version must increment by one")
        self._candidates[candidate.candidate_id] = candidate
        self._candidate_aggregation[candidate.aggregation_key] = candidate.candidate_id
        return True

    def get_skill_improvement_candidate(self, candidate_id: str) -> SkillImprovementCandidate | None:
        return self._candidates.get(candidate_id)

    def find_skill_improvement_candidate_by_aggregation_key(
        self,
        aggregation_key: str,
    ) -> SkillImprovementCandidate | None:
        candidate_id = self._candidate_aggregation.get(aggregation_key)
        return self._candidates.get(candidate_id) if candidate_id is not None else None

    def list_skill_improvement_candidates(
        self,
        *,
        status: SkillImprovementCandidateStatus | None = None,
        tenant_id: str | None = None,
        data_class: SocEvaluationDataClass | None = None,
        skill_name: str | None = None,
        limit: int = 100,
    ) -> list[SkillImprovementCandidate]:
        candidates = list(self._candidates.values())
        if status is not None:
            candidates = [item for item in candidates if item.status is status]
        if tenant_id is not None:
            candidates = [item for item in candidates if item.tenant_id == tenant_id]
        if data_class is not None:
            candidates = [item for item in candidates if item.data_class is data_class]
        if skill_name is not None:
            candidates = [item for item in candidates if item.target_skill.skill_name == skill_name]
        return sorted(candidates, key=lambda item: (item.updated_at, item.candidate_id), reverse=True)[:limit]

    def append_mutation_audit(self, record: SocMutationAuditRecord) -> None:
        key = (record.operation, record.idempotency_key)
        if key in self._mutation_audits:
            raise SkillImprovementRepositoryConflictError(f"mutation audit idempotency key {record.idempotency_key} already exists")
        self._mutation_audits[key] = record

    def find_mutation_audit_by_idempotency_key(
        self,
        operation: SocMutationOperation,
        idempotency_key: str,
    ) -> SocMutationAuditRecord | None:
        return self._mutation_audits.get((operation, idempotency_key))

    def list_mutation_audits(
        self,
        *,
        operation: SocMutationOperation | None = None,
        run_id: str | None = None,
        queue_id: str | None = None,
        target_id: str | None = None,
        limit: int = 100,
    ) -> list[SocMutationAuditRecord]:
        records = list(self._mutation_audits.values())
        if operation is not None:
            records = [item for item in records if item.operation is operation]
        if run_id is not None:
            records = [item for item in records if item.run_id == run_id]
        if queue_id is not None:
            records = [item for item in records if item.queue_id == queue_id]
        if target_id is not None:
            records = [item for item in records if item.target_id == target_id]
        return sorted(records, key=lambda item: item.occurred_at, reverse=True)[:limit]


__all__ = [
    "InMemorySkillImprovementRepository",
    "SkillImprovementRepositoryConflictError",
]
