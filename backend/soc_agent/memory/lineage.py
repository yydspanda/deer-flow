"""Pure helpers for projecting governed Memory objects onto Pattern lineages."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from soc_agent.contracts import (
    MemoryPatternObservation,
    SocMemoryCandidate,
    SocMemoryCandidateSourceType,
)


def memory_pattern_lineage_metadata(
    observation: MemoryPatternObservation,
) -> dict[str, str]:
    """Return the stable Pattern identity safe to persist on a candidate source."""

    return {
        "lineage_key": observation.lineage_key,
        "aggregation_key": observation.aggregation_key,
        "pattern_observation_id": observation.observation_id,
        "environment": observation.environment,
        "data_class": observation.data_class.value,
        "memory_profile_id": observation.profile_id,
        "memory_profile_version": observation.profile_version,
        "memory_feature_schema_version": observation.feature_schema_version,
    }


def memory_candidate_lineage_key(candidate: SocMemoryCandidate) -> str | None:
    """Read an explicitly persisted Pattern lineage from a candidate."""

    value = candidate.metadata.get("lineage_key")
    if isinstance(value, str) and value:
        return value
    value = candidate.source.metadata.get("lineage_key")
    return value if isinstance(value, str) and value else None


def project_memory_candidates_to_pattern_lineages(
    candidates: Iterable[SocMemoryCandidate],
    observations: Iterable[MemoryPatternObservation],
) -> list[SocMemoryCandidate]:
    """Attach read-only lineage metadata using exact, auditable source identity.

    Repeated-pattern candidates link through their frozen aggregation source. An
    analyst-promoted run links through the exact Runtime run that produced an
    existing Pattern observation. The projection never changes stored candidates
    or Pattern support counts.
    """

    selected_observations = list(observations)
    selected_lineages = {item.lineage_key for item in selected_observations}
    by_aggregation: dict[str, list[MemoryPatternObservation]] = defaultdict(list)
    by_run: dict[str, list[MemoryPatternObservation]] = defaultdict(list)
    for observation in selected_observations:
        by_aggregation[observation.aggregation_key].append(observation)
        by_run[observation.source.run_id].append(observation)

    projected: dict[tuple[str, str], SocMemoryCandidate] = {}
    for candidate in candidates:
        explicit_lineage = memory_candidate_lineage_key(candidate)
        if explicit_lineage is not None:
            if explicit_lineage in selected_lineages:
                projected[(candidate.candidate_id, explicit_lineage)] = candidate
            continue

        candidate_observations: list[MemoryPatternObservation] = []
        source_id = candidate.source.source_id or ""
        if source_id.startswith("memory_pattern:"):
            candidate_observations.extend(by_aggregation.get(source_id.removeprefix("memory_pattern:"), []))
        elif _is_manual_run_promotion(candidate) and candidate.source.run_id:
            candidate_observations.extend(by_run.get(candidate.source.run_id, []))

        for observation in candidate_observations:
            linked = _project_candidate(candidate, observation)
            if linked is not None:
                projected[(candidate.candidate_id, observation.lineage_key)] = linked

    return sorted(
        projected.values(),
        key=lambda item: (item.created_at, item.candidate_id),
        reverse=True,
    )


def _project_candidate(
    candidate: SocMemoryCandidate,
    observation: MemoryPatternObservation,
) -> SocMemoryCandidate | None:
    if candidate.source.run_id and candidate.source.run_id != observation.source.run_id:
        return None
    if candidate.source.alert_id and candidate.source.alert_id != observation.source.alert_id:
        return None
    if candidate.tenant_id and candidate.tenant_id != observation.tenant_id:
        return None
    if not _profile_matches(candidate, observation):
        return None
    if not _environment_matches(candidate, observation):
        return None

    lineage_metadata = memory_pattern_lineage_metadata(observation)
    return candidate.model_copy(
        update={
            "source": candidate.source.model_copy(
                update={
                    "metadata": {
                        **candidate.source.metadata,
                        **lineage_metadata,
                    }
                }
            ),
            "metadata": {
                **candidate.metadata,
                **lineage_metadata,
            },
        }
    )


def _is_manual_run_promotion(candidate: SocMemoryCandidate) -> bool:
    return candidate.source.source_type is SocMemoryCandidateSourceType.MANUAL_NOTE and (candidate.source.metadata.get("promote_to_memory") is True or candidate.metadata.get("source") == "manual_run_promotion")


def _profile_matches(
    candidate: SocMemoryCandidate,
    observation: MemoryPatternObservation,
) -> bool:
    if candidate.applicability is not None:
        candidate_profile = (
            candidate.applicability.profile_id,
            candidate.applicability.profile_version,
            candidate.applicability.feature_schema_version,
        )
    else:
        candidate_profile = tuple(
            candidate.metadata.get(key) or candidate.source.metadata.get(key)
            for key in (
                "memory_profile_id",
                "memory_profile_version",
                "memory_feature_schema_version",
            )
        )
        if not all(isinstance(item, str) and item for item in candidate_profile):
            return False
    return candidate_profile == (
        observation.profile_id,
        observation.profile_version,
        observation.feature_schema_version,
    )


def _environment_matches(
    candidate: SocMemoryCandidate,
    observation: MemoryPatternObservation,
) -> bool:
    environments: list[str] = []
    if candidate.applicability is not None:
        environments.extend(candidate.applicability.required_facets.get("environment", []))
    source_environment = candidate.source.metadata.get("environment")
    if isinstance(source_environment, str) and source_environment:
        environments.append(source_environment)
    return not environments or observation.environment in environments


__all__ = [
    "memory_candidate_lineage_key",
    "memory_pattern_lineage_metadata",
    "project_memory_candidates_to_pattern_lineages",
]
