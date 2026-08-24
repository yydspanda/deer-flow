"""Held-out, human-labeled evaluation for governed SOC Memory."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from soc_agent.application import build_soc_memory_profile_registry
from soc_agent.automation import InMemorySocAutomationRepository
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AdjudicatedRoleType,
    AnalysisContextReferenceKind,
    AnalysisRun,
    AnalysisRunStatus,
    ConfidenceLabelReviewSource,
    ConfidenceLabelReviewStatus,
    Decision,
    EntrySurface,
    LLMAnalysisRequest,
    NetworkBoundaryDirection,
    RoleVerificationStatus,
    ServiceRequestContext,
    SocAutomationContributorKind,
    SocDecisionTransitionKind,
    SocEvaluationDataClass,
    SocMemoryApplicabilityStatus,
    SocMemoryRecord,
    SocMemoryRecordStatus,
    Verdict,
)
from soc_agent.core import SocAutomationService, SocMemoryService
from soc_agent.memory import (
    ConfirmedMemoryAnalysisRequestEnricher,
    InMemoryMemoryCandidateRepository,
    memory_query_from_analysis_request,
)
from soc_agent.utils.hashing import stable_hash

DEFAULT_MEMORY_EVAL_FIXTURE = Path(__file__).resolve().parents[2] / "samples/eval/memory/pingan_profile_v6_simulation_v1.json"


class MemoryEvalRelationship(StrEnum):
    """Human judgment for one held-out alert and one frozen Memory record."""

    DECISION_APPLICABLE = "decision_applicable"
    CONTEXT_ONLY = "context_only"
    UNRELATED = "unrelated"


class MemoryEvalVerifierOutcome(StrEnum):
    NOT_TRIGGERED = "not_triggered"
    CONFIRMED = "confirmed"
    CHALLENGED = "challenged"
    UNRESOLVED = "unresolved"
    UNAVAILABLE = "unavailable"
    MISSING_RESULT = "missing_result"


class MemoryEvalRoleLabel(BaseModel):
    """Comparable semantic role without model evidence references."""

    model_config = ConfigDict(extra="forbid")

    role: AdjudicatedRoleType
    entity_type: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=1000)

    @field_validator("entity_type", "value")
    @classmethod
    def normalize_role_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("role label text must not be blank")
        return normalized


class MemoryEvalPredictionSnapshot(BaseModel):
    """Bounded analyzer output copied into a review fixture."""

    model_config = ConfigDict(extra="forbid")

    scenario_keys: list[str] = Field(default_factory=list, max_length=30)
    boundary_direction: NetworkBoundaryDirection | None = None
    roles: list[MemoryEvalRoleLabel] = Field(default_factory=list, max_length=30)
    verifier_outcome: MemoryEvalVerifierOutcome = MemoryEvalVerifierOutcome.NOT_TRIGGERED
    verifier_failure_kind: str | None = Field(default=None, max_length=128)

    @field_validator("scenario_keys")
    @classmethod
    def normalize_scenario_keys(cls, values: list[str]) -> list[str]:
        return _normalized_unique(values, field_name="scenario_keys")

    @field_validator("roles")
    @classmethod
    def require_unique_roles(cls, values: list[MemoryEvalRoleLabel]) -> list[MemoryEvalRoleLabel]:
        keys = [_role_key(item) for item in values]
        if len(keys) != len(set(keys)):
            raise ValueError("prediction roles must be unique")
        return values

    @model_validator(mode="after")
    def validate_verifier_outcome(self) -> MemoryEvalPredictionSnapshot:
        if self.verifier_outcome in {
            MemoryEvalVerifierOutcome.UNAVAILABLE,
            MemoryEvalVerifierOutcome.MISSING_RESULT,
        }:
            if not self.verifier_failure_kind:
                raise ValueError("failed verifier outcome requires verifier_failure_kind")
        elif self.verifier_failure_kind is not None:
            raise ValueError("successful or non-triggered verifier cannot carry a failure kind")
        return self


class MemoryEvalHumanTruth(BaseModel):
    """Independent analyst truth and pairwise Memory relevance labels."""

    model_config = ConfigDict(extra="forbid")

    review_status: ConfidenceLabelReviewStatus = ConfidenceLabelReviewStatus.PENDING_REVIEW
    review_source: ConfidenceLabelReviewSource | None = None
    reviewer_id: str | None = Field(default=None, max_length=200)
    reviewed_at: datetime | None = None
    review_reason: str | None = Field(default=None, max_length=4000)
    actual_verdict: Verdict | None = None
    actual_scenario_keys: list[str] | None = Field(default=None, max_length=30)
    actual_boundary_direction: NetworkBoundaryDirection | None = None
    actual_roles: list[MemoryEvalRoleLabel] | None = Field(default=None, max_length=30)
    expected_review_required: bool | None = None
    expected_memory_relationships: dict[str, MemoryEvalRelationship] | None = None

    @field_validator("actual_scenario_keys")
    @classmethod
    def normalize_actual_scenarios(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return _normalized_unique(values, field_name="actual_scenario_keys")

    @field_validator("actual_roles")
    @classmethod
    def require_unique_actual_roles(cls, values: list[MemoryEvalRoleLabel] | None) -> list[MemoryEvalRoleLabel] | None:
        if values is None:
            return None
        keys = [_role_key(item) for item in values]
        if len(keys) != len(set(keys)):
            raise ValueError("actual roles must be unique")
        return values

    @model_validator(mode="after")
    def validate_review_boundary(self) -> MemoryEvalHumanTruth:
        review_fields = (
            self.review_source,
            self.reviewer_id,
            self.reviewed_at,
            self.review_reason,
        )
        truth_fields = (
            self.actual_verdict,
            self.actual_scenario_keys,
            self.actual_boundary_direction,
            self.actual_roles,
            self.expected_review_required,
            self.expected_memory_relationships,
        )
        if self.review_status is ConfidenceLabelReviewStatus.PENDING_REVIEW:
            if any(value is not None for value in (*review_fields, *truth_fields)):
                raise ValueError("pending Memory eval truth cannot carry review labels")
            return self

        if self.review_source is None or not self.reviewer_id or self.reviewed_at is None or not self.review_reason:
            raise ValueError("reviewed Memory eval truth requires source, reviewer, time, and reason")
        if self.reviewed_at.utcoffset() is None:
            raise ValueError("Memory eval reviewed_at must include a timezone")
        if self.review_status is ConfidenceLabelReviewStatus.ACCEPTED:
            if self.actual_verdict is None or self.actual_verdict in {
                Verdict.UNKNOWN,
                Verdict.NEEDS_REVIEW,
            }:
                raise ValueError("accepted Memory eval truth requires a conclusive actual_verdict")
            if self.expected_review_required is None:
                raise ValueError("accepted Memory eval truth requires expected_review_required")
            if self.expected_memory_relationships is None:
                raise ValueError("accepted Memory eval truth requires pairwise Memory relationships")
        return self


class MemoryEvalRecordFixture(BaseModel):
    """One frozen, retrieval-active record and its non-held-out source lineage."""

    model_config = ConfigDict(extra="forbid")

    record: SocMemoryRecord
    source_alert_ids: list[str] = Field(min_length=1, max_length=10000)

    @field_validator("source_alert_ids")
    @classmethod
    def normalize_source_alert_ids(cls, values: list[str]) -> list[str]:
        return _normalized_unique(values, field_name="source_alert_ids")

    @model_validator(mode="after")
    def require_active_confirmed_record(self) -> MemoryEvalRecordFixture:
        if self.record.status is not SocMemoryRecordStatus.CONFIRMED:
            raise ValueError("Memory eval records must be confirmed")
        if not self.record.retrieval_enabled:
            raise ValueError("Memory eval records must have governed retrieval enabled")
        return self


class MemoryEvalCaseFixture(BaseModel):
    """One frozen held-out query with independent predictions and truth."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    input_hash: str = Field(min_length=1, max_length=256)
    source_path: str | None = Field(default=None, max_length=2000)
    request: LLMAnalysisRequest
    base_decision: Decision
    prediction: MemoryEvalPredictionSnapshot = Field(default_factory=MemoryEvalPredictionSnapshot)
    truth: MemoryEvalHumanTruth = Field(default_factory=MemoryEvalHumanTruth)


