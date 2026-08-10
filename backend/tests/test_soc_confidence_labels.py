from __future__ import annotations

import json
from pathlib import Path

import pytest

from soc_agent.cli import main
from soc_agent.contracts import (
    AnalysisReasoningBasis,
    AnalysisReasoningItem,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunStatus,
    ConfidenceCalibrationLabelSet,
    ConfidenceCalibrationSample,
    ConfidenceLabelCorpusManifest,
    ConfidenceLabelReviewSource,
    ConfidenceLabelReviewStatus,
    Decision,
    DecisionConfidenceSource,
    DecisionEvidenceState,
    DecisionReviewReason,
    EvidenceItem,
    SocEvaluationDataClass,
    Verdict,
)
from soc_agent.eval import (
    build_confidence_label_corpus_manifest,
    build_confidence_label_set,
    calibrate_confidence,
    calibration_samples_from_label_set,
    validate_confidence_label_set,
    verify_confidence_label_corpus_manifest,
)


def _live_run(*, suffix: str = "1", confidence: float = 0.81) -> AnalysisRun:
    analysis = AnalysisResult(
        verdict=Verdict.TRUE_POSITIVE,
        confidence=confidence,
        summary="Bounded evidence indicates malicious activity.",
        evidence=[
            EvidenceItem(
                evidence_ref="E-000000000001",
                source="primary_evidence.content",
                description="Observed IOC",
                value="ioc-1",
            )
        ],
        reasoning=[
            AnalysisReasoningItem(
                reasoning_id="R-01",
                statement="The observed behavior matches the alert context.",
                basis=[AnalysisReasoningBasis.CURRENT_EVIDENCE],
                evidence_refs=["E-000000000001"],
                confidence=confidence,
            )
        ],
        reason="The observed behavior matches the alert context.",
        recommended_action="Review and contain the affected asset.",
    )
    return AnalysisRun(
        run_id=f"RUN-LABEL-{suffix}",
        alert_id=f"alert-{suffix}",
        status=AnalysisRunStatus.NEEDS_REVIEW,
        pipeline_version="soc-runtime-v1",
        model_name="deepseek-v4-pro",
        prompt_version="soc-analysis-v2",
        input_hash=f"input-hash-{suffix}",
        analysis=analysis,
        decision=Decision(
            verdict=analysis.verdict,
            confidence=analysis.confidence,
            confidence_source=DecisionConfidenceSource.LLM_SELF_REPORT,
            evidence_state=DecisionEvidenceState.PARTIAL,
            suggested_action=analysis.recommended_action,
            needs_review=True,
            review_reasons=[DecisionReviewReason.CONFIDENCE_NOT_CALIBRATED],
            reason="Human review is required.",
        ),
    )


def _accepted(sample: ConfidenceCalibrationSample, *, verdict: Verdict, reviewer: str = "analyst-1") -> ConfidenceCalibrationSample:
    payload = sample.model_dump(mode="json")
    payload.update(
        {
            "actual_verdict": verdict.value,
            "review_status": ConfidenceLabelReviewStatus.ACCEPTED.value,
            "review_source": ConfidenceLabelReviewSource.HUMAN_REVIEW.value,
            "reviewer_id": reviewer,
            "reviewed_at": "2026-07-15T00:00:00Z",
            "review_reason": "Reviewed against the source evidence.",
        }
    )
    return ConfidenceCalibrationSample.model_validate(payload)


def test_build_confidence_label_set_keeps_only_bounded_review_context() -> None:
    label_set = build_confidence_label_set([("run-1.json", _live_run())])

    sample = label_set.samples[0]
    assert sample.review_status is ConfidenceLabelReviewStatus.PENDING_REVIEW
    assert sample.actual_verdict is None
    assert sample.model_name == "deepseek-v4-pro"
    assert sample.evidence_ungrounded_count == 1
    assert not hasattr(sample, "input_payload")

    report = validate_confidence_label_set(label_set)
    assert report.calibratable is False
    assert report.pending_count == 1
    assert "require analyst review" in " ".join(report.errors)


