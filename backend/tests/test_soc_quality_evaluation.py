from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from soc_agent.cli import main
from soc_agent.contracts import (
    ConfidenceCalibrationLabelSet,
    ConfidenceCalibrationSample,
    ConfidenceLabelReviewSource,
    ConfidenceLabelReviewStatus,
    Verdict,
)
from soc_agent.eval import (
    DEFAULT_CORRELATION_EVAL_FIXTURE,
    build_confidence_label_corpus_manifest,
    build_soc_quality_evaluation_report,
    load_confidence_label_corpus_manifest,
    load_confidence_label_set,
    load_correlation_eval_fixture,
    run_correlation_eval,
    run_manifest_bound_confidence_calibration,
    run_offline_eval,
    run_scenario_eval,
    verify_confidence_label_corpus_manifest,
)

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"
CONFIDENCE_FIXTURES = Path(__file__).resolve().parents[1] / "samples" / "eval" / "confidence"


def _alert_sample(name: str) -> tuple[str, dict]:
    path = SAMPLES / name
    return str(path), json.loads(path.read_text(encoding="utf-8"))


def _simulation_label_set() -> ConfidenceCalibrationLabelSet:
    reviewed_at = datetime(2026, 8, 5, tzinfo=UTC)
    verdicts = [
        (Verdict.TRUE_POSITIVE, Verdict.TRUE_POSITIVE, 0.88),
        (Verdict.TRUE_POSITIVE, Verdict.FALSE_POSITIVE, 0.72),
        (Verdict.FALSE_POSITIVE, Verdict.FALSE_POSITIVE, 0.64),
        (Verdict.SUSPICIOUS, Verdict.TRUE_POSITIVE, 0.51),
    ]
    return ConfidenceCalibrationLabelSet(
        label_set_id="CLS-PI03B-SIMULATION-V1",
        created_at=reviewed_at,
        samples=[
            ConfidenceCalibrationSample(
                sample_id=f"CLS-SIM-{index}",
                run_id=f"RUN-SIM-{index}",
                alert_id=f"ALERT-SIM-{index}",
                input_hash=f"input-simulation-{index}",
                source_path=f"fixture://pi03b/{index}",
                predicted_verdict=predicted,
                actual_verdict=actual,
                confidence=confidence,
                model_name="simulation-model-v1",
                prompt_version="soc-analysis-simulation-v1",
                pipeline_version="soc-runtime-simulation-v1",
                summary="Synthetic reviewed output used only to exercise calibration code.",
                recommended_action="Keep this result inside offline simulation.",
                review_status=ConfidenceLabelReviewStatus.ACCEPTED,
                review_source=ConfidenceLabelReviewSource.SIMULATION_FIXTURE,
                reviewer_id="simulation-fixture:pi03b-v1",
                reviewed_at=reviewed_at,
                review_reason="Deterministic synthetic label; not analyst ground truth.",
            )
            for index, (predicted, actual, confidence) in enumerate(verdicts, start=1)
        ],
    )


def _simulation_manifest(label_set: ConfidenceCalibrationLabelSet):
    return build_confidence_label_corpus_manifest(
        label_set,
        corpus_version="pi03b-simulation-v1",
        tenant_id="vendor-neutral-simulation",
        environment="local-dev",
        data_class="simulation",
        created_by="simulation-fixture:pi03b-v1",
        rationale="Exercise all PI-03B evaluation paths without claiming real SOC quality.",
        source_refs=["fixture:pi03b:synthetic-labels-v1"],
        created_at=datetime(2026, 8, 5, tzinfo=UTC),
    )


def _quality_report():
    label_set = _simulation_label_set()
    manifest = _simulation_manifest(label_set)
    samples = [
        _alert_sample("approved_scanner.json"),
        _alert_sample("malicious_ioc.json"),
    ]
    confidence = run_manifest_bound_confidence_calibration(
        manifest,
        label_set,
        bin_count=2,
        minimum_samples=4,
        minimum_threshold_samples=1,
    )
    return build_soc_quality_evaluation_report(
        corpus_manifest_id=manifest.manifest_id,
        offline_runtime=run_offline_eval(samples),
        scenario_evaluation=run_scenario_eval(samples),
        correlation_evaluation=run_correlation_eval(load_correlation_eval_fixture(DEFAULT_CORRELATION_EVAL_FIXTURE)),
        confidence_calibration=confidence,
    )


