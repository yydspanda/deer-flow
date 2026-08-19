"""Browser-driven DEV explorer for the complete reviewed PingAn alert corpus."""

from __future__ import annotations

import copy
import hashlib
import math
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from soc_agent.contracts import (
    AnalysisRun,
    AnalysisRunStatus,
    AuditAction,
    MemoryPatternDataClass,
    MemoryPatternSourceType,
    SensitiveEvidenceMode,
    ServiceRequestContext,
)
from soc_agent.core import SocAnalysisService, SocMemoryPatternService
from soc_agent.core.runtime import build_analysis_request_for_payload
from soc_agent.db import SqlAlchemyAlertRepository
from soc_agent.demo.corpus_loader import load_restricted_dataframe_pickle
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile
from soc_agent.llm import SocLLMSettings
from soc_agent.normalizers import normalize_alert_payload
from soc_agent.utils.hashing import stable_hash

CORPUS_WORKBENCH_VERSION = "soc.corpus_dev_workbench.v1"
CORPUS_WORKBENCH_ENVIRONMENT = "dev"
CORPUS_WORKBENCH_TENANT = "pingan"
_WINDOW_SECONDS = 86_400

CorpusReadiness = Literal[
    "candidate_window",
    "recurrent_strong",
    "singleton_strong",
    "recurrent_context_only",
    "context_only_singleton",
    "fingerprint_missing",
]


class SocCorpusWorkbenchError(ValueError):
    """Base error for an invalid local corpus workbench operation."""


@dataclass(frozen=True)
class _CorpusCase:
    alert_id: str
    source_index: int
    payload: dict[str, Any]
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


class SocCorpusWorkbenchSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    alert_count: int = Field(ge=1)


class SocCorpusWorkbenchModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    model_name: str | None = None
    thinking_enabled: bool
    role_verifier_enabled: bool
    role_verifier_model_name: str | None = None


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
    memory_id: str | None = None
    memory_status: str | None = None
    memory_contexts: list[SocCorpusWorkbenchMemoryContext] = Field(default_factory=list)
    memory_directive_applied: bool = False
    memory_effect: str | None = None
    decision_stages: list[SocCorpusWorkbenchDecisionStage] = Field(default_factory=list)


class SocCorpusWorkbenchState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.corpus_dev_workbench.v1"] = "soc.corpus_dev_workbench.v1"
    safety: SocCorpusWorkbenchSafety
    source: SocCorpusWorkbenchSource
    model: SocCorpusWorkbenchModelConfig
    readiness: SocCorpusWorkbenchReadiness
    groups: list[SocCorpusWorkbenchGroup]
    alerts: list[SocCorpusWorkbenchAlert]


class SocCorpusWorkbenchProcessResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.corpus_dev_workbench_process.v1"] = "soc.corpus_dev_workbench_process.v1"
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
    ) -> None:
        self._repository = repository
        self._analysis_service = analysis_service
        self._pattern_service = pattern_service
        self._source_path = source_path.expanduser().resolve()
        self._settings = settings
        self._database_file = database_file
        self._source_sha256 = _sha256_file(self._source_path)
        self._cases = _load_cases(self._source_path)

    def get_state(self) -> SocCorpusWorkbenchState:
        observations_by_alert = self._observations_by_alert()
        runs = self._runs_by_alert(observations_by_alert)
        queues_by_run = {item.run_id: item for item in self._repository.list_review_items(status=None, limit=10_000)}
        replay_by_key = {item.aggregation_key: self._pattern_service.replay(item.aggregation_key) for item in observations_by_alert.values()}
        alerts = [
            self._alert_view(
                case,
                run=runs.get(case.alert_id),
                observation=observations_by_alert.get(case.alert_id),
                replay_by_key=replay_by_key,
                queue_by_run=queues_by_run,
            )
            for case in self._cases.values()
        ]
        return SocCorpusWorkbenchState(
            safety=SocCorpusWorkbenchSafety(database_file=self._database_file),
            source=SocCorpusWorkbenchSource(
                file_name=self._source_path.name,
                sha256=self._source_sha256,
                alert_count=len(self._cases),
            ),
            model=SocCorpusWorkbenchModelConfig(
                mode=self._settings.mode.value,
                model_name=self._settings.model_name,
                thinking_enabled=self._settings.thinking_enabled,
                role_verifier_enabled=self._settings.role_verifier_enabled,
                role_verifier_model_name=self._settings.role_verifier_model_name,
            ),
            readiness=_readiness(self._cases.values(), alerts),
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

        request_context = context.model_copy(update={"idempotency_key": self._analysis_idempotency_key(alert_id)})
        run = self._analysis_service.analyze(
            copy.deepcopy(case.payload),
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

    def _runs_by_alert(
        self,
        observations_by_alert: Mapping[str, Any],
    ) -> dict[str, AnalysisRun]:
        selected: dict[str, AnalysisRun] = {}
        for alert_id, case in self._cases.items():
            observation = observations_by_alert.get(alert_id)
            run = self._repository.get_run(observation.source.run_id) if observation is not None and observation.source.run_id is not None else None
            if run is None:
                audit = self._repository.find_audit_record_by_idempotency_key(
                    self._analysis_idempotency_key(alert_id),
                    action=AuditAction.ANALYSIS.value,
                )
                run = self._repository.get_run(audit.run_id) if audit is not None else None
            if run is None or run.alert_id != alert_id or run.input_hash != case.payload_hash:
                continue
            selected[alert_id] = run
        return selected

    def _alert_view(
        self,
        case: _CorpusCase,
        *,
        run: AnalysisRun | None,
        observation: Any | None,
        replay_by_key: Mapping[str, Any],
        queue_by_run: Mapping[str, Any],
    ) -> SocCorpusWorkbenchAlert:
        transition = None
        queue = None
        memory_uses = []
        if run is not None:
            transitions = self._repository.list_decision_transitions(
                run_id=run.run_id,
                limit=10,
            )
            transition = transitions[0] if transitions else None
            queue = queue_by_run.get(run.run_id)
            memory_uses = self._repository.list_memory_uses(
                run_id=run.run_id,
                limit=100,
            )
        replay = replay_by_key.get(observation.aggregation_key) if observation is not None else None
        candidate = self._repository.find_memory_candidate_by_source_id(f"memory_pattern:{observation.aggregation_key}") if observation is not None else None
        record = self._repository.get_memory_record_by_candidate_id(candidate.candidate_id) if candidate is not None else None
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
        return SocCorpusWorkbenchAlert(
            alert_id=case.alert_id,
            source_index=case.source_index,
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
            can_process=observation is None,
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
            memory_id=(record.memory_id if record is not None else None),
            memory_status=(record.status.value if record is not None else None),
            memory_contexts=memory_contexts,
            memory_directive_applied=applied_use is not None,
            memory_effect=(applied_use.effect.value if applied_use is not None else None),
            decision_stages=stages,
        )


def _load_cases(path: Path) -> dict[str, _CorpusCase]:
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
            deep=True,
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
        loaded.append(
            _CorpusCase(
                alert_id=alert_id,
                source_index=source_index,
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
            )
        )
    if len({item.alert_id for item in loaded}) != len(loaded):
        raise SocCorpusWorkbenchError("DEV corpus alert IDs must be unique")
    group_counts = Counter(item.group_id for item in loaded)
    window_counts = Counter(item.window_id for item in loaded)
    finalized = [
        replace(
            item,
            group_alert_count=group_counts[item.group_id],
            window_alert_count=window_counts[item.window_id],
            readiness=_case_readiness(
                item,
                group_alert_count=group_counts[item.group_id],
                window_alert_count=window_counts[item.window_id],
            ),
        )
        for item in loaded
    ]
    return {item.alert_id: item for item in finalized}


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
    "CORPUS_WORKBENCH_VERSION",
    "SocCorpusWorkbenchError",
    "SocCorpusWorkbenchProcessResult",
    "SocCorpusWorkbenchService",
    "SocCorpusWorkbenchState",
]
