"""Manifest-bound, replayable SOC quality-flow evaluation."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from soc_agent.contracts import (
    ConfidenceCalibrationLabelSet,
    ConfidenceCalibrationReport,
    ConfidenceLabelCorpusManifest,
    ConfidenceLabelCorpusVerificationReport,
    SocEvaluationDataClass,
)
from soc_agent.eval.confidence import calibrate_confidence
from soc_agent.eval.correlation import CorrelationEvalReport
from soc_agent.eval.labels import (
    calibration_samples_from_label_set,
    verify_confidence_label_corpus_manifest,
)
from soc_agent.eval.offline import OfflineEvalReport
from soc_agent.eval.scenarios import ScenarioEvalReport


class SocQualityComponentStatus(StrEnum):
    """Engineering status for one composed offline-evaluation path."""

    PASSED = "passed"
    FAILED = "failed"


class ConfidenceCalibrationExecutionReport(BaseModel):
    """Manifest-bound calibration execution that never publishes a profile."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "soc.confidence_calibration_execution.v1"
    data_class: SocEvaluationDataClass
    mocked: bool
    manifest_verification: ConfidenceLabelCorpusVerificationReport
    calibration_report: ConfidenceCalibrationReport
    evaluation_flow_passed: bool = True
    real_quality_claim_allowed: bool = False
    profile_publish_allowed: bool = False
    automation_allowed: bool = False
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_governance_boundary(self) -> ConfidenceCalibrationExecutionReport:
        if self.mocked is not (self.data_class is SocEvaluationDataClass.SIMULATION):
            raise ValueError("calibration execution mocked state must match data class")
        if not self.manifest_verification.integrity_passed:
            raise ValueError("calibration execution requires an integrity-verified corpus")
        if not self.manifest_verification.calibration_input_eligible:
            raise ValueError("calibration execution requires a fully reviewed label set")
        if self.manifest_verification.data_class is not self.data_class:
            raise ValueError("calibration execution data class must match its corpus")
        if self.manifest_verification.mocked is not self.mocked:
            raise ValueError("calibration execution mocked state must match verification")
        if self.calibration_report.label_set_id != self.manifest_verification.label_set_id:
            raise ValueError("calibration report label set must match the corpus verification")
        if not self.evaluation_flow_passed:
            raise ValueError("a calibration execution report represents only a completed flow")
        if self.real_quality_claim_allowed or self.profile_publish_allowed or self.automation_allowed:
            raise ValueError("offline calibration cannot publish quality, profile, or automation decisions")
        return self


class SocQualityEvaluationDiff(BaseModel):
    """Stable component-level replay diff against a prior quality report."""

    baseline_evaluation_id: str
    baseline_semantic_sha256: str
    changed: bool = False
    changed_components: list[str] = Field(default_factory=list)