def test_calibration_requires_homogeneous_accepted_human_labels() -> None:
    pending = build_confidence_label_set([("run-1.json", _live_run()), ("run-2.json", _live_run(suffix="2", confidence=0.42))])
    label_set = ConfidenceCalibrationLabelSet(
        label_set_id=pending.label_set_id,
        samples=[
            _accepted(pending.samples[0], verdict=Verdict.TRUE_POSITIVE),
            _accepted(pending.samples[1], verdict=Verdict.FALSE_POSITIVE),
        ],
    )

    samples = calibration_samples_from_label_set(label_set)
    report = calibrate_confidence(
        samples,
        label_set_id=label_set.label_set_id,
        bin_count=2,
        minimum_samples=2,
        minimum_threshold_samples=1,
    )

    assert report.label_set_id == label_set.label_set_id
    assert report.dataset_hash == report.threshold_profile.dataset_hash
    assert report.model_name == "deepseek-v4-pro"
    assert report.prompt_version == "soc-analysis-v2"
    assert report.actual_verdict_counts == {"false_positive": 1, "true_positive": 1}
    assert report.threshold_profile.auto_action_allowed is False


def test_label_set_rejects_duplicate_alert_replays_and_mixed_model_scope() -> None:
    pending = build_confidence_label_set([("run-1.json", _live_run()), ("run-2.json", _live_run(suffix="2"))])
    first = _accepted(pending.samples[0], verdict=Verdict.TRUE_POSITIVE)
    second_payload = _accepted(pending.samples[1], verdict=Verdict.FALSE_POSITIVE).model_dump(mode="json")
    second_payload["input_hash"] = first.input_hash
    second_payload["model_name"] = "another-model"
    label_set = ConfidenceCalibrationLabelSet(
        label_set_id=pending.label_set_id,
        samples=[first, ConfidenceCalibrationSample.model_validate(second_payload)],
    )

    report = validate_confidence_label_set(label_set)

    assert report.calibratable is False
    assert any("duplicate input_hash" in error for error in report.errors)
    assert any("mix model_name" in error for error in report.errors)
    with pytest.raises(ValueError, match="not calibratable"):
        calibration_samples_from_label_set(label_set)


