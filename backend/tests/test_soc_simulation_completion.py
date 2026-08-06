from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from soc_agent.cli import main
from soc_agent.contracts import (
    ActorContext,
    EntrySurface,
    ServiceRequestContext,
    SkillFeedbackObservationCreateCommand,
    SkillFeedbackSourceRef,
    SkillFeedbackSourceType,
    SkillImprovementFailureFacet,
    SkillImprovementIngestReport,
    SkillPackageVersionRef,
    SocEvaluationDataClass,
    SocOperationsAvailability,
    SocOperationsKafkaSnapshot,
    SocOperationsMeasurementGap,
    SocOperationsPersistedSnapshot,
    SocOperationsSnapshot,
    SocPersistedOperationsMetrics,
)
from soc_agent.core import (
    SocRolloutRehearsalService,
    SocSkillImprovementService,
    load_soc_rollout_rehearsal_request,
)
from soc_agent.eval import (
    DEFAULT_CORRELATION_EVAL_FIXTURE,
    SocSimulationCompletionComponentId,
    SocSimulationCompletionRequest,
    build_soc_quality_evaluation_report,
    load_confidence_label_corpus_manifest,
    load_confidence_label_set,
    load_correlation_eval_fixture,
    load_soc_simulation_completion_report,
    run_correlation_eval,
    run_manifest_bound_confidence_calibration,
    run_offline_eval,
    run_scenario_eval,
    run_soc_simulation_completion,
)
from soc_agent.skill_improvement import InMemorySkillImprovementRepository

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ALERT_SAMPLES = BACKEND_ROOT / "samples" / "alerts"
CONFIDENCE_SAMPLES = BACKEND_ROOT / "samples" / "eval" / "confidence"
ROLLOUT_SAMPLE = BACKEND_ROOT / "samples" / "rollout" / "pi05a_vendor_neutral_simulation.json"
PACKAGE_HASH = "5b4d67d365ee24b16b22c73f3bf8430cbefea80f0eef95cd007de1664f850431"
GUIDANCE_HASH = "f840ccfc16ce9a799c7fa8065798df8f4d6453d781242c4573c8695590ddcf28"


def _write_json(path: Path, payload: object) -> None:
    if hasattr(payload, "model_dump_json"):
        rendered = payload.model_dump_json(indent=2, exclude_none=True)
    else:
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(f"{rendered}\n", encoding="utf-8")


def _pi01_report() -> dict:
    return {
        "schema_version": "soc.pingan_shadow_acceptance.v2",
        "report_id": "PI01E-TESTSIMULATION50",
        "acceptance_mode": "external_simulation",
        "evidence_class": "simulated",
        "gate_status": "passed",
        "ramp_stage": "50",
        "blocking_failure_ids": [],
        "checks": [{"check_id": "simulation_complete", "status": "passed"}],
        "claims": {
            "automatic_expansion_allowed": False,
            "closes_real_provider_gate": False,
            "external_simulation_passed": True,
            "internal_real_gate_passed": False,
            "model_accuracy_evaluated": False,
            "next_stage_requires_human_review": True,
            "pilot_ready": False,
            "real_provider_evidence": False,
            "technical_shadow_gate_passed": True,
        },
        "configuration": {
            "provider_modes": {"pingan_asset": "fake"},
            "required_result_mode": "mock",
        },
        "inputs": {"secrets_included": False, "selected_count": 50},
        "metrics": {
            "investigation_shadow": {
                "failed_count": 0,
                "missing_evidence_count": 0,
                "mock_result_count": 50,
                "persisted_evidence_count": 50,
                "planned_action_count": 50,
                "provider_invocation_count": 50,
                "real_result_count": 0,
                "unauthorized_side_effect_counts": {
                    "auto_close_allowed": 0,
                    "base_run_mutation": 0,
                    "confirmed_memory_write_allowed": 0,
                    "high_risk_actions_allowed": 0,
                },
            },
            "paired_compatibility": {
                "deterministic_projection_mismatch_count": 0,
                "review_routing_difference_count": 0,
                "shared_item_count": 50,
            },
        },
    }


