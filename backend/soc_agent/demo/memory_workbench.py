"""Browser-driven DEV workbench for one reviewed repeated-alert cohort."""

from __future__ import annotations

import copy
import hashlib
import pickle
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from soc_agent.contracts import (
    AnalysisRun,
    AnalysisRunStatus,
    LLMAnalysisRequest,
    MemoryPatternDataClass,
    MemoryPatternSourceType,
    ServiceRequestContext,
    SocMemoryCandidate,
    SocMemoryRecord,
)
from soc_agent.core import SocAnalysisService, SocMemoryPatternService
from soc_agent.db import SqlAlchemyAlertRepository
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile
from soc_agent.llm import SocLLMSettings
from soc_agent.normalizers import normalize_alert_payload
from soc_agent.utils.hashing import stable_hash

MEMORY_WORKBENCH_VERSION = "soc.memory_dev_workbench.v1"
MEMORY_WORKBENCH_ENVIRONMENT = "dev"
MEMORY_WORKBENCH_TENANT = "pingan"
MEMORY_WORKBENCH_RULE_CODE = "RPAADM_002010"
MEMORY_WORKBENCH_RULE_NAME = "GalaxyLab_T1003-SAM-Dumping"
MEMORY_WORKBENCH_DETECTION_KEY = "leagsoft-edr:rule_code:rpaadm_002010"

_ALLOWED_PICKLE_GLOBALS = {
    ("pandas", "DataFrame"),
    ("pandas", "Index"),
    ("pandas", "RangeIndex"),
    ("pandas.core.frame", "DataFrame"),
    ("pandas.core.internals.managers", "BlockManager"),
    ("pandas._libs.internals", "_unpickle_block"),
    ("numpy.core.numeric", "_frombuffer"),
    ("numpy._core.numeric", "_frombuffer"),
    ("numpy", "dtype"),
    ("builtins", "slice"),
    ("numpy.core.multiarray", "_reconstruct"),
    ("numpy._core.multiarray", "_reconstruct"),
    ("numpy", "ndarray"),
    ("collections", "Counter"),
    ("pandas.core.indexes.base", "_new_Index"),
    ("pandas.core.indexes.base", "Index"),
    ("pandas.core.indexes.range", "RangeIndex"),
}


class SocMemoryWorkbenchError(ValueError):
    """Base error for an invalid local workbench operation."""


class SocMemoryWorkbenchConflictError(SocMemoryWorkbenchError):
    """Raised when a user attempts to skip a governed workbench step."""


@dataclass(frozen=True)
class _CaseSpec:
    alert_id: str
    phase: Literal["construction", "held_out", "additional"]
    phase_order: int


@dataclass(frozen=True)
class _LoadedCase:
    spec: _CaseSpec
    payload: dict[str, Any]
    payload_hash: str
    observed_at: str
    endpoint: str | None
    host_name: str | None
    process_names: tuple[str, ...]
    behavior_fingerprint: str
    behavior_components: tuple[str, ...]
    source_index: int


_CASE_SPECS = (
    _CaseSpec("1984426", "construction", 1),
    _CaseSpec("1984281", "construction", 2),
    _CaseSpec("1984525", "construction", 3),
    _CaseSpec("1984510", "construction", 4),
    _CaseSpec("1984659", "construction", 5),
    _CaseSpec("1984919", "held_out", 1),
    _CaseSpec("1966874", "additional", 1),
    _CaseSpec("1966879", "additional", 2),
    _CaseSpec("1967880", "additional", 3),
    _CaseSpec("1974113", "additional", 4),
    _CaseSpec("1980607", "additional", 5),
    _CaseSpec("1980502", "additional", 6),
    _CaseSpec("1980722", "additional", 7),
    _CaseSpec("1982981", "additional", 8),
)


class SocMemoryWorkbenchModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    model_name: str | None = None
    thinking_enabled: bool
    role_verifier_enabled: bool
    role_verifier_model_name: str | None = None