def test_cli_prepares_pending_label_set_from_analysis_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_path = tmp_path / "run.json"
    run_path.write_text(_live_run().model_dump_json(indent=2), encoding="utf-8")

    assert main(["eval", "labels", "prepare", str(run_path), "--pretty"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["schema_version"] == "soc.confidence_calibration_label_set.v1"
    assert payload["samples"][0]["review_status"] == "pending_review"


def test_cli_validation_returns_nonzero_until_labels_are_reviewed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    label_set = build_confidence_label_set([("run-1.json", _live_run())])
    label_path = tmp_path / "labels.json"
    label_path.write_text(label_set.model_dump_json(indent=2), encoding="utf-8")

    assert main(["eval", "labels", "validate", str(label_path), "--pretty"]) == 1
    report = json.loads(capsys.readouterr().out)

    assert report["calibratable"] is False
    assert report["pending_count"] == 1


def test_simulation_corpus_is_integrity_valid_but_never_real_quality_evidence() -> None:
    pending = build_confidence_label_set(
        [
            ("run-1.json", _live_run()),
            ("run-2.json", _live_run(suffix="2", confidence=0.42)),
        ]
    )
    label_set = ConfidenceCalibrationLabelSet(
        label_set_id=pending.label_set_id,
        samples=[
            _accepted(pending.samples[0], verdict=Verdict.TRUE_POSITIVE),
            _accepted(pending.samples[1], verdict=Verdict.FALSE_POSITIVE),
        ],
    )

    manifest = build_confidence_label_corpus_manifest(
        label_set,
        corpus_version="pingan-simulation-v1",
        tenant_id="pingan",
        environment="local-dev",
        data_class=SocEvaluationDataClass.SIMULATION,
        created_by="simulation-owner",
        rationale="Exercise the complete label and calibration workflow without claiming production quality.",
        source_refs=["simulation:pi-03a:seed-v1"],
    )
    report = verify_confidence_label_corpus_manifest(manifest, label_set)

    assert manifest.mocked is True
    assert manifest.review_source_counts == {"human_review": 2}
    assert manifest.calibration_input_eligible is True
    assert manifest.real_quality_claim_allowed is False
    assert report.integrity_passed is True
    assert report.calibration_input_eligible is True
    assert report.review_source_summary_matches is True
    assert report.real_quality_claim_allowed is False
    assert any("cannot support real quality claims" in warning for warning in report.warnings)


def test_corpus_verification_detects_label_payload_tampering() -> None:
    label_set = build_confidence_label_set([("run-1.json", _live_run())])
    manifest = build_confidence_label_corpus_manifest(
        label_set,
        corpus_version="simulation-v1",
        tenant_id="pingan",
        environment="local-dev",
        data_class="simulation",
        created_by="simulation-owner",
        rationale="Seal the pending review bundle before analyst changes.",
        source_refs=["batch:runtime-simulation-001"],
    )
    changed_sample = label_set.samples[0].model_copy(update={"summary": "Changed after the corpus was sealed."})
    changed = label_set.model_copy(update={"samples": [changed_sample]})

    report = verify_confidence_label_corpus_manifest(manifest, changed)

    assert report.integrity_passed is False
    assert report.label_set_hash_matches is False
    assert report.sample_identity_matches is True


def test_corpus_supersession_requires_same_scope_and_changed_payload() -> None:
    pending = build_confidence_label_set([("run-1.json", _live_run())])
    first = build_confidence_label_corpus_manifest(
        pending,
        corpus_version="simulation-v1",
        tenant_id="pingan",
        environment="local-dev",
        data_class="simulation",
        created_by="simulation-owner",
        rationale="Initial pending review corpus.",
        source_refs=["simulation:pi-03a:v1"],
    )
    reviewed = pending.model_copy(
        update={
            "samples": [
                _accepted(
                    pending.samples[0],
                    verdict=Verdict.TRUE_POSITIVE,
                )
            ]
        }
    )

    second = build_confidence_label_corpus_manifest(
        reviewed,
        corpus_version="simulation-v2",
        tenant_id="pingan",
        environment="local-dev",
        data_class="simulation",
        created_by="simulation-owner",
        rationale="Supersede the pending corpus after explicit analyst review.",
        source_refs=["simulation:pi-03a:v2"],
        supersedes=first,
    )

    assert second.supersedes_manifest_id == first.manifest_id
    assert second.supersedes_manifest_sha256
    with pytest.raises(ValueError, match="separate supersession chains"):
        build_confidence_label_corpus_manifest(
            reviewed,
            corpus_version="real-v1",
            tenant_id="pingan",
            environment="local-dev",
            data_class="desensitized_real",
            created_by="analyst-owner",
            rationale="A real corpus cannot inherit simulation provenance.",
            source_refs=["approved-corpus:real-v1"],
            supersedes=first,
        )


def test_cli_seals_and_verifies_simulation_corpus(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    label_set = build_confidence_label_set([("run-1.json", _live_run())])
    label_path = tmp_path / "labels.json"
    label_path.write_text(label_set.model_dump_json(indent=2), encoding="utf-8")

    assert (
        main(
            [
                "eval",
                "labels",
                "seal",
                str(label_path),
                "--corpus-version",
                "simulation-v1",
                "--tenant-id",
                "pingan",
                "--environment",
                "local-dev",
                "--data-class",
                "simulation",
                "--created-by",
                "simulation-owner",
                "--rationale",
                "Exercise the manifest boundary with pending fixture labels.",
                "--source-ref",
                "simulation:cli-test",
                "--pretty",
            ]
        )
        == 0
    )
    manifest_payload = json.loads(capsys.readouterr().out)
    manifest = ConfidenceLabelCorpusManifest.model_validate(manifest_payload)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    assert (
        main(
            [
                "eval",
                "labels",
                "verify",
                str(manifest_path),
                str(label_path),
                "--pretty",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["integrity_passed"] is True
    assert report["mocked"] is True
    assert report["real_quality_claim_allowed"] is False
