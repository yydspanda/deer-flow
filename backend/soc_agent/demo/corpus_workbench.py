"""Browser-driven DEV explorer for the complete reviewed PingAn alert corpus."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sqlite3
import zlib
from collections import Counter, OrderedDict, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from soc_agent.contracts import (
    AnalysisRun,
    AnalysisRunStatus,
    LLMAnalysisRequest,
    MemoryPatternDataClass,
    MemoryPatternSourceType,
    PipelineStepStatus,
    SensitiveEvidenceMode,
    ServiceRequestContext,
    SocMemoryCandidateSourceType,
    SocOperationalDisposition,
    Verdict,
)
from soc_agent.core import SocAnalysisService, SocMemoryPatternService
from soc_agent.core.runtime import build_analysis_request_for_payload
from soc_agent.db import SqlAlchemyAlertRepository
from soc_agent.demo.corpus_loader import load_restricted_dataframe_pickle
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile
from soc_agent.llm import SocLLMSettings
from soc_agent.normalizers import normalize_alert_payload
from soc_agent.prompts.analysis import (
    ANALYSIS_PROMPT_VERSION,
    AnalysisPromptSizeError,
    build_analysis_prompt,
)
from soc_agent.utils.hashing import stable_hash

CORPUS_WORKBENCH_VERSION = "soc.corpus_dev_workbench.v2"
CORPUS_WORKBENCH_INDEX_VERSION = "soc.corpus_workbench_index.v3"
CORPUS_WORKBENCH_PAYLOAD_STORE_VERSION = "soc.corpus_workbench_payload_store.v1"
CORPUS_WORKBENCH_ENVIRONMENT = "dev-corpus-eval"
CORPUS_WORKBENCH_TENANT = "pingan"
_WINDOW_SECONDS = PingAnSocMemoryProfile.identity.aggregation_window_seconds or 86_400
_PAYLOAD_CACHE_SIZE = 8
_PINGAN_TIMEZONE = ZoneInfo("Asia/Shanghai")

CorpusOperationalLabel = Literal["忽略", "转交"]
CorpusProjectedDisposition = Literal["ignore", "transfer", "undetermined"]
CorpusComparisonStatus = Literal[
    "matched",
    "mismatched",
    "unscored",
    "not_run",
    "unlabeled",
]
CorpusLabelTemporalStatus = Literal[
    "valid",
    "label_time_missing",
    "label_precedes_alert",
    "unlabeled",
]

CorpusReadiness = Literal[
    "candidate_window",
    "recurrent_strong",
    "singleton_strong",
    "recurrent_context_only",
    "context_only_singleton",
    "fingerprint_missing",
]
CorpusExecutionStatus = Literal[
    "not_started",
    "running",
    "analysis_complete",
    "completed",
    "failed",
]
CorpusExecutionPhaseStatus = Literal[
    "pending",
    "running",
    "success",
    "failed",
    "skipped",
]
CorpusAuditArtifactStatus = Literal["available", "partial", "unavailable"]
CorpusAuditArtifactSource = Literal[
    "persisted_run",
    "persisted_downstream",
    "read_model_projection",
]


class SocCorpusWorkbenchError(ValueError):
    """Base error for an invalid local corpus workbench operation."""


@dataclass(frozen=True)
class _CorpusCase:
    alert_id: str
    source_index: int
    sequence_number: int
    payload: dict[str, Any] | None
    payload_hash: str
    observed_at: str
    observed_at_value: datetime
    topic: str | None
    source_type: str
    source_system: str | None
    product: str | None
    detection_key: str | None
    rule_code: str | None
    rule_name: str | None
    category: str | None
    severity: str | None
    endpoint: str | None
    host_name: str | None
    process_names: tuple[str, ...]
    behavior_fingerprint: str | None
    behavior_components: tuple[str, ...]
    behavior_strength: str | None
    decision_eligible: bool
    group_id: str
    window_id: str
    window_start: str
    window_end: str
    operational_label_available: bool = False
    operational_label: CorpusOperationalLabel | None = None
    operational_label_observed_at: str | None = None
    operational_label_method: str | None = None
    operational_label_reason: str | None = None
    operational_label_status: str | None = None
    label_temporal_status: CorpusLabelTemporalStatus = "unlabeled"
    group_alert_count: int = 1
    window_alert_count: int = 1
    readiness: CorpusReadiness = "fingerprint_missing"


class SocCorpusWorkbenchSafety(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment: Literal["dev"] = "dev"
    database_backend: Literal["sqlite"] = "sqlite"
    database_file: str
    source_data_class: Literal["operational"] = "operational"
    historical_replay: Literal[True] = True
    internal_providers: Literal["off_or_mock"] = "off_or_mock"
    tenant_policy: Literal["disabled"] = "disabled"
    external_action_execution: Literal[False] = False
    memory_scope: str = CORPUS_WORKBENCH_ENVIRONMENT
    pattern_window_days: float = Field(
        default=round(_WINDOW_SECONDS / 86_400, 2),
        gt=0,
    )
    replay_order: Literal["canonical_event_time_asc_within_memory_group"] = "canonical_event_time_asc_within_memory_group"
    label_visibility: Literal["hidden_until_runtime_decision"] = "hidden_until_runtime_decision"


class SocCorpusWorkbenchSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    alert_count: int = Field(ge=1)
    labeled_alert_count: int = Field(ge=0)
    unlabeled_alert_count: int = Field(ge=0)
    first_event_time: str
    last_event_time: str
    sort_order: Literal["canonical_event_time_asc_alert_id_asc"] = "canonical_event_time_asc_alert_id_asc"
    index_file_name: str | None = None
    index_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    payload_store_file_name: str | None = None
    payload_store_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


class SocCorpusWorkbenchModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    model_name: str | None = None
    thinking_enabled: bool
    role_verifier_enabled: bool
    role_verifier_model_name: str | None = None


class SocCorpusWorkbenchExecutionStep(BaseModel):
    """Secret-safe projection of one persisted Runtime step."""

    model_config = ConfigDict(extra="forbid")

    step_name: str
    label: str
    status: CorpusExecutionPhaseStatus
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    warning_count: int = Field(default=0, ge=0)
    error: str | None = None


class SocCorpusWorkbenchExecutionPhase(BaseModel):
    """Analyst-facing phase composed from one or more persisted Runtime steps."""

    model_config = ConfigDict(extra="forbid")

    phase: str
    label: str
    status: CorpusExecutionPhaseStatus
    summary: str
    duration_ms: int | None = Field(default=None, ge=0)
    metrics: dict[str, str | int | float | bool] = Field(default_factory=dict)
    steps: list[SocCorpusWorkbenchExecutionStep] = Field(default_factory=list)


class SocCorpusWorkbenchExecution(BaseModel):
    """Lightweight live/read-after-run timeline for one corpus alert."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.corpus_dev_execution.v1"] = "soc.corpus_dev_execution.v1"
    alert_id: str
    status: CorpusExecutionStatus
    current_phase: str | None = None
    run_id: str | None = None
    run_status: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    elapsed_ms: int | None = Field(default=None, ge=0)
    total_duration_ms: int | None = Field(default=None, ge=0)
    model_name: str | None = None
    provider_purpose: str | None = None
    provider_attempt_count: int = Field(default=0, ge=0)
    observation_id: str | None = None
    aggregation_key: str | None = None
    candidate_id: str | None = None
    phases: list[SocCorpusWorkbenchExecutionPhase] = Field(default_factory=list)


class SocCorpusWorkbenchAuditSafety(BaseModel):
    """Explicit boundary for the heavyweight DEV-only audit surface."""

    model_config = ConfigDict(extra="forbid")

    dev_only: Literal[True] = True
    admin_only: Literal[True] = True
    contains_raw_alert_data: Literal[True] = True
    contains_model_context: Literal[True] = True
    reexecutes_runtime: Literal[False] = False
    mutates_state: Literal[False] = False


