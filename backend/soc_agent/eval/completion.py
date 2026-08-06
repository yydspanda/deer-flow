"""Fail-closed PI-05B aggregation of existing simulation evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from soc_agent.contracts import (
    SkillImprovementCandidateStatus,
    SkillImprovementIngestReport,
    SkillImprovementReplayReport,
    SocEvaluationDataClass,
    SocOperationsAvailability,
    SocOperationsSnapshot,
    SocRolloutGateId,
    SocRolloutRehearsalReport,
    SocRolloutStage,
)
from soc_agent.eval.quality import SocQualityComponentStatus, SocQualityEvaluationReport

SIMULATION_COMPLETION_POLICY_VERSION = "soc.simulation_completion_policy.v1"


class SocSimulationCompletionComponentId(StrEnum):
    PI01_EXTERNAL_SIMULATION = "pi01.external_simulation"
    PI03_QUALITY_EVALUATION = "pi03.quality_evaluation"
    PI03_SKILL_IMPROVEMENT = "pi03.skill_improvement"
    PI04_OPERATIONS_VISIBILITY = "pi04.operations_visibility"
    PI05_ROLLOUT_REHEARSAL = "pi05.rollout_rehearsal"


class SocSimulationCompletionStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class SocSimulationCompletionArtifactPaths(BaseModel):
    """Artifact paths are operator input and never copied into the output report."""

    model_config = ConfigDict(extra="forbid")

    pi01_external_simulation: str = Field(min_length=1, max_length=2048)
    pi03_quality_evaluation: str = Field(min_length=1, max_length=2048)
    pi03_skill_ingest: str = Field(min_length=1, max_length=2048)
    pi03_skill_replay: str = Field(min_length=1, max_length=2048)
    pi04_operations_snapshot: str = Field(min_length=1, max_length=2048)
    pi05_rollout_rehearsal: str = Field(min_length=1, max_length=2048)


class SocSimulationCompletionRequest(BaseModel):
    """Explicit simulation-only PI-05B request."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.simulation_completion_request.v1"] = "soc.simulation_completion_request.v1"
    request_id: str = Field(min_length=1, max_length=128)
    requested_by: str = Field(min_length=1, max_length=128)
    rationale: str = Field(min_length=1, max_length=2000)
    artifacts: SocSimulationCompletionArtifactPaths
    confirm_simulation_only: Literal[True]


class SocSimulationArtifactDigest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_role: str = Field(min_length=1, max_length=128)
    artifact_name: str = Field(min_length=1, max_length=512)
    readable: bool
    schema_version: str | None = Field(default=None, max_length=128)
    artifact_id: str | None = Field(default=None, max_length=128)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error_code: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_digest(self) -> SocSimulationArtifactDigest:
        if self.readable and (self.sha256 is None or self.error_code is not None):
            raise ValueError("readable artifacts require a digest and cannot carry a read error")
        if not self.readable and self.error_code is None:
            raise ValueError("unreadable artifacts require a sanitized error code")
        return self


class SocSimulationCompletionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_id: str = Field(min_length=1, max_length=128)
    passed: bool
    detail: str = Field(min_length=1, max_length=1000)


class SocSimulationCompletionComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: SocSimulationCompletionComponentId
    status: SocSimulationCompletionStatus
    artifacts: list[SocSimulationArtifactDigest] = Field(min_length=1)
    checks: list[SocSimulationCompletionCheck] = Field(min_length=1)
    semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    simulation_provenance_verified: bool
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_component_status(self) -> SocSimulationCompletionComponent:
        expected = SocSimulationCompletionStatus.PASSED if all(item.passed for item in self.checks) else SocSimulationCompletionStatus.FAILED
        if self.status is not expected:
            raise ValueError("component status must match its checks")
        if self.simulation_provenance_verified is not all(item.passed for item in self.checks if item.check_id.endswith("simulation_provenance")):
            raise ValueError("simulation provenance flag must match provenance checks")
        return self


class SocSimulationRealIntegrationDebt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate_id: SocRolloutGateId
    status: Literal["open"] = "open"
    reason: str = Field(min_length=1, max_length=1000)
    required_evidence: str = Field(min_length=1, max_length=1000)


class SocSimulationCompletionDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline_completion_id: str = Field(min_length=1, max_length=64)
    baseline_semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed: bool
    changed_components: list[SocSimulationCompletionComponentId] = Field(default_factory=list)
    artifact_bytes_changed_components: list[SocSimulationCompletionComponentId] = Field(default_factory=list)