def test_simulation_calibration_is_manifest_bound_and_non_publishable() -> None:
    label_set = _simulation_label_set()
    manifest = _simulation_manifest(label_set)

    report = run_manifest_bound_confidence_calibration(
        manifest,
        label_set,
        bin_count=2,
        minimum_samples=4,
        minimum_threshold_samples=1,
    )

    assert manifest.review_source_counts == {"simulation_fixture": 4}
    assert report.mocked is True
    assert report.evaluation_flow_passed is True
    assert report.real_quality_claim_allowed is False
    assert report.profile_publish_allowed is False
    assert report.automation_allowed is False


def test_tracked_pi03b_simulation_fixture_keeps_exact_manifest_hash() -> None:
    label_set = load_confidence_label_set(CONFIDENCE_FIXTURES / "pi03b_simulation_label_set.json")
    manifest = load_confidence_label_corpus_manifest(CONFIDENCE_FIXTURES / "pi03b_simulation_manifest.json")

    report = verify_confidence_label_corpus_manifest(manifest, label_set)

    assert report.integrity_passed is True
    assert report.review_source_summary_matches is True
    assert manifest.review_source_counts == {"simulation_fixture": 4}


def test_real_corpus_rejects_simulation_fixture_labels() -> None:
    with pytest.raises(ValueError, match="real corpus cannot contain simulation-fixture labels"):
        build_confidence_label_corpus_manifest(
            _simulation_label_set(),
            corpus_version="real-v1",
            tenant_id="tenant-real",
            environment="staging",
            data_class="desensitized_real",
            created_by="analyst-1",
            rationale="Invalid attempt to relabel synthetic truth as real truth.",
            source_refs=["corpus:real-v1"],
        )


def test_quality_report_composes_existing_evaluators_and_replays_stably() -> None:
    first = _quality_report()
    second = build_soc_quality_evaluation_report(
        corpus_manifest_id=first.corpus_manifest_id,
        offline_runtime=run_offline_eval(
            [
                _alert_sample("approved_scanner.json"),
                _alert_sample("malicious_ioc.json"),
            ]
        ),
        scenario_evaluation=run_scenario_eval(
            [
                _alert_sample("approved_scanner.json"),
                _alert_sample("malicious_ioc.json"),
            ],
            baseline=first.scenario_evaluation,
        ),
        correlation_evaluation=run_correlation_eval(
            load_correlation_eval_fixture(DEFAULT_CORRELATION_EVAL_FIXTURE),
            baseline=first.correlation_evaluation,
        ),
        confidence_calibration=first.confidence_calibration,
        baseline=first,
    )

    assert first.engineering_flow_passed is True
    assert set(first.component_statuses.values()) == {"passed"}
    assert first.mocked is True
    assert first.real_quality_claim_allowed is False
    assert first.rollout_allowed is False
    assert second.evaluation_id == first.evaluation_id
    assert second.diff is not None
    assert second.diff.changed is False
    assert second.diff.changed_components == []


def test_cli_quality_runs_complete_simulation_flow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    label_set = _simulation_label_set()
    manifest = _simulation_manifest(label_set)
    label_path = tmp_path / "labels.json"
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "reports" / "quality.json"
    label_path.write_text(label_set.model_dump_json(indent=2), encoding="utf-8")
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    code = main(
        [
            "eval",
            "quality",
            str(SAMPLES),
            "--glob",
            "approved_scanner.json",
            "--label-set",
            str(label_path),
            "--corpus-manifest",
            str(manifest_path),
            "--bins",
            "2",
            "--minimum-samples",
            "4",
            "--minimum-threshold-samples",
            "1",
            "--output",
            str(output_path),
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    saved = json.loads(output_path.read_text(encoding="utf-8"))

    assert code == 0
    assert payload["schema_version"] == "soc.quality_evaluation_report.v1"
    assert payload["engineering_flow_passed"] is True
    assert payload["mocked"] is True
    assert payload["real_quality_claim_allowed"] is False
    assert payload["profile_publish_allowed"] is False
    assert payload["rollout_allowed"] is False
    assert saved == payload