def _quality_report():
    label_set = load_confidence_label_set(CONFIDENCE_SAMPLES / "pi03b_simulation_label_set.json")
    manifest = load_confidence_label_corpus_manifest(CONFIDENCE_SAMPLES / "pi03b_simulation_manifest.json")
    samples = [
        (str(path), json.loads(path.read_text(encoding="utf-8")))
        for path in (
            ALERT_SAMPLES / "approved_scanner.json",
            ALERT_SAMPLES / "malicious_ioc.json",
        )
    ]
    confidence = run_manifest_bound_confidence_calibration(
        manifest,
        label_set,
        bin_count=2,
        minimum_samples=4,
        minimum_threshold_samples=1,
    )
    first_scenarios = run_scenario_eval(samples)
    first_correlation = run_correlation_eval(load_correlation_eval_fixture(DEFAULT_CORRELATION_EVAL_FIXTURE))
    first = build_soc_quality_evaluation_report(
        corpus_manifest_id=manifest.manifest_id,
        offline_runtime=run_offline_eval(samples),
        scenario_evaluation=first_scenarios,
        correlation_evaluation=first_correlation,
        confidence_calibration=confidence,
    )
    return build_soc_quality_evaluation_report(
        corpus_manifest_id=manifest.manifest_id,
        offline_runtime=run_offline_eval(samples),
        scenario_evaluation=run_scenario_eval(samples, baseline=first_scenarios),
        correlation_evaluation=run_correlation_eval(
            load_correlation_eval_fixture(DEFAULT_CORRELATION_EVAL_FIXTURE),
            baseline=first_correlation,
        ),
        confidence_calibration=confidence,
        baseline=first,
    )


def _skill_reports() -> tuple[SkillImprovementIngestReport, object]:
    service = SocSkillImprovementService(repository=InMemorySkillImprovementRepository())
    context = ServiceRequestContext(
        actor=ActorContext(
            actor_id="pi05b-simulation",
            surface=EntrySurface.TEST,
            roles=["soc_engineer"],
        )
    )
    observed_at = datetime(2026, 8, 5, 1, tzinfo=UTC)
    commands = [
        SkillFeedbackObservationCreateCommand(
            idempotency_key=f"pi05b-simulation-feedback-{index}",
            tenant_id="tenant-simulation",
            data_class=SocEvaluationDataClass.SIMULATION,
            source=SkillFeedbackSourceRef(
                source_type=SkillFeedbackSourceType.SIMULATION_FIXTURE,
                source_id=f"SIM-FEEDBACK-{index}",
                run_id=f"RUN-SIM-{index}",
                alert_id=f"ALERT-SIM-{index}",
                observed_at=observed_at + timedelta(minutes=index),
            ),
            target_skill=SkillPackageVersionRef(
                skill_name="soc-network-apt-triage",
                package_hash=PACKAGE_HASH,
                guidance_hash=GUIDANCE_HASH,
            ),
            scenario_key="reverse_shell",
            failure_facet=SkillImprovementFailureFacet.MANUAL_CHECK_GUIDANCE_INADEQUATE,
            feedback_summary="Repeated simulation feedback identifies one bounded guidance gap.",
            suggested_change="Add one bounded process-owner verification step.",
            representative_sample_ref=f"fixture://pi05b/reverse-shell/{index}",
            replay_set_refs=[f"fixture://pi05b/replay/{index}"],
        )
        for index in range(1, 5)
    ]
    results = [service.ingest_feedback(command, context=context) for command in commands]
    candidate_ids = sorted({result.candidate.candidate_id for result in results if result.candidate is not None})
    ingest = SkillImprovementIngestReport(
        input_count=len(commands),
        simulation_count=len(commands),
        real_feedback_count=0,
        candidate_ids=candidate_ids,
        candidate_count=len(candidate_ids),
        mocked=True,
        results=results,
    )
    return ingest, service.replay_candidate(candidate_ids[0])


def _operations_snapshot() -> SocOperationsSnapshot:
    return SocOperationsSnapshot(
        generated_at=datetime(2026, 8, 5, 12, tzinfo=UTC),
        persisted=SocOperationsPersistedSnapshot(
            availability=SocOperationsAvailability.AVAILABLE,
            backend="sqlite",
            metrics=SocPersistedOperationsMetrics(analysis_run_count=4),
        ),
        kafka=SocOperationsKafkaSnapshot(
            availability=SocOperationsAvailability.NOT_CONFIGURED,
            enabled=False,
            settings_valid=True,
        ),
        measurement_gaps=[
            SocOperationsMeasurementGap(metric=metric, reason="Not measured in local simulation.")
            for metric in (
                "kafka.consumer_lag",
                "model.compute_utilization",
                "production.slo_compliance",
            )
        ],
    )


def _rollout_replay():
    request = load_soc_rollout_rehearsal_request(ROLLOUT_SAMPLE)
    first = SocRolloutRehearsalService(clock=lambda: datetime(2026, 8, 5, 12, tzinfo=UTC)).rehearse(request)
    return SocRolloutRehearsalService(clock=lambda: datetime(2026, 8, 6, 12, tzinfo=UTC)).rehearse(request, baseline=first)


