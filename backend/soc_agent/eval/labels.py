"""Governed human-label bundles for offline SOC confidence calibration."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from soc_agent.contracts import (
    AnalysisRun,
    ConfidenceCalibrationLabelSet,
    ConfidenceCalibrationSample,
    ConfidenceLabelCorpusManifest,
    ConfidenceLabelCorpusVerificationReport,
    ConfidenceLabelReviewStatus,
    ConfidenceLabelSetValidationReport,
    DecisionConfidenceSource,
    SocEvaluationDataClass,
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


def build_confidence_label_corpus_manifest(
    label_set: ConfidenceCalibrationLabelSet,
    *,
    corpus_version: str,
    tenant_id: str,
    environment: str,
    data_class: SocEvaluationDataClass | str,
    created_by: str,
    rationale: str,
    source_refs: Sequence[str],
    supersedes: ConfidenceLabelCorpusManifest | None = None,
    created_at: datetime | None = None,
) -> ConfidenceLabelCorpusManifest:
    """Seal one exact label-set payload without granting production quality claims."""

    resolved_data_class = SocEvaluationDataClass(data_class)
    normalized_source_refs = _nonempty_unique(source_refs, field_name="source_refs")
    validation = validate_confidence_label_set(label_set)
    label_set_sha256 = _label_set_sha256(label_set)
    sample_ids, sample_identity_sha256 = _sample_identity(label_set)
    reviewer_ids = sorted({sample.reviewer_id for sample in label_set.samples if sample.reviewer_id is not None})
    review_source_counts = dict(sorted(Counter(sample.review_source.value for sample in label_set.samples if sample.review_source is not None).items()))
    supersedes_manifest_id = None
    supersedes_manifest_sha256 = None
    if supersedes is not None:
        _validate_supersession(
            supersedes,
            corpus_version=corpus_version,
            tenant_id=tenant_id,
            environment=environment,
            data_class=resolved_data_class,
            label_set_sha256=label_set_sha256,
        )
        supersedes_manifest_id = supersedes.manifest_id
        supersedes_manifest_sha256 = _stable_hash(supersedes.model_dump(mode="json", exclude_none=True))

    identity = {
        "corpus_version": corpus_version.strip(),
        "tenant_id": tenant_id.strip(),
        "environment": environment.strip(),
        "data_class": resolved_data_class.value,
        "created_by": created_by.strip(),
        "rationale": rationale.strip(),
        "source_refs": normalized_source_refs,
        "label_set_id": label_set.label_set_id,
        "label_set_sha256": label_set_sha256,
        "sample_identity_sha256": sample_identity_sha256,
        "review_source_counts": review_source_counts,
        "supersedes_manifest_id": supersedes_manifest_id,
        "supersedes_manifest_sha256": supersedes_manifest_sha256,
    }
    manifest_id = f"CLCM-{_stable_hash(identity)[:12].upper()}"
    return ConfidenceLabelCorpusManifest(
        manifest_id=manifest_id,
        created_at=created_at or datetime.now(UTC),
        corpus_version=corpus_version,
        tenant_id=tenant_id,
        environment=environment,
        data_class=resolved_data_class,
        created_by=created_by,
        rationale=rationale,
        source_refs=normalized_source_refs,
        label_set_id=label_set.label_set_id,
        label_set_sha256=label_set_sha256,
        sample_count=len(label_set.samples),
        sample_ids=sample_ids,
        sample_identity_sha256=sample_identity_sha256,
        accepted_count=validation.accepted_count,
        pending_count=validation.pending_count,
        excluded_count=validation.excluded_count,
        reviewer_ids=reviewer_ids,
        review_source_counts=review_source_counts,
        calibration_input_eligible=validation.calibratable,
        mocked=resolved_data_class is SocEvaluationDataClass.SIMULATION,
        real_quality_claim_allowed=False,
        supersedes_manifest_id=supersedes_manifest_id,
        supersedes_manifest_sha256=supersedes_manifest_sha256,
    )


def load_confidence_label_corpus_manifest(
    path: str | Path,
) -> ConfidenceLabelCorpusManifest:
    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        return ConfidenceLabelCorpusManifest.model_validate(payload)
    except OSError as exc:
        raise ValueError(f"cannot read confidence corpus manifest: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid confidence corpus manifest JSON: {exc}") from exc
    except ValidationError as exc:
        raise ValueError(f"invalid confidence corpus manifest: {exc}") from exc


def verify_confidence_label_corpus_manifest(
    manifest: ConfidenceLabelCorpusManifest,
    label_set: ConfidenceCalibrationLabelSet,
) -> ConfidenceLabelCorpusVerificationReport:
    """Verify immutable payload identity separately from calibration readiness."""

    validation = validate_confidence_label_set(label_set)
    sample_ids, sample_identity_sha256 = _sample_identity(label_set)
    label_set_hash_matches = manifest.label_set_sha256 == _label_set_sha256(label_set)
    sample_identity_matches = manifest.sample_count == len(label_set.samples) and manifest.sample_ids == sample_ids and manifest.sample_identity_sha256 == sample_identity_sha256
    review_summary_matches = (
        manifest.accepted_count == validation.accepted_count and manifest.pending_count == validation.pending_count and manifest.excluded_count == validation.excluded_count and manifest.calibration_input_eligible is validation.calibratable
    )
    current_review_source_counts = dict(sorted(Counter(sample.review_source.value for sample in label_set.samples if sample.review_source is not None).items()))
    review_source_summary_matches = manifest.review_source_counts == current_review_source_counts
    errors: list[str] = []
    if manifest.label_set_id != label_set.label_set_id:
        errors.append("manifest label_set_id does not match the label set")
    if not label_set_hash_matches:
        errors.append("label-set payload hash does not match the sealed manifest")
    if not sample_identity_matches:
        errors.append("sample identities do not match the sealed manifest")
    if not review_summary_matches:
        errors.append("review status summary does not match the sealed manifest")
    if not review_source_summary_matches:
        errors.append("review source summary does not match the sealed manifest")
    warnings = list(validation.warnings)
    if manifest.data_class is SocEvaluationDataClass.SIMULATION:
        warnings.append("simulation corpus may exercise evaluation code but cannot support real quality claims")
    if not validation.calibratable:
        warnings.extend(validation.errors)
    return ConfidenceLabelCorpusVerificationReport(
        manifest_id=manifest.manifest_id,
        label_set_id=label_set.label_set_id,
        data_class=manifest.data_class,
        mocked=manifest.mocked,
        integrity_passed=not errors,
        label_set_hash_matches=label_set_hash_matches,
        sample_identity_matches=sample_identity_matches,
        review_summary_matches=review_summary_matches,
        review_source_summary_matches=review_source_summary_matches,
        calibration_input_eligible=validation.calibratable,
        real_quality_claim_allowed=False,
        errors=errors,
        warnings=_ordered_unique(warnings),
    )


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


def _label_set_sha256(label_set: ConfidenceCalibrationLabelSet) -> str:
    return _stable_hash(label_set.model_dump(mode="json", exclude_none=True))


def _sample_identity(
    label_set: ConfidenceCalibrationLabelSet,
) -> tuple[list[str], str]:
    samples = sorted(label_set.samples, key=lambda sample: sample.sample_id)
    sample_ids = [sample.sample_id for sample in samples]
    identity = [
        {
            "sample_id": sample.sample_id,
            "run_id": sample.run_id,
            "alert_id": sample.alert_id,
            "input_hash": sample.input_hash,
            "review_status": sample.review_status.value,
            "review_source": sample.review_source.value if sample.review_source is not None else None,
            "actual_verdict": (sample.actual_verdict.value if sample.actual_verdict is not None else None),
            "reviewer_id": sample.reviewer_id,
            "reviewed_at": (sample.reviewed_at.isoformat() if sample.reviewed_at is not None else None),
        }
        for sample in samples
    ]
    return sample_ids, _stable_hash(identity)


def _validate_supersession(
    previous: ConfidenceLabelCorpusManifest,
    *,
    corpus_version: str,
    tenant_id: str,
    environment: str,
    data_class: SocEvaluationDataClass,
    label_set_sha256: str,
) -> None:
    if previous.tenant_id != tenant_id.strip():
        raise ValueError("superseding corpus must keep the same tenant_id")
    if previous.environment != environment.strip():
        raise ValueError("superseding corpus must keep the same environment")
    if previous.data_class is not data_class:
        raise ValueError("simulation and real corpora require separate supersession chains")
    if previous.corpus_version == corpus_version.strip():
        raise ValueError("superseding corpus requires a new corpus_version")
    if previous.label_set_sha256 == label_set_sha256:
        raise ValueError("superseding corpus must change the sealed label-set payload")


def _nonempty_unique(
    values: Sequence[str],
    *,
    field_name: str,
) -> list[str]:
    normalized = [value.strip() for value in values]
    if not normalized or any(not value for value in normalized):
        raise ValueError(f"{field_name} must contain non-blank values")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} values must be unique")
    return normalized


def _ordered_unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


__all__ = [
    "build_confidence_label_corpus_manifest",
    "build_confidence_label_set",
    "calibration_samples_from_label_set",
    "load_analysis_runs_for_labeling",
    "load_confidence_label_corpus_manifest",
    "load_confidence_label_set",
    "validate_confidence_label_set",
    "verify_confidence_label_corpus_manifest",
]
