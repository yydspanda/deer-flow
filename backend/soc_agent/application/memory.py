"""Application composition for tenant-aware SOC memory profiles."""

from __future__ import annotations

import os

from soc_agent.integrations.pingan.memory import PingAnSocMemoryProfile
from soc_agent.memory.profiles import SocMemoryProfileRegistry


def build_soc_memory_profile_registry(*, normalization_review_mode: str | None = None) -> SocMemoryProfileRegistry:
    """Register reviewed tenant profiles ahead of the generic fallback."""

    mode = normalization_review_mode if normalization_review_mode is not None else os.environ.get("SOC_NORMALIZATION_ASSIST_MODE", "off")
    semantic_features = mode.strip().lower() == "apply"
    return SocMemoryProfileRegistry([PingAnSocMemoryProfile(semantic_features=semantic_features)])


def build_soc_memory_lesson_draft_service(repository, *, settings=None):
    from soc_agent.core import SocMemoryLessonDraftService, SocMemoryService
    from soc_agent.llm import build_configured_memory_lesson_drafter

    registry = build_soc_memory_profile_registry()
    governance = SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
        memory_evolution_repository=repository,
        mutation_audit_repository=repository,
        mutation_uow=repository,
        analysis_run_repository=repository,
        profile_registry=registry,
    )
    return SocMemoryLessonDraftService(
        candidate_repository=repository,
        drafter=build_configured_memory_lesson_drafter(settings=settings),
        governance_service=governance,
        profile_registry=registry,
    )


__all__ = ["build_soc_memory_profile_registry", "build_soc_memory_lesson_draft_service"]