class SocQualityEvaluationReport(BaseModel):
    """One composed quality-flow report with explicit simulation provenance."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "soc.quality_evaluation_report.v1"
    evaluation_id: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data_class: SocEvaluationDataClass
    mocked: bool
    analyzer_mode: str = "stub_replay"
    corpus_manifest_id: str
    component_statuses: dict[str, SocQualityComponentStatus]
    component_hashes: dict[str, str]
    semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    engineering_flow_passed: bool
    real_quality_claim_allowed: bool = False
    profile_publish_allowed: bool = False
    rollout_allowed: bool = False
    automation_allowed: bool = False
    limitations: list[str] = Field(default_factory=list)
    offline_runtime: OfflineEvalReport
    scenario_evaluation: ScenarioEvalReport
    correlation_evaluation: CorrelationEvalReport
    confidence_calibration: ConfidenceCalibrationExecutionReport
    diff: SocQualityEvaluationDiff | None = None

    @model_validator(mode="after")
    def validate_quality_boundary(self) -> SocQualityEvaluationReport:
        expected_components = {
            "confidence_calibration",
            "correlation_evaluation",
            "offline_runtime",
            "scenario_evaluation",
        }
        if set(self.component_statuses) != expected_components:
            raise ValueError("quality report component statuses are incomplete")
        if set(self.component_hashes) != expected_components:
            raise ValueError("quality report component hashes are incomplete")
        if self.mocked is not (self.data_class is SocEvaluationDataClass.SIMULATION):
            raise ValueError("quality report mocked state must match data class")
        if self.confidence_calibration.data_class is not self.data_class:
            raise ValueError("quality report and confidence corpus data classes must match")
        expected_pass = all(status is SocQualityComponentStatus.PASSED for status in self.component_statuses.values())
        if self.engineering_flow_passed is not expected_pass:
            raise ValueError("quality report pass state must match component statuses")
        if self.real_quality_claim_allowed or self.profile_publish_allowed or self.rollout_allowed or self.automation_allowed:
            raise ValueError("offline quality evaluation cannot authorize downstream decisions")
        return self


def run_manifest_bound_confidence_calibration(
    manifest: ConfidenceLabelCorpusManifest,
    label_set: ConfidenceCalibrationLabelSet,
    *,
    bin_count: int = 10,
    target_accuracy: float = 0.9,
    minimum_samples: int = 30,
    minimum_threshold_samples: int = 10,
) -> ConfidenceCalibrationExecutionReport:
    """Verify one corpus before computing descriptive calibration metrics."""

    verification = verify_confidence_label_corpus_manifest(manifest, label_set)
    if not verification.integrity_passed:
        raise ValueError("confidence corpus integrity verification failed")
    if not verification.calibration_input_eligible:
        raise ValueError("confidence corpus is not eligible for calibration")
    calibration = calibrate_confidence(
        calibration_samples_from_label_set(label_set),
        bin_count=bin_count,
        target_accuracy=target_accuracy,
        minimum_samples=minimum_samples,
        minimum_threshold_samples=minimum_threshold_samples,
        label_set_id=label_set.label_set_id,
    )
    warnings = list(dict.fromkeys([*verification.warnings, *calibration.warnings]))
    return ConfidenceCalibrationExecutionReport(
        data_class=manifest.data_class,
        mocked=manifest.mocked,
        manifest_verification=verification,
        calibration_report=calibration,
        warnings=warnings,
    )


def build_soc_quality_evaluation_report(
    *,
    corpus_manifest_id: str,
    offline_runtime: OfflineEvalReport,
    scenario_evaluation: ScenarioEvalReport,
    correlation_evaluation: CorrelationEvalReport,
    confidence_calibration: ConfidenceCalibrationExecutionReport,
    baseline: SocQualityEvaluationReport | None = None,
) -> SocQualityEvaluationReport:
    """Compose existing evaluators without introducing a second scoring system."""

    statuses = {
        "offline_runtime": (
            SocQualityComponentStatus.PASSED if offline_runtime.sample_count > 0 and offline_runtime.failed_count == 0 and offline_runtime.parse_success_count == offline_runtime.sample_count else SocQualityComponentStatus.FAILED
        ),
        "scenario_evaluation": (SocQualityComponentStatus.PASSED if scenario_evaluation.sample_count > 0 and scenario_evaluation.failed_count == 0 else SocQualityComponentStatus.FAILED),
        "correlation_evaluation": (SocQualityComponentStatus.PASSED if correlation_evaluation.case_count > 0 and correlation_evaluation.integrity_passed else SocQualityComponentStatus.FAILED),
        "confidence_calibration": (SocQualityComponentStatus.PASSED if confidence_calibration.evaluation_flow_passed else SocQualityComponentStatus.FAILED),
    }
    component_hashes = {
        "offline_runtime": _stable_hash(_offline_snapshot(offline_runtime)),
        "scenario_evaluation": _stable_hash(_scenario_snapshot(scenario_evaluation)),
        "correlation_evaluation": _stable_hash(_correlation_snapshot(correlation_evaluation)),
        "confidence_calibration": _stable_hash(_confidence_snapshot(confidence_calibration)),
    }
    semantic_sha256 = _stable_hash(
        {
            "data_class": confidence_calibration.data_class.value,
            "corpus_manifest_id": corpus_manifest_id,
            "component_hashes": component_hashes,
        }
    )
    evaluation_id = f"SQE-{semantic_sha256[:12].upper()}"
    diff = _quality_diff(baseline, evaluation_id, semantic_sha256, component_hashes) if baseline is not None else None
    limitations = _quality_limitations(
        offline_runtime=offline_runtime,
        scenario_evaluation=scenario_evaluation,
        correlation_evaluation=correlation_evaluation,
        confidence_calibration=confidence_calibration,
    )
    return SocQualityEvaluationReport(
        evaluation_id=evaluation_id,
        data_class=confidence_calibration.data_class,
        mocked=confidence_calibration.mocked,
        corpus_manifest_id=corpus_manifest_id,
        component_statuses=statuses,
        component_hashes=component_hashes,
        semantic_sha256=semantic_sha256,
        engineering_flow_passed=all(status is SocQualityComponentStatus.PASSED for status in statuses.values()),
        real_quality_claim_allowed=False,
        profile_publish_allowed=False,
        rollout_allowed=False,
        automation_allowed=False,
        limitations=limitations,
        offline_runtime=offline_runtime,
        scenario_evaluation=scenario_evaluation,
        correlation_evaluation=correlation_evaluation,
        confidence_calibration=confidence_calibration,
        diff=diff,
    )


def load_soc_quality_evaluation_report(path: str | Path) -> SocQualityEvaluationReport:
    """Load one prior report for stable replay comparison."""

    report_path = Path(path)
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        return SocQualityEvaluationReport.model_validate(payload)
    except OSError as exc:
        raise ValueError(f"cannot read SOC quality report: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid SOC quality report JSON: {exc}") from exc


def _quality_diff(
    baseline: SocQualityEvaluationReport,
    evaluation_id: str,
    semantic_sha256: str,
    component_hashes: dict[str, str],
) -> SocQualityEvaluationDiff:
    changed_components = sorted(name for name in set(baseline.component_hashes) | set(component_hashes) if baseline.component_hashes.get(name) != component_hashes.get(name))
    changed = baseline.semantic_sha256 != semantic_sha256 or bool(changed_components)
    if not changed and baseline.evaluation_id != evaluation_id:
        raise ValueError("stable quality semantics produced a different evaluation id")
    return SocQualityEvaluationDiff(
        baseline_evaluation_id=baseline.evaluation_id,
        baseline_semantic_sha256=baseline.semantic_sha256,
        changed=changed,
        changed_components=changed_components,
    )


def _quality_limitations(
    *,
    offline_runtime: OfflineEvalReport,
    scenario_evaluation: ScenarioEvalReport,
    correlation_evaluation: CorrelationEvalReport,
    confidence_calibration: ConfidenceCalibrationExecutionReport,
) -> list[str]:
    limitations = list(confidence_calibration.warnings)
    if confidence_calibration.data_class is SocEvaluationDataClass.SIMULATION:
        limitations.append("simulation inputs validate engineering flow only and cannot support real quality claims")
    if offline_runtime.ungrounded_evidence_count:
        limitations.append(f"offline replay contains {offline_runtime.ungrounded_evidence_count} ungrounded evidence item(s)")
    if scenario_evaluation.missing_scenario_taxonomy_keys:
        limitations.append(f"scenario fixture does not cover {len(scenario_evaluation.missing_scenario_taxonomy_keys)} taxonomy key(s)")
    correlation_false_positives = correlation_evaluation.retrieval_metrics.false_positive + correlation_evaluation.dedup_metrics.false_positive
    if correlation_false_positives:
        limitations.append(f"correlation fixture reports {correlation_false_positives} retrieval/dedup false-positive decision(s)")
    return list(dict.fromkeys(limitations))


def _offline_snapshot(report: OfflineEvalReport) -> dict[str, object]:
    payload = report.model_dump(mode="json", exclude_none=True)
    for result in payload.get("results", []):
        result.pop("path", None)
        result.pop("stub_run_id", None)
        result.pop("llm_run_id", None)
    return payload


def _scenario_snapshot(report: ScenarioEvalReport) -> dict[str, object]:
    payload = report.model_dump(mode="json", exclude_none=True)
    payload.pop("diff", None)
    for result in payload.get("results", []):
        result.pop("path", None)
        result.pop("run_id", None)
        result.pop("alert_id", None)
        for finding in result.get("findings", []):
            finding.pop("finding_id", None)
    return payload


def _correlation_snapshot(report: CorrelationEvalReport) -> dict[str, object]:
    payload = report.model_dump(mode="json", exclude_none=True)
    payload.pop("generated_at", None)
    payload.pop("fixture_path", None)
    payload.pop("diff", None)
    return payload


def _confidence_snapshot(report: ConfidenceCalibrationExecutionReport) -> dict[str, object]:
    payload = report.model_dump(mode="json", exclude_none=True)
    payload["calibration_report"]["threshold_profile"].pop("created_at", None)
    return payload


def _stable_hash(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ConfidenceCalibrationExecutionReport",
    "SocQualityComponentStatus",
    "SocQualityEvaluationDiff",
    "SocQualityEvaluationReport",
    "build_soc_quality_evaluation_report",
    "load_soc_quality_evaluation_report",
    "run_manifest_bound_confidence_calibration",
]
