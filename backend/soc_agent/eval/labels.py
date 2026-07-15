"""Governed human-label bundles for offline SOC confidence calibration."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path

from pydantic import ValidationError

from soc_agent.contracts import (
    AnalysisRun,
    ConfidenceCalibrationLabelSet,
    ConfidenceCalibrationSample,
    ConfidenceLabelReviewStatus,
    ConfidenceLabelSetValidationReport,
    DecisionConfidenceSource,
)


def load_analysis_runs_for_labeling(
    path: str | Path,
    *,
    glob_pattern: str = "*.json",
) -> list[tuple[str, AnalysisRun]]:
    """Load complete AnalysisRun JSON artifacts without retaining raw input in labels."""

    source = Path(path)
    if source.is_dir():
        files = sorted(item for item in source.glob(glob_pattern) if item.is_file())
    elif source.is_file():
        files = [source]
    else:
        raise ValueError(f"analysis run path does not exist: {source}")
    if not files:
        raise ValueError(f"no analysis run JSON matched: {source} ({glob_pattern})")

    runs: list[tuple[str, AnalysisRun]] = []
    for file_path in files:
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            run = AnalysisRun.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"invalid analysis run artifact {file_path}: {exc}") from exc
        runs.append((str(file_path), run))
    return runs


def build_confidence_label_set(
    runs: Sequence[tuple[str, AnalysisRun]],
) -> ConfidenceCalibrationLabelSet:
    """Create pending analyst labels from live LLM runs."""

    if not runs:
        raise ValueError("at least one analysis run is required")

    samples: list[ConfidenceCalibrationSample] = []
    seen_run_ids: set[str] = set()
    for source_path, run in runs:
        if run.run_id in seen_run_ids:
            raise ValueError(f"duplicate analysis run_id: {run.run_id}")
        seen_run_ids.add(run.run_id)
        if run.analysis is None or run.decision is None or not run.input_hash:
            raise ValueError(f"analysis run {run.run_id} is incomplete and cannot be labeled")
        if run.decision.confidence_source is not DecisionConfidenceSource.LLM_SELF_REPORT:
            raise ValueError(f"analysis run {run.run_id} is not a live LLM prediction")

        grounding = run.analysis_evidence_grounding
        samples.append(
            ConfidenceCalibrationSample(
                sample_id=_sample_id(run.run_id),
                run_id=run.run_id,
                alert_id=run.alert_id,
                input_hash=run.input_hash,
                source_path=source_path,
                predicted_verdict=run.analysis.verdict,
                confidence=run.analysis.confidence,
                model_name=run.model_name,
                prompt_version=run.prompt_version,
                pipeline_version=run.pipeline_version,
                summary=run.analysis.summary,
                recommended_action=run.analysis.recommended_action,
                evidence_grounded_count=grounding.grounded_count if grounding is not None else 0,
                evidence_ungrounded_count=grounding.ungrounded_count if grounding is not None else len(run.analysis.evidence),
                review_reasons=list(run.decision.review_reasons),
            )
        )

    label_set_payload = [
        {
            "run_id": sample.run_id,
            "input_hash": sample.input_hash,
            "model_name": sample.model_name,
            "prompt_version": sample.prompt_version,
            "pipeline_version": sample.pipeline_version,
        }
        for sample in sorted(samples, key=lambda item: (item.input_hash, item.run_id))
    ]
    digest = _stable_hash(label_set_payload)[:12].upper()
    return ConfidenceCalibrationLabelSet(label_set_id=f"CLS-{digest}", samples=samples)


def load_confidence_label_set(path: str | Path) -> ConfidenceCalibrationLabelSet:
    label_path = Path(path)
    try:
        payload = json.loads(label_path.read_text(encoding="utf-8"))
        return ConfidenceCalibrationLabelSet.model_validate(payload)
    except OSError as exc:
        raise ValueError(f"cannot read confidence label set: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid confidence label set JSON: {exc}") from exc
    except ValidationError as exc:
        raise ValueError(f"invalid confidence label set: {exc}") from exc


def validate_confidence_label_set(
    label_set: ConfidenceCalibrationLabelSet,
) -> ConfidenceLabelSetValidationReport:
    counts = Counter(sample.review_status for sample in label_set.samples)
    accepted = [sample for sample in label_set.samples if sample.review_status is ConfidenceLabelReviewStatus.ACCEPTED]
    included = [sample for sample in label_set.samples if sample.review_status is not ConfidenceLabelReviewStatus.EXCLUDED]
    errors: list[str] = []
    warnings: list[str] = []

    if counts[ConfidenceLabelReviewStatus.PENDING_REVIEW]:
        errors.append(f"{counts[ConfidenceLabelReviewStatus.PENDING_REVIEW]} label(s) still require analyst review")
    if not accepted:
        errors.append("no accepted human labels are available for calibration")

    duplicate_run_ids = _duplicates(sample.run_id for sample in label_set.samples)
    if duplicate_run_ids:
        errors.append(f"duplicate run_id values: {', '.join(duplicate_run_ids)}")
    duplicate_input_hashes = _duplicates(sample.input_hash for sample in included)
    if duplicate_input_hashes:
        errors.append("accepted labels contain duplicate input_hash values; replayed alerts are not independent samples")

    model_names = sorted({sample.model_name for sample in included})
    prompt_versions = sorted({sample.prompt_version for sample in included})
    pipeline_versions = sorted({sample.pipeline_version for sample in included})
    if len(model_names) > 1:
        errors.append("accepted labels mix model_name values")
    if len(prompt_versions) > 1:
        errors.append("accepted labels mix prompt_version values")
    if len(pipeline_versions) > 1:
        errors.append("accepted labels mix pipeline_version values")

    verdict_counts = Counter(sample.actual_verdict.value for sample in accepted if sample.actual_verdict is not None)
    if accepted and len(verdict_counts) < 2:
        warnings.append("accepted labels contain fewer than two actual verdict classes")

    return ConfidenceLabelSetValidationReport(
        label_set_id=label_set.label_set_id,
        sample_count=len(label_set.samples),
        accepted_count=len(accepted),
        pending_count=counts[ConfidenceLabelReviewStatus.PENDING_REVIEW],
        excluded_count=counts[ConfidenceLabelReviewStatus.EXCLUDED],
        calibratable=not errors,
        model_names=model_names,
        prompt_versions=prompt_versions,
        pipeline_versions=pipeline_versions,
        actual_verdict_counts=dict(sorted(verdict_counts.items())),
        errors=errors,
        warnings=warnings,
    )


def calibration_samples_from_label_set(
    label_set: ConfidenceCalibrationLabelSet,
) -> list[ConfidenceCalibrationSample]:
    report = validate_confidence_label_set(label_set)
    if not report.calibratable:
        raise ValueError("confidence label set is not calibratable: " + "; ".join(report.errors))
    return [sample for sample in label_set.samples if sample.review_status is ConfidenceLabelReviewStatus.ACCEPTED]


def _sample_id(run_id: str) -> str:
    return f"CAL-{hashlib.sha256(run_id.encode()).hexdigest()[:12].upper()}"


def _stable_hash(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _duplicates(values: Iterable[str]) -> list[str]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


__all__ = [
    "build_confidence_label_set",
    "calibration_samples_from_label_set",
    "load_analysis_runs_for_labeling",
    "load_confidence_label_set",
    "validate_confidence_label_set",
]
