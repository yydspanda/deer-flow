"""Repeated-pattern source projection and in-memory persistence helpers."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from soc_agent.contracts import (
    AnalysisRun,
    AnalysisRunStatus,
    MemoryPatternDataClass,
    MemoryPatternDimension,
    MemoryPatternObservation,
    MemoryPatternObservationCreateCommand,
    MemoryPatternSignature,
    MemoryPatternSourceRef,
    MemoryPatternSourceType,
    SocMemoryCandidate,
)
from soc_agent.memory.candidates import InMemoryMemoryCandidateRepository
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
        if observation.observation_id in self._pattern_observations:
            raise MemoryPatternRepositoryConflictError(f"memory pattern observation {observation.observation_id} already exists")
        self._pattern_observations[observation.observation_id] = observation
        self._pattern_idempotency[observation.idempotency_key] = observation.observation_id
        self._pattern_source_identity[source_key] = observation.observation_id

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
        )[:limit]


def memory_pattern_command_from_run(
    run: AnalysisRun,
    *,
    source_type: MemoryPatternSourceType,
    transport_ref: str,
    environment: str,
    data_class: MemoryPatternDataClass,
    policy_fingerprint: str,
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
    signature = _signature_from_run(run)
    input_identity = run.input_hash or stable_hash({"tenant_id": tenant_id, "alert_id": run.alert_id})
    source_id = stable_hash(
        {
            "tenant_id": tenant_id,
            "alert_id": run.alert_id,
            "input_identity": input_identity,
        }
    )
    observed_at = _source_observed_at(run)
    evidence_refs = [f"run:{run.run_id}", f"alert:{run.alert_id}"]
    grounding = run.analysis_evidence_grounding
    if grounding is not None:
        evidence_refs.extend(f"analysis_evidence:{run.run_id}:{item.evidence_index}" for item in grounding.items if item.status.value == "grounded")
    return MemoryPatternObservationCreateCommand(
        idempotency_key=(f"memory-pattern:{policy_fingerprint[:16]}:{stable_hash(transport_ref)}"),
        tenant_id=tenant_id,
        environment=environment,
        data_class=data_class,
        source=MemoryPatternSourceRef(
            source_type=source_type,
            source_id=source_id,
            transport_ref=transport_ref,
            run_id=run.run_id,
            alert_id=run.alert_id,
            observed_at=observed_at,
        ),
        signature=signature,
        evidence_refs=list(dict.fromkeys(evidence_refs)),
        metadata={
            "pipeline_version": run.pipeline_version,
            "model_name": run.model_name,
            "prompt_version": run.prompt_version,
            "runtime_status": run.status.value,
            "window_time_source": "canonical_alert.event.event_time",
        },
    )


def _signature_from_run(run: AnalysisRun) -> MemoryPatternSignature:
    request = run.llm_analysis_request
    if request is None:
        raise MemoryPatternIneligibleError("bounded analysis request is required")
    primary = next(
        (item for item in (run.analysis.scenario_assessments if run.analysis else []) if item.is_primary),
        None,
    )
    common_facets = _common_facets(run)
    if primary is not None:
        value = _normalize_pattern_value(primary.scenario_key or primary.scenario_name)
        return MemoryPatternSignature(
            dimension=MemoryPatternDimension.SCENARIO,
            value=value,
            label=primary.scenario_name,
            origin=f"analysis:{primary.origin.value}",
            facets={
                **common_facets,
                "scenario_origin": [primary.origin.value],
                "activity_stage": [primary.activity_stage.value],
            },
        )
    if request.detection.detection_key:
        return MemoryPatternSignature(
            dimension=MemoryPatternDimension.DETECTION,
            value=_normalize_pattern_value(request.detection.detection_key),
            label=request.detection.rule_name or request.detection.detection_key,
            origin="canonical_detection",
            facets=common_facets,
        )
    if request.classification.category:
        return MemoryPatternSignature(
            dimension=MemoryPatternDimension.CATEGORY,
            value=_normalize_pattern_value(request.classification.category),
            label=request.classification.category,
            origin="canonical_category",
            facets=common_facets,
        )
    raise MemoryPatternIneligibleError("Runtime result has no primary scenario, detection key, or category")


def _common_facets(run: AnalysisRun) -> dict[str, list[str]]:
    request = run.llm_analysis_request
    if request is None:
        return {}
    facets: dict[str, list[str]] = {"source_type": [request.source.source_type.value]}
    for key, value in (
        ("source_system", request.source.source_system),
        ("category", request.classification.category),
        ("detection_key", request.detection.detection_key),
    ):
        if value:
            facets[key] = [value]
    return facets


def _normalize_pattern_value(value: str) -> str:
    normalized = " ".join(value.split()).casefold()
    if not normalized:
        raise MemoryPatternIneligibleError("memory pattern value is blank")
    if len(normalized) <= 256:
        return normalized
    return f"sha256:{stable_hash(normalized)}"


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


__all__ = [
    "InMemoryMemoryPatternRepository",
    "MemoryPatternIneligibleError",
    "MemoryPatternRepositoryConflictError",
    "memory_pattern_command_from_run",
]