def _completion_request() -> SocSimulationCompletionRequest:
    return SocSimulationCompletionRequest(
        request_id="pi05b-test-v1",
        requested_by="simulation-test",
        rationale="Exercise the complete simulation artifact gate.",
        artifacts={
            "pi01_external_simulation": "pi01.json",
            "pi03_quality_evaluation": "quality.json",
            "pi03_skill_ingest": "skill-ingest.json",
            "pi03_skill_replay": "skill-replay.json",
            "pi04_operations_snapshot": "operations.json",
            "pi05_rollout_rehearsal": "rollout.json",
        },
        confirm_simulation_only=True,
    )


def _write_valid_artifacts(tmp_path: Path) -> SocSimulationCompletionRequest:
    skill_ingest, skill_replay = _skill_reports()
    artifacts = {
        "pi01.json": _pi01_report(),
        "quality.json": _quality_report(),
        "skill-ingest.json": skill_ingest,
        "skill-replay.json": skill_replay,
        "operations.json": _operations_snapshot(),
        "rollout.json": _rollout_replay(),
    }
    for name, payload in artifacts.items():
        _write_json(tmp_path / name, payload)
    return _completion_request()


def test_completion_gate_passes_all_simulation_components_without_real_readiness(
    tmp_path: Path,
) -> None:
    request = _write_valid_artifacts(tmp_path)
    first = run_soc_simulation_completion(
        request,
        artifact_base_dir=tmp_path,
        clock=lambda: datetime(2026, 8, 5, 13, tzinfo=UTC),
    )
    replay = run_soc_simulation_completion(
        request,
        artifact_base_dir=tmp_path,
        baseline=first,
        clock=lambda: datetime(2026, 8, 6, 13, tzinfo=UTC),
    )

    assert first.engineering_completion_gate_passed is True
    assert first.simulation_track_complete is True
    assert len(first.components) == len(SocSimulationCompletionComponentId)
    assert all(item.status == "passed" for item in first.components)
    assert all(item.simulation_provenance_verified for item in first.components)
    assert first.pilot_ready is False
    assert first.production_ready is False
    assert first.real_stage_transition_count == 0
    assert first.external_effect_count == 0
    assert len(first.real_integration_debt) == 7
    assert replay.completion_id == first.completion_id
    assert replay.diff is not None
    assert replay.diff.changed is False
    assert replay.diff.changed_components == []
    assert replay.diff.artifact_bytes_changed_components == []


def test_completion_gate_fails_closed_when_an_artifact_is_missing(tmp_path: Path) -> None:
    request = _write_valid_artifacts(tmp_path)
    (tmp_path / "operations.json").unlink()

    report = run_soc_simulation_completion(request, artifact_base_dir=tmp_path)
    operations = next(item for item in report.components if item.component_id is SocSimulationCompletionComponentId.PI04_OPERATIONS_VISIBILITY)

    assert report.engineering_completion_gate_passed is False
    assert report.pilot_ready is False
    assert operations.status == "failed"
    assert operations.artifacts[0].error_code == "artifact_unreadable"


def test_completion_gate_rejects_a_simulated_real_provider_claim(tmp_path: Path) -> None:
    request = _write_valid_artifacts(tmp_path)
    overclaim = _pi01_report()
    overclaim["claims"]["real_provider_evidence"] = True
    _write_json(tmp_path / "pi01.json", overclaim)

    report = run_soc_simulation_completion(request, artifact_base_dir=tmp_path)
    pi01 = next(item for item in report.components if item.component_id is SocSimulationCompletionComponentId.PI01_EXTERNAL_SIMULATION)

    assert report.simulation_track_complete is False
    assert pi01.status == "failed"
    assert next(item for item in pi01.checks if item.check_id == "pi01.claim_boundary").passed is False
    assert report.real_rollout_claim_allowed is False


def test_cli_writes_completion_report_and_returns_nonzero_for_missing_artifact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = _write_valid_artifacts(tmp_path)
    request_path = tmp_path / "request.json"
    output_path = tmp_path / "reports" / "completion.json"
    _write_json(request_path, request)

    first_code = main(
        [
            "rollout",
            "completion",
            str(request_path),
            "--output",
            str(output_path),
            "--pretty",
        ]
    )
    first_stdout = json.loads(capsys.readouterr().out)
    saved = load_soc_simulation_completion_report(output_path)

    assert first_code == 0
    assert first_stdout["completion_id"] == saved.completion_id
    assert saved.engineering_completion_gate_passed is True

    (tmp_path / "operations.json").unlink()
    failed_code = main(["rollout", "completion", str(request_path)])
    failed = json.loads(capsys.readouterr().out)

    assert failed_code == 1
    assert failed["engineering_completion_gate_passed"] is False
    assert failed["pilot_ready"] is False
