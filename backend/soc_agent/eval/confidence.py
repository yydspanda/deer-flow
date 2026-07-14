"""Offline confidence calibration for SOC analysis and replay outputs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from soc_agent.contracts import (
    ConfidenceCalibrationBin,
    ConfidenceCalibrationReport,
    ConfidenceCalibrationSample,
    ConfidenceThresholdProfile,
)


def load_confidence_calibration_samples(path: str | Path) -> list[ConfidenceCalibrationSample]:
    """Load either a JSON array or JSONL calibration sample file."""

    sample_path = Path(path)
    try:
        text = sample_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read calibration samples: {exc}") from exc

    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        raw = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                raw.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid calibration JSONL at line {line_number}: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError("calibration sample file must be a JSON array or JSONL objects")
    try:
        samples = TypeAdapter(list[ConfidenceCalibrationSample]).validate_python(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid calibration sample: {exc}") from exc
    if not samples:
        raise ValueError("calibration sample file is empty")
    return samples


def calibrate_confidence(
    samples: Sequence[ConfidenceCalibrationSample],
    *,
    bin_count: int = 10,
    target_accuracy: float = 0.9,
    minimum_samples: int = 30,
    minimum_threshold_samples: int = 10,
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
    verdicts = {sample.actual_verdict for sample in samples}
    if len(verdicts) < 2:
        warnings.append("actual verdicts contain fewer than two classes; calibration is not representative")
    if review_below >= 1.0:
        warnings.append("no confidence threshold met the target accuracy and support requirements")

    profile_payload = {
        "sample_count": len(samples),
        "target_accuracy": target_accuracy,
        "review_below": review_below,
        "bins": [item.model_dump(mode="json") for item in bins],
    }
    profile_hash = hashlib.sha256(json.dumps(profile_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]
    return ConfidenceCalibrationReport(
        sample_count=len(samples),
        accuracy=accuracy,
        brier_score=brier_score,
        expected_calibration_error=expected_calibration_error,
        bins=bins,
        threshold_profile=ConfidenceThresholdProfile(
            profile_version=f"soc-confidence-{profile_hash}",
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


__all__ = ["calibrate_confidence", "load_confidence_calibration_samples"]