class SocCorpusWorkbenchAuditArtifact(BaseModel):
    """One ordered, downloadable artifact in a persisted Runtime audit bundle."""

    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    artifact_id: str = Field(min_length=1)
    file_name: str = Field(min_length=1)
    phase: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    status: CorpusAuditArtifactStatus
    source: CorpusAuditArtifactSource
    metrics: dict[str, str | int | float | bool] = Field(default_factory=dict)
    review_guide: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class SocCorpusWorkbenchAuditBundle(BaseModel):
    """Heavyweight, read-only DEV audit projection for one persisted run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.corpus_dev_audit_bundle.v1"] = "soc.corpus_dev_audit_bundle.v1"
    alert_id: str
    run_id: str
    generated_at: str
    pipeline_version: str
    model_name: str
    prompt_version: str
    input_hash: str | None = None
    safety: SocCorpusWorkbenchAuditSafety = Field(default_factory=SocCorpusWorkbenchAuditSafety)
    execution: SocCorpusWorkbenchExecution
    artifacts: list[SocCorpusWorkbenchAuditArtifact] = Field(default_factory=list)


class SocCorpusWorkbenchReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_alert_count: int
    fingerprint_coverage_count: int
    decision_eligible_alert_count: int
    recurrent_group_count: int
    recurrent_alert_count: int
    candidate_window_group_count: int
    candidate_window_alert_count: int
    processed_count: int
    failed_count: int
    memory_hit_alert_count: int


class SocCorpusWorkbenchEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label_kind: Literal["operational_disposition"] = "operational_disposition"
    label_counts: dict[str, int]
    temporally_valid_label_count: int
    temporally_invalid_label_count: int
    unlabeled_count: int
    processed_labeled_count: int
    base_matched_count: int
    base_mismatched_count: int
    base_unscored_count: int
    base_match_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    effective_matched_count: int
    effective_mismatched_count: int
    effective_unscored_count: int
    effective_match_rate: float | None = Field(default=None, ge=0.0, le=1.0)


class SocCorpusWorkbenchGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str
    source_type: str
    detection_key: str | None = None
    rule_code: str | None = None
    rule_name: str | None = None
    behavior_fingerprint: str | None = None
    behavior_components: list[str]
    decision_eligible: bool
    alert_count: int
    window_count: int
    max_window_alert_count: int
    candidate_window_count: int
    processed_count: int
    memory_hit_count: int


class SocCorpusWorkbenchMemoryContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_ref: str
    label: str
    source_id: str
    summary: str


class SocCorpusWorkbenchDecisionStage(BaseModel):
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


class SocCorpusWorkbenchAlert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_id: str
    source_index: int
    sequence_number: int = Field(ge=1)
    observed_at: str
    topic: str | None = None
    source_type: str
    source_system: str | None = None
    product: str | None = None
    detection_key: str | None = None
    rule_code: str | None = None
    rule_name: str | None = None
    category: str | None = None
    severity: str | None = None
    endpoint: str | None = None
    host_name: str | None = None
    process_names: list[str]
    behavior_fingerprint: str | None = None
    behavior_components: list[str]
    behavior_strength: str | None = None
    decision_eligible: bool
    readiness: CorpusReadiness
    group_id: str
    group_alert_count: int
    window_alert_count: int
    window_start: str
    window_end: str
    workflow_state: Literal["ready", "analysis_only", "completed", "failed"]
    can_process: bool
    blocked_by_alert_id: str | None = None
    run_id: str | None = None
    analysis_status: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    total_duration_ms: int | None = None
    output_quality: str | None = None
    failure_kind: str | None = None
    failure_message: str | None = None
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
    candidate_id: str | None = None
    candidate_status: str | None = None
    manual_candidate_id: str | None = None
    manual_candidate_status: str | None = None
    memory_id: str | None = None
    memory_status: str | None = None
    memory_contexts: list[SocCorpusWorkbenchMemoryContext] = Field(default_factory=list)
    memory_directive_applied: bool = False
    memory_effect: str | None = None
    decision_stages: list[SocCorpusWorkbenchDecisionStage] = Field(default_factory=list)
    operational_label_available: bool = False
    operational_label_revealed: bool = False
    operational_label: CorpusOperationalLabel | None = None
    operational_label_observed_at: str | None = None
    operational_label_method: str | None = None
    operational_label_reason: str | None = None
    operational_label_status: str | None = None
    label_temporal_status: CorpusLabelTemporalStatus = "unlabeled"
    base_operational_projection: CorpusProjectedDisposition = "undetermined"
    effective_operational_projection: CorpusProjectedDisposition = "undetermined"
    base_label_comparison: CorpusComparisonStatus = "not_run"
    effective_label_comparison: CorpusComparisonStatus = "not_run"
    base_projection_basis: str | None = None
    effective_projection_basis: str | None = None


class SocCorpusWorkbenchState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.corpus_dev_workbench.v2"] = "soc.corpus_dev_workbench.v2"
    safety: SocCorpusWorkbenchSafety
    source: SocCorpusWorkbenchSource
    model: SocCorpusWorkbenchModelConfig
    readiness: SocCorpusWorkbenchReadiness
    evaluation: SocCorpusWorkbenchEvaluation
    groups: list[SocCorpusWorkbenchGroup]
    alerts: list[SocCorpusWorkbenchAlert]


class SocCorpusWorkbenchProcessResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.corpus_dev_workbench_process.v2"] = "soc.corpus_dev_workbench_process.v2"
    alert_id: str
    run_id: str | None = None
    observation_id: str | None = None
    idempotent: bool
    state: SocCorpusWorkbenchState


class SocCorpusWorkbenchService:
    """Run arbitrary server-owned corpus alerts through official SOC services."""

    def __init__(
        self,
        *,
        repository: SqlAlchemyAlertRepository,
        analysis_service: SocAnalysisService,
        pattern_service: SocMemoryPatternService,
        source_path: Path,
        settings: SocLLMSettings,
        database_file: str,
        index_path: Path | None = None,
    ) -> None:
        self._repository = repository
        self._analysis_service = analysis_service
        self._pattern_service = pattern_service
        self._source_path = source_path.expanduser().resolve()
        self._settings = settings
        self._database_file = database_file
        self._source_sha256 = _sha256_file(self._source_path)
        self._index_path = index_path.expanduser().resolve() if index_path is not None else self._source_path.with_suffix(".workbench-index.json")
        self._cases = _load_cases(
            self._source_path,
            source_sha256=self._source_sha256,
            index_path=self._index_path,
        )
        self._payload_cache: OrderedDict[str, dict[str, Any]] = OrderedDict((item.alert_id, item.payload) for item in self._cases.values() if item.payload is not None)
        self._payload_store_path, self._payload_store_sha256 = _resolve_payload_store(self._index_path) if self._index_path.is_file() else (None, None)
        self._index_sha256 = _sha256_file(self._index_path) if self._index_path.is_file() else None

    def get_state(self) -> SocCorpusWorkbenchState:
        observations_by_alert = self._observations_by_alert()
        runs = self._runs_by_alert()
        queues_by_run = {item.run_id: item for item in self._repository.list_review_items(status=None, limit=10_000)}
        replay_by_key = {aggregation_key: self._pattern_service.replay(aggregation_key) for aggregation_key in {item.aggregation_key for item in observations_by_alert.values()}}
        candidate_by_source: dict[str, Any] = {}
        source_ids = [f"memory_pattern:{item.aggregation_key}" for item in observations_by_alert.values()]
        for item in self._repository.find_memory_candidates_by_source_ids(source_ids):
            candidate_by_source.setdefault(item.source.source_id, item)
        manual_candidate_by_run: dict[str, Any] = {}
        for item in self._repository.list_memory_candidates(status=None, limit=10_000):
            if item.source.source_type is SocMemoryCandidateSourceType.MANUAL_NOTE and item.source.run_id is not None:
                manual_candidate_by_run.setdefault(item.source.run_id, item)
        record_by_candidate: dict[str, Any] = {}
        for item in self._repository.find_memory_records_by_candidate_ids([item.candidate_id for item in candidate_by_source.values()]):
            record_by_candidate.setdefault(item.source_candidate_id, item)
        transitions_by_run: dict[str, Any] = {}
        for item in self._repository.list_decision_transitions(limit=10_000):
            transitions_by_run.setdefault(item.run_id, item)
        memory_uses_by_run: dict[str, list[Any]] = defaultdict(list)
        for item in self._repository.list_memory_uses(limit=10_000):
            memory_uses_by_run[item.run_id].append(item)
        processable, blockers = _chronological_processability(
            self._cases.values(),
            observations_by_alert,
        )
        alerts = [
            self._alert_view(
                case,
                run=runs.get(case.alert_id),
                observation=observations_by_alert.get(case.alert_id),
                replay_by_key=replay_by_key,
                queue_by_run=queues_by_run,
                transition_by_run=transitions_by_run,
                memory_uses_by_run=memory_uses_by_run,
                candidate_by_source=candidate_by_source,
                manual_candidate_by_run=manual_candidate_by_run,
                record_by_candidate=record_by_candidate,
                can_process=case.alert_id in processable,
                blocked_by_alert_id=blockers.get(case.alert_id),
            )
            for case in self._cases.values()
        ]
        labeled_count = sum(item.operational_label_available for item in self._cases.values())
        ordered_cases = list(self._cases.values())
        first_case = ordered_cases[0]
        last_case = ordered_cases[-1]
        return SocCorpusWorkbenchState(
            safety=SocCorpusWorkbenchSafety(database_file=self._database_file),
            source=SocCorpusWorkbenchSource(
                file_name=self._source_path.name,
                sha256=self._source_sha256,
                alert_count=len(self._cases),
                labeled_alert_count=labeled_count,
                unlabeled_alert_count=len(self._cases) - labeled_count,
                first_event_time=first_case.observed_at,
                last_event_time=last_case.observed_at,
                index_file_name=(self._index_path.name if self._index_path.is_file() else None),
                index_sha256=self._index_sha256,
                payload_store_file_name=(self._payload_store_path.name if self._payload_store_path is not None else None),
                payload_store_sha256=self._payload_store_sha256,
            ),
            model=SocCorpusWorkbenchModelConfig(
                mode=self._settings.mode.value,
                model_name=self._settings.model_name,
                thinking_enabled=self._settings.thinking_enabled,
                role_verifier_enabled=self._settings.role_verifier_enabled,
                role_verifier_model_name=self._settings.role_verifier_model_name,
            ),
            readiness=_readiness(self._cases.values(), alerts),
            evaluation=_evaluation(self._cases.values(), alerts),
            groups=_group_views(self._cases.values(), alerts),
            alerts=alerts,
        )

    def process_alert(
        self,
        alert_id: str,
        *,
        context: ServiceRequestContext,
    ) -> SocCorpusWorkbenchProcessResult:
        case = self._cases.get(alert_id)
        if case is None:
            raise SocCorpusWorkbenchError(f"alert {alert_id!r} is not part of the configured DEV corpus")
        if "soc_admin" not in context.actor.roles:
            raise SocCorpusWorkbenchError("the DEV corpus workbench requires the soc_admin role")
        before = self.get_state()
        current = next(item for item in before.alerts if item.alert_id == alert_id)
        if current.observation_id is not None:
            return SocCorpusWorkbenchProcessResult(
                alert_id=alert_id,
                run_id=current.run_id,
                observation_id=current.observation_id,
                idempotent=True,
                state=before,
            )
        if not current.can_process:
            blocker = current.blocked_by_alert_id or "an earlier alert"
            raise SocCorpusWorkbenchError(f"alert {alert_id} is blocked by earlier same-pattern alert {blocker}; historical Memory replay must follow canonical event time")

        request_context = context.model_copy(update={"idempotency_key": self._analysis_idempotency_key(alert_id)})
        run = self._analysis_service.analyze(
            copy.deepcopy(self._payload_for_case(case)),
            context=request_context,
        )
        if run.status is AnalysisRunStatus.FAILED:
            return SocCorpusWorkbenchProcessResult(
                alert_id=alert_id,
                run_id=run.run_id,
                idempotent=False,
                state=self.get_state(),
            )
        aggregation = self._pattern_service.observe_run(
            run,
            source_type=MemoryPatternSourceType.BATCH_ALERT,
            transport_ref=(f"soc-corpus-dev-web:{self._source_sha256}:{alert_id}:v1"),
            environment=CORPUS_WORKBENCH_ENVIRONMENT,
            data_class=MemoryPatternDataClass.OPERATIONAL,
            context=request_context,
        )
        return SocCorpusWorkbenchProcessResult(
            alert_id=alert_id,
            run_id=run.run_id,
            observation_id=aggregation.observation.observation_id,
            idempotent=aggregation.idempotent,
            state=self.get_state(),
        )

    def get_execution(self, alert_id: str) -> SocCorpusWorkbenchExecution:
        """Return one lightweight persisted timeline without rebuilding corpus state."""

        case = self._cases.get(alert_id)
        if case is None:
            raise SocCorpusWorkbenchError(f"alert {alert_id!r} is not part of the configured DEV corpus")
        run = self._run_for_case(case)
        identity = PingAnSocMemoryProfile.identity
        observations = self._repository.list_memory_pattern_observations(
            tenant_id=CORPUS_WORKBENCH_TENANT,
            environment=CORPUS_WORKBENCH_ENVIRONMENT,
            data_class=MemoryPatternDataClass.OPERATIONAL,
            source_type=MemoryPatternSourceType.BATCH_ALERT,
            alert_id=alert_id,
            limit=100,
        )
        matching_observations = [item for item in observations if item.profile_id == identity.profile_id and item.profile_version == identity.profile_version and item.feature_schema_version == identity.feature_schema_version]
        observation = max(matching_observations, key=lambda item: item.created_at) if matching_observations else None
        replay = self._pattern_service.replay(observation.aggregation_key) if observation is not None else None
        candidate = self._repository.find_memory_candidate_by_source_id(f"memory_pattern:{observation.aggregation_key}") if observation is not None else None
        return _execution_view(
            alert_id=alert_id,
            run=run,
            observation=observation,
            replay=replay,
            candidate=candidate,
        )

    def get_audit_bundle(
        self,
        alert_id: str,
        *,
        context: ServiceRequestContext,
    ) -> SocCorpusWorkbenchAuditBundle:
        """Return the complete persisted DEV audit trail without re-running Runtime."""

        if "soc_admin" not in context.actor.roles:
            raise SocCorpusWorkbenchError("the DEV corpus audit bundle requires the soc_admin role")
        case = self._cases.get(alert_id)
        if case is None:
            raise SocCorpusWorkbenchError(f"alert {alert_id!r} is not part of the configured DEV corpus")
        run = self._run_for_case(case)
        if run is None:
            raise SocCorpusWorkbenchError(f"alert {alert_id} has no persisted Runtime run to audit")

        identity = PingAnSocMemoryProfile.identity
        observations = self._repository.list_memory_pattern_observations(
            tenant_id=CORPUS_WORKBENCH_TENANT,
            environment=CORPUS_WORKBENCH_ENVIRONMENT,
            data_class=MemoryPatternDataClass.OPERATIONAL,
            source_type=MemoryPatternSourceType.BATCH_ALERT,
            alert_id=alert_id,
            limit=100,
        )
        matching_observations = [item for item in observations if item.profile_id == identity.profile_id and item.profile_version == identity.profile_version and item.feature_schema_version == identity.feature_schema_version]
        observation = max(matching_observations, key=lambda item: item.created_at) if matching_observations else None
        replay = self._pattern_service.replay(observation.aggregation_key) if observation is not None else None
        pattern_source_id = f"memory_pattern:{observation.aggregation_key}" if observation is not None else None
        candidates = [item for item in self._repository.list_memory_candidates(status=None, limit=10_000) if item.source.run_id == run.run_id or (pattern_source_id is not None and item.source.source_id == pattern_source_id)]
        memory_records = self._repository.find_memory_records_by_candidate_ids([item.candidate_id for item in candidates])
        review_items = [item for item in self._repository.list_review_items(status=None, limit=10_000) if item.run_id == run.run_id]
        summary = self._repository.get_alert_summary(run.run_id)
        decision_transitions = self._repository.list_decision_transitions(
            run_id=run.run_id,
            limit=100,
        )
        memory_uses = self._repository.list_memory_uses(
            run_id=run.run_id,
            limit=500,
        )
        execution = _execution_view(
            alert_id=alert_id,
            run=run,
            observation=observation,
            replay=replay,
            candidate=(candidates[0] if candidates else None),
        )
        return _audit_bundle(
            run=run,
            execution=execution,
            observation=observation,
            replay=replay,
            candidates=candidates,
            memory_records=memory_records,
            review_items=review_items,
            summary=summary,
            decision_transitions=decision_transitions,
            memory_uses=memory_uses,
        )

    def _run_for_case(self, case: _CorpusCase) -> AnalysisRun | None:
        for run in self._repository.list_runs_by_alert_id(
            case.alert_id,
            limit=20,
        ):
            request = run.llm_analysis_request
            if run.input_hash == case.payload_hash and request is not None and request.environment == CORPUS_WORKBENCH_ENVIRONMENT:
                return run
        return None

    def _payload_for_case(self, case: _CorpusCase) -> dict[str, Any]:
        if case.payload is not None:
            return case.payload
        payload = self._payload_cache.get(case.alert_id)
        if payload is not None:
            self._payload_cache.move_to_end(case.alert_id)
        if payload is None and self._payload_store_path is not None:
            payload = _load_payload_from_store(self._payload_store_path, case)
            self._payload_cache[case.alert_id] = payload
            while len(self._payload_cache) > _PAYLOAD_CACHE_SIZE:
                self._payload_cache.popitem(last=False)
        if payload is None:
            self._payload_cache.update(
                _load_payloads(
                    self._source_path,
                    self._cases,
                )
            )
            payload = self._payload_cache.get(case.alert_id)
        if payload is None:
            raise SocCorpusWorkbenchError(f"alert {case.alert_id} payload is missing from the configured corpus")
        return payload

    def _analysis_idempotency_key(self, alert_id: str) -> str:
        identity = PingAnSocMemoryProfile.identity
        generation = stable_hash(
            {
                "workbench_version": CORPUS_WORKBENCH_VERSION,
                "profile_id": identity.profile_id,
                "profile_version": identity.profile_version,
                "feature_schema_version": identity.feature_schema_version,
            }
        )[:16]
        return f"soc-corpus-dev:{self._source_sha256[:16]}:{alert_id}:{generation}"

    def _observations_by_alert(self) -> dict[str, Any]:
        identity = PingAnSocMemoryProfile.identity
        observations = self._repository.list_memory_pattern_observations(
            tenant_id=CORPUS_WORKBENCH_TENANT,
            environment=CORPUS_WORKBENCH_ENVIRONMENT,
            data_class=MemoryPatternDataClass.OPERATIONAL,
            source_type=MemoryPatternSourceType.BATCH_ALERT,
            limit=10_000,
        )
        selected: dict[str, Any] = {}
        for item in observations:
            alert_id = item.source.alert_id
            if alert_id not in self._cases or item.profile_id != identity.profile_id or item.profile_version != identity.profile_version or item.feature_schema_version != identity.feature_schema_version:
                continue
            previous = selected.get(alert_id)
            if previous is None or item.created_at > previous.created_at:
                selected[alert_id] = item
        return selected

    def _runs_by_alert(self) -> dict[str, AnalysisRun]:
        selected: dict[str, AnalysisRun] = {}
        for run in self._repository.list_runs(limit=10_000):
            case = self._cases.get(run.alert_id)
            request = run.llm_analysis_request
            if case is None or run.input_hash != case.payload_hash or request is None or request.environment != CORPUS_WORKBENCH_ENVIRONMENT:
                continue
            previous = selected.get(run.alert_id)
            if previous is None or run.started_at > previous.started_at:
                selected[run.alert_id] = run
        return selected

    def _alert_view(
        self,
        case: _CorpusCase,
        *,
        run: AnalysisRun | None,
        observation: Any | None,
        replay_by_key: Mapping[str, Any],
        queue_by_run: Mapping[str, Any],
        transition_by_run: Mapping[str, Any],
        memory_uses_by_run: Mapping[str, list[Any]],
        candidate_by_source: Mapping[str, Any],
        manual_candidate_by_run: Mapping[str, Any],
        record_by_candidate: Mapping[str, Any],
        can_process: bool,
        blocked_by_alert_id: str | None,
    ) -> SocCorpusWorkbenchAlert:
        transition = transition_by_run.get(run.run_id) if run is not None else None
        queue = None
        memory_uses = memory_uses_by_run.get(run.run_id, []) if run is not None else []
        if run is not None:
            queue = queue_by_run.get(run.run_id)
        replay = replay_by_key.get(observation.aggregation_key) if observation is not None else None
        candidate = candidate_by_source.get(f"memory_pattern:{observation.aggregation_key}") if observation is not None else None
        manual_candidate = manual_candidate_by_run.get(run.run_id) if run is not None else None
        record = record_by_candidate.get(candidate.candidate_id) if candidate is not None else None
        decision = run.decision if run is not None else None
        effective = transition.after if transition is not None else decision
        analysis = run.analysis if run is not None else None
        memory_contexts: list[SocCorpusWorkbenchMemoryContext] = []
        if run is not None and run.llm_analysis_request is not None:
            for item in run.llm_analysis_request.context_catalog:
                if item.kind.value != "confirmed_memory":
                    continue
                memory_contexts.append(
                    SocCorpusWorkbenchMemoryContext(
                        context_ref=item.context_ref,
                        label=item.label,
                        source_id=item.source_id,
                        summary=item.summary,
                    )
                )
        stages: list[SocCorpusWorkbenchDecisionStage] = []
        if transition is not None:
            stages = [
                SocCorpusWorkbenchDecisionStage(
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
        if observation is not None:
            workflow_state: Literal["ready", "analysis_only", "completed", "failed"] = "completed"
        elif run is not None and run.status is AnalysisRunStatus.FAILED:
            workflow_state = "failed"
        elif run is not None:
            workflow_state = "analysis_only"
        else:
            workflow_state = "ready"
        applied_use = next(
            (item for item in memory_uses if item.directive_applied),
            None,
        )
        failure = run.failure if run is not None else None
        label_revealed = decision is not None and case.operational_label_available
        effective_disposition = None
        if transition is not None:
            effective_stage = next(
                (item for item in reversed(transition.stages) if item.stage.value == "effective"),
                None,
            )
            effective_disposition = effective_stage.disposition_after if effective_stage is not None else None
        base_projection, base_basis = _project_operational_outcome(
            decision=decision,
            disposition=None,
        )
        effective_projection, effective_basis = _project_operational_outcome(
            decision=effective,
            disposition=effective_disposition,
        )
        return SocCorpusWorkbenchAlert(
            alert_id=case.alert_id,
            source_index=case.source_index,
            sequence_number=case.sequence_number,
            observed_at=case.observed_at,
            topic=case.topic,
            source_type=case.source_type,
            source_system=case.source_system,
            product=case.product,
            detection_key=case.detection_key,
            rule_code=case.rule_code,
            rule_name=case.rule_name,
            category=case.category,
            severity=case.severity,
            endpoint=case.endpoint,
            host_name=case.host_name,
            process_names=list(case.process_names),
            behavior_fingerprint=case.behavior_fingerprint,
            behavior_components=list(case.behavior_components),
            behavior_strength=case.behavior_strength,
            decision_eligible=case.decision_eligible,
            readiness=case.readiness,
            group_id=case.group_id,
            group_alert_count=case.group_alert_count,
            window_alert_count=case.window_alert_count,
            window_start=case.window_start,
            window_end=case.window_end,
            workflow_state=workflow_state,
            can_process=can_process,
            blocked_by_alert_id=blocked_by_alert_id,
            run_id=(run.run_id if run is not None else None),
            analysis_status=(run.status.value if run is not None else None),
            model_name=(run.model_name if run is not None else None),
            prompt_version=(run.prompt_version if run is not None else None),
            total_duration_ms=(run.total_duration_ms if run is not None else None),
            output_quality=(run.analysis_output_quality.status.value if run is not None and run.analysis_output_quality is not None else None),
            failure_kind=(failure.kind.value if failure is not None else None),
            failure_message=(failure.message if failure is not None else None),
            base_verdict=(decision.verdict.value if decision is not None else None),
            base_confidence=(decision.confidence if decision is not None else None),
            base_needs_review=(decision.needs_review if decision is not None else None),
            effective_verdict=(effective.verdict.value if effective is not None else None),
            effective_confidence=(effective.confidence if effective is not None else None),
            effective_needs_review=(effective.needs_review if effective is not None else None),
            analysis_summary=(analysis.summary if analysis is not None else None),
            analysis_reason=(analysis.reason if analysis is not None else None),
            queue_id=(queue.queue_id if queue is not None else None),
            observation_id=(observation.observation_id if observation is not None else None),
            aggregation_key=(observation.aggregation_key if observation is not None else None),
            pattern_support_count=(replay.support_count if replay is not None else None),
            pattern_distinct_source_count=(replay.distinct_source_count if replay is not None else None),
            pattern_quality_gate_passed=(replay.cohort_quality.quality_gate_passed if replay is not None else None),
            pattern_consistency_ratio=(replay.cohort_quality.consistency_ratio if replay is not None else None),
            candidate_id=(candidate.candidate_id if candidate is not None else None),
            candidate_status=(candidate.status.value if candidate is not None else None),
            manual_candidate_id=(manual_candidate.candidate_id if manual_candidate is not None else None),
            manual_candidate_status=(manual_candidate.status.value if manual_candidate is not None else None),
            memory_id=(record.memory_id if record is not None else None),
            memory_status=(record.status.value if record is not None else None),
            memory_contexts=memory_contexts,
            memory_directive_applied=applied_use is not None,
            memory_effect=(applied_use.effect.value if applied_use is not None else None),
            decision_stages=stages,
            operational_label_available=case.operational_label_available,
            operational_label_revealed=label_revealed,
            operational_label=(case.operational_label if label_revealed else None),
            operational_label_observed_at=(case.operational_label_observed_at if label_revealed else None),
            operational_label_method=(case.operational_label_method if label_revealed else None),
            operational_label_reason=(case.operational_label_reason if label_revealed else None),
            operational_label_status=(case.operational_label_status if label_revealed else None),
            label_temporal_status=case.label_temporal_status,
            base_operational_projection=base_projection,
            effective_operational_projection=effective_projection,
            base_label_comparison=_compare_operational_label(
                case,
                projection=base_projection,
                decision_available=decision is not None,
            ),
            effective_label_comparison=_compare_operational_label(
                case,
                projection=effective_projection,
                decision_available=effective is not None,
            ),
            base_projection_basis=base_basis,
            effective_projection_basis=effective_basis,
        )


_EXECUTION_PHASES: tuple[tuple[str, str], ...] = (
    ("normalize", "归一化 / Normalize"),
    ("facts", "实体与事实 / Entities & Facts"),
    ("context", "上下文与 Skills / Context & Skills"),
    ("reasoning", "模型研判 / LLM Analysis"),
    ("validation", "结果校验 / Validate"),
    ("decision", "决策生成 / Decision"),
    ("memory", "模式与记忆 / Pattern & Memory"),
)

_EXECUTION_STEP_PHASE = {
    "normalize": "normalize",
    "entity_extract": "facts",
    "fact_reconstruct": "facts",
    "build_analysis_input": "context",
    "skill_context": "context",
    "reference_catalog": "context",
    "analyze_llm": "reasoning",
    "analyze_stub": "reasoning",
    "schema_validate": "validation",
    "evidence_grounding": "validation",
    "role_verification_gate": "validation",
    "verify_roles_llm": "validation",
    "analysis_materiality": "validation",
    "decide": "decision",
    "pattern_observation": "memory",
}

_EXECUTION_STEP_LABELS = {
    "normalize": "厂商数据转通用告警",
    "entity_extract": "实体提取",
    "fact_reconstruct": "事实与角色重建",
    "build_analysis_input": "构建有界模型输入",
    "skill_context": "选择研判 Skills",
    "reference_catalog": "冻结证据与知识引用",
    "analyze_llm": "主模型结构化研判",
    "analyze_stub": "确定性 Stub 研判",
    "schema_validate": "输出 Schema 校验",
    "evidence_grounding": "证据引用校验",
    "role_verification_gate": "角色复核 Gate",
    "verify_roles_llm": "角色二次复核",
    "analysis_materiality": "质量影响范围判定",
    "decide": "生成 Base Decision",
    "pattern_observation": "写入 Pattern Observation",
}


def _audit_bundle(
    *,
    run: AnalysisRun,
    execution: SocCorpusWorkbenchExecution,
    observation: Any | None,
    replay: Any | None,
    candidates: list[Any],
    memory_records: list[Any],
    review_items: list[Any],
    summary: Any | None,
    decision_transitions: list[Any],
    memory_uses: list[Any],
) -> SocCorpusWorkbenchAuditBundle:
    """Project persisted run/downstream state into ordered DEV audit artifacts."""

    request = run.llm_analysis_request
    facts = run.fact_reconstruction
    analysis = run.analysis
    grounding = run.analysis_evidence_grounding
    materiality = run.analysis_materiality
    raw_messages = _raw_message_inventory(run.input_payload)
    primary_evidence = _audit_evidence(request.primary_evidence) if request is not None else None
    supplementary_evidence = [_audit_evidence(item) for item in request.supplementary_evidence] if request is not None else []
    canonical_projection = None
    model_visible_context, model_projection_lineage = _audit_model_visible_context(
        run,
        request,
    )
    if request is not None:
        canonical_projection = {
            "source": _audit_json(request.source),
            "detection": _audit_json(request.detection),
            "classification": _audit_json(request.classification),
            "entities": _audit_json(request.canonical_entities),
        }

    artifacts = [
        _audit_artifact(
            sequence=1,
            artifact_id="run-manifest",
            file_name="01-run-manifest.json",
            phase="runtime",
            title="运行清单 / Run Manifest",
            description="本次执行的身份、版本、时钟、步骤和失败边界；它是后续所有阶段产物的审计索引。",
            status="available",
            source="persisted_run",
            metrics={
                "run_status": run.status.value,
                "pipeline": run.pipeline_version,
                "model": run.model_name,
                "steps": len(run.steps),
                "duration_ms": run.total_duration_ms or 0,
            },
            review_guide=[
                "先确认 alert_id、run_id、input_hash 和模型/Prompt 版本是否属于本次运行。",
                "逐项检查 step 的状态、耗时、warning 和 output_hash；失败 run 重点查看 failure。",
            ],
            payload={
                "run_identity": {
                    "run_id": run.run_id,
                    "alert_id": run.alert_id,
                    "status": run.status.value,
                    "pipeline_version": run.pipeline_version,
                    "model_name": run.model_name,
                    "prompt_version": run.prompt_version,
                    "input_hash": run.input_hash,
                    "replay_of_run_id": run.replay_of_run_id,
                    "started_at": run.started_at.isoformat(),
                    "ended_at": run.ended_at.isoformat() if run.ended_at else None,
                    "total_duration_ms": run.total_duration_ms,
                },
                "steps": _audit_json(run.steps),
                "failure": _audit_json(run.failure),
            },
        ),
        _audit_artifact(
            sequence=2,
            artifact_id="source-input",
            file_name="02-source-input.json",
            phase="input",
            title="原始输入 / Source Input",
            description="Runtime 实际收到并按原样持久化的告警；这里可核对外层字段、hitLog、每条 zeusRawLogs 与原始 message。",
            status="available" if run.input_payload is not None else "unavailable",
            source="persisted_run",
            metrics={
                "payload_bytes": _json_size(run.input_payload),
                "top_level_fields": len(run.input_payload or {}),
                "raw_message_count": len(raw_messages),
                "raw_message_chars": sum(item["length"] for item in raw_messages),
            },
            review_guide=[
                "确认原始 payload 没有被 canonical 字段替代或丢弃；后续所有结果都应能回溯到这里。",
                "查看 raw_message_inventory 了解 message 数量和路径，再在 input_payload 中检查完整原文。",
            ],
            payload={
                "input_hash": run.input_hash,
                "raw_message_inventory": raw_messages,
                "input_payload": run.input_payload,
            },
        ),
        _audit_artifact(
            sequence=3,
            artifact_id="canonical-normalization",
            file_name="03-canonical-normalization.json",
            phase="normalize",
            title="解析与归一化 / Parse & Normalize",
            description="展示 Adapter 选择了哪一层证据、message 如何解析、哪些字段进入通用 AlertInput，以及未进入模型的字段去向。",
            status=("available" if run.normalized_alert is not None else "partial" if request is not None else "unavailable"),
            source="persisted_run",
            metrics={
                "adapter": (run.normalization_report.adapter if run.normalization_report else "unavailable"),
                "message_schemas": (len(run.normalization_report.message_schemas) if run.normalization_report else 0),
                "selected_layer": (facts.evidence_policy.selected_layer.value if facts and facts.evidence_policy else "unavailable"),
                "canonical_fields": (len(run.normalization_report.normalized_fields) if run.normalization_report else 0),
                "parsed_messages": (1 if primary_evidence is not None else 0) + len(supplementary_evidence),
            },
            review_guide=[
                "重点检查 evidence_input_policy：message 可用时应选择 raw_message，并把其他结构化字段保留为审计/备用层。",
                "normalized_alert 是当次真正生成的 canonical AlertInput；旧 run 若未持久化它，会显示 partial 和只读 canonical_projection_fallback。",
                "parsed_message_projections 展示实际送往有界分析上下文的解析结果、截断、编码压缩和省略清单。",
            ],
            payload={
                "normalized_alert": _audit_json(run.normalized_alert),
                "canonical_projection_fallback": (canonical_projection if run.normalized_alert is None else None),
                "normalization_report": _audit_json(run.normalization_report),
                "normalization_monitoring_result": _audit_json(run.normalization_monitoring_result),
                "evidence_input_policy": _audit_json(facts.evidence_policy if facts else None),
                "parsed_message_projections": {
                    "primary": primary_evidence,
                    "supplementary": supplementary_evidence,
                },
            },
        ),
        _audit_artifact(
            sequence=4,
            artifact_id="entity-extraction",
            file_name="04-entity-extraction.json",
            phase="entities",
            title="实体提取 / Entity Extraction",
            description="从 canonical 告警和解析证据中提取 IP、主机、账号、进程、规则等实体，并保留角色、来源路径与置信度。",
            status="available" if run.entities is not None else "unavailable",
            source="persisted_run",
            metrics={
                "mentions": len(run.entities.mentions) if run.entities else 0,
                "entity_types": len(run.extraction_report.entity_counts) if run.extraction_report else 0,
                "warnings": len(run.extraction_report.warnings) if run.extraction_report else 0,
            },
            review_guide=[
                "mentions 是逐条实体证据；role 表示 source、destination、attacker、victim 等当前语义，而不是只把 IP 二分。",
                "用 evidence_path 回查原始输入或解析字段，确认高价值实体没有漏掉或被错误赋予角色。",
            ],
            payload={
                "entities": _audit_json(run.entities),
                "extraction_report": _audit_json(run.extraction_report),
            },
        ),
        _audit_artifact(
            sequence=5,
            artifact_id="fact-reconstruction",
            file_name="05-fact-reconstruction.json",
            phase="facts",
            title="事实与角色重建 / Fact Reconstruction",
            description="对字段可信度、canonical 来源、攻击场景、网络角色和冲突进行可追踪重建，供模型和决策层使用。",
            status="available" if facts is not None else "unavailable",
            source="persisted_run",
            metrics={
                "field_trusts": len(facts.field_trusts) if facts else 0,
                "role_claims": len(facts.role_claims) if facts else 0,
                "role_resolutions": len(facts.role_resolutions) if facts else 0,
                "scenario_hypotheses": len(facts.scenario_hypotheses) if facts else 0,
                "conflicts": len(facts.conflict_reports) if facts else 0,
            },
            review_guide=[
                "canonical_field_provenance 说明每个通用字段最终选了哪个来源以及有哪些候选值。",
                "conflict_reports 只是冲突记录；结合 role_resolutions 和 resolution_status 判断它是否已解决、是否阻断自动化。",
            ],
            payload={"fact_reconstruction": _audit_json(facts)},
        ),
        _audit_artifact(
            sequence=6,
            artifact_id="bounded-analysis-input",
            file_name="06-bounded-analysis-input.json",
            phase="context",
            title="模型输入与 Skills / Bounded Analysis Input",
            description="分开展示模型可见的业务化上下文与 Runtime 保留的完整请求审计；E-* 是当前证据，S/A/M/C/T 是受治理上下文。",
            status=("available" if model_projection_lineage["status"] == "exact_for_prompt_version" else "partial" if request is not None else "unavailable"),
            source="read_model_projection",
            metrics={
                "evidence_catalog": len(request.evidence_catalog) if request else 0,
                "context_catalog": len(request.context_catalog) if request else 0,
                "selected_skills": len(request.skill_context.selected_skills) if request else 0,
                "projected_fields": (request.evidence_coverage.counts.get("llm_projected_count", 0) if request else 0),
                "omissions": (request.evidence_coverage.counts.get("omission_count", 0) if request else 0),
                "high_value_gaps": len(request.evidence_coverage.high_value_gaps) if request else 0,
            },
            review_guide=[
                "优先审阅 model_visible_context；它不包含完整 vendor path 清单、schema fingerprint 或原始未裁剪请求。",
                "projection_lineage 只有 exact_for_prompt_version 才表示当前 Builder 能精确重建该次模型上下文；旧 Prompt Run 会明确标为部分可用。",
                "runtime_request_audit 是持久化的完整 Runtime 请求，用于回放字段覆盖和来源路径，不等于模型逐字看到的内容。",
                "E-* 是当前告警证据，S-* 是 Skill，A-* 是 Adapter 语义，M-* 是确认 Memory，C-* 是租户知识，T-* 是工具证据。",
            ],
            payload={
                "model_visible_context": model_visible_context,
                "projection_lineage": model_projection_lineage,
                "runtime_request_audit": _audit_json(request),
            },
        ),
        _audit_artifact(
            sequence=7,
            artifact_id="model-analysis-output",
            file_name="07-model-analysis-output.json",
            phase="reasoning",
            title="模型研判输出 / Model Analysis Output",
            description="展示主模型结构化结论、推理链引用、场景、方向、角色和建议，以及 Provider 调用审计元数据。",
            status="available" if analysis is not None else "unavailable",
            source="persisted_run",
            metrics={
                "verdict": (_enum_value(analysis.verdict) if analysis else "unavailable"),
                "confidence": analysis.confidence if analysis else 0.0,
                "reasoning_items": len(analysis.reasoning) if analysis else 0,
                "scenarios": len(analysis.scenario_assessments) if analysis else 0,
                "provider_attempts": len(run.provider_request_journals),
            },
            review_guide=[
                "先看 verdict、summary、reason，再通过 decision_evidence_refs / decision_reasoning_refs 回到 E-* 和 R-*。",
                "Provider journal 只记录模型、版本、时间和失败边界；不包含凭证。模型原始自由文本若未持久化，不会在这里伪造。",
            ],
            payload={
                "analysis": _audit_json(analysis),
                "request_journal": _audit_json(run.request_journal),
                "provider_request_journals": _audit_json(run.provider_request_journals),
            },
        ),
        _audit_artifact(
            sequence=8,
            artifact_id="output-validation",
            file_name="08-output-validation.json",
            phase="validation",
            title="输出校验 / Quality, Grounding & Materiality",
            description="Runtime 对模型 JSON、引用完整性、局部降级影响和可选角色二次复核做确定性校验。",
            status=("available" if run.analysis_output_quality is not None else "unavailable"),
            source="persisted_run",
            metrics={
                "output_quality": (_enum_value(run.analysis_output_quality.status) if run.analysis_output_quality else "unavailable"),
                "grounded": grounding.grounded_count if grounding else 0,
                "rejected": grounding.ungrounded_count if grounding else 0,
                "decision_usable": materiality.decision_usable if materiality else False,
                "review_required": materiality.review_required if materiality else False,
                "role_verification": (_enum_value(run.role_adjudication_verification.status) if run.role_adjudication_verification else "not_run"),
            },
            review_guide=[
                "Grounding 只验证模型引用是否存在和对应，不重新否定模型基于已引用事实作出的安全推理。",
                "Materiality 决定哪个损坏区块会阻断哪个能力；可用的核心 verdict 不应因可选角色区块失败而整体丢失。",
            ],
            payload={
                "analysis_output_quality": _audit_json(run.analysis_output_quality),
                "analysis_evidence_grounding": _audit_json(grounding),
                "analysis_materiality": _audit_json(materiality),
                "role_verification_trigger": _audit_json(run.role_verification_trigger),
                "role_adjudication_verification": _audit_json(run.role_adjudication_verification),
            },
        ),
        _audit_artifact(
            sequence=9,
            artifact_id="decision-lineage",
            file_name="09-decision-lineage.json",
            phase="decision",
            title="决策沿革 / Decision Lineage",
            description="展示 Runtime Base Decision、AlertSummary、ReviewQueue，以及后续 Memory/Tenant/Effective 决策变更记录。",
            status="available" if run.decision is not None else "unavailable",
            source="persisted_downstream",
            metrics={
                "verdict": (_enum_value(run.decision.verdict) if run.decision else "unavailable"),
                "confidence": run.decision.confidence if run.decision else 0.0,
                "needs_review": run.decision.needs_review if run.decision else False,
                "review_items": len(review_items),
                "decision_transitions": len(decision_transitions),
            },
            review_guide=[
                "Base Decision 是本次 Runtime 的技术结论；Memory、租户策略和 Effective Decision 必须通过 transition 留下前后差异。",
                "ReviewQueue 是人工工作流，不等于模型失败；检查 needs_review 原因和自动化能力 guard。",
            ],
            payload={
                "base_decision": _audit_json(run.decision),
                "alert_summary": _audit_json(summary),
                "review_items": _audit_json(review_items),
                "decision_transitions": _audit_json(decision_transitions),
                "corrections": _audit_json(run.corrections),
                "role_adjudication_revisions": _audit_json(run.role_adjudication_revisions),
            },
        ),
        _audit_artifact(
            sequence=10,
            artifact_id="memory-pattern-write",
            file_name="10-memory-pattern-write.json",
            phase="memory",
            title="Pattern 与 Memory / Pattern & Memory",
            description="展示本次告警如何形成 Pattern Observation、如何进入固定窗口聚合，以及 Candidate、Memory 与召回使用记录。",
            status="available" if observation is not None else "partial",
            source="persisted_downstream",
            metrics={
                "observation": observation.observation_id if observation is not None else "not_written",
                "support_count": replay.support_count if replay is not None else 0,
                "distinct_sources": replay.distinct_source_count if replay is not None else 0,
                "candidates": len(candidates),
                "memory_records": len(memory_records),
                "memory_uses": len(memory_uses),
            },
            review_guide=[
                "Observation 是一次有效告警观察，不等于一条 Memory；Replay 汇总同一 aggregation_key 在窗口内的支持度和一致性。",
                "Candidate 仍需治理审核；confirmed/enabled Memory 及其本次 M-* 使用记录必须单独可见。",
            ],
            payload={
                "pattern_observation": _audit_json(observation),
                "pattern_replay": _audit_json(replay),
                "memory_candidates": _audit_json(candidates),
                "memory_records": _audit_json(memory_records),
                "memory_uses": _audit_json(memory_uses),
            },
        ),
    ]
    return SocCorpusWorkbenchAuditBundle(
        alert_id=run.alert_id,
        run_id=run.run_id,
        generated_at=datetime.now(UTC).isoformat(),
        pipeline_version=run.pipeline_version,
        model_name=run.model_name,
        prompt_version=run.prompt_version,
        input_hash=run.input_hash,
        execution=execution,
        artifacts=artifacts,
    )


def _audit_artifact(**values: Any) -> SocCorpusWorkbenchAuditArtifact:
    return SocCorpusWorkbenchAuditArtifact.model_validate(values)


def _audit_model_visible_context(
    run: AnalysisRun,
    request: LLMAnalysisRequest | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Rebuild model-visible context without presenting old builders as exact."""

    if request is None:
        return None, {
            "status": "unavailable",
            "run_prompt_version": run.prompt_version,
            "builder_prompt_version": ANALYSIS_PROMPT_VERSION,
            "exact": False,
            "note": "该 Run 没有持久化 LLMAnalysisRequest，无法重建模型可见上下文。",
        }

    try:
        prompt = build_analysis_prompt(request)
    except (AnalysisPromptSizeError, TypeError) as exc:
        return None, {
            "status": "unavailable",
            "run_prompt_version": run.prompt_version,
            "builder_prompt_version": ANALYSIS_PROMPT_VERSION,
            "exact": False,
            "note": f"当前 Prompt Builder 无法重建该请求：{exc}",
        }

    exact = run.prompt_version == prompt.prompt_version
    return _audit_json(prompt.context), {
        "status": ("exact_for_prompt_version" if exact else "reconstructed_with_current_builder"),
        "run_prompt_version": run.prompt_version,
        "builder_prompt_version": prompt.prompt_version,
        "prompt_example_id": prompt.example_id,
        "exact": exact,
        "note": ("模型可见上下文由同版本 Prompt Builder 从冻结请求精确重建。" if exact else "该 Run 使用旧 Prompt 版本；当前内容仅用于迁移审阅，不代表历史调用的逐字输入。"),
    }


