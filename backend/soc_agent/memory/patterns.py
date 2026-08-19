"""Repeated-pattern source projection and in-memory persistence helpers."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime

from soc_agent.contracts import (
    AnalysisRun,
    AnalysisRunStatus,
    MemoryCenterInventory,
    MemoryCenterProfileInventory,
    MemoryPatternDataClass,
    MemoryPatternLessonObservation,
    MemoryPatternLineageStats,
    MemoryPatternLineageStatsPage,
    MemoryPatternObservation,
    MemoryPatternObservationCreateCommand,
    MemoryPatternRiskClass,
    MemoryPatternSourceRef,
    MemoryPatternSourceType,
    SocMemoryCandidate,
    SocMemoryCandidateStatus,
    Verdict,
)
from soc_agent.memory.candidates import InMemoryMemoryCandidateRepository
from soc_agent.memory.facets import reusable_facet_values
from soc_agent.memory.profiles import GenericSocMemoryProfile, SocMemoryProfile
from soc_agent.normalizers import normalize_alert_payload
from soc_agent.utils.hashing import stable_hash


class MemoryPatternIneligibleError(ValueError):
    """Raised when a Runtime result cannot safely identify a recurrence cohort."""


class MemoryPatternRepositoryConflictError(ValueError):
    """Raised when an immutable pattern observation identity conflicts."""


class InMemoryMemoryPatternRepository(InMemoryMemoryCandidateRepository):
    """Combined candidate/observation repository for tests and explicit simulations."""

    def __init__(
        self,
        *,
        observations: Iterable[MemoryPatternObservation] = (),
        candidates: Iterable[SocMemoryCandidate] = (),
    ) -> None:
        super().__init__(candidates)
        self._pattern_observations: dict[str, MemoryPatternObservation] = {}
        self._pattern_idempotency: dict[str, str] = {}
        self._pattern_source_identity: dict[tuple[str, str], str] = {}
        self._pattern_occurrence_identity: dict[tuple[str, str], str] = {}
        for observation in observations:
            self.save_memory_pattern_observation(observation)

    def save_memory_pattern_observation(
        self,
        observation: MemoryPatternObservation,
    ) -> None:
        idempotent_id = self._pattern_idempotency.get(observation.idempotency_key)
        if idempotent_id is not None:
            if self._pattern_observations[idempotent_id] != observation:
                raise MemoryPatternRepositoryConflictError(f"memory pattern idempotency key {observation.idempotency_key} already exists")
            return
        source_key = (observation.aggregation_key, observation.source.source_id)
        source_observation_id = self._pattern_source_identity.get(source_key)
        if source_observation_id is not None:
            if self._pattern_observations[source_observation_id] != observation:
                raise MemoryPatternRepositoryConflictError("memory pattern aggregation/source identity already exists")
            return
        occurrence_key = (observation.aggregation_key, observation.occurrence_key)
        occurrence_observation_id = self._pattern_occurrence_identity.get(occurrence_key)
        if occurrence_observation_id is not None:
            if self._pattern_observations[occurrence_observation_id] != observation:
                raise MemoryPatternRepositoryConflictError("memory pattern aggregation/occurrence identity already exists")
            return
        if observation.observation_id in self._pattern_observations:
            raise MemoryPatternRepositoryConflictError(f"memory pattern observation {observation.observation_id} already exists")
        self._pattern_observations[observation.observation_id] = observation
        self._pattern_idempotency[observation.idempotency_key] = observation.observation_id
        self._pattern_source_identity[source_key] = observation.observation_id
        self._pattern_occurrence_identity[occurrence_key] = observation.observation_id

    def get_memory_pattern_observation(
        self,
        observation_id: str,
    ) -> MemoryPatternObservation | None:
        return self._pattern_observations.get(observation_id)

    def find_memory_pattern_observation_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> MemoryPatternObservation | None:
        observation_id = self._pattern_idempotency.get(idempotency_key)
        if observation_id is None:
            return None
        return self._pattern_observations.get(observation_id)

    def list_memory_pattern_observations(
        self,
        *,
        aggregation_key: str | None = None,
        lineage_key: str | None = None,
        tenant_id: str | None = None,
        environment: str | None = None,
        data_class: MemoryPatternDataClass | None = None,
        source_type: MemoryPatternSourceType | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[MemoryPatternObservation]:
        observations = list(self._pattern_observations.values())
        if aggregation_key is not None:
            observations = [item for item in observations if item.aggregation_key == aggregation_key]
        if lineage_key is not None:
            observations = [item for item in observations if item.lineage_key == lineage_key]
        if tenant_id is not None:
            observations = [item for item in observations if item.tenant_id == tenant_id]
        if environment is not None:
            observations = [item for item in observations if item.environment == environment]
        if data_class is not None:
            observations = [item for item in observations if item.data_class is data_class]
        if source_type is not None:
            observations = [item for item in observations if item.source.source_type is source_type]
        return sorted(
            observations,
            key=lambda item: (item.source.observed_at, item.observation_id),
        )[offset : offset + limit]

    def get_memory_center_inventory(self) -> MemoryCenterInventory:
        observations = list(self._pattern_observations.values())
        profile_patterns: dict[tuple[str, str, str], set[str]] = {}
        profile_windows: dict[tuple[str, str, str], set[str]] = {}
        profile_observations: Counter[tuple[str, str, str]] = Counter()
        for item in observations:
            profile_key = (
                item.profile_id,
                item.profile_version,
                item.feature_schema_version,
            )
            profile_patterns.setdefault(profile_key, set()).add(item.lineage_key)
            profile_windows.setdefault(profile_key, set()).add(item.aggregation_key)
            profile_observations[profile_key] += 1
        return MemoryCenterInventory(
            pattern_count=len({item.lineage_key for item in observations}),
            aggregation_window_count=len({item.aggregation_key for item in observations}),
            observation_count=len(observations),
            candidate_status_counts=dict(Counter(item.status.value for item in self._candidates.values())),
            record_status_counts=dict(Counter(item.status.value for item in self._records.values())),
            retrieval_enabled_record_count=sum(item.retrieval_enabled for item in self._records.values()),
            profile_inventory=[
                MemoryCenterProfileInventory(
                    profile_id=key[0],
                    profile_version=key[1],
                    feature_schema_version=key[2],
                    pattern_count=len(profile_patterns[key]),
                    aggregation_window_count=len(profile_windows[key]),
                    observation_count=profile_observations[key],
                )
                for key in sorted(profile_patterns)
            ],
        )

    def list_memory_pattern_lineage_stats(
        self,
        *,
        tenant_id: str | None = None,
        environment: str | None = None,
        data_class: MemoryPatternDataClass | None = None,
        profile_id: str | None = None,
        search: str | None = None,
        include_terminal_history: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> MemoryPatternLineageStatsPage:
        groups: dict[str, list[MemoryPatternObservation]] = {}
        normalized_search = (search or "").strip().casefold()
        terminal_lineage_keys = set() if include_terminal_history else self._terminal_memory_pattern_lineage_keys()
        for item in self._pattern_observations.values():
            if item.lineage_key in terminal_lineage_keys:
                continue
            if tenant_id is not None and item.tenant_id != tenant_id:
                continue
            if environment is not None and item.environment != environment:
                continue
            if data_class is not None and item.data_class is not data_class:
                continue
            if profile_id is not None and item.profile_id != profile_id:
                continue
            if (
                normalized_search
                and normalized_search
                not in " ".join(
                    (
                        item.lineage_key,
                        item.signature.value,
                        item.signature.label,
                        item.source.alert_id,
                        item.profile_id,
                    )
                ).casefold()
            ):
                continue
            groups.setdefault(item.lineage_key, []).append(item)
        stats = [_memory_pattern_lineage_stats(items) for items in groups.values()]
        stats.sort(
            key=lambda item: (item.last_observed_at, item.lineage_key),
            reverse=True,
        )
        return MemoryPatternLineageStatsPage(
            items=stats[offset : offset + limit],
            total=len(stats),
            limit=limit,
            offset=offset,
        )

    def _terminal_memory_pattern_lineage_keys(self) -> set[str]:
        active_statuses = {
            SocMemoryCandidateStatus.PENDING_REVIEW,
            SocMemoryCandidateStatus.CONFIRMED_CANDIDATE,
            SocMemoryCandidateStatus.CONFIRMED,
        }
        candidates_by_lineage = {lineage_key: self.find_memory_candidates_by_lineage_keys([lineage_key]) for lineage_key in {item.lineage_key for item in self._pattern_observations.values()}}
        record_candidate_ids = {record.source_candidate_id for record in self._records.values()}
        return {lineage_key for lineage_key, candidates in candidates_by_lineage.items() if candidates and not any(candidate.status in active_statuses or candidate.candidate_id in record_candidate_ids for candidate in candidates)}

    def find_memory_candidates_by_lineage_keys(
        self,
        lineage_keys: Iterable[str],
    ) -> list[SocMemoryCandidate]:
        selected = set(lineage_keys)
        aggregation_keys = {item.aggregation_key for item in self._pattern_observations.values() if item.lineage_key in selected}
        source_ids = {f"memory_pattern:{item}" for item in aggregation_keys}
        return sorted(
            (item for item in self._candidates.values() if item.source.source_id in source_ids),
            key=lambda item: (item.created_at, item.candidate_id),
            reverse=True,
        )


def memory_pattern_command_from_run(
    run: AnalysisRun,
    *,
    source_type: MemoryPatternSourceType,
    transport_ref: str,
    environment: str,
    data_class: MemoryPatternDataClass,
    policy_fingerprint: str,
    profile: SocMemoryProfile | None = None,
) -> MemoryPatternObservationCreateCommand:
    """Project one completed Runtime result into a neutral recurrence observation."""

    if run.status not in {AnalysisRunStatus.SUCCESS, AnalysisRunStatus.NEEDS_REVIEW}:
        raise MemoryPatternIneligibleError("only completed Runtime results may become memory pattern observations")
    request = run.llm_analysis_request
    if request is None:
        raise MemoryPatternIneligibleError("completed Runtime result has no bounded analysis request")
    tenant_id = (request.tenant_id or "").strip()
    if not tenant_id:
        raise MemoryPatternIneligibleError("memory pattern aggregation requires an explicit tenant_id")
    resolved_profile = profile or GenericSocMemoryProfile()
    common_facets = _common_facets(run, profile=resolved_profile)
    # The observation environment is an operator-owned cohort boundary. Keep it
    # in the canonical feature set even when the analyzer request did not carry
    # an environment so tenant profiles can prevent cross-environment reuse.
    common_facets["environment"] = [environment]
    try:
        signature = resolved_profile.build_pattern_signature(
            run,
            facets=common_facets,
        )
    except ValueError as exc:
        raise MemoryPatternIneligibleError(str(exc)) from exc
    lesson = _lesson_from_run(run)
    input_identity = run.input_hash or stable_hash({"tenant_id": tenant_id, "alert_id": run.alert_id})
    source_id = stable_hash(
        {
            "tenant_id": tenant_id,
            "alert_id": run.alert_id,
            "input_identity": input_identity,
        }
    )
    observed_at = _source_observed_at(run)
    occurrence_key = resolved_profile.build_occurrence_key(
        run,
        signature=signature,
        facets=common_facets,
        observed_at=observed_at,
    )
    evidence_refs = [f"run:{run.run_id}", f"alert:{run.alert_id}"]
    grounding = run.analysis_evidence_grounding
    if grounding is not None:
        evidence_refs.extend(f"analysis_evidence:{run.run_id}:{item.evidence_index}" for item in grounding.items if item.status.value == "grounded")
    profile_identity = resolved_profile.identity
    observation_request_identity = stable_hash(
        {
            "transport_ref": transport_ref,
            "profile_id": profile_identity.profile_id,
            "profile_version": profile_identity.profile_version,
            "feature_schema_version": profile_identity.feature_schema_version,
        }
    )
    return MemoryPatternObservationCreateCommand(
        # A profile upgrade intentionally re-projects the same transport
        # occurrence. Retries within one profile remain idempotent, while an
        # old observation can never collide with its new feature contract.
        idempotency_key=(f"memory-pattern:{policy_fingerprint[:16]}:{observation_request_identity}"),
        tenant_id=tenant_id,
        environment=environment,
        data_class=data_class,
        profile_id=resolved_profile.identity.profile_id,
        profile_version=resolved_profile.identity.profile_version,
        feature_schema_version=resolved_profile.identity.feature_schema_version,
        occurrence_key=occurrence_key,
        source=MemoryPatternSourceRef(
            source_type=source_type,
            source_id=source_id,
            transport_ref=transport_ref,
            run_id=run.run_id,
            alert_id=run.alert_id,
            observed_at=observed_at,
        ),
        signature=signature,
        lesson=lesson,
        evidence_refs=list(dict.fromkeys(evidence_refs)),
        metadata={
            "pipeline_version": run.pipeline_version,
            "model_name": run.model_name,
            "prompt_version": run.prompt_version,
            "runtime_status": run.status.value,
            "window_time_source": "canonical_alert.event.event_time",
            "memory_profile_id": resolved_profile.identity.profile_id,
            "memory_profile_version": resolved_profile.identity.profile_version,
            "memory_feature_schema_version": resolved_profile.identity.feature_schema_version,
        },
    )


def _lesson_from_run(run: AnalysisRun) -> MemoryPatternLessonObservation:
    analysis = run.analysis
    if analysis is None:
        raise MemoryPatternIneligibleError("memory pattern aggregation requires a completed analysis conclusion")
    decision = run.decision
    verdict = decision.verdict if decision is not None else analysis.verdict
    primary = next(
        (item for item in analysis.scenario_assessments if item.is_primary),
        None,
    )
    direction = analysis.network_direction
    return MemoryPatternLessonObservation(
        verdict=verdict,
        risk_class=_risk_class_for_verdict(verdict),
        needs_review=(decision.needs_review if decision is not None else run.status is AnalysisRunStatus.NEEDS_REVIEW),
        evidence_state=(decision.evidence_state if decision is not None else None),
        summary=analysis.summary,
        reason=analysis.reason,
        recommended_action=analysis.recommended_action,
        primary_scenario_key=(primary.scenario_key if primary is not None else None),
        primary_scenario_name=(primary.scenario_name if primary is not None else None),
        activity_stage=(primary.activity_stage if primary is not None else None),
        boundary_direction=(direction.boundary_direction.value if direction is not None else None),
        semantic_direction=(direction.semantic_direction if direction is not None else None),
    )


def _common_facets(
    run: AnalysisRun,
    *,
    profile: SocMemoryProfile,
) -> dict[str, list[str]]:
    request = run.llm_analysis_request
    if request is None:
        return {}
    return reusable_facet_values(
        profile.project_run_facets(run),
        {
            "source_type",
            "source_system",
            "vendor",
            "product",
            "integration_name",
            "detection_key",
            "detection_signature",
            "rule_code",
            "rule_name",
            "category",
            "severity",
            "environment",
            "scenario_key",
            "behavior_component",
            "behavior_component_strong",
            "behavior_component_weak",
            "behavior_fingerprint",
            "behavior_strength",
            "role_entity",
            "entity",
            "skill",
        },
    )


def _risk_class_for_verdict(verdict: Verdict) -> MemoryPatternRiskClass:
    if verdict in {Verdict.TRUE_POSITIVE, Verdict.SUSPICIOUS}:
        return MemoryPatternRiskClass.RISK
    if verdict is Verdict.FALSE_POSITIVE:
        return MemoryPatternRiskClass.BENIGN
    return MemoryPatternRiskClass.UNRESOLVED


def _source_observed_at(run: AnalysisRun) -> datetime:
    payload = run.input_payload
    if payload is None:
        raise MemoryPatternIneligibleError("memory pattern aggregation requires the original Runtime input payload")
    try:
        event_time = normalize_alert_payload(payload).event.event_time
    except (TypeError, ValueError) as exc:
        raise MemoryPatternIneligibleError("memory pattern aggregation could not reconstruct canonical event time") from exc
    if event_time is None:
        raise MemoryPatternIneligibleError("memory pattern aggregation requires canonical alert event_time")
    if event_time.tzinfo is None or event_time.utcoffset() is None:
        raise MemoryPatternIneligibleError("canonical alert event_time must be timezone-aware")
    return event_time.astimezone(UTC)


def _memory_pattern_lineage_stats(
    observations: list[MemoryPatternObservation],
) -> MemoryPatternLineageStats:
    ordered = sorted(
        observations,
        key=lambda item: (item.source.observed_at, item.observation_id),
    )
    first = ordered[0]
    return MemoryPatternLineageStats(
        lineage_key=first.lineage_key,
        tenant_id=first.tenant_id,
        environment=first.environment,
        data_class=first.data_class,
        profile_id=first.profile_id,
        profile_version=first.profile_version,
        feature_schema_version=first.feature_schema_version,
        pattern_dimension=first.signature.dimension.value,
        pattern_value=first.signature.value,
        pattern_label=first.signature.label,
        support_count=len(ordered),
        distinct_source_count=len({item.source.source_id for item in ordered}),
        aggregation_window_count=len({item.aggregation_key for item in ordered}),
        first_observed_at=ordered[0].source.observed_at,
        last_observed_at=ordered[-1].source.observed_at,
        first_window_start=min(item.window_start for item in ordered),
        last_window_end=max(item.window_end for item in ordered),
    )


__all__ = [
    "InMemoryMemoryPatternRepository",
    "MemoryPatternIneligibleError",
    "MemoryPatternRepositoryConflictError",
    "memory_pattern_command_from_run",
]
