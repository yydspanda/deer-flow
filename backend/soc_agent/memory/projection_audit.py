"""Freeze optional fact-consumption accounting alongside the selected profile."""

from soc_agent.contracts import LLMAnalysisRequest
from soc_agent.contracts.normalization import MemoryProjectionAudit
from soc_agent.memory.profiles import SocMemoryProfile


def bind_memory_profile(request: LLMAnalysisRequest, profile: SocMemoryProfile) -> LLMAnalysisRequest:
    identity = {"profile_id": profile.identity.profile_id, "profile_version": profile.identity.profile_version, "feature_schema_version": profile.identity.feature_schema_version}
    updates = {"memory_profile": identity, "memory_projection_audit": None}
    if not request.memory_profile:
        updates["memory_aggregation_window_seconds"] = getattr(profile, "default_aggregation_window_seconds", None)
    usage_projector = getattr(profile, "projection_usage", None)
    usages = usage_projector(request) if usage_projector else []
    if usages:
        gap_projector = getattr(profile, "projection_gaps", None)
        updates["memory_projection_audit"] = MemoryProjectionAudit(
            profile=identity,
            usages=usages[:2000],
            projection_gaps=gap_projector(request) if gap_projector else [],
            omitted_usage_count=max(0, len(usages) - 2000),
        )
    return request.model_copy(update=updates)
