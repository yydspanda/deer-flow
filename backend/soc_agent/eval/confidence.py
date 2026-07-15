"""Offline confidence calibration for SOC analysis and replay outputs."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Sequence

from soc_agent.contracts import (
    ConfidenceCalibrationBin,
    ConfidenceCalibrationReport,
    ConfidenceCalibrationSample,
    ConfidenceLabelReviewStatus,
    ConfidenceThresholdProfile,
)


def calibrate_confidence(
    samples: Sequence[ConfidenceCalibrationSample],
    *,
    bin_count: int = 10,
    target_accuracy: float = 0.9,
    minimum_samples: int = 30,
    minimum_threshold_samples: int = 10,
    label_set_id: str | None = None,
) -> ConfidenceCalibrationReport:
    """Measure confidence reliability and propose a review-only threshold profile."""

    if not samples:
        raise ValueError("at least one calibration sample is required")
    if bin_count < 2 or bin_count > 100:
        raise ValueError("bin_count must be between 2 and 100")
    if not 0.0 < target_accuracy <= 1.0:
        raise ValueError("target_accuracy must be within (0, 1]")
    if minimum_samples < 1 or minimum_threshold_samples < 1:
        raise ValueError("minimum sample counts must be positive")
    if any(sample.review_status is not ConfidenceLabelReviewStatus.ACCEPTED for sample in samples):
        raise ValueError("confidence calibration accepts only analyst-accepted labels")
    if len({sample.input_hash for sample in samples}) != len(samples):
        raise ValueError("confidence calibration samples must have unique input_hash values")

    model_names = {sample.model_name for sample in samples}
    prompt_versions = {sample.prompt_version for sample in samples}
    pipeline_versions = {sample.pipeline_version for sample in samples}
    if len(model_names) != 1 or len(prompt_versions) != 1 or len(pipeline_versions) != 1:
        raise ValueError("confidence calibration samples must use one model, prompt, and pipeline version")
    model_name = next(iter(model_names))
    prompt_version = next(iter(prompt_versions))
    pipeline_version = next(iter(pipeline_versions))
    actual_verdicts = [sample.actual_verdict for sample in samples]
    if any(verdict is None for verdict in actual_verdicts):
        raise ValueError("confidence calibration samples require actual_verdict")

    correctness = [sample.predicted_verdict == sample.actual_verdict for sample in samples]
    accuracy = sum(correctness) / len(samples)
    brier_score = sum((sample.confidence - float(correct)) ** 2 for sample, correct in zip(samples, correctness, strict=True)) / len(samples)
    bins = _calibration_bins(samples, correctness=correctness, bin_count=bin_count)
    expected_calibration_error = sum((item.sample_count / len(samples)) * abs(item.average_confidence - item.empirical_accuracy) for item in bins)
    review_below = _review_threshold(
        samples,
        correctness=correctness,
        target_accuracy=target_accuracy,
        minimum_threshold_samples=minimum_threshold_samples,
    )
    warnings: list[str] = []
    if len(samples) < minimum_samples:
        warnings.append(f"sample count {len(samples)} is below governance minimum {minimum_samples}; threshold is provisional")
    verdicts = set(actual_verdicts)
    if len(verdicts) < 2:
        warnings.append("actual verdicts contain fewer than two classes; calibration is not representative")
    if review_below >= 1.0:
        warnings.append("no confidence threshold met the target accuracy and support requirements")

    dataset_payload = [
        {
            "run_id": sample.run_id,
            "input_hash": sample.input_hash,
            "predicted_verdict": sample.predicted_verdict.value,
            "actual_verdict": sample.actual_verdict.value if sample.actual_verdict is not None else None,
            "confidence": sample.confidence,
            "reviewer_id": sample.reviewer_id,
            "reviewed_at": sample.reviewed_at.isoformat() if sample.reviewed_at is not None else None,
        }
        for sample in sorted(samples, key=lambda item: (item.input_hash, item.run_id))
    ]
    dataset_hash = hashlib.sha256(json.dumps(dataset_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    profile_payload = {
        "label_set_id": label_set_id,
        "dataset_hash": dataset_hash,
        "model_name": model_name,
        "prompt_version": prompt_version,
        "pipeline_version": pipeline_version,
        "sample_count": len(samples),
        "target_accuracy": target_accuracy,
        "review_below": review_below,
        "bins": [item.model_dump(mode="json") for item in bins],
    }
    profile_hash = hashlib.sha256(json.dumps(profile_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]
    return ConfidenceCalibrationReport(
        label_set_id=label_set_id,
        dataset_hash=dataset_hash,
        model_name=model_name,
        prompt_version=prompt_version,
        pipeline_version=pipeline_version,
        sample_count=len(samples),
        actual_verdict_counts=dict(sorted(Counter(verdict.value for verdict in actual_verdicts if verdict is not None).items())),
        accuracy=accuracy,
        brier_score=brier_score,
        expected_calibration_error=expected_calibration_error,
        bins=bins,
        threshold_profile=ConfidenceThresholdProfile(
            profile_version=f"soc-confidence-{profile_hash}",
            label_set_id=label_set_id,
            dataset_hash=dataset_hash,
            model_name=model_name,
            prompt_version=prompt_version,
            pipeline_version=pipeline_version,
            review_below=review_below,
            auto_action_allowed=False,
        ),
        warnings=warnings,
    )


def _calibration_bins(
    samples: Sequence[ConfidenceCalibrationSample],
    *,
    correctness: Sequence[bool],
    bin_count: int,
) -> list[ConfidenceCalibrationBin]:
    width = 1.0 / bin_count
    result: list[ConfidenceCalibrationBin] = []
    for index in range(bin_count):
        lower = index * width
        upper = 1.0 if index == bin_count - 1 else (index + 1) * width
        selected = [(sample, correct) for sample, correct in zip(samples, correctness, strict=True) if lower <= sample.confidence <= upper and (index == bin_count - 1 or sample.confidence < upper)]
        if not selected:
            continue
        result.append(
            ConfidenceCalibrationBin(
                lower_bound=lower,
                upper_bound=upper,
                sample_count=len(selected),
                average_confidence=sum(item.confidence for item, _ in selected) / len(selected),
                empirical_accuracy=sum(correct for _, correct in selected) / len(selected),
            )
        )
    return result


def _review_threshold(
    samples: Sequence[ConfidenceCalibrationSample],
    *,
    correctness: Sequence[bool],
    target_accuracy: float,
    minimum_threshold_samples: int,
) -> float:
    # Prefer the highest supported threshold. Lower thresholds can be promoted
    # only in a later governed release after more representative samples exist.
    candidates = sorted({sample.confidence for sample in samples}, reverse=True)
    for threshold in candidates:
        selected = [correct for sample, correct in zip(samples, correctness, strict=True) if sample.confidence >= threshold]
        if len(selected) >= minimum_threshold_samples and sum(selected) / len(selected) >= target_accuracy:
            return threshold
    return 1.0


__all__ = ["calibrate_confidence"]