class MemoryHeldOutEvalFixture(BaseModel):
    """Versioned corpus keeping Memory construction and query samples separate."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_heldout_eval_fixture.v1"] = "soc.memory_heldout_eval_fixture.v1"
    fixture_set_id: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    data_class: SocEvaluationDataClass
    mocked: bool
    tenant_id: str = Field(min_length=1, max_length=200)
    environment: str = Field(min_length=1, max_length=128)
    memory_profile_id: str = Field(min_length=1, max_length=200)
    memory_profile_version: str = Field(min_length=1, max_length=100)
    memory_feature_schema_version: str = Field(min_length=1, max_length=200)
    evaluated_at: datetime
    source_refs: list[str] = Field(min_length=1, max_length=100)
    records: list[MemoryEvalRecordFixture] = Field(min_length=1, max_length=200)
    cases: list[MemoryEvalCaseFixture] = Field(min_length=1, max_length=10000)
    fixture_path: str | None = None
    rollout_authorized: Literal[False] = False

    @field_validator("source_refs")
    @classmethod
    def normalize_source_refs(cls, values: list[str]) -> list[str]:
        return _normalized_unique(values, field_name="source_refs")

    @model_validator(mode="after")
    def validate_held_out_boundary(self) -> MemoryHeldOutEvalFixture:
        if self.evaluated_at.utcoffset() is None:
            raise ValueError("Memory eval evaluated_at must include a timezone")
        if self.mocked is not (self.data_class is SocEvaluationDataClass.SIMULATION):
            raise ValueError("mocked must agree with Memory eval data_class")

        memory_ids = [item.record.memory_id for item in self.records]
        if len(memory_ids) != len(set(memory_ids)):
            raise ValueError("Memory eval record IDs must be unique")
        case_ids = [item.case_id for item in self.cases]
        run_ids = [item.run_id for item in self.cases]
        input_hashes = [item.input_hash for item in self.cases]
        for values, label in (
            (case_ids, "case_id"),
            (run_ids, "run_id"),
            (input_hashes, "input_hash"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"held-out Memory eval {label} values must be unique")

        source_alert_ids = {alert_id for item in self.records for alert_id in item.source_alert_ids}
        held_out_alert_ids = {item.request.alert_id for item in self.cases}
        overlap = sorted(source_alert_ids & held_out_alert_ids)
        if overlap:
            raise ValueError("Memory construction and held-out alert IDs overlap: " + ", ".join(overlap[:20]))

        expected_memory_ids = set(memory_ids)
        for item in self.records:
            record = item.record
            if record.tenant_id != self.tenant_id or record.tenant_scope != self.tenant_id:
                raise ValueError("Memory eval record tenant does not match fixture tenant")
            metadata = record.metadata
            expected_profile = {
                "memory_profile_id": self.memory_profile_id,
                "memory_profile_version": self.memory_profile_version,
                "memory_feature_schema_version": self.memory_feature_schema_version,
            }
            for key, value in expected_profile.items():
                if metadata.get(key) != value:
                    raise ValueError(f"Memory eval record {record.memory_id} has mismatched {key}")

        for case in self.cases:
            if case.request.alert_id in source_alert_ids:
                raise ValueError("held-out alert cannot be a Memory source alert")
            if case.request.tenant_id != self.tenant_id:
                raise ValueError("Memory eval request tenant does not match fixture tenant")
            if case.request.environment != self.environment:
                raise ValueError("Memory eval request environment does not match fixture environment")
            if case.truth.review_status is ConfidenceLabelReviewStatus.ACCEPTED:
                relationships = case.truth.expected_memory_relationships or {}
                if set(relationships) != expected_memory_ids:
                    raise ValueError(f"accepted case {case.case_id} must label every frozen Memory record")
            if self.data_class is SocEvaluationDataClass.DESENSITIZED_REAL:
                if case.truth.review_source is ConfidenceLabelReviewSource.SIMULATION_FIXTURE:
                    raise ValueError("desensitized-real Memory eval cannot use simulation labels")
            elif case.truth.review_status is not ConfidenceLabelReviewStatus.PENDING_REVIEW and case.truth.review_source is not ConfidenceLabelReviewSource.SIMULATION_FIXTURE:
                raise ValueError("simulation Memory eval must use simulation_fixture labels")
        return self


class MemoryEvalBinaryMetrics(BaseModel):
    true_positive: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    true_negative: int = Field(ge=0)
    positive_support: int = Field(ge=0)
    negative_support: int = Field(ge=0)
    precision: float | None = Field(default=None, ge=0.0, le=1.0)
    recall: float | None = Field(default=None, ge=0.0, le=1.0)
    f1: float | None = Field(default=None, ge=0.0, le=1.0)


class MemoryEvalAccuracyMetric(BaseModel):
    support: int = Field(ge=0)
    correct: int = Field(ge=0)
    accuracy: float | None = Field(default=None, ge=0.0, le=1.0)


class MemoryEvalPairResult(BaseModel):
    memory_id: str
    expected_relationship: MemoryEvalRelationship | None = None
    retrieved: bool
    score: float | None = Field(default=None, ge=0.0)
    applicability_status: SocMemoryApplicabilityStatus | None = None
    context_only: bool = False
    predicted_lesson_applicable: bool = False
    predicted_directive_eligible: bool = False
    match_reasons: list[str] = Field(default_factory=list)
    matched_facets: dict[str, list[str]] = Field(default_factory=dict)


class MemoryEvalCaseResult(BaseModel):
    case_id: str
    run_id: str
    alert_id: str
    review_status: ConfidenceLabelReviewStatus
    retrieved_memory_ids: list[str] = Field(default_factory=list)
    context_only_memory_ids: list[str] = Field(default_factory=list)
    directive_applicable_memory_ids: list[str] = Field(default_factory=list)
    base_verdict: Verdict
    effective_verdict: Verdict
    actual_verdict: Verdict | None = None
    transition_kind: SocDecisionTransitionKind
    base_needs_review: bool
    effective_needs_review: bool
    expected_review_required: bool | None = None
    scenario_correct: bool | None = None
    boundary_direction_correct: bool | None = None
    roles_correct: bool | None = None
    verifier_outcome: MemoryEvalVerifierOutcome
    pair_results: list[MemoryEvalPairResult] = Field(default_factory=list)


class MemoryEvalReviewBurden(BaseModel):
    labeled_case_count: int = Field(ge=0)
    expected_review_count: int = Field(ge=0)
    base_review_count: int = Field(ge=0)
    effective_review_count: int = Field(ge=0)
    review_reduction_count: int = Field(ge=0)
    review_reduction_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    base_review_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    effective_review_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    unsafe_review_clear_count: int = Field(ge=0)
    unnecessary_review_count: int = Field(ge=0)


class MemoryEvalVerifierMetrics(BaseModel):
    case_count: int = Field(ge=0)
    triggered_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    failure_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    outcome_counts: dict[str, int] = Field(default_factory=dict)


class MemoryHeldOutEvalReport(BaseModel):
    """Read-only Memory quality report; it never grants rollout authority."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_heldout_eval_report.v1"] = "soc.memory_heldout_eval_report.v1"
    fixture_schema_version: str
    fixture_set_id: str
    fixture_path: str | None = None
    fixture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data_class: SocEvaluationDataClass
    mocked: bool
    memory_profile_id: str
    memory_profile_version: str
    memory_feature_schema_version: str
    record_count: int = Field(ge=0)
    case_count: int = Field(ge=0)
    accepted_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    evaluation_status: Literal[
        "blocked_pending_labels",
        "partial",
        "complete_simulation",
        "complete_desensitized_real",
    ]
    retrieval_metrics: MemoryEvalBinaryMetrics
    pattern_lesson_metrics: MemoryEvalBinaryMetrics
    directive_eligibility_metrics: MemoryEvalBinaryMetrics
    base_verdict_accuracy: MemoryEvalAccuracyMetric
    effective_verdict_accuracy: MemoryEvalAccuracyMetric
    directive_override_accuracy: MemoryEvalAccuracyMetric
    scenario_accuracy: MemoryEvalAccuracyMetric
    boundary_direction_accuracy: MemoryEvalAccuracyMetric
    role_accuracy: MemoryEvalAccuracyMetric
    review_burden: MemoryEvalReviewBurden
    verifier_metrics: MemoryEvalVerifierMetrics
    unsafe_override_count: int = Field(ge=0)
    integrity_passed: bool
    real_quality_metrics_available: bool
    rollout_authorized: Literal[False] = False
    limitations: list[str] = Field(default_factory=list)
    results: list[MemoryEvalCaseResult] = Field(default_factory=list)