class SocSimulationCompletionReport(BaseModel):
    """Engineering completion proof that cannot authorize a real rollout."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.simulation_completion_report.v1"] = "soc.simulation_completion_report.v1"
    policy_version: Literal["soc.simulation_completion_policy.v1"] = SIMULATION_COMPLETION_POLICY_VERSION
    completion_id: str = Field(pattern=r"^SCG-[0-9A-F]{12}$")
    generated_at: datetime
    request_id: str
    requested_by: str
    rationale: str
    data_class: Literal[SocEvaluationDataClass.SIMULATION] = SocEvaluationDataClass.SIMULATION
    mocked: Literal[True] = True
    components: list[SocSimulationCompletionComponent] = Field(min_length=len(SocSimulationCompletionComponentId))
    component_hashes: dict[SocSimulationCompletionComponentId, str]
    artifact_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    simulation_track_complete: bool
    engineering_completion_gate_passed: bool
    current_real_stage: Literal[SocRolloutStage.NOT_STARTED] = SocRolloutStage.NOT_STARTED
    real_integration_debt: list[SocSimulationRealIntegrationDebt] = Field(min_length=len(SocRolloutGateId))
    real_stage_transition_count: Literal[0] = 0
    external_effect_count: Literal[0] = 0
    pilot_ready: Literal[False] = False
    production_ready: Literal[False] = False
    real_rollout_claim_allowed: Literal[False] = False
    auto_close_allowed: Literal[False] = False
    external_state_mutation_allowed: Literal[False] = False
    high_risk_action_execution_allowed: Literal[False] = False
    limitations: list[str] = Field(min_length=1)
    diff: SocSimulationCompletionDiff | None = None

    @model_validator(mode="after")
    def validate_completion_boundary(self) -> SocSimulationCompletionReport:
        component_ids = {item.component_id for item in self.components}
        if len(component_ids) != len(self.components) or component_ids != set(SocSimulationCompletionComponentId):
            raise ValueError("completion report requires exactly one assessment for every v1 component")
        if set(self.component_hashes) != set(SocSimulationCompletionComponentId):
            raise ValueError("completion report component hashes are incomplete")
        expected_pass = all(item.status is SocSimulationCompletionStatus.PASSED for item in self.components)
        if self.simulation_track_complete is not expected_pass:
            raise ValueError("simulation completion must match component statuses")
        if self.engineering_completion_gate_passed is not expected_pass:
            raise ValueError("engineering completion gate must match component statuses")
        debt_ids = {item.gate_id for item in self.real_integration_debt}
        if len(debt_ids) != len(self.real_integration_debt) or debt_ids != set(SocRolloutGateId):
            raise ValueError("every real rollout gate must remain explicit integration debt")
        return self


class _ProjectionModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class _Pi01Check(_ProjectionModel):
    check_id: str
    status: str


class _Pi01Claims(_ProjectionModel):
    automatic_expansion_allowed: bool
    closes_real_provider_gate: bool
    external_simulation_passed: bool
    internal_real_gate_passed: bool
    model_accuracy_evaluated: bool
    next_stage_requires_human_review: bool
    pilot_ready: bool
    real_provider_evidence: bool
    technical_shadow_gate_passed: bool


class _Pi01Configuration(_ProjectionModel):
    provider_modes: dict[str, str]
    required_result_mode: str


class _Pi01Inputs(_ProjectionModel):
    secrets_included: bool
    selected_count: int


class _Pi01InvestigationMetrics(_ProjectionModel):
    failed_count: int
    missing_evidence_count: int
    mock_result_count: int
    persisted_evidence_count: int
    planned_action_count: int
    provider_invocation_count: int
    real_result_count: int
    unauthorized_side_effect_counts: dict[str, int]


class _Pi01CompatibilityMetrics(_ProjectionModel):
    deterministic_projection_mismatch_count: int
    review_routing_difference_count: int
    shared_item_count: int


class _Pi01Metrics(_ProjectionModel):
    investigation_shadow: _Pi01InvestigationMetrics
    paired_compatibility: _Pi01CompatibilityMetrics


class _Pi01ExternalSimulationReport(_ProjectionModel):
    schema_version: str
    report_id: str
    acceptance_mode: str
    evidence_class: str
    gate_status: str
    ramp_stage: str
    blocking_failure_ids: list[str]
    checks: list[_Pi01Check]
    claims: _Pi01Claims
    configuration: _Pi01Configuration
    inputs: _Pi01Inputs
    metrics: _Pi01Metrics


@dataclass
class _LoadedArtifact:
    role: str
    digest: SocSimulationArtifactDigest
    payload: object | None


def run_soc_simulation_completion(
    request: SocSimulationCompletionRequest,
    *,
    artifact_base_dir: str | Path = ".",
    baseline: SocSimulationCompletionReport | None = None,
    clock: Callable[[], datetime] | None = None,
) -> SocSimulationCompletionReport:
    """Aggregate existing reports without invoking Runtime, Provider, broker, or DB."""

    base_dir = Path(artifact_base_dir)
    paths = request.artifacts
    components = [
        _assess_pi01(_load_artifact(base_dir, paths.pi01_external_simulation, "pi01_shadow_report")),
        _assess_pi03_quality(_load_artifact(base_dir, paths.pi03_quality_evaluation, "pi03_quality_report")),
        _assess_pi03_skill(
            _load_artifact(base_dir, paths.pi03_skill_ingest, "pi03_skill_ingest_report"),
            _load_artifact(base_dir, paths.pi03_skill_replay, "pi03_skill_replay_report"),
        ),
        _assess_pi04_operations(_load_artifact(base_dir, paths.pi04_operations_snapshot, "pi04_operations_snapshot")),
        _assess_pi05_rehearsal(_load_artifact(base_dir, paths.pi05_rollout_rehearsal, "pi05_rollout_rehearsal")),
    ]
    component_hashes = {item.component_id: item.semantic_sha256 for item in components}
    artifact_set_sha256 = _stable_hash(
        {
            item.component_id.value: [
                {
                    "role": artifact.artifact_role,
                    "sha256": artifact.sha256,
                    "error_code": artifact.error_code,
                }
                for artifact in item.artifacts
            ]
            for item in components
        }
    )
    semantic_sha256 = _stable_hash(
        {
            "policy_version": SIMULATION_COMPLETION_POLICY_VERSION,
            "request_id": request.request_id,
            "component_hashes": {component_id.value: value for component_id, value in component_hashes.items()},
            "component_statuses": {item.component_id.value: item.status.value for item in components},
            "open_real_gate_ids": [item.value for item in SocRolloutGateId],
        }
    )
    completion_id = f"SCG-{semantic_sha256[:12].upper()}"
    passed = all(item.status is SocSimulationCompletionStatus.PASSED for item in components)
    diff = _build_completion_diff(
        baseline=baseline,
        completion_id=completion_id,
        semantic_sha256=semantic_sha256,
        components=components,
    )
    return SocSimulationCompletionReport(
        completion_id=completion_id,
        generated_at=(clock or (lambda: datetime.now(UTC)))(),
        request_id=request.request_id,
        requested_by=request.requested_by,
        rationale=request.rationale,
        components=components,
        component_hashes=component_hashes,
        artifact_set_sha256=artifact_set_sha256,
        semantic_sha256=semantic_sha256,
        simulation_track_complete=passed,
        engineering_completion_gate_passed=passed,
        real_integration_debt=_real_integration_debt(),
        limitations=[
            "This report aggregates simulation and local artifacts only; it is not Pilot Ready or Production Ready evidence.",
            "Real Provider, production infrastructure, real-label quality, deployed SLO, accountable owner, rollback, and cohort-enforcement gates remain open.",
            "No Runtime, model, Provider, broker, database mutation, feature flag, Zeus state, auto-close, or response action is invoked by PI-05B.",
        ],
        diff=diff,
    )


def load_soc_simulation_completion_request(path: str | Path) -> SocSimulationCompletionRequest:
    return _load_model(path, SocSimulationCompletionRequest, "simulation completion request")


def load_soc_simulation_completion_report(path: str | Path) -> SocSimulationCompletionReport:
    return _load_model(path, SocSimulationCompletionReport, "simulation completion report")


def _assess_pi01(artifact: _LoadedArtifact) -> SocSimulationCompletionComponent:
    report = _validate_artifact(artifact, _Pi01ExternalSimulationReport)
    if report is None:
        return _invalid_component(
            SocSimulationCompletionComponentId.PI01_EXTERNAL_SIMULATION,
            [artifact],
        )
    shadow = report.metrics.investigation_shadow
    compatibility = report.metrics.paired_compatibility
    claims = report.claims
    checks = [
        _check("pi01.contract", True, "PI-01E report satisfies the frozen v2 projection."),
        _check(
            "pi01.simulation_provenance",
            report.acceptance_mode == "external_simulation"
            and report.evidence_class == "simulated"
            and report.configuration.required_result_mode == "mock"
            and bool(report.configuration.provider_modes)
            and set(report.configuration.provider_modes.values()) == {"fake"}
            and report.inputs.secrets_included is False,
            "The report is explicit secret-free external simulation using fake Provider modes.",
            "PI-01E provenance is not an explicit secret-free fake-provider simulation.",
        ),
        _check(
            "pi01.final_cohort",
            report.ramp_stage == "50" and report.inputs.selected_count == 50 and compatibility.shared_item_count == 50,
            "The approved final external-simulation cohort contains the same 50 paired alerts.",
            "The report is not the approved paired 50-alert simulation stage.",
        ),
        _check(
            "pi01.internal_checks",
            report.gate_status == "passed" and not report.blocking_failure_ids and bool(report.checks) and all(item.status == "passed" for item in report.checks),
            "All PI-01E evaluator checks passed with no blocking failure.",
            "At least one PI-01E evaluator check or blocking failure is present.",
        ),
        _check(
            "pi01.claim_boundary",
            claims.external_simulation_passed
            and claims.technical_shadow_gate_passed
            and not claims.real_provider_evidence
            and not claims.closes_real_provider_gate
            and not claims.internal_real_gate_passed
            and not claims.model_accuracy_evaluated
            and not claims.pilot_ready
            and not claims.automatic_expansion_allowed
            and claims.next_stage_requires_human_review,
            "Simulation claims remain technical-only and keep real-provider/Pilot gates open.",
            "The PI-01E report overclaims real evidence, model quality, expansion, or Pilot readiness.",
        ),
        _check(
            "pi01.mock_evidence",
            shadow.provider_invocation_count > 0
            and shadow.planned_action_count == shadow.provider_invocation_count
            and shadow.mock_result_count == shadow.provider_invocation_count
            and shadow.persisted_evidence_count == shadow.provider_invocation_count
            and shadow.real_result_count == 0
            and shadow.failed_count == 0
            and shadow.missing_evidence_count == 0,
            "Every planned fake Provider call produced persisted mock evidence with no missing result.",
            "PI-01E fake Provider invocation/evidence counts are incomplete or contain real/failing results.",
        ),
        _check(
            "pi01.compatibility_and_side_effects",
            compatibility.deterministic_projection_mismatch_count == 0
            and compatibility.review_routing_difference_count == 0
            and bool(shadow.unauthorized_side_effect_counts)
            and all(value == 0 for value in shadow.unauthorized_side_effect_counts.values()),
            "Deterministic projections/review routing match and unauthorized side effects are zero.",
            "PI-01E reports compatibility drift or an unauthorized side effect.",
        ),
    ]
    semantic = _stable_hash(report.model_dump(mode="json", exclude_none=True))
    return _component(
        SocSimulationCompletionComponentId.PI01_EXTERNAL_SIMULATION,
        [artifact],
        checks,
        semantic,
        limitations=[
            "All observed Provider results were mocked and the 50-alert cohort observed no real Provider hit.",
        ],
    )


def _assess_pi03_quality(artifact: _LoadedArtifact) -> SocSimulationCompletionComponent:
    report = _validate_artifact(artifact, SocQualityEvaluationReport)
    if report is None:
        return _invalid_component(
            SocSimulationCompletionComponentId.PI03_QUALITY_EVALUATION,
            [artifact],
        )
    expected_semantic = _stable_hash(
        {
            "data_class": report.data_class.value,
            "corpus_manifest_id": report.corpus_manifest_id,
            "component_hashes": report.component_hashes,
        }
    )
    checks = [
        _check("pi03_quality.contract", True, "PI-03B report satisfies the typed quality contract."),
        _check(
            "pi03_quality.simulation_provenance",
            report.data_class is SocEvaluationDataClass.SIMULATION and report.mocked,
            "Quality evidence is explicitly simulation-only.",
            "Quality evidence is not marked as simulation/mock data.",
        ),
        _check(
            "pi03_quality.components",
            report.engineering_flow_passed and all(status is SocQualityComponentStatus.PASSED for status in report.component_statuses.values()),
            "All composed offline quality evaluators passed their engineering flow.",
            "At least one composed quality evaluator failed.",
        ),
        _check(
            "pi03_quality.identity",
            report.semantic_sha256 == expected_semantic and report.evaluation_id == f"SQE-{expected_semantic[:12].upper()}",
            "Quality semantic hash and evaluation identity are internally consistent.",
            "Quality semantic hash or evaluation identity does not match its projection.",
        ),
        _check(
            "pi03_quality.replay",
            report.diff is not None and not report.diff.changed and not report.diff.changed_components,
            "Quality replay is semantically stable.",
            "Quality report lacks an unchanged baseline replay.",
        ),
        _check(
            "pi03_quality.claim_boundary",
            not report.real_quality_claim_allowed and not report.profile_publish_allowed and not report.rollout_allowed and not report.automation_allowed,
            "Quality output cannot publish a profile, authorize rollout, or enable automation.",
            "Quality output attempts to authorize a downstream real decision.",
        ),
    ]
    return _component(
        SocSimulationCompletionComponentId.PI03_QUALITY_EVALUATION,
        [artifact],
        checks,
        expected_semantic,
        limitations=list(report.limitations),
    )


def _assess_pi03_skill(
    ingest_artifact: _LoadedArtifact,
    replay_artifact: _LoadedArtifact,
) -> SocSimulationCompletionComponent:
    ingest = _validate_artifact(ingest_artifact, SkillImprovementIngestReport)
    replay = _validate_artifact(replay_artifact, SkillImprovementReplayReport)
    artifacts = [ingest_artifact, replay_artifact]
    if ingest is None or replay is None:
        return _invalid_component(
            SocSimulationCompletionComponentId.PI03_SKILL_IMPROVEMENT,
            artifacts,
        )
    candidates = [result.candidate for result in ingest.results if result.candidate is not None]
    observations = [result.observation for result in ingest.results]
    checks = [
        _check("pi03_skill.contract", True, "PI-03C ingest and replay reports satisfy their typed contracts."),
        _check(
            "pi03_skill.simulation_provenance",
            ingest.mocked and ingest.simulation_count == ingest.input_count and ingest.real_feedback_count == 0 and all(item.data_class is SocEvaluationDataClass.SIMULATION and item.mocked for item in observations),
            "Every feedback observation is an explicit simulation fixture.",
            "Skill feedback mixes real and simulation provenance or is not fully mocked.",
        ),
        _check(
            "pi03_skill.candidate_boundary",
            ingest.candidate_count > 0 and bool(candidates) and all(item is not None and item.status is SkillImprovementCandidateStatus.PENDING_REVIEW and item.human_review_required and item.mocked for item in candidates),
            "Repeated feedback creates only pending, human-reviewed Skill candidates.",
            "The Skill backlog has no candidate or contains a non-pending/non-simulation candidate.",
        ),
        _check(
            "pi03_skill.replay",
            replay.candidate_id in ingest.candidate_ids
            and replay.observation_count == ingest.input_count
            and replay.source_integrity_passed
            and not replay.changed
            and replay.baseline_candidate_content_hash == replay.recomputed_candidate_content_hash,
            "Candidate aggregation replay is source-complete and semantically unchanged.",
            "Candidate replay drifted, lost source integrity, or does not match the ingest report.",
        ),
        _check(
            "pi03_skill.claim_boundary",
            not ingest.skill_mutation_allowed
            and not ingest.skill_activation_allowed
            and not ingest.real_quality_claim_allowed
            and not replay.skill_behavior_replay_executed
            and not replay.skill_mutation_allowed
            and not replay.skill_activation_allowed
            and not replay.real_quality_claim_allowed
            and all(not item.skill_mutation_allowed and not item.memory_write_allowed and not item.runtime_decision_allowed for item in observations),
            "Feedback aggregation cannot edit/activate Skills, write memory, or change Runtime decisions.",
            "PI-03C output attempts to mutate Skill/memory/Runtime or claim real quality.",
        ),
    ]
    semantic = _stable_hash(
        {
            "ingest": {
                "input_count": ingest.input_count,
                "simulation_count": ingest.simulation_count,
                "real_feedback_count": ingest.real_feedback_count,
                "candidate_ids": ingest.candidate_ids,
                "observation_hashes": [item.content_hash for item in observations],
                "candidate_hashes": [item.candidate_content_hash for item in candidates if item is not None],
            },
            "replay": replay.model_dump(
                mode="json",
                exclude={"created_at"},
                exclude_none=True,
            ),
        }
    )
    return _component(
        SocSimulationCompletionComponentId.PI03_SKILL_IMPROVEMENT,
        artifacts,
        checks,
        semantic,
        limitations=[
            "The source classifier for real analyst/external feedback remains data-gated.",
            "Aggregation replay does not execute the proposed Skill behavior or publish a package.",
        ],
    )


def _assess_pi04_operations(artifact: _LoadedArtifact) -> SocSimulationCompletionComponent:
    snapshot = _validate_artifact(artifact, SocOperationsSnapshot)
    if snapshot is None:
        return _invalid_component(
            SocSimulationCompletionComponentId.PI04_OPERATIONS_VISIBILITY,
            [artifact],
        )
    gap_ids = {item.metric for item in snapshot.measurement_gaps}
    required_gaps = {
        "kafka.consumer_lag",
        "model.compute_utilization",
        "production.slo_compliance",
    }
    checks = [
        _check("pi04.contract", True, "PI-04 snapshot satisfies the strict operations contract."),
        _check(
            "pi04.simulation_provenance",
            snapshot.persisted.availability is SocOperationsAvailability.AVAILABLE and snapshot.persisted.backend == "sqlite" and not snapshot.production_slo_evidence_available,
            "Operations evidence is an available local SQLite snapshot with no production-SLO claim.",
            "Operations evidence is not the expected local SQLite simulation snapshot.",
        ),
        _check(
            "pi04.visibility",
            snapshot.persisted.metrics is not None and snapshot.kafka.availability is not SocOperationsAvailability.UNAVAILABLE,
            "Persisted counters are available and Kafka is either explicit not-configured/not-measured or reachable.",
            "Persisted operations metrics are unavailable or Kafka probing failed.",
        ),
        _check(
            "pi04.measurement_boundaries",
            required_gaps.issubset(gap_ids) and all(item.availability is SocOperationsAvailability.NOT_MEASURED for item in snapshot.measurement_gaps),
            "Lag, model compute, and production SLO remain explicitly not measured.",
            "Required production telemetry gaps are missing or falsely presented as measured.",
        ),
    ]
    projection = snapshot.model_dump(mode="json", exclude={"generated_at"}, exclude_none=True)
    return _component(
        SocSimulationCompletionComponentId.PI04_OPERATIONS_VISIBILITY,
        [artifact],
        checks,
        _stable_hash(projection),
        limitations=[
            "Local counters and UI reachability do not prove deployed PostgreSQL, Kafka lag, compute, Prometheus, or SLO behavior.",
        ],
    )


def _assess_pi05_rehearsal(artifact: _LoadedArtifact) -> SocSimulationCompletionComponent:
    report = _validate_artifact(artifact, SocRolloutRehearsalReport)
    if report is None:
        return _invalid_component(
            SocSimulationCompletionComponentId.PI05_ROLLOUT_REHEARSAL,
            [artifact],
        )
    expected_semantic = _stable_hash(
        {
            "policy_version": report.policy_version,
            "plan_id": report.plan_id,
            "plan_version": report.plan_version,
            "component_hashes": report.component_hashes,
        }
    )
    checks = [
        _check("pi05.contract", True, "PI-05A report satisfies the typed rehearsal contract."),
        _check(
            "pi05.simulation_provenance",
            report.data_class is SocEvaluationDataClass.SIMULATION and report.mocked,
            "Rollout rehearsal is explicitly simulation-only.",
            "Rollout rehearsal is not marked as simulation/mock data.",
        ),
        _check(
            "pi05.rehearsal",
            report.engineering_rehearsal_passed and report.rollback.passed and report.simulated_stage_transition_count > 0,
            "Stage-gate and ordered rollback control flow completed in memory.",
            "Rollout or rollback rehearsal did not complete.",
        ),
        _check(
            "pi05.identity_and_replay",
            report.semantic_sha256 == expected_semantic and report.rehearsal_id == f"SRR-{expected_semantic[:12].upper()}" and report.diff is not None and not report.diff.changed and not report.diff.changed_components,
            "Rehearsal identity is valid and baseline replay is unchanged.",
            "Rehearsal identity is invalid or baseline replay changed.",
        ),
        _check(
            "pi05.real_gate_boundary",
            report.current_real_stage is SocRolloutStage.NOT_STARTED
            and report.real_stage_transition_count == 0
            and report.external_effect_count == 0
            and all(not item.real_gate_satisfied for item in report.gate_assessments)
            and all(not item.real_promotion_eligible for item in report.stage_assessments),
            "Every real gate remains blocked with zero real transition or external effect.",
            "A simulation attempted to satisfy a real gate, promotion, transition, or external effect.",
        ),
        _check(
            "pi05.claim_boundary",
            not report.stage_transition_allowed
            and not report.production_approval_granted
            and not report.real_rollout_claim_allowed
            and not report.auto_close_allowed
            and not report.external_state_mutation_allowed
            and not report.high_risk_action_execution_allowed,
            "Rehearsal cannot approve rollout, close work, mutate external state, or execute high-risk actions.",
            "PI-05A output attempts to authorize a real rollout side effect.",
        ),
    ]
    return _component(
        SocSimulationCompletionComponentId.PI05_ROLLOUT_REHEARSAL,
        [artifact],
        checks,
        expected_semantic,
        limitations=list(report.limitations),
    )


def _load_artifact(base_dir: Path, configured_path: str, role: str) -> _LoadedArtifact:
    path = Path(configured_path)
    if not path.is_absolute():
        path = base_dir / path
    try:
        raw = path.read_bytes()
    except OSError:
        return _LoadedArtifact(
            role=role,
            digest=SocSimulationArtifactDigest(
                artifact_role=role,
                artifact_name=path.name or role,
                readable=False,
                error_code="artifact_unreadable",
            ),
            payload=None,
        )
    digest = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _LoadedArtifact(
            role=role,
            digest=SocSimulationArtifactDigest(
                artifact_role=role,
                artifact_name=path.name or role,
                readable=True,
                sha256=digest,
            ),
            payload=None,
        )
    schema_version = payload.get("schema_version") if isinstance(payload, dict) else None
    return _LoadedArtifact(
        role=role,
        digest=SocSimulationArtifactDigest(
            artifact_role=role,
            artifact_name=path.name or role,
            readable=True,
            schema_version=schema_version if isinstance(schema_version, str) else None,
            sha256=digest,
        ),
        payload=payload,
    )


def _validate_artifact(artifact: _LoadedArtifact, model_type):
    if artifact.payload is None:
        return None
    try:
        report = model_type.model_validate(artifact.payload)
    except ValidationError:
        return None
    artifact_id = _artifact_identity(report)
    artifact.digest = artifact.digest.model_copy(update={"artifact_id": artifact_id})
    return report


def _artifact_identity(report: object) -> str | None:
    for field_name in ("report_id", "evaluation_id", "candidate_id", "rehearsal_id"):
        value = getattr(report, field_name, None)
        if isinstance(value, str):
            return value
    candidate_ids = getattr(report, "candidate_ids", None)
    if isinstance(candidate_ids, list) and len(candidate_ids) == 1 and isinstance(candidate_ids[0], str):
        return candidate_ids[0]
    return None


def _invalid_component(
    component_id: SocSimulationCompletionComponentId,
    artifacts: list[_LoadedArtifact],
) -> SocSimulationCompletionComponent:
    checks = [
        _check(
            f"{component_id.value}.contract",
            False,
            "",
            "A required artifact is missing, malformed, or violates its typed contract.",
        ),
        _check(
            f"{component_id.value}.simulation_provenance",
            False,
            "",
            "Simulation provenance cannot be verified from an invalid artifact.",
        ),
    ]
    semantic = _stable_hash(
        {
            "component_id": component_id.value,
            "artifacts": [
                {
                    "role": item.role,
                    "readable": item.digest.readable,
                    "sha256": item.digest.sha256,
                    "schema_version": item.digest.schema_version,
                    "payload_valid": item.payload is not None,
                }
                for item in artifacts
            ],
            "status": "failed",
        }
    )
    return _component(
        component_id,
        artifacts,
        checks,
        semantic,
        limitations=["Invalid artifacts fail closed and cannot be replaced by narrative claims."],
    )


def _component(
    component_id: SocSimulationCompletionComponentId,
    artifacts: list[_LoadedArtifact],
    checks: list[SocSimulationCompletionCheck],
    semantic_sha256: str,
    *,
    limitations: list[str],
) -> SocSimulationCompletionComponent:
    status = SocSimulationCompletionStatus.PASSED if all(item.passed for item in checks) else SocSimulationCompletionStatus.FAILED
    provenance_checks = [item for item in checks if item.check_id.endswith("simulation_provenance")]
    return SocSimulationCompletionComponent(
        component_id=component_id,
        status=status,
        artifacts=[item.digest for item in artifacts],
        checks=checks,
        semantic_sha256=semantic_sha256,
        simulation_provenance_verified=bool(provenance_checks) and all(item.passed for item in provenance_checks),
        limitations=list(dict.fromkeys(limitations)),
    )


def _check(
    check_id: str,
    passed: bool,
    success_detail: str,
    failure_detail: str | None = None,
) -> SocSimulationCompletionCheck:
    return SocSimulationCompletionCheck(
        check_id=check_id,
        passed=passed,
        detail=success_detail if passed else (failure_detail or "The completion check failed."),
    )


def _real_integration_debt() -> list[SocSimulationRealIntegrationDebt]:
    details = {
        SocRolloutGateId.REAL_PROVIDER_EVIDENCE: (
            "Only fake Provider results are present.",
            "Fresh mocked=false hit, not-found, error, MCP, persisted-evidence, and context-readback acceptance.",
        ),
        SocRolloutGateId.PRODUCTION_INFRASTRUCTURE: (
            "SQLite/local broker evidence is not PostgreSQL/Kafka/K8s production evidence.",
            "Deployment-specific ACL/TLS, capacity, recovery, transaction, and health evidence.",
        ),
        SocRolloutGateId.REAL_QUALITY_EVALUATION: (
            "Quality and Skill inputs are simulation fixtures.",
            "A desensitized, human-reviewed real corpus with reproducible quality and calibration reports.",
        ),
        SocRolloutGateId.OPERATIONS_SLO: (
            "Lag, model compute, Provider network latency/cost, Prometheus, and SLO remain unmeasured.",
            "Deployed telemetry with reviewed SLO thresholds, alerting, and retention evidence.",
        ),
        SocRolloutGateId.ACCOUNTABLE_OWNERS: (
            "Owner identities in PI-05A are simulation placeholders.",
            "Authenticated, scoped, current approvals from every required accountable owner.",
        ),
        SocRolloutGateId.ROLLBACK_READINESS: (
            "Rollback actions were exercised only in memory.",
            "A deployed rollback drill proving ingress pause, cohort disablement, preservation, routing, notification, and verification.",
        ),
        SocRolloutGateId.COHORT_ISOLATION: (
            "No deployed feature flag or cohort enforcement was changed.",
            "A deployed, independently disableable cohort with audited scope enforcement.",
        ),
    }
    return [
        SocSimulationRealIntegrationDebt(
            gate_id=gate_id,
            reason=details[gate_id][0],
            required_evidence=details[gate_id][1],
        )
        for gate_id in SocRolloutGateId
    ]


def _build_completion_diff(
    *,
    baseline: SocSimulationCompletionReport | None,
    completion_id: str,
    semantic_sha256: str,
    components: list[SocSimulationCompletionComponent],
) -> SocSimulationCompletionDiff | None:
    if baseline is None:
        return None
    current_hashes = {item.component_id: item.semantic_sha256 for item in components}
    changed_components = [component_id for component_id in SocSimulationCompletionComponentId if baseline.component_hashes.get(component_id) != current_hashes.get(component_id)]
    baseline_artifacts = {item.component_id: [artifact.sha256 for artifact in item.artifacts] for item in baseline.components}
    current_artifacts = {item.component_id: [artifact.sha256 for artifact in item.artifacts] for item in components}
    artifact_changes = [component_id for component_id in SocSimulationCompletionComponentId if baseline_artifacts.get(component_id) != current_artifacts.get(component_id)]
    changed = baseline.semantic_sha256 != semantic_sha256 or bool(changed_components)
    if not changed and baseline.completion_id != completion_id:
        raise ValueError("stable completion semantics produced a different completion id")
    return SocSimulationCompletionDiff(
        baseline_completion_id=baseline.completion_id,
        baseline_semantic_sha256=baseline.semantic_sha256,
        changed=changed,
        changed_components=changed_components,
        artifact_bytes_changed_components=artifact_changes,
    )


def _load_model(path: str | Path, model_type, label: str):
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {label} JSON: {exc}") from exc
    return model_type.model_validate(payload)


def _stable_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "SIMULATION_COMPLETION_POLICY_VERSION",
    "SocSimulationArtifactDigest",
    "SocSimulationCompletionArtifactPaths",
    "SocSimulationCompletionCheck",
    "SocSimulationCompletionComponent",
    "SocSimulationCompletionComponentId",
    "SocSimulationCompletionDiff",
    "SocSimulationCompletionReport",
    "SocSimulationCompletionRequest",
    "SocSimulationCompletionStatus",
    "SocSimulationRealIntegrationDebt",
    "load_soc_simulation_completion_report",
    "load_soc_simulation_completion_request",
    "run_soc_simulation_completion",
]
