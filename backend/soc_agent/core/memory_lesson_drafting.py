"""Read-only Core Service for candidate-level Business Lesson drafting."""

from __future__ import annotations

from soc_agent.contracts import (
    ServiceRequestContext,
    SocMemoryBusinessLessonDraft,
    SocMemoryCandidateStatus,
    Verdict,
)
from soc_agent.memory.lessons import promote_memory_applicability_facets
from soc_agent.protocols import MemoryBusinessLessonDrafter, MemoryCandidateRepository

from .access_control import SOC_MEMORY_REVIEWER_ROLES, require_actor_roles
from .errors import (
    SocServiceConflictError,
    SocServiceError,
    SocServiceNotFoundError,
)


class SocMemoryLessonDraftService:
    """Stable read-only service for candidate-level AI lesson drafting."""

    REVIEWER_ROLES = SOC_MEMORY_REVIEWER_ROLES

    def __init__(
        self,
        *,
        candidate_repository: MemoryCandidateRepository,
        drafter: MemoryBusinessLessonDrafter,
    ) -> None:
        self._candidate_repository = candidate_repository
        self._drafter = drafter

    def draft_business_lesson(
        self,
        candidate_id: str,
        *,
        reviewer_verdict: Verdict,
        reviewer_context: str | None = None,
        promoted_facet_keys: list[str] | None = None,
        context: ServiceRequestContext | None = None,
    ) -> SocMemoryBusinessLessonDraft:
        """Generate a non-persisted draft without changing candidate state."""

        request_context = context or ServiceRequestContext()
        require_actor_roles(
            request_context,
            self.REVIEWER_ROLES,
            operation="drafting a SOC memory business lesson",
        )
        candidate = self._candidate_repository.get_memory_candidate(candidate_id)
        if candidate is None:
            raise SocServiceNotFoundError(f"memory candidate {candidate_id} not found")
        if candidate.status not in {
            SocMemoryCandidateStatus.PENDING_REVIEW,
            SocMemoryCandidateStatus.CONFIRMED_CANDIDATE,
        }:
            raise SocServiceConflictError("business lesson drafts are allowed only for reviewable candidates")
        if candidate.applicability is None:
            raise SocServiceConflictError("business lesson drafting requires reviewed machine applicability")
        try:
            draft_applicability = promote_memory_applicability_facets(
                candidate.applicability,
                promoted_facet_keys or [],
            )
        except ValueError as exc:
            raise SocServiceConflictError(str(exc)) from exc
        draft_candidate = candidate.model_copy(
            update={"applicability": draft_applicability},
        )
        try:
            return self._drafter.draft(
                draft_candidate,
                reviewer_verdict=reviewer_verdict,
                reviewer_context=reviewer_context,
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary becomes a typed service failure
            raise SocServiceError(f"memory business lesson draft failed: {type(exc).__name__}: {exc}") from exc


__all__ = ["SocMemoryLessonDraftService"]