class SocMemoryWorkbenchSafety(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment: Literal["dev"] = "dev"
    database_backend: Literal["sqlite"] = "sqlite"
    database_file: str
    source_data_class: Literal["operational"] = "operational"
    historical_replay: Literal[True] = True
    internal_providers: Literal["off_or_mock"] = "off_or_mock"
    tenant_policy: Literal["disabled"] = "disabled"
    external_action_execution: Literal[False] = False


class SocMemoryWorkbenchSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_alert_count: Literal[14] = 14


class SocMemoryWorkbenchCohort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: Literal["pingan"] = "pingan"
    rule_code: Literal["RPAADM_002010"] = "RPAADM_002010"
    rule_name: Literal["GalaxyLab_T1003-SAM-Dumping"] = "GalaxyLab_T1003-SAM-Dumping"
    detection_key: Literal["leagsoft-edr:rule_code:rpaadm_002010"] = "leagsoft-edr:rule_code:rpaadm_002010"
    behavior_fingerprint: str
    behavior_components: list[str]
    construction_target: Literal[5] = 5
    held_out_target: Literal[1] = 1
    additional_count: Literal[8] = 8


class SocMemoryWorkbenchDecisionStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    status: str
    verdict: str
    confidence: float
    needs_review: bool
    suggested_action: str
    disposition: str | None = None
    source_id: str | None = None
    summary: str


class SocMemoryWorkbenchMemoryContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_ref: str
    label: str
    source_id: str
    summary: str


class SocMemoryWorkbenchAlert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_id: str
    phase: Literal["construction", "held_out", "additional"]
    phase_order: int
    observed_at: str
    endpoint: str | None = None
    host_name: str | None = None
    process_names: list[str]
    workflow_state: Literal[
        "locked",
        "ready",
        "analysis_only",
        "completed",
        "failed",
    ]
    can_process: bool
    run_id: str | None = None
    analysis_status: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    total_duration_ms: int | None = None
    output_quality: str | None = None
    base_verdict: str | None = None
    base_confidence: float | None = None
    base_needs_review: bool | None = None
    effective_verdict: str | None = None
    effective_confidence: float | None = None
    effective_needs_review: bool | None = None
    analysis_summary: str | None = None
    analysis_reason: str | None = None
    queue_id: str | None = None
    observation_id: str | None = None
    aggregation_key: str | None = None
    pattern_support_count: int | None = None
    pattern_distinct_source_count: int | None = None
    pattern_quality_gate_passed: bool | None = None
    pattern_consistency_ratio: float | None = None
    memory_contexts: list[SocMemoryWorkbenchMemoryContext] = Field(default_factory=list)
    decision_stages: list[SocMemoryWorkbenchDecisionStage] = Field(default_factory=list)


class SocMemoryWorkbenchCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    status: str
    candidate_type: str
    summary: str
    support_count: int
    distinct_source_count: int
    consistency_ratio: float
    source_run_id: str | None = None
    source_alert_id: str | None = None
    review_queue_id: str | None = None
    memory_id: str | None = None
    memory_status: str | None = None
    retrieval_enabled: bool = False
    decision_directive_ready: bool = False
    business_lesson_ready: bool = False


class SocMemoryWorkbenchProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processed_count: int
    construction_processed: int
    construction_target: Literal[5] = 5
    candidate_state: Literal[
        "collecting",
        "quality_gate_blocked",
        "pending_review",
        "confirmed_candidate",
        "confirmed",
        "rejected",
        "expired",
        "deprecated",
    ]
    memory_state: Literal[
        "not_created",
        "confirmed_inactive",
        "confirmed_context_only",
        "decision_ready",
    ]
    held_out_unlocked: bool
    held_out_processed: bool
    next_alert_id: str | None = None
    next_action: Literal[
        "process_construction",
        "review_candidate",
        "enable_memory",
        "process_held_out",
        "process_additional",
        "quality_gate_blocked",
        "complete",
    ]


class SocMemoryWorkbenchState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_dev_workbench.v1"] = "soc.memory_dev_workbench.v1"
    safety: SocMemoryWorkbenchSafety
    source: SocMemoryWorkbenchSource
    model: SocMemoryWorkbenchModelConfig
    cohort: SocMemoryWorkbenchCohort
    progress: SocMemoryWorkbenchProgress
    candidate: SocMemoryWorkbenchCandidate | None = None
    alerts: list[SocMemoryWorkbenchAlert]


class SocMemoryWorkbenchProcessResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_dev_workbench_process.v1"] = "soc.memory_dev_workbench_process.v1"
    alert_id: str
    run_id: str | None = None
    observation_id: str | None = None
    idempotent: bool
    state: SocMemoryWorkbenchState


class _RestrictedDataFrameUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> Any:
        if (module, name) not in _ALLOWED_PICKLE_GLOBALS:
            raise pickle.UnpicklingError(f"blocked pickle global: {module}.{name}")
        return super().find_class(module, name)


class SocMemoryWorkbenchService:
    """Orchestrate a fixed DEV cohort through official SOC application services."""

    def __init__(
        self,
        *,
        repository: SqlAlchemyAlertRepository,
        analysis_service: SocAnalysisService,
        pattern_service: SocMemoryPatternService,
        source_path: Path,
        settings: SocLLMSettings,
        database_file: str,
    ) -> None:
        self._repository = repository
        self._analysis_service = analysis_service
        self._pattern_service = pattern_service
        self._source_path = source_path.expanduser().resolve()
        self._settings = settings
        self._database_file = database_file
        self._source_sha256 = _sha256_file(self._source_path)
        self._cases = _load_cases(self._source_path)
        fingerprints = {item.behavior_fingerprint for item in self._cases.values()}
        component_sets = {item.behavior_components for item in self._cases.values()}
        if len(fingerprints) != 1 or len(component_sets) != 1:
            raise SocMemoryWorkbenchError("the reviewed DEV cohort no longer has one PingAn v4 behavior fingerprint")
        self._behavior_fingerprint = next(iter(fingerprints))
        self._behavior_components = next(iter(component_sets))

    def get_state(self) -> SocMemoryWorkbenchState:
        runs = self._runs_by_alert()
        queues_by_run = {
            item.run_id: item
            for item in self._repository.list_review_items(
                status=None,
                limit=500,
            )
        }
        observations = self._repository.list_memory_pattern_observations(
            tenant_id=MEMORY_WORKBENCH_TENANT,
            environment=MEMORY_WORKBENCH_ENVIRONMENT,
            data_class=MemoryPatternDataClass.OPERATIONAL,
            source_type=MemoryPatternSourceType.BATCH_ALERT,
            limit=500,
        )
        profile_identity = PingAnSocMemoryProfile.identity
        observations = [item for item in observations if item.profile_id == profile_identity.profile_id and item.profile_version == profile_identity.profile_version and item.feature_schema_version == profile_identity.feature_schema_version]
        observations_by_alert = {item.source.alert_id: item for item in observations if item.source.alert_id in self._cases}
        replay_by_key = {item.aggregation_key: self._pattern_service.replay(item.aggregation_key) for item in observations_by_alert.values()}
        candidate = _candidate_for_observations(
            self._repository,
            observations_by_alert.values(),
        )
        record = self._repository.get_memory_record_by_candidate_id(candidate.candidate_id) if candidate is not None else None
        candidate_view = _candidate_view(
            candidate,
            record=record,
            queues_by_run=queues_by_run,
        )
        progress = _progress(
            observations_by_alert=observations_by_alert,
            candidate=candidate,
            record=record,
            replay_by_key=replay_by_key,
        )
        alerts = [
            self._alert_view(
                case,
                run=runs.get(case.spec.alert_id),
                observation=observations_by_alert.get(case.spec.alert_id),
                replay_by_key=replay_by_key,
                queue_by_run=queues_by_run,
                can_process=(progress.next_alert_id == case.spec.alert_id),
            )
            for case in self._cases.values()
        ]
        return SocMemoryWorkbenchState(
            safety=SocMemoryWorkbenchSafety(
                database_file=self._database_file,
            ),
            source=SocMemoryWorkbenchSource(
                file_name=self._source_path.name,
                sha256=self._source_sha256,
            ),
            model=SocMemoryWorkbenchModelConfig(
                mode=self._settings.mode.value,
                model_name=self._settings.model_name,
                thinking_enabled=self._settings.thinking_enabled,
                role_verifier_enabled=self._settings.role_verifier_enabled,
                role_verifier_model_name=(self._settings.role_verifier_model_name),
            ),
            cohort=SocMemoryWorkbenchCohort(
                behavior_fingerprint=self._behavior_fingerprint,
                behavior_components=list(self._behavior_components),
            ),
            progress=progress,
            candidate=candidate_view,
            alerts=alerts,
        )

    def process_alert(
        self,
        alert_id: str,
        *,
        context: ServiceRequestContext,
    ) -> SocMemoryWorkbenchProcessResult:
        case = self._cases.get(alert_id)
        if case is None:
            raise SocMemoryWorkbenchError(f"alert {alert_id!r} is not part of the fixed DEV cohort")
        if "soc_admin" not in context.actor.roles:
            raise SocMemoryWorkbenchError("the DEV memory workbench requires the soc_admin role")

        before = self.get_state()
        current = next(item for item in before.alerts if item.alert_id == alert_id)
        if current.observation_id is not None:
            return SocMemoryWorkbenchProcessResult(
                alert_id=alert_id,
                run_id=current.run_id,
                observation_id=current.observation_id,
                idempotent=True,
                state=before,
            )
        if before.progress.next_alert_id != alert_id:
            raise SocMemoryWorkbenchConflictError(f"alert {alert_id} is locked; next alert is {before.progress.next_alert_id or 'none'}")

        idempotency_key = f"soc-memory-dev:{self._source_sha256[:16]}:{alert_id}:{PingAnSocMemoryProfile.identity.feature_schema_version}"
        request_context = context.model_copy(update={"idempotency_key": idempotency_key})
        run = self._analysis_service.analyze(
            copy.deepcopy(case.payload),
            context=request_context,
        )
        if run.status is AnalysisRunStatus.FAILED:
            state = self.get_state()
            return SocMemoryWorkbenchProcessResult(
                alert_id=alert_id,
                run_id=run.run_id,
                idempotent=False,
                state=state,
            )

        aggregation = self._pattern_service.observe_run(
            run,
            source_type=MemoryPatternSourceType.BATCH_ALERT,
            transport_ref=(f"soc-memory-dev-web:{self._source_sha256}:{alert_id}:v1"),
            environment=MEMORY_WORKBENCH_ENVIRONMENT,
            data_class=MemoryPatternDataClass.OPERATIONAL,
            context=request_context,
        )
        return SocMemoryWorkbenchProcessResult(
            alert_id=alert_id,
            run_id=run.run_id,
            observation_id=aggregation.observation.observation_id,
            idempotent=aggregation.idempotent,
            state=self.get_state(),
        )

    def _runs_by_alert(self) -> dict[str, AnalysisRun]:
        selected: dict[str, AnalysisRun] = {}
        for run in self._repository.list_runs(limit=500):
            case = self._cases.get(run.alert_id)
            if case is None or run.input_hash != case.payload_hash:
                continue
            selected.setdefault(run.alert_id, run)
        return selected

    def _alert_view(
        self,
        case: _LoadedCase,
        *,
        run: AnalysisRun | None,
        observation: Any | None,
        replay_by_key: Mapping[str, Any],
        queue_by_run: Mapping[str, Any],
        can_process: bool,
    ) -> SocMemoryWorkbenchAlert:
        transition = None
        queue = None
        if run is not None:
            transitions = self._repository.list_decision_transitions(
                run_id=run.run_id,
                limit=10,
            )
            transition = transitions[0] if transitions else None
            queue = queue_by_run.get(run.run_id)
        replay = replay_by_key.get(observation.aggregation_key) if observation is not None else None
        decision = run.decision if run is not None else None
        effective = transition.after if transition is not None else decision
        analysis = run.analysis if run is not None else None
        memory_contexts = []
        if run is not None and run.llm_analysis_request is not None:
            for item in run.llm_analysis_request.context_catalog:
                if item.kind.value != "confirmed_memory":
                    continue
                memory_contexts.append(
                    SocMemoryWorkbenchMemoryContext(
                        context_ref=item.context_ref,
                        label=item.label,
                        source_id=item.source_id,
                        summary=item.summary,
                    )
                )
        stages = []
        if transition is not None:
            stages = [
                SocMemoryWorkbenchDecisionStage(
                    stage=item.stage.value,
                    status=item.status.value,
                    verdict=item.after.verdict.value,
                    confidence=item.after.confidence,
                    needs_review=item.after.needs_review,
                    suggested_action=item.after.suggested_action,
                    disposition=(item.disposition_after.value if item.disposition_after is not None else None),
                    source_id=item.source_id,
                    summary=item.summary,
                )
                for item in transition.stages
            ]
        workflow_state: Literal["locked", "ready", "analysis_only", "completed", "failed"]
        if observation is not None:
            workflow_state = "completed"
        elif run is not None and run.status is AnalysisRunStatus.FAILED:
            workflow_state = "failed"
        elif run is not None:
            workflow_state = "analysis_only"
        elif can_process:
            workflow_state = "ready"
        else:
            workflow_state = "locked"
        return SocMemoryWorkbenchAlert(
            alert_id=case.spec.alert_id,
            phase=case.spec.phase,
            phase_order=case.spec.phase_order,
            observed_at=case.observed_at,
            endpoint=case.endpoint,
            host_name=case.host_name,
            process_names=list(case.process_names),
            workflow_state=workflow_state,
            can_process=can_process,
            run_id=(run.run_id if run is not None else None),
            analysis_status=(run.status.value if run is not None else None),
            model_name=(run.model_name if run is not None else None),
            prompt_version=(run.prompt_version if run is not None else None),
            total_duration_ms=(run.total_duration_ms if run is not None else None),
            output_quality=(run.analysis_output_quality.status.value if run is not None and run.analysis_output_quality is not None else None),
            base_verdict=(decision.verdict.value if decision else None),
            base_confidence=(decision.confidence if decision else None),
            base_needs_review=(decision.needs_review if decision else None),
            effective_verdict=(effective.verdict.value if effective is not None else None),
            effective_confidence=(effective.confidence if effective is not None else None),
            effective_needs_review=(effective.needs_review if effective is not None else None),
            analysis_summary=(analysis.summary if analysis else None),
            analysis_reason=(analysis.reason if analysis else None),
            queue_id=(queue.queue_id if queue is not None else None),
            observation_id=(observation.observation_id if observation is not None else None),
            aggregation_key=(observation.aggregation_key if observation is not None else None),
            pattern_support_count=(replay.support_count if replay is not None else None),
            pattern_distinct_source_count=(replay.distinct_source_count if replay is not None else None),
            pattern_quality_gate_passed=(replay.cohort_quality.quality_gate_passed if replay is not None else None),
            pattern_consistency_ratio=(replay.cohort_quality.consistency_ratio if replay is not None else None),
            memory_contexts=memory_contexts,
            decision_stages=stages,
        )


def _candidate_for_observations(
    repository: SqlAlchemyAlertRepository,
    observations: Any,
) -> SocMemoryCandidate | None:
    for observation in observations:
        candidate = repository.find_memory_candidate_by_source_id(f"memory_pattern:{observation.aggregation_key}")
        if candidate is not None:
            return candidate
    return None


def _candidate_view(
    candidate: SocMemoryCandidate | None,
    *,
    record: SocMemoryRecord | None,
    queues_by_run: Mapping[str, Any],
) -> SocMemoryWorkbenchCandidate | None:
    if candidate is None:
        return None
    quality = candidate.metadata.get("cohort_quality")
    quality = quality if isinstance(quality, Mapping) else {}
    queue = queues_by_run.get(candidate.source.run_id) if candidate.source.run_id is not None else None
    return SocMemoryWorkbenchCandidate(
        candidate_id=candidate.candidate_id,
        status=candidate.status.value,
        candidate_type=candidate.candidate_type.value,
        summary=candidate.summary,
        support_count=int(candidate.metadata.get("support_count_at_creation", 0)),
        distinct_source_count=int(candidate.metadata.get("distinct_source_count_at_creation", 0)),
        consistency_ratio=float(quality.get("consistency_ratio", 0.0)),
        source_run_id=candidate.source.run_id,
        source_alert_id=candidate.source.alert_id,
        review_queue_id=(queue.queue_id if queue is not None else None),
        memory_id=(record.memory_id if record is not None else None),
        memory_status=(record.status.value if record is not None else None),
        retrieval_enabled=(record.retrieval_enabled if record is not None else False),
        decision_directive_ready=(record is not None and record.decision_directive is not None),
        business_lesson_ready=(record is not None and record.business_lesson is not None),
    )


def _progress(
    *,
    observations_by_alert: Mapping[str, Any],
    candidate: SocMemoryCandidate | None,
    record: SocMemoryRecord | None,
    replay_by_key: Mapping[str, Any],
) -> SocMemoryWorkbenchProgress:
    construction = [item for item in _CASE_SPECS if item.phase == "construction"]
    construction_processed = sum(item.alert_id in observations_by_alert for item in construction)
    held_out = next(item for item in _CASE_SPECS if item.phase == "held_out")
    held_out_processed = held_out.alert_id in observations_by_alert
    decision_ready = bool(record is not None and record.retrieval_enabled and record.decision_directive is not None and record.business_lesson is not None)
    candidate_state: Literal[
        "collecting",
        "quality_gate_blocked",
        "pending_review",
        "confirmed_candidate",
        "confirmed",
        "rejected",
        "expired",
        "deprecated",
    ]
    if candidate is None:
        gate_blocked = construction_processed == len(construction) and any(report.threshold_met and not report.cohort_quality.quality_gate_passed for report in replay_by_key.values())
        candidate_state = "quality_gate_blocked" if gate_blocked else "collecting"
    else:
        candidate_state = candidate.status.value
    if record is None:
        memory_state: Literal[
            "not_created",
            "confirmed_inactive",
            "confirmed_context_only",
            "decision_ready",
        ] = "not_created"
    elif decision_ready:
        memory_state = "decision_ready"
    elif record.retrieval_enabled:
        memory_state = "confirmed_context_only"
    else:
        memory_state = "confirmed_inactive"

    next_alert_id = None
    next_action: Literal[
        "process_construction",
        "review_candidate",
        "enable_memory",
        "process_held_out",
        "process_additional",
        "quality_gate_blocked",
        "complete",
    ]
    next_construction = next(
        (item for item in construction if item.alert_id not in observations_by_alert),
        None,
    )
    if next_construction is not None:
        next_alert_id = next_construction.alert_id
        next_action = "process_construction"
    elif candidate is None:
        next_action = "quality_gate_blocked"
    elif record is None:
        next_action = "review_candidate"
    elif not decision_ready:
        next_action = "enable_memory"
    elif not held_out_processed:
        next_alert_id = held_out.alert_id
        next_action = "process_held_out"
    else:
        next_additional = next(
            (item for item in _CASE_SPECS if item.phase == "additional" and item.alert_id not in observations_by_alert),
            None,
        )
        if next_additional is None:
            next_action = "complete"
        else:
            next_alert_id = next_additional.alert_id
            next_action = "process_additional"
    return SocMemoryWorkbenchProgress(
        processed_count=len(observations_by_alert),
        construction_processed=construction_processed,
        candidate_state=candidate_state,
        memory_state=memory_state,
        held_out_unlocked=decision_ready,
        held_out_processed=held_out_processed,
        next_alert_id=next_alert_id,
        next_action=next_action,
    )


def _load_cases(path: Path) -> dict[str, _LoadedCase]:
    if not path.is_file():
        raise SocMemoryWorkbenchError(f"configured DEV memory corpus does not exist: {path}")
    try:
        import pandas as pd
    except ImportError as exc:
        raise SocMemoryWorkbenchError("DEV memory workbench requires the backend pingan-dev dependencies") from exc
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        with path.open("rb") as handle:
            frame = _RestrictedDataFrameUnpickler(handle).load()
    if not isinstance(frame, pd.DataFrame):
        raise SocMemoryWorkbenchError(f"expected pandas DataFrame, got {type(frame)!r}")
    required = {"alert_id", "alert_full_data"}
    missing = required.difference(frame.columns)
    if missing:
        raise SocMemoryWorkbenchError(f"DEV memory corpus is missing columns: {sorted(missing)}")

    specs = {item.alert_id: item for item in _CASE_SPECS}
    loaded: dict[str, _LoadedCase] = {}
    profile = PingAnSocMemoryProfile()
    for source_index, row in frame.iterrows():
        alert_id = str(row.get("alert_id", "")).strip()
        spec = specs.get(alert_id)
        if spec is None:
            continue
        wrapper = row.get("alert_full_data")
        if not isinstance(wrapper, Mapping):
            raise SocMemoryWorkbenchError(f"alert {alert_id} has no alert_full_data object")
        raw_payload = wrapper.get("alert_data")
        if not isinstance(raw_payload, Mapping):
            raise SocMemoryWorkbenchError(f"alert {alert_id} has no alert_full_data.alert_data object")
        payload = copy.deepcopy(dict(raw_payload))
        source_tenant_id = payload.get("tenant_id") or payload.get("tenantId")
        if source_tenant_id is not None and str(source_tenant_id) != MEMORY_WORKBENCH_TENANT:
            raise SocMemoryWorkbenchError(f"alert {alert_id} declares unexpected tenant {source_tenant_id!r}")
        if source_tenant_id is None:
            # The local historical export predates the multi-tenant envelope.
            # This is the same trusted-ingress default used by the batch runner;
            # the source pickle remains unchanged.
            payload["tenant_id"] = MEMORY_WORKBENCH_TENANT
        alert = normalize_alert_payload(payload)
        if alert.alert_id != alert_id:
            raise SocMemoryWorkbenchError(f"corpus row {alert_id} normalized to alert {alert.alert_id}")
        if alert.tenant_id != MEMORY_WORKBENCH_TENANT:
            raise SocMemoryWorkbenchError(f"alert {alert_id} is outside the PingAn tenant boundary")
        if alert.detection.detection_key != MEMORY_WORKBENCH_DETECTION_KEY:
            raise SocMemoryWorkbenchError(f"alert {alert_id} does not match the reviewed detection cohort")
        if alert.event.event_time is None:
            raise SocMemoryWorkbenchError(f"alert {alert_id} has no canonical event_time")
        endpoint = alert.entities.host.ip_addresses[0] if alert.entities.host.ip_addresses else alert.entities.network.source_ip or alert.entities.network.destination_ip
        process_names = _process_names(alert.entities.process)
        memory_facets = profile.project_query_facets(
            LLMAnalysisRequest(
                alert_id=alert.alert_id,
                tenant_id=alert.tenant_id,
                environment=MEMORY_WORKBENCH_ENVIRONMENT,
                source=alert.source,
                detection=alert.detection,
                classification=alert.classification,
                canonical_entities=alert.entities,
            )
        )
        behavior_fingerprints = memory_facets.get("behavior_fingerprint", [])
        behavior_components = memory_facets.get("behavior_component_core", [])
        if len(behavior_fingerprints) != 1 or not behavior_components:
            raise SocMemoryWorkbenchError(f"alert {alert_id} has no decision-eligible PingAn v4 behavior fingerprint")
        loaded[alert_id] = _LoadedCase(
            spec=spec,
            payload=payload,
            payload_hash=stable_hash(payload),
            observed_at=alert.event.event_time.isoformat(),
            endpoint=endpoint,
            host_name=alert.entities.host.host_name,
            process_names=tuple(process_names),
            behavior_fingerprint=behavior_fingerprints[0],
            behavior_components=tuple(behavior_components),
            source_index=int(source_index),
        )
    missing_alerts = sorted(set(specs).difference(loaded))
    if missing_alerts:
        raise SocMemoryWorkbenchError(f"DEV memory corpus is missing cohort alerts: {missing_alerts}")
    return {item.alert_id: loaded[item.alert_id] for item in _CASE_SPECS}


def _process_names(process: Any) -> list[str]:
    values: list[str] = []
    for value in (process.process_name, process.parent_process_name):
        if value and value not in values:
            values.append(value)
    for observation in process.observations:
        for node in observation.nodes:
            if node.process_name not in values:
                values.append(node.process_name)
    return values[:8]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "MEMORY_WORKBENCH_ENVIRONMENT",
    "MEMORY_WORKBENCH_VERSION",
    "SocMemoryWorkbenchConflictError",
    "SocMemoryWorkbenchError",
    "SocMemoryWorkbenchProcessResult",
    "SocMemoryWorkbenchService",
    "SocMemoryWorkbenchState",
]