def load_memory_eval_fixture(path: str | Path) -> MemoryHeldOutEvalFixture:
    fixture_path = Path(path)
    try:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        fixture = MemoryHeldOutEvalFixture.model_validate(payload)
    except OSError as exc:
        raise ValueError(f"cannot read Memory eval fixture {fixture_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Memory eval fixture JSON {fixture_path}: {exc}") from exc
    except ValidationError as exc:
        raise ValueError(f"invalid Memory eval fixture {fixture_path}: {exc}") from exc
    return fixture.model_copy(update={"fixture_path": str(fixture_path)})


def load_memory_eval_report(path: str | Path) -> MemoryHeldOutEvalReport:
    report_path = Path(path)
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        return MemoryHeldOutEvalReport.model_validate(payload)
    except OSError as exc:
        raise ValueError(f"cannot read Memory eval report {report_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Memory eval report JSON {report_path}: {exc}") from exc
    except ValidationError as exc:
        raise ValueError(f"invalid Memory eval report {report_path}: {exc}") from exc


def load_memory_records_for_eval(path: str | Path) -> list[SocMemoryRecord]:
    """Load a list, a ``records`` wrapper, or a CLI ``items`` wrapper."""

    record_path = Path(path)
    try:
        payload = json.loads(record_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read Memory records {record_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Memory record JSON {record_path}: {exc}") from exc
    raw_records: object = payload
    if isinstance(payload, Mapping):
        raw_records = payload.get("records", payload.get("items"))
    if not isinstance(raw_records, list) or not raw_records:
        raise ValueError("Memory record file must contain a non-empty list, records, or items")
    try:
        return [SocMemoryRecord.model_validate(item) for item in raw_records]
    except ValidationError as exc:
        raise ValueError(f"invalid Memory record fixture: {exc}") from exc


def build_memory_eval_fixture(
    runs: Sequence[tuple[str, AnalysisRun]],
    records: Sequence[SocMemoryRecord],
    *,
    fixture_set_id: str | None,
    description: str,
    data_class: SocEvaluationDataClass | str,
    tenant_id: str,
    environment: str,
    source_refs: Sequence[str],
    evaluated_at: datetime | None = None,
) -> MemoryHeldOutEvalFixture:
    """Create a pending-review fixture from frozen Runtime runs and Memory records."""

    if not runs or not records:
        raise ValueError("Memory eval preparation requires runs and records")
    normalized_tenant_id = tenant_id.strip()
    if not normalized_tenant_id:
        raise ValueError("Memory eval tenant_id must not be blank")
    normalized_environment = environment.strip().casefold()
    if not normalized_environment:
        raise ValueError("Memory eval environment must not be blank")
    registry = build_soc_memory_profile_registry()
    prepared_cases: list[MemoryEvalCaseFixture] = []
    seen_run_ids: set[str] = set()
    for source_path, run in runs:
        if run.run_id in seen_run_ids:
            raise ValueError(f"duplicate AnalysisRun in Memory eval input: {run.run_id}")
        seen_run_ids.add(run.run_id)
        if run.analysis is None or run.decision is None or run.llm_analysis_request is None or not run.input_hash:
            raise ValueError(f"AnalysisRun {run.run_id} is incomplete for Memory evaluation")
        request_tenant_id = run.llm_analysis_request.tenant_id
        if request_tenant_id is not None and request_tenant_id.strip() != normalized_tenant_id:
            raise ValueError(f"AnalysisRun {run.run_id} tenant conflicts with Memory eval tenant")
        request_environment = run.llm_analysis_request.environment
        if request_environment is not None and request_environment.strip().casefold() != normalized_environment:
            raise ValueError(f"AnalysisRun {run.run_id} environment conflicts with Memory eval environment")
        request = run.llm_analysis_request.model_copy(
            update={"tenant_id": normalized_tenant_id, "environment": normalized_environment},
            deep=True,
        )
        profile = registry.resolve_request(request)
        prepared_cases.append(
            MemoryEvalCaseFixture(
                case_id=f"ME-{stable_hash({'run_id': run.run_id, 'input_hash': run.input_hash})[:12].upper()}",
                run_id=run.run_id,
                input_hash=run.input_hash,
                source_path=source_path,
                request=request,
                base_decision=run.decision,
                prediction=_prediction_from_run(run),
            )
        )

    profile = registry.resolve_request(prepared_cases[0].request)
    for case in prepared_cases[1:]:
        identity = registry.resolve_request(case.request).identity
        if identity != profile.identity:
            raise ValueError("one Memory eval fixture cannot mix Memory profiles")

    record_fixtures = [
        MemoryEvalRecordFixture(
            record=record,
            source_alert_ids=_record_source_alert_ids(record),
        )
        for record in records
    ]
    resolved_data_class = SocEvaluationDataClass(data_class)
    identity_payload = {
        "records": sorted((item.record.memory_id, item.record.version, item.record.content_hash, item.record.facets_hash) for item in record_fixtures),
        "cases": sorted((item.run_id, item.input_hash) for item in prepared_cases),
        "profile": {
            "profile_id": profile.identity.profile_id,
            "profile_version": profile.identity.profile_version,
            "feature_schema_version": profile.identity.feature_schema_version,
        },
    }
    resolved_id = fixture_set_id or f"MEF-{stable_hash(identity_payload)[:12].upper()}"
    return MemoryHeldOutEvalFixture(
        fixture_set_id=resolved_id,
        description=description,
        data_class=resolved_data_class,
        mocked=resolved_data_class is SocEvaluationDataClass.SIMULATION,
        tenant_id=normalized_tenant_id,
        environment=normalized_environment,
        memory_profile_id=profile.identity.profile_id,
        memory_profile_version=profile.identity.profile_version,
        memory_feature_schema_version=profile.identity.feature_schema_version,
        evaluated_at=evaluated_at or datetime.now(UTC),
        source_refs=list(source_refs),
        records=record_fixtures,
        cases=prepared_cases,
    )


def run_memory_eval(fixture: MemoryHeldOutEvalFixture) -> MemoryHeldOutEvalReport:
    """Replay actual Retrieval v2 and Base-to-Memory decision semantics read-only."""

    registry = build_soc_memory_profile_registry()
    memory_repository = InMemoryMemoryCandidateRepository()
    for item in fixture.records:
        memory_repository.save_memory_record(item.record)
    memory_service = SocMemoryService(
        record_repository=memory_repository,
        profile_registry=registry,
        now_provider=lambda: fixture.evaluated_at,
    )
    automation_repository = InMemorySocAutomationRepository()
    automation_service = SocAutomationService(
        repository=automation_repository,
        policy=None,
        environment=fixture.environment,
        memory_repository=memory_repository,
        now_provider=lambda: fixture.evaluated_at,
    )
    enricher = ConfirmedMemoryAnalysisRequestEnricher(
        memory_service,
        profile_registry=registry,
        environment=fixture.environment,
    )

    results: list[MemoryEvalCaseResult] = []
    for case in fixture.cases:
        profile = registry.resolve_request(case.request)
        identity = profile.identity
        if identity.profile_id != fixture.memory_profile_id or identity.profile_version != fixture.memory_profile_version or identity.feature_schema_version != fixture.memory_feature_schema_version:
            raise ValueError(f"case {case.case_id} resolves to a different Memory profile")
        query = memory_query_from_analysis_request(case.request, profile=profile)
        retrieval = memory_service.find_relevant_records(query)
        enriched_request = enricher(case.request)
        run = AnalysisRun(
            run_id=case.run_id,
            alert_id=case.request.alert_id,
            status=(AnalysisRunStatus.NEEDS_REVIEW if case.base_decision.needs_review else AnalysisRunStatus.SUCCESS),
            input_hash=case.input_hash,
            llm_analysis_request=enriched_request,
            decision=case.base_decision,
        )
        transition = automation_service.evaluate(
            run,
            context=_evaluation_context(case.case_id),
        ).decision_transition
        matches = {item.memory_id: item for item in retrieval.matches}
        contributor_refs = {item.ref_id for item in transition.contributors if item.kind is SocAutomationContributorKind.CONFIRMED_MEMORY}
        eligible_directive_memory_ids = {
            str(item.metadata["memory_id"])
            for item in enriched_request.context_catalog
            if item.kind is AnalysisContextReferenceKind.CONFIRMED_MEMORY and item.context_ref in contributor_refs and isinstance(item.metadata.get("memory_id"), str)
        }
        relationships = case.truth.expected_memory_relationships or {}
        pair_results: list[MemoryEvalPairResult] = []
        directive_ids: list[str] = []
        context_only_ids: list[str] = []
        for record_fixture in fixture.records:
            memory_id = record_fixture.record.memory_id
            match = matches.get(memory_id)
            applicability = match.applicability_report if match is not None else None
            status = applicability.status if applicability is not None else None
            context_only = bool(applicability is not None and applicability.context_only_allowed)
            predicted_lesson_applicable = bool(match is not None and status is SocMemoryApplicabilityStatus.APPLICABLE)
            predicted_directive_eligible = bool(memory_id in eligible_directive_memory_ids)
            if context_only:
                context_only_ids.append(memory_id)
            if predicted_directive_eligible:
                directive_ids.append(memory_id)
            pair_results.append(
                MemoryEvalPairResult(
                    memory_id=memory_id,
                    expected_relationship=relationships.get(memory_id),
                    retrieved=match is not None,
                    score=match.score if match is not None else None,
                    applicability_status=status,
                    context_only=context_only,
                    predicted_lesson_applicable=predicted_lesson_applicable,
                    predicted_directive_eligible=predicted_directive_eligible,
                    match_reasons=match.match_reasons if match is not None else [],
                    matched_facets=match.matched_facets if match is not None else {},
                )
            )
        truth = case.truth
        results.append(
            MemoryEvalCaseResult(
                case_id=case.case_id,
                run_id=case.run_id,
                alert_id=case.request.alert_id,
                review_status=truth.review_status,
                retrieved_memory_ids=sorted(matches),
                context_only_memory_ids=sorted(context_only_ids),
                directive_applicable_memory_ids=sorted(directive_ids),
                base_verdict=transition.before.verdict,
                effective_verdict=transition.after.verdict,
                actual_verdict=truth.actual_verdict,
                transition_kind=transition.transition_kind,
                base_needs_review=transition.before.needs_review,
                effective_needs_review=transition.after.needs_review,
                expected_review_required=truth.expected_review_required,
                scenario_correct=_scenario_correct(case),
                boundary_direction_correct=_boundary_direction_correct(case),
                roles_correct=_roles_correct(case),
                verifier_outcome=case.prediction.verifier_outcome,
                pair_results=pair_results,
            )
        )

    return _build_report(fixture, results)


def _build_report(
    fixture: MemoryHeldOutEvalFixture,
    results: Sequence[MemoryEvalCaseResult],
) -> MemoryHeldOutEvalReport:
    counts = Counter(item.review_status for item in results)
    accepted = [item for item in results if item.review_status is ConfidenceLabelReviewStatus.ACCEPTED]
    retrieval_pairs: list[tuple[bool, bool]] = []
    lesson_pairs: list[tuple[bool, bool]] = []
    directive_pairs: list[tuple[bool, bool]] = []
    for result in accepted:
        for pair in result.pair_results:
            assert pair.expected_relationship is not None
            retrieval_pairs.append(
                (
                    pair.expected_relationship is not MemoryEvalRelationship.UNRELATED,
                    pair.retrieved,
                )
            )
            lesson_pairs.append(
                (
                    pair.expected_relationship is MemoryEvalRelationship.DECISION_APPLICABLE,
                    pair.predicted_lesson_applicable,
                )
            )
            directive_pairs.append(
                (
                    pair.expected_relationship is MemoryEvalRelationship.DECISION_APPLICABLE,
                    pair.predicted_directive_eligible,
                )
            )

    override_results = [item for item in accepted if item.transition_kind is SocDecisionTransitionKind.OVERRIDDEN]
    unsafe_override_count = sum(item.actual_verdict is not None and item.effective_verdict is not item.actual_verdict for item in override_results)
    pending_count = counts[ConfidenceLabelReviewStatus.PENDING_REVIEW]
    accepted_count = counts[ConfidenceLabelReviewStatus.ACCEPTED]
    if accepted_count == 0:
        evaluation_status = "blocked_pending_labels"
    elif pending_count:
        evaluation_status = "partial"
    elif fixture.data_class is SocEvaluationDataClass.SIMULATION:
        evaluation_status = "complete_simulation"
    else:
        evaluation_status = "complete_desensitized_real"

    limitations = [
        "Evaluation is read-only and never grants rollout or action authority.",
        "Memory construction alerts and held-out query alerts are disjoint by contract.",
    ]
    if pending_count:
        limitations.append(f"{pending_count} held-out case(s) still require independent analyst review.")
    if fixture.data_class is SocEvaluationDataClass.SIMULATION:
        limitations.append("Simulation labels prove evaluation wiring only, not PingAn production quality.")

    return MemoryHeldOutEvalReport(
        fixture_schema_version=fixture.schema_version,
        fixture_set_id=fixture.fixture_set_id,
        fixture_path=fixture.fixture_path,
        fixture_sha256=_fixture_hash(fixture),
        data_class=fixture.data_class,
        mocked=fixture.mocked,
        memory_profile_id=fixture.memory_profile_id,
        memory_profile_version=fixture.memory_profile_version,
        memory_feature_schema_version=fixture.memory_feature_schema_version,
        record_count=len(fixture.records),
        case_count=len(results),
        accepted_count=accepted_count,
        pending_count=pending_count,
        excluded_count=counts[ConfidenceLabelReviewStatus.EXCLUDED],
        evaluation_status=evaluation_status,
        retrieval_metrics=_binary_metrics(retrieval_pairs),
        pattern_lesson_metrics=_binary_metrics(lesson_pairs),
        directive_eligibility_metrics=_binary_metrics(directive_pairs),
        base_verdict_accuracy=_accuracy([item.base_verdict is item.actual_verdict for item in accepted]),
        effective_verdict_accuracy=_accuracy([item.effective_verdict is item.actual_verdict for item in accepted]),
        directive_override_accuracy=_accuracy([item.effective_verdict is item.actual_verdict for item in override_results]),
        scenario_accuracy=_optional_accuracy([item.scenario_correct for item in accepted]),
        boundary_direction_accuracy=_optional_accuracy([item.boundary_direction_correct for item in accepted]),
        role_accuracy=_optional_accuracy([item.roles_correct for item in accepted]),
        review_burden=_review_burden(accepted),
        verifier_metrics=_verifier_metrics(results),
        unsafe_override_count=unsafe_override_count,
        integrity_passed=True,
        real_quality_metrics_available=(fixture.data_class is SocEvaluationDataClass.DESENSITIZED_REAL and accepted_count > 0 and pending_count == 0),
        limitations=limitations,
        results=list(results),
    )


def _prediction_from_run(run: AnalysisRun) -> MemoryEvalPredictionSnapshot:
    assert run.analysis is not None
    scenarios = [item.scenario_key or item.scenario_name for item in run.analysis.scenario_assessments]
    direction = run.analysis.network_direction
    boundary_direction = direction.boundary_direction if direction.status.value != "not_assessed" else None
    roles = [
        MemoryEvalRoleLabel(
            role=item.role,
            entity_type=item.entity_type,
            value=item.value,
        )
        for item in run.analysis.role_adjudication.roles
        if item.value is not None
    ]
    trigger = run.role_verification_trigger
    verification = run.role_adjudication_verification
    if trigger is None or not trigger.triggered:
        outcome = MemoryEvalVerifierOutcome.NOT_TRIGGERED
        failure_kind = None
    elif verification is None:
        outcome = MemoryEvalVerifierOutcome.MISSING_RESULT
        failure_kind = "missing_result"
    else:
        outcome = MemoryEvalVerifierOutcome(verification.status.value)
        failure_kind = verification.failure_kind.value if verification.status is RoleVerificationStatus.UNAVAILABLE and verification.failure_kind is not None else None
    return MemoryEvalPredictionSnapshot(
        scenario_keys=scenarios,
        boundary_direction=boundary_direction,
        roles=roles,
        verifier_outcome=outcome,
        verifier_failure_kind=failure_kind,
    )


def _record_source_alert_ids(record: SocMemoryRecord) -> list[str]:
    values: list[str] = []
    if record.source.alert_id:
        values.append(record.source.alert_id)
    for container in (record.source.metadata, record.metadata):
        raw = container.get("source_alert_ids")
        if isinstance(raw, list):
            values.extend(str(item) for item in raw if str(item).strip())
    if not values:
        raise ValueError(f"Memory record {record.memory_id} lacks source alert lineage for held-out validation")
    return list(dict.fromkeys(" ".join(str(value).split()) for value in values if " ".join(str(value).split())))


def _scenario_correct(case: MemoryEvalCaseFixture) -> bool | None:
    actual = case.truth.actual_scenario_keys
    if actual is None:
        return None
    return set(case.prediction.scenario_keys) == set(actual)


def _boundary_direction_correct(case: MemoryEvalCaseFixture) -> bool | None:
    actual = case.truth.actual_boundary_direction
    if actual is None:
        return None
    return case.prediction.boundary_direction is actual


def _roles_correct(case: MemoryEvalCaseFixture) -> bool | None:
    actual = case.truth.actual_roles
    if actual is None:
        return None
    return {_role_key(item) for item in case.prediction.roles} == {_role_key(item) for item in actual}


def _review_burden(
    results: Sequence[MemoryEvalCaseResult],
) -> MemoryEvalReviewBurden:
    support = len(results)
    expected_review_count = sum(item.expected_review_required is True for item in results)
    base_review_count = sum(item.base_needs_review for item in results)
    effective_review_count = sum(item.effective_needs_review for item in results)
    review_reduction_count = sum(item.base_needs_review and not item.effective_needs_review for item in results)
    base_correct = sum(item.expected_review_required is item.base_needs_review for item in results)
    effective_correct = sum(item.expected_review_required is item.effective_needs_review for item in results)
    return MemoryEvalReviewBurden(
        labeled_case_count=support,
        expected_review_count=expected_review_count,
        base_review_count=base_review_count,
        effective_review_count=effective_review_count,
        review_reduction_count=review_reduction_count,
        review_reduction_rate=(review_reduction_count / base_review_count if base_review_count else None),
        base_review_accuracy=base_correct / support if support else None,
        effective_review_accuracy=effective_correct / support if support else None,
        unsafe_review_clear_count=sum(item.expected_review_required is True and not item.effective_needs_review for item in results),
        unnecessary_review_count=sum(item.expected_review_required is False and item.effective_needs_review for item in results),
    )


def _verifier_metrics(
    results: Sequence[MemoryEvalCaseResult],
) -> MemoryEvalVerifierMetrics:
    counts = Counter(item.verifier_outcome.value for item in results)
    triggered_count = len(results) - counts[MemoryEvalVerifierOutcome.NOT_TRIGGERED.value]
    failed_count = counts[MemoryEvalVerifierOutcome.UNAVAILABLE.value] + counts[MemoryEvalVerifierOutcome.MISSING_RESULT.value]
    return MemoryEvalVerifierMetrics(
        case_count=len(results),
        triggered_count=triggered_count,
        failed_count=failed_count,
        failure_rate=(failed_count / triggered_count if triggered_count else None),
        outcome_counts=dict(sorted(counts.items())),
    )


def _binary_metrics(
    pairs: Sequence[tuple[bool, bool]],
) -> MemoryEvalBinaryMetrics:
    true_positive = sum(expected and predicted for expected, predicted in pairs)
    false_positive = sum(not expected and predicted for expected, predicted in pairs)
    false_negative = sum(expected and not predicted for expected, predicted in pairs)
    true_negative = sum(not expected and not predicted for expected, predicted in pairs)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else None
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else None
    f1 = 2 * precision * recall / (precision + recall) if precision is not None and recall is not None and precision + recall else None
    return MemoryEvalBinaryMetrics(
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        true_negative=true_negative,
        positive_support=true_positive + false_negative,
        negative_support=false_positive + true_negative,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def _accuracy(values: Sequence[bool]) -> MemoryEvalAccuracyMetric:
    support = len(values)
    correct = sum(values)
    return MemoryEvalAccuracyMetric(
        support=support,
        correct=correct,
        accuracy=correct / support if support else None,
    )


def _optional_accuracy(values: Sequence[bool | None]) -> MemoryEvalAccuracyMetric:
    return _accuracy([value for value in values if value is not None])


def _role_key(item: MemoryEvalRoleLabel) -> tuple[str, str, str]:
    return (
        item.role.value,
        item.entity_type.casefold(),
        item.value.casefold(),
    )


def _normalized_unique(values: Sequence[str], *, field_name: str) -> list[str]:
    normalized = [" ".join(str(value).split()) for value in values]
    if any(not value for value in normalized):
        raise ValueError(f"{field_name} values must not be blank")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} values must be unique")
    return normalized


def _fixture_hash(fixture: MemoryHeldOutEvalFixture) -> str:
    payload = fixture.model_dump(
        mode="json",
        exclude={"fixture_path"},
        exclude_none=True,
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _evaluation_context(case_id: str) -> ServiceRequestContext:
    return ServiceRequestContext(
        idempotency_key=f"memory-eval:{case_id}",
        actor=ActorContext(
            actor_id="soc-memory-evaluator",
            actor_type=ActorType.SERVICE,
            surface=EntrySurface.TEST,
            roles=["soc_automation"],
        ),
    )


__all__ = [
    "DEFAULT_MEMORY_EVAL_FIXTURE",
    "MemoryEvalAccuracyMetric",
    "MemoryEvalBinaryMetrics",
    "MemoryEvalCaseFixture",
    "MemoryEvalCaseResult",
    "MemoryEvalHumanTruth",
    "MemoryEvalPairResult",
    "MemoryEvalPredictionSnapshot",
    "MemoryEvalRecordFixture",
    "MemoryEvalRelationship",
    "MemoryEvalReviewBurden",
    "MemoryEvalRoleLabel",
    "MemoryEvalVerifierMetrics",
    "MemoryEvalVerifierOutcome",
    "MemoryHeldOutEvalFixture",
    "MemoryHeldOutEvalReport",
    "build_memory_eval_fixture",
    "load_memory_eval_fixture",
    "load_memory_eval_report",
    "load_memory_records_for_eval",
    "run_memory_eval",
]