def _audit_json(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _audit_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_audit_json(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _audit_evidence(value: Any) -> dict[str, Any]:
    projected = _audit_json(value)
    if not isinstance(projected, dict):
        return {"value": projected}
    content = projected.get("content")
    if isinstance(content, str):
        try:
            projected["content_decoded"] = json.loads(content)
            projected["content_format"] = "json"
        except json.JSONDecodeError:
            projected["content_decoded"] = content
            projected["content_format"] = "text"
    return projected


def _raw_message_inventory(value: Any, *, path: str = "$") -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if str(key).lower() == "message" and isinstance(item, str):
                inventory.append(
                    {
                        "path": child_path,
                        "length": len(item),
                        "sha256": hashlib.sha256(item.encode("utf-8")).hexdigest(),
                    }
                )
            inventory.extend(_raw_message_inventory(item, path=child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            inventory.extend(_raw_message_inventory(item, path=f"{path}[{index}]"))
    return inventory


def _json_size(value: Any) -> int:
    if value is None:
        return 0
    return len(json.dumps(_audit_json(value), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _execution_view(
    *,
    alert_id: str,
    run: AnalysisRun | None,
    observation: Any | None,
    replay: Any | None,
    candidate: Any | None,
) -> SocCorpusWorkbenchExecution:
    now = datetime.now(UTC)
    if run is None:
        status: CorpusExecutionStatus = "not_started"
    elif run.status is AnalysisRunStatus.RUNNING:
        status = "running"
    elif run.status is AnalysisRunStatus.FAILED:
        status = "failed"
    elif observation is None:
        status = "analysis_complete"
    else:
        status = "completed"

    projected_steps = [_execution_step(step) for step in (run.steps if run else [])]
    journal = run.request_journal if run is not None else None
    if (
        run is not None
        and run.status is AnalysisRunStatus.RUNNING
        and journal is not None
        and journal.status.value == "running"
        and not any(item.step_name == journal.provider_step_name and item.status == "running" for item in projected_steps)
    ):
        provider_elapsed = max(
            0,
            int((now - journal.provider_started_at).total_seconds() * 1000),
        )
        projected_steps.append(
            SocCorpusWorkbenchExecutionStep(
                step_name=journal.provider_step_name,
                label=_step_label(journal.provider_step_name),
                status="running",
                started_at=journal.provider_started_at.isoformat(),
                duration_ms=provider_elapsed,
            )
        )

    pattern_status: CorpusExecutionPhaseStatus
    if observation is not None:
        pattern_status = "success"
    elif run is not None and run.status is AnalysisRunStatus.FAILED:
        pattern_status = "skipped"
    elif run is not None and run.status is not AnalysisRunStatus.RUNNING:
        pattern_status = "running"
    else:
        pattern_status = "pending"
    projected_steps.append(
        SocCorpusWorkbenchExecutionStep(
            step_name="pattern_observation",
            label=_step_label("pattern_observation"),
            status=pattern_status,
            started_at=(observation.created_at.isoformat() if observation is not None else None),
            ended_at=(observation.created_at.isoformat() if observation is not None else None),
            duration_ms=0 if observation is not None else None,
        )
    )

    phases: list[SocCorpusWorkbenchExecutionPhase] = []
    for phase_key, phase_label in _EXECUTION_PHASES:
        phase_steps = [item for item in projected_steps if _step_phase(item.step_name) == phase_key]
        phase_status = _phase_status(
            phase_key,
            phase_steps,
            run=run,
            execution_status=status,
        )
        duration_values = [item.duration_ms for item in phase_steps if item.duration_ms is not None]
        phases.append(
            SocCorpusWorkbenchExecutionPhase(
                phase=phase_key,
                label=phase_label,
                status=phase_status,
                summary=_phase_summary(
                    phase_key,
                    phase_status,
                    run=run,
                    observation=observation,
                    replay=replay,
                ),
                duration_ms=(sum(duration_values) if duration_values else None),
                metrics=_phase_metrics(
                    phase_key,
                    run=run,
                    observation=observation,
                    replay=replay,
                    candidate=candidate,
                ),
                steps=phase_steps,
            )
        )

    current_phase = next(
        (item.phase for item in phases if item.status == "running"),
        None,
    )
    if current_phase is None and status == "failed":
        current_phase = next(
            (item.phase for item in phases if item.status == "failed"),
            None,
        )
    elapsed_ms = None
    if run is not None:
        elapsed_end = now if run.status is AnalysisRunStatus.RUNNING else run.ended_at or now
        elapsed_ms = max(
            0,
            int((elapsed_end - run.started_at).total_seconds() * 1000),
        )
    return SocCorpusWorkbenchExecution(
        alert_id=alert_id,
        status=status,
        current_phase=current_phase,
        run_id=run.run_id if run is not None else None,
        run_status=run.status.value if run is not None else None,
        started_at=run.started_at.isoformat() if run is not None else None,
        ended_at=(run.ended_at.isoformat() if run is not None and run.ended_at is not None else None),
        elapsed_ms=elapsed_ms,
        total_duration_ms=run.total_duration_ms if run is not None else None,
        model_name=run.model_name if run is not None else None,
        provider_purpose=(journal.provider_purpose.value if journal is not None else None),
        provider_attempt_count=(len(run.provider_request_journals) if run is not None else 0),
        observation_id=(observation.observation_id if observation is not None else None),
        aggregation_key=(observation.aggregation_key if observation is not None else None),
        candidate_id=(candidate.candidate_id if candidate is not None else None),
        phases=phases,
    )


def _execution_step(step: Any) -> SocCorpusWorkbenchExecutionStep:
    return SocCorpusWorkbenchExecutionStep(
        step_name=step.step_name,
        label=_step_label(step.step_name),
        status=_pipeline_status(step.status),
        started_at=step.started_at.isoformat(),
        ended_at=step.ended_at.isoformat() if step.ended_at is not None else None,
        duration_ms=step.duration_ms,
        warning_count=len(step.warnings),
        error=step.error,
    )


def _pipeline_status(status: PipelineStepStatus) -> CorpusExecutionPhaseStatus:
    if status is PipelineStepStatus.SUCCESS:
        return "success"
    if status is PipelineStepStatus.FAILED:
        return "failed"
    if status is PipelineStepStatus.SKIPPED:
        return "skipped"
    if status is PipelineStepStatus.RUNNING:
        return "running"
    return "pending"


def _step_phase(step_name: str) -> str:
    if step_name in _EXECUTION_STEP_PHASE:
        return _EXECUTION_STEP_PHASE[step_name]
    if step_name.startswith("analyze_"):
        return "reasoning"
    if step_name.startswith("verify_"):
        return "validation"
    return "validation"


def _step_label(step_name: str) -> str:
    return _EXECUTION_STEP_LABELS.get(step_name, step_name.replace("_", " "))


def _phase_status(
    phase: str,
    steps: list[SocCorpusWorkbenchExecutionStep],
    *,
    run: AnalysisRun | None,
    execution_status: CorpusExecutionStatus,
) -> CorpusExecutionPhaseStatus:
    statuses = {item.status for item in steps}
    if "failed" in statuses:
        return "failed"
    if "running" in statuses:
        return "running"
    if "success" in statuses:
        return "success"
    if execution_status == "failed":
        return "skipped"
    if run is None:
        return "pending"
    if execution_status in {"analysis_complete", "completed"} and phase != "memory":
        return "skipped"
    return "pending"


def _phase_summary(
    phase: str,
    status: CorpusExecutionPhaseStatus,
    *,
    run: AnalysisRun | None,
    observation: Any | None,
    replay: Any | None,
) -> str:
    if status == "pending":
        return "等待上游阶段完成"
    if status == "running":
        if phase == "reasoning":
            return "模型 Provider 正在处理有界证据与受治理上下文"
        if phase == "memory":
            return "Runtime 已完成，正在写入重复模式观察"
        return "阶段正在执行"
    if status == "failed":
        return run.failure.message if run is not None and run.failure is not None else "阶段执行失败"
    if status == "skipped":
        return "因上游失败或当前配置未启用而跳过"
    if phase == "normalize":
        return "已生成厂商无关的 canonical AlertInput"
    if phase == "facts":
        return "已提取实体并重建场景、网络角色和字段冲突"
    if phase == "context":
        return "已形成限长证据目录，并解析适用的 Skills 与确认记忆"
    if phase == "reasoning":
        return "模型输出已解析为结构化 AnalysisResult"
    if phase == "validation":
        return "已完成 Schema、证据引用、角色与质量影响范围校验"
    if phase == "decision":
        verdict = run.decision.verdict.value if run is not None and run.decision is not None else "unknown"
        return f"已生成可审计 Base Decision：{verdict}"
    support = replay.support_count if replay is not None else 1
    return f"已写入 Observation；当前固定窗口累计 {support} 条"


def _phase_metrics(
    phase: str,
    *,
    run: AnalysisRun | None,
    observation: Any | None,
    replay: Any | None,
    candidate: Any | None,
) -> dict[str, str | int | float | bool]:
    if run is None:
        return {}
    metrics: dict[str, str | int | float | bool] = {}
    if phase == "normalize" and run.normalization_report is not None:
        report = run.normalization_report
        metrics.update(
            {
                "adapter": report.adapter,
                "canonical_fields": len(report.normalized_fields),
                "missing_fields": len(report.missing_fields),
                "unmapped_fields": report.unmapped_field_count,
            }
        )
    elif phase == "facts":
        if run.extraction_report is not None:
            metrics["entity_mentions"] = run.extraction_report.mention_count
            if run.extraction_report.entity_counts:
                metrics["entity_types"] = ", ".join(f"{key}:{value}" for key, value in sorted(run.extraction_report.entity_counts.items()))
        if run.fact_reconstruction is not None:
            facts = run.fact_reconstruction
            metrics.update(
                {
                    "scenario_hypotheses": len(facts.scenario_hypotheses),
                    "role_claims": len(facts.role_claims),
                    "role_resolutions": len(facts.role_resolutions),
                    "conflicts": len(facts.conflict_reports),
                }
            )
    elif phase == "context" and run.llm_analysis_request is not None:
        request = run.llm_analysis_request
        skills = [item.skill_name for item in request.skill_context.selected_skills]
        metrics.update(
            {
                "evidence_items": len(request.evidence_catalog),
                "context_items": len(request.context_catalog),
                "selected_skill_count": len(skills),
                "selected_skills": ", ".join(skills) if skills else "none",
                "high_value_gaps": len(request.evidence_coverage.high_value_gaps),
            }
        )
    elif phase == "reasoning":
        metrics["model"] = run.model_name
        if run.analysis_output_quality is not None:
            metrics["output_quality"] = run.analysis_output_quality.status.value
        analysis_step = next(
            (item for item in reversed(run.steps) if item.step_name.startswith("analyze_")),
            None,
        )
        usage = analysis_step.metadata.get("usage") if analysis_step is not None else None
        if isinstance(usage, Mapping):
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    metrics[key] = value
    elif phase == "validation":
        grounding = run.analysis_evidence_grounding
        if grounding is not None:
            metrics.update(
                {
                    "grounded_refs": grounding.grounded_count,
                    "rejected_refs": grounding.ungrounded_count,
                    "grounded_reasoning": grounding.reasoning_grounded_count,
                    "rejected_reasoning": grounding.reasoning_ungrounded_count,
                }
            )
        if run.analysis_materiality is not None:
            metrics["decision_usable"] = run.analysis_materiality.decision_usable
            metrics["review_required"] = run.analysis_materiality.review_required
        if run.role_adjudication_verification is not None:
            metrics["role_verification"] = run.role_adjudication_verification.status.value
    elif phase == "decision" and run.decision is not None:
        metrics.update(
            {
                "verdict": run.decision.verdict.value,
                "confidence": round(run.decision.confidence, 4),
                "needs_review": run.decision.needs_review,
                "evidence_state": run.decision.evidence_state.value,
            }
        )
    elif phase == "memory" and observation is not None:
        metrics.update(
            {
                "observation_id": observation.observation_id,
                "pattern_dimension": observation.signature.dimension.value,
                "window_days": round(
                    observation.aggregation_policy.window_seconds / 86_400,
                    2,
                ),
                "support_count": replay.support_count if replay is not None else 1,
                "distinct_sources": replay.distinct_source_count if replay is not None else 1,
                "quality_gate_passed": (replay.cohort_quality.quality_gate_passed if replay is not None else False),
                "candidate": candidate.candidate_id if candidate is not None else "not_created",
            }
        )
    return metrics


def _load_cases(
    path: Path,
    *,
    source_sha256: str | None = None,
    index_path: Path | None = None,
) -> dict[str, _CorpusCase]:
    resolved_index = index_path or path.with_suffix(".workbench-index.json")
    if resolved_index.is_file():
        return _load_cases_from_index(
            resolved_index,
            source_path=path,
            source_sha256=source_sha256 or _sha256_file(path),
        )
    return _build_cases_from_frame(path)


def build_corpus_workbench_index(
    source_path: Path,
    *,
    output_path: Path | None = None,
) -> Path:
    """Build a bounded, source-hash-bound index for the large DEV corpus."""

    source = source_path.expanduser().resolve()
    target = output_path.expanduser().resolve() if output_path is not None else source.with_suffix(".workbench-index.json")
    source_sha256 = _sha256_file(source)
    cases = _build_cases_from_frame(source)
    payload_store = corpus_workbench_payload_store_path(
        source,
        index_path=target,
    )
    _write_payload_store(
        payload_store,
        cases.values(),
        source_sha256=source_sha256,
    )
    payload_store_sha256 = _sha256_file(payload_store)
    payload = {
        "schema_version": CORPUS_WORKBENCH_INDEX_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "file_name": source.name,
            "size_bytes": source.stat().st_size,
            "sha256": source_sha256,
            "alert_count": len(cases),
        },
        "sort_order": "canonical_event_time_asc_alert_id_asc",
        "payload_store": {
            "schema_version": CORPUS_WORKBENCH_PAYLOAD_STORE_VERSION,
            "file_name": payload_store.name,
            "size_bytes": payload_store.stat().st_size,
            "sha256": payload_store_sha256,
        },
        "memory_profile": {
            "profile_id": PingAnSocMemoryProfile.identity.profile_id,
            "profile_version": PingAnSocMemoryProfile.identity.profile_version,
            "feature_schema_version": (PingAnSocMemoryProfile.identity.feature_schema_version),
            "aggregation_window_seconds": _WINDOW_SECONDS,
        },
        "cases": [_case_index_record(item) for item in cases.values()],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def corpus_workbench_payload_store_path(
    source_path: Path,
    *,
    index_path: Path | None = None,
) -> Path:
    source = source_path.expanduser().resolve()
    parent = index_path.expanduser().resolve().parent if index_path is not None else source.parent
    return parent / f"{source.stem}.workbench-payloads.sqlite"


def _write_payload_store(
    path: Path,
    cases: Any,
    *,
    source_sha256: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute(
            """
            CREATE TABLE payloads (
                alert_id TEXT PRIMARY KEY,
                source_index INTEGER NOT NULL,
                payload_hash TEXT NOT NULL,
                raw_size INTEGER NOT NULL,
                payload_zlib BLOB NOT NULL
            ) WITHOUT ROWID
            """
        )
        count = 0
        for item in cases:
            if item.payload is None:
                raise SocCorpusWorkbenchError(f"cannot build payload store without alert {item.alert_id} payload")
            try:
                encoded = json.dumps(
                    item.payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise SocCorpusWorkbenchError(f"alert {item.alert_id} payload is not JSON serializable: {exc}") from exc
            connection.execute(
                "INSERT INTO payloads VALUES (?, ?, ?, ?, ?)",
                (
                    item.alert_id,
                    item.source_index,
                    item.payload_hash,
                    len(encoded),
                    sqlite3.Binary(zlib.compress(encoded, level=6)),
                ),
            )
            count += 1
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [
                ("schema_version", CORPUS_WORKBENCH_PAYLOAD_STORE_VERSION),
                ("source_sha256", source_sha256),
                ("alert_count", str(count)),
            ],
        )
        connection.commit()
    finally:
        connection.close()
    temporary.replace(path)


def _build_cases_from_frame(path: Path) -> dict[str, _CorpusCase]:
    try:
        frame = load_restricted_dataframe_pickle(path)
    except ValueError as exc:
        raise SocCorpusWorkbenchError(str(exc)) from exc
    required = {"alert_id", "alert_full_data"}
    missing = required.difference(frame.columns)
    if missing:
        raise SocCorpusWorkbenchError(f"DEV corpus is missing columns: {sorted(missing)}")

    profile = PingAnSocMemoryProfile()
    loaded: list[_CorpusCase] = []
    for source_index, (_, row) in enumerate(frame.iterrows()):
        alert_id = _text(row.get("alert_id"))
        if not alert_id:
            raise SocCorpusWorkbenchError(f"corpus row {source_index} has no alert_id")
        wrapper = row.get("alert_full_data")
        if not isinstance(wrapper, Mapping):
            raise SocCorpusWorkbenchError(f"alert {alert_id} has no alert_full_data object")
        raw_payload = wrapper.get("alert_data")
        if not isinstance(raw_payload, Mapping):
            raise SocCorpusWorkbenchError(f"alert {alert_id} has no alert_full_data.alert_data object")
        payload = copy.deepcopy(dict(raw_payload))
        source_tenant_id = payload.get("tenant_id") or payload.get("tenantId")
        if source_tenant_id is None:
            payload["tenant_id"] = CORPUS_WORKBENCH_TENANT
        elif str(source_tenant_id) != CORPUS_WORKBENCH_TENANT:
            raise SocCorpusWorkbenchError(f"alert {alert_id} declares unexpected tenant {source_tenant_id!r}")
        alert = normalize_alert_payload(payload)
        if alert.alert_id != alert_id:
            raise SocCorpusWorkbenchError(f"corpus row {alert_id} normalized to alert {alert.alert_id}")
        if alert.event.event_time is None:
            raise SocCorpusWorkbenchError(f"alert {alert_id} has no canonical event_time")
        request = build_analysis_request_for_payload(
            payload,
            sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
        ).model_copy(
            update={
                "tenant_id": alert.tenant_id,
                "environment": CORPUS_WORKBENCH_ENVIRONMENT,
            },
            deep=False,
        )
        facets = profile.project_query_facets(request)
        detection_key = _first(facets.get("detection_key"))
        detection_signature = _first(facets.get("detection_signature"))
        behavior_fingerprint = _first(facets.get("behavior_fingerprint"))
        behavior_strength = _first(facets.get("behavior_strength"))
        behavior_components = tuple(facets.get("behavior_component_core") or facets.get("behavior_component") or [])
        decision_eligible = bool(behavior_fingerprint and behavior_strength == "strong" and (not detection_key or detection_signature))
        group_id = _group_id(
            alert_id=alert_id,
            detection_key=detection_key,
            detection_signature=detection_signature,
            behavior_fingerprint=behavior_fingerprint,
        )
        window_start_value, window_end_value = _fixed_window(alert.event.event_time)
        window_id = stable_hash(
            {
                "group_id": group_id,
                "window_start": window_start_value.isoformat(),
                "window_end": window_end_value.isoformat(),
            }
        )
        endpoint = alert.entities.host.ip_addresses[0] if alert.entities.host.ip_addresses else alert.entities.network.source_ip or alert.entities.network.destination_ip
        label_available = _row_bool(row.get("operational_label_available"))
        label = _operational_label(row.get("ground_label"))
        label_record = row.get("operational_label_record")
        if not isinstance(label_record, Mapping):
            label_record = {}
        label_observed_at_value = _source_datetime(label_record.get("updated_date"))
        label_temporal_status = _label_temporal_status(
            available=label_available,
            event_time=alert.event.event_time,
            label_observed_at=label_observed_at_value,
        )
        loaded.append(
            _CorpusCase(
                alert_id=alert_id,
                source_index=source_index,
                sequence_number=0,
                payload=payload,
                payload_hash=stable_hash(payload),
                observed_at=alert.event.event_time.isoformat(),
                observed_at_value=alert.event.event_time,
                topic=_text(row.get("topic")) or _text(payload.get("topic")),
                source_type=alert.source.source_type.value,
                source_system=alert.source.source_system,
                product=alert.source.product,
                detection_key=detection_key,
                rule_code=alert.detection.rule_code,
                rule_name=alert.detection.rule_name,
                category=alert.classification.category,
                severity=alert.classification.severity,
                endpoint=endpoint,
                host_name=alert.entities.host.host_name,
                process_names=tuple(_process_names(alert.entities.process)),
                behavior_fingerprint=behavior_fingerprint,
                behavior_components=behavior_components,
                behavior_strength=behavior_strength,
                decision_eligible=decision_eligible,
                group_id=group_id,
                window_id=window_id,
                window_start=window_start_value.isoformat(),
                window_end=window_end_value.isoformat(),
                operational_label_available=label_available,
                operational_label=label,
                operational_label_observed_at=(label_observed_at_value.isoformat() if label_observed_at_value is not None else None),
                operational_label_method=_text(row.get("operational_label_method")),
                operational_label_reason=(_text(row.get("ignore_reason")) or _text(label_record.get("备注"))),
                operational_label_status=_text(row.get("status_label")),
                label_temporal_status=label_temporal_status,
            )
        )
    if len({item.alert_id for item in loaded}) != len(loaded):
        raise SocCorpusWorkbenchError("DEV corpus alert IDs must be unique")
    loaded.sort(
        key=lambda item: (
            item.observed_at_value.astimezone(UTC),
            _alert_id_sort_key(item.alert_id),
            item.source_index,
        )
    )
    group_counts = Counter(item.group_id for item in loaded)
    window_counts = Counter(item.window_id for item in loaded)
    finalized = [
        replace(
            item,
            sequence_number=sequence_number,
            group_alert_count=group_counts[item.group_id],
            window_alert_count=window_counts[item.window_id],
            readiness=_case_readiness(
                item,
                group_alert_count=group_counts[item.group_id],
                window_alert_count=window_counts[item.window_id],
            ),
        )
        for sequence_number, item in enumerate(loaded, start=1)
    ]
    return {item.alert_id: item for item in finalized}


def _load_cases_from_index(
    path: Path,
    *,
    source_path: Path,
    source_sha256: str,
) -> dict[str, _CorpusCase]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SocCorpusWorkbenchError(f"invalid corpus workbench index {path}: {exc}") from exc
    if not isinstance(document, Mapping):
        raise SocCorpusWorkbenchError("corpus workbench index must be a JSON object")
    if document.get("schema_version") != CORPUS_WORKBENCH_INDEX_VERSION:
        raise SocCorpusWorkbenchError("unsupported corpus workbench index schema")
    identity = PingAnSocMemoryProfile.identity
    expected_profile = {
        "profile_id": identity.profile_id,
        "profile_version": identity.profile_version,
        "feature_schema_version": identity.feature_schema_version,
        "aggregation_window_seconds": _WINDOW_SECONDS,
    }
    if document.get("memory_profile") != expected_profile:
        raise SocCorpusWorkbenchError("corpus workbench index does not match the current PingAn Memory profile; rebuild it")
    source = document.get("source")
    if not isinstance(source, Mapping):
        raise SocCorpusWorkbenchError("corpus workbench index has no source identity")
    if source.get("sha256") != source_sha256:
        raise SocCorpusWorkbenchError("corpus workbench index does not match the configured PKL; rebuild it")
    if source.get("file_name") != source_path.name:
        raise SocCorpusWorkbenchError("corpus workbench index source file name mismatch")
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise SocCorpusWorkbenchError("corpus workbench index contains no cases")
    cases: list[_CorpusCase] = []
    try:
        for item in raw_cases:
            if not isinstance(item, Mapping):
                raise TypeError("case must be an object")
            observed_at = datetime.fromisoformat(str(item["observed_at"]))
            if observed_at.utcoffset() is None:
                raise ValueError("observed_at must include a timezone")
            label_temporal_status = str(item["label_temporal_status"])
            if label_temporal_status not in {
                "valid",
                "label_time_missing",
                "label_precedes_alert",
                "unlabeled",
            }:
                raise ValueError(f"unsupported label_temporal_status {label_temporal_status!r}")
            readiness = str(item["readiness"])
            if readiness not in {
                "candidate_window",
                "recurrent_strong",
                "singleton_strong",
                "recurrent_context_only",
                "context_only_singleton",
                "fingerprint_missing",
            }:
                raise ValueError(f"unsupported readiness {readiness!r}")
            cases.append(
                _CorpusCase(
                    alert_id=str(item["alert_id"]),
                    source_index=int(item["source_index"]),
                    sequence_number=int(item["sequence_number"]),
                    payload=None,
                    payload_hash=str(item["payload_hash"]),
                    observed_at=observed_at.isoformat(),
                    observed_at_value=observed_at,
                    topic=_text(item.get("topic")),
                    source_type=str(item["source_type"]),
                    source_system=_text(item.get("source_system")),
                    product=_text(item.get("product")),
                    detection_key=_text(item.get("detection_key")),
                    rule_code=_text(item.get("rule_code")),
                    rule_name=_text(item.get("rule_name")),
                    category=_text(item.get("category")),
                    severity=_text(item.get("severity")),
                    endpoint=_text(item.get("endpoint")),
                    host_name=_text(item.get("host_name")),
                    process_names=tuple(item.get("process_names") or []),
                    behavior_fingerprint=_text(item.get("behavior_fingerprint")),
                    behavior_components=tuple(item.get("behavior_components") or []),
                    behavior_strength=_text(item.get("behavior_strength")),
                    decision_eligible=bool(item.get("decision_eligible")),
                    group_id=str(item["group_id"]),
                    window_id=str(item["window_id"]),
                    window_start=str(item["window_start"]),
                    window_end=str(item["window_end"]),
                    operational_label_available=bool(item.get("operational_label_available")),
                    operational_label=_operational_label(item.get("operational_label")),
                    operational_label_observed_at=_text(item.get("operational_label_observed_at")),
                    operational_label_method=_text(item.get("operational_label_method")),
                    operational_label_reason=_text(item.get("operational_label_reason")),
                    operational_label_status=_text(item.get("operational_label_status")),
                    label_temporal_status=label_temporal_status,
                    group_alert_count=int(item["group_alert_count"]),
                    window_alert_count=int(item["window_alert_count"]),
                    readiness=readiness,
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise SocCorpusWorkbenchError(f"invalid corpus workbench index case: {exc}") from exc
    if len(cases) != int(source.get("alert_count") or 0):
        raise SocCorpusWorkbenchError("corpus workbench index alert count mismatch")
    if [item.sequence_number for item in cases] != list(range(1, len(cases) + 1)):
        raise SocCorpusWorkbenchError("corpus workbench index sequence is not contiguous")
    if len({item.alert_id for item in cases}) != len(cases):
        raise SocCorpusWorkbenchError("corpus workbench index alert IDs must be unique")
    return {item.alert_id: item for item in cases}


def _resolve_payload_store(index_path: Path) -> tuple[Path, str]:
    try:
        document = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SocCorpusWorkbenchError(f"invalid corpus workbench index {index_path}: {exc}") from exc
    store = document.get("payload_store") if isinstance(document, Mapping) else None
    if not isinstance(store, Mapping):
        raise SocCorpusWorkbenchError("corpus workbench index has no random-access payload store; rebuild it")
    if store.get("schema_version") != CORPUS_WORKBENCH_PAYLOAD_STORE_VERSION:
        raise SocCorpusWorkbenchError("unsupported corpus payload store schema")
    file_name = _text(store.get("file_name"))
    expected_sha256 = _text(store.get("sha256"))
    if file_name is None or Path(file_name).name != file_name or expected_sha256 is None or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise SocCorpusWorkbenchError("invalid corpus payload store identity")
    path = index_path.parent / file_name
    if not path.is_file():
        raise SocCorpusWorkbenchError(f"corpus payload store is missing: {path}")
    actual_sha256 = _sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise SocCorpusWorkbenchError("corpus payload store hash does not match the workbench index")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    except sqlite3.Error as exc:
        raise SocCorpusWorkbenchError(f"invalid corpus payload store {path}: {exc}") from exc
    finally:
        connection.close()
    if metadata.get("schema_version") != CORPUS_WORKBENCH_PAYLOAD_STORE_VERSION:
        raise SocCorpusWorkbenchError("corpus payload store metadata version mismatch")
    return path, actual_sha256


def _load_payload_from_store(
    path: Path,
    case: _CorpusCase,
) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            """
            SELECT source_index, payload_hash, raw_size, payload_zlib
            FROM payloads
            WHERE alert_id = ?
            """,
            (case.alert_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise SocCorpusWorkbenchError(f"failed to read alert {case.alert_id} from payload store: {exc}") from exc
    finally:
        connection.close()
    if row is None:
        raise SocCorpusWorkbenchError(f"alert {case.alert_id} is missing from the corpus payload store")
    source_index, payload_hash, raw_size, compressed = row
    if int(source_index) != case.source_index or str(payload_hash) != case.payload_hash:
        raise SocCorpusWorkbenchError(f"alert {case.alert_id} payload store identity mismatch")
    try:
        encoded = zlib.decompress(compressed)
        payload = json.loads(encoded)
    except (TypeError, zlib.error, json.JSONDecodeError) as exc:
        raise SocCorpusWorkbenchError(f"alert {case.alert_id} payload store content is invalid: {exc}") from exc
    if len(encoded) != int(raw_size) or not isinstance(payload, dict):
        raise SocCorpusWorkbenchError(f"alert {case.alert_id} payload store content failed validation")
    if stable_hash(payload) != case.payload_hash:
        raise SocCorpusWorkbenchError(f"alert {case.alert_id} payload store hash mismatch")
    return payload


def _case_index_record(item: _CorpusCase) -> dict[str, Any]:
    return {
        "alert_id": item.alert_id,
        "source_index": item.source_index,
        "sequence_number": item.sequence_number,
        "payload_hash": item.payload_hash,
        "observed_at": item.observed_at,
        "topic": item.topic,
        "source_type": item.source_type,
        "source_system": item.source_system,
        "product": item.product,
        "detection_key": item.detection_key,
        "rule_code": item.rule_code,
        "rule_name": item.rule_name,
        "category": item.category,
        "severity": item.severity,
        "endpoint": item.endpoint,
        "host_name": item.host_name,
        "process_names": list(item.process_names),
        "behavior_fingerprint": item.behavior_fingerprint,
        "behavior_components": list(item.behavior_components),
        "behavior_strength": item.behavior_strength,
        "decision_eligible": item.decision_eligible,
        "group_id": item.group_id,
        "window_id": item.window_id,
        "window_start": item.window_start,
        "window_end": item.window_end,
        "operational_label_available": item.operational_label_available,
        "operational_label": item.operational_label,
        "operational_label_observed_at": item.operational_label_observed_at,
        "operational_label_method": item.operational_label_method,
        "operational_label_reason": item.operational_label_reason,
        "operational_label_status": item.operational_label_status,
        "label_temporal_status": item.label_temporal_status,
        "group_alert_count": item.group_alert_count,
        "window_alert_count": item.window_alert_count,
        "readiness": item.readiness,
    }


def _load_payloads(
    path: Path,
    cases: Mapping[str, _CorpusCase],
) -> dict[str, dict[str, Any]]:
    try:
        frame = load_restricted_dataframe_pickle(path)
    except ValueError as exc:
        raise SocCorpusWorkbenchError(str(exc)) from exc
    payloads: dict[str, dict[str, Any]] = {}
    for source_index, (_, row) in enumerate(frame.iterrows()):
        alert_id = _text(row.get("alert_id"))
        case = cases.get(alert_id or "")
        if case is None:
            continue
        wrapper = row.get("alert_full_data")
        raw_payload = wrapper.get("alert_data") if isinstance(wrapper, Mapping) else None
        if not isinstance(raw_payload, Mapping):
            raise SocCorpusWorkbenchError(f"alert {alert_id} has no alert_full_data.alert_data object")
        payload = copy.deepcopy(dict(raw_payload))
        source_tenant_id = payload.get("tenant_id") or payload.get("tenantId")
        if source_tenant_id is None:
            payload["tenant_id"] = CORPUS_WORKBENCH_TENANT
        elif str(source_tenant_id) != CORPUS_WORKBENCH_TENANT:
            raise SocCorpusWorkbenchError(f"alert {alert_id} declares unexpected tenant {source_tenant_id!r}")
        if stable_hash(payload) != case.payload_hash:
            raise SocCorpusWorkbenchError(f"alert {alert_id} payload no longer matches the workbench index")
        if source_index != case.source_index:
            raise SocCorpusWorkbenchError(f"alert {alert_id} source index no longer matches the workbench index")
        payloads[alert_id] = payload
    missing = sorted(set(cases) - set(payloads))
    if missing:
        raise SocCorpusWorkbenchError(f"configured corpus is missing indexed alerts: {missing[:20]}")
    return payloads


def _case_readiness(
    item: _CorpusCase,
    *,
    group_alert_count: int,
    window_alert_count: int,
) -> CorpusReadiness:
    if item.behavior_fingerprint is None:
        return "fingerprint_missing"
    if item.decision_eligible and window_alert_count >= 5:
        return "candidate_window"
    if item.decision_eligible and group_alert_count >= 2:
        return "recurrent_strong"
    if item.decision_eligible:
        return "singleton_strong"
    if group_alert_count >= 2:
        return "recurrent_context_only"
    return "context_only_singleton"


def _chronological_processability(
    cases: Any,
    observations_by_alert: Mapping[str, Any],
) -> tuple[set[str], dict[str, str]]:
    """Permit only the earliest unprocessed alert in each Memory cohort."""

    grouped: dict[str, list[_CorpusCase]] = defaultdict(list)
    for item in cases:
        grouped[item.group_id].append(item)
    processable: set[str] = set()
    blockers: dict[str, str] = {}
    for items in grouped.values():
        first_unprocessed: str | None = None
        for item in sorted(
            items,
            key=lambda case: (
                case.observed_at_value.astimezone(UTC),
                _alert_id_sort_key(case.alert_id),
                case.source_index,
            ),
        ):
            if item.alert_id in observations_by_alert:
                continue
            if first_unprocessed is None:
                first_unprocessed = item.alert_id
                processable.add(item.alert_id)
            else:
                blockers[item.alert_id] = first_unprocessed
    return processable, blockers


def _project_operational_outcome(
    *,
    decision: Any | None,
    disposition: SocOperationalDisposition | None,
) -> tuple[CorpusProjectedDisposition, str | None]:
    """Project technical truth into the legacy two-lane operational label space."""

    if decision is None:
        return "undetermined", None
    if disposition in {
        SocOperationalDisposition.CLOSED_FALSE_POSITIVE,
        SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE,
        SocOperationalDisposition.SUPPRESSED,
        SocOperationalDisposition.IGNORED,
        SocOperationalDisposition.DUPLICATE,
    }:
        return "ignore", f"disposition:{disposition.value}"
    if disposition in {
        SocOperationalDisposition.CLOSED_TRUE_POSITIVE,
        SocOperationalDisposition.ESCALATED,
    }:
        return "transfer", f"disposition:{disposition.value}"

    verdict = decision.verdict
    if verdict is Verdict.FALSE_POSITIVE:
        return "ignore", f"verdict:{verdict.value}"
    if verdict in {Verdict.TRUE_POSITIVE, Verdict.SUSPICIOUS}:
        return "transfer", f"verdict:{verdict.value}"
    return "undetermined", f"verdict:{verdict.value}"


def _compare_operational_label(
    case: _CorpusCase,
    *,
    projection: CorpusProjectedDisposition,
    decision_available: bool,
) -> CorpusComparisonStatus:
    if not case.operational_label_available:
        return "unlabeled"
    if not decision_available:
        return "not_run"
    if case.label_temporal_status != "valid" or case.operational_label is None:
        return "unscored"
    if projection == "undetermined":
        return "unscored"
    expected: CorpusProjectedDisposition = "ignore" if case.operational_label == "忽略" else "transfer"
    return "matched" if projection == expected else "mismatched"


def _evaluation(
    cases: Any,
    alerts: list[SocCorpusWorkbenchAlert],
) -> SocCorpusWorkbenchEvaluation:
    cases = list(cases)
    label_counts = Counter(item.operational_label for item in cases if item.operational_label_available and item.operational_label is not None)
    valid_count = sum(item.operational_label_available and item.operational_label is not None and item.label_temporal_status == "valid" for item in cases)
    invalid_count = sum(item.operational_label_available and item.label_temporal_status != "valid" for item in cases)
    processed = [item for item in alerts if item.base_label_comparison in {"matched", "mismatched", "unscored"}]
    base_matched = sum(item.base_label_comparison == "matched" for item in processed)
    base_mismatched = sum(item.base_label_comparison == "mismatched" for item in processed)
    base_unscored = sum(item.base_label_comparison == "unscored" for item in processed)
    effective_matched = sum(item.effective_label_comparison == "matched" for item in processed)
    effective_mismatched = sum(item.effective_label_comparison == "mismatched" for item in processed)
    effective_unscored = sum(item.effective_label_comparison == "unscored" for item in processed)
    base_denominator = base_matched + base_mismatched
    effective_denominator = effective_matched + effective_mismatched
    return SocCorpusWorkbenchEvaluation(
        label_counts=dict(sorted(label_counts.items())),
        temporally_valid_label_count=valid_count,
        temporally_invalid_label_count=invalid_count,
        unlabeled_count=sum(not item.operational_label_available for item in cases),
        processed_labeled_count=len(processed),
        base_matched_count=base_matched,
        base_mismatched_count=base_mismatched,
        base_unscored_count=base_unscored,
        base_match_rate=(base_matched / base_denominator if base_denominator else None),
        effective_matched_count=effective_matched,
        effective_mismatched_count=effective_mismatched,
        effective_unscored_count=effective_unscored,
        effective_match_rate=(effective_matched / effective_denominator if effective_denominator else None),
    )


def _readiness(
    cases: Any,
    alerts: list[SocCorpusWorkbenchAlert],
) -> SocCorpusWorkbenchReadiness:
    cases = list(cases)
    recurrent_groups = {item.group_id for item in cases if item.group_alert_count >= 2}
    candidate_windows = {item.window_id for item in cases if item.decision_eligible and item.window_alert_count >= 5}
    return SocCorpusWorkbenchReadiness(
        total_alert_count=len(cases),
        fingerprint_coverage_count=sum(item.behavior_fingerprint is not None for item in cases),
        decision_eligible_alert_count=sum(item.decision_eligible for item in cases),
        recurrent_group_count=len(recurrent_groups),
        recurrent_alert_count=sum(item.group_alert_count >= 2 for item in cases),
        candidate_window_group_count=len(candidate_windows),
        candidate_window_alert_count=sum(item.window_id in candidate_windows for item in cases),
        processed_count=sum(item.workflow_state == "completed" for item in alerts),
        failed_count=sum(item.workflow_state == "failed" for item in alerts),
        memory_hit_alert_count=sum(bool(item.memory_contexts) for item in alerts),
    )


def _group_views(
    cases: Any,
    alerts: list[SocCorpusWorkbenchAlert],
) -> list[SocCorpusWorkbenchGroup]:
    grouped_cases: dict[str, list[_CorpusCase]] = defaultdict(list)
    for item in cases:
        grouped_cases[item.group_id].append(item)
    alerts_by_group: dict[str, list[SocCorpusWorkbenchAlert]] = defaultdict(list)
    for item in alerts:
        alerts_by_group[item.group_id].append(item)
    views: list[SocCorpusWorkbenchGroup] = []
    for group_id, items in grouped_cases.items():
        representative = items[0]
        windows = Counter(item.window_id for item in items)
        group_alerts = alerts_by_group[group_id]
        views.append(
            SocCorpusWorkbenchGroup(
                group_id=group_id,
                source_type=representative.source_type,
                detection_key=representative.detection_key,
                rule_code=representative.rule_code,
                rule_name=representative.rule_name,
                behavior_fingerprint=representative.behavior_fingerprint,
                behavior_components=list(representative.behavior_components),
                decision_eligible=representative.decision_eligible,
                alert_count=len(items),
                window_count=len(windows),
                max_window_alert_count=max(windows.values()),
                candidate_window_count=sum(count >= 5 for count in windows.values()) if representative.decision_eligible else 0,
                processed_count=sum(item.workflow_state == "completed" for item in group_alerts),
                memory_hit_count=sum(bool(item.memory_contexts) for item in group_alerts),
            )
        )
    return sorted(
        views,
        key=lambda item: (
            -item.candidate_window_count,
            -int(item.decision_eligible),
            -item.alert_count,
            item.rule_name or "",
        ),
    )


def _group_id(
    *,
    alert_id: str,
    detection_key: str | None,
    detection_signature: str | None,
    behavior_fingerprint: str | None,
) -> str:
    if detection_key and behavior_fingerprint:
        identity: dict[str, Any] = {
            "dimension": "compound",
            "detection_key": detection_key,
            "detection_signature": detection_signature,
            "behavior_fingerprint": behavior_fingerprint,
        }
    elif detection_key:
        identity = {
            "dimension": "detection",
            "detection_key": detection_key,
            "detection_signature": detection_signature,
        }
    elif behavior_fingerprint:
        identity = {
            "dimension": "behavior",
            "behavior_fingerprint": behavior_fingerprint,
        }
    else:
        identity = {"dimension": "unavailable", "alert_id": alert_id}
    return f"CG-{stable_hash(identity)[:12].upper()}"


def _fixed_window(value: datetime) -> tuple[datetime, datetime]:
    observed_utc = value.astimezone(UTC)
    epoch = int(observed_utc.timestamp())
    window_epoch = epoch - (epoch % _WINDOW_SECONDS)
    start = datetime.fromtimestamp(window_epoch, UTC)
    return start, start + timedelta(seconds=_WINDOW_SECONDS)


def _process_names(process: Any) -> list[str]:
    values: list[str] = []
    for value in (process.process_name, process.parent_process_name):
        if value and value not in values:
            values.append(value)
    for observation in process.observations:
        for node in observation.nodes:
            if node.process_name and node.process_name not in values:
                values.append(node.process_name)
    return values[:8]


def _row_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = _text(value)
    return normalized is not None and normalized.lower() in {
        "1",
        "true",
        "yes",
        "y",
    }


def _operational_label(value: Any) -> CorpusOperationalLabel | None:
    normalized = _text(value)
    if normalized == "忽略":
        return "忽略"
    if normalized == "转交":
        return "转交"
    return None


def _source_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        normalized = _text(value)
        if normalized is None:
            return None
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.utcoffset() is None:
        return parsed.replace(tzinfo=_PINGAN_TIMEZONE)
    return parsed


def _label_temporal_status(
    *,
    available: bool,
    event_time: datetime,
    label_observed_at: datetime | None,
) -> CorpusLabelTemporalStatus:
    if not available:
        return "unlabeled"
    if label_observed_at is None:
        return "label_time_missing"
    if label_observed_at.astimezone(UTC) < event_time.astimezone(UTC):
        return "label_precedes_alert"
    return "valid"


def _alert_id_sort_key(value: str) -> tuple[int, int | str]:
    try:
        return 0, int(value)
    except ValueError:
        return 1, value


def _text(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    normalized = str(value).strip()
    return normalized or None


def _first(values: list[str] | None) -> str | None:
    return values[0] if values else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "CORPUS_WORKBENCH_ENVIRONMENT",
    "CORPUS_WORKBENCH_INDEX_VERSION",
    "CORPUS_WORKBENCH_VERSION",
    "SocCorpusWorkbenchAuditBundle",
    "SocCorpusWorkbenchError",
    "SocCorpusWorkbenchExecution",
    "SocCorpusWorkbenchProcessResult",
    "SocCorpusWorkbenchService",
    "SocCorpusWorkbenchState",
    "build_corpus_workbench_index",
]
