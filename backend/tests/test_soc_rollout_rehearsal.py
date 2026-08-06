from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from soc_agent.cli import main
from soc_agent.contracts import (
    SocRolloutGateId,
    SocRolloutRehearsalRequest,
    SocRolloutStage,
)
from soc_agent.core import (
    SocRolloutRehearsalService,
    load_soc_rollout_rehearsal_report,
    load_soc_rollout_rehearsal_request,
)

FIXTURE = Path(__file__).resolve().parents[1] / "samples" / "rollout" / "pi05a_vendor_neutral_simulation.json"


def _fixture_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _request() -> SocRolloutRehearsalRequest:
    return load_soc_rollout_rehearsal_request(FIXTURE)


def test_rehearsal_exercises_full_virtual_flow_without_real_effects() -> None:
    report = SocRolloutRehearsalService(clock=lambda: datetime(2026, 8, 5, 12, tzinfo=UTC)).rehearse(_request())

    assert report.schema_version == "soc.rollout_rehearsal_report.v1"
    assert report.engineering_rehearsal_passed is True
    assert report.mocked is True
    assert report.current_real_stage is SocRolloutStage.NOT_STARTED
    assert report.scope.cohort_id == "vendor-neutral-simulation-cohort"
    assert len(report.owners) == 5
    assert report.requested_by == "simulation-fixture:pi05a-v1"
    assert report.simulated_final_stage is SocRolloutStage.SHADOW
    assert report.simulated_stage_transition_count == 4
    assert report.real_stage_transition_count == 0
    assert report.external_effect_count == 0
    assert report.stage_transition_allowed is False
    assert report.production_approval_granted is False
    assert report.real_rollout_claim_allowed is False
    assert report.auto_close_allowed is False
    assert report.external_state_mutation_allowed is False
    assert report.high_risk_action_execution_allowed is False
    assert len(report.gate_assessments) == len(SocRolloutGateId)
    assert all(item.observed_reason for item in report.gate_assessments)
    assert all(item.real_gate_satisfied is False for item in report.gate_assessments)
    assert all(item.real_promotion_eligible is False for item in report.stage_assessments)
    assert all(item.blocked_gate_ids for item in report.stage_assessments)
    assert report.rollback.passed is True
    assert report.rollback.external_effect_count == 0
    assert all(item.external_effect_executed is False for item in report.steps)
    for stage in (
        SocRolloutStage.SHADOW,
        SocRolloutStage.LIMITED_PILOT,
        SocRolloutStage.CONTROLLED_ROLLOUT,
    ):
        assessment_index = next(index for index, step in enumerate(report.steps) if step.kind.value == "assess_real_gates" and step.to_stage is stage)
        transition_index = next(index for index, step in enumerate(report.steps) if step.kind.value == "simulate_stage_transition" and step.to_stage is stage)
        assert assessment_index < transition_index


def test_rehearsal_replay_is_semantically_stable_across_generation_time() -> None:
    request = _request()
    first = SocRolloutRehearsalService(clock=lambda: datetime(2026, 8, 5, 12, tzinfo=UTC)).rehearse(request)
    second = SocRolloutRehearsalService(clock=lambda: datetime(2026, 8, 6, 12, tzinfo=UTC)).rehearse(request, baseline=first)

    assert second.generated_at != first.generated_at
    assert second.rehearsal_id == first.rehearsal_id
    assert second.semantic_sha256 == first.semantic_sha256
    assert second.diff is not None
    assert second.diff.changed is False
    assert second.diff.changed_components == []


def test_rehearsal_diff_reports_a_changed_plan_scope() -> None:
    baseline = SocRolloutRehearsalService().rehearse(_request())
    payload = _fixture_payload()
    payload["plan"]["scope"]["maximum_alert_count"] = 51
    changed_request = SocRolloutRehearsalRequest.model_validate(payload)

    report = SocRolloutRehearsalService().rehearse(
        changed_request,
        baseline=baseline,
    )

    assert report.diff is not None
    assert report.diff.changed is True
    assert report.diff.changed_components == ["plan"]
    assert report.rehearsal_id != baseline.rehearsal_id


def test_simulation_evidence_cannot_close_a_real_gate() -> None:
    payload = _fixture_payload()
    payload["plan"]["gates"][0]["status"] = "passed"
    payload["plan"]["gates"][0]["observed_at"] = "2026-08-05T09:00:00+08:00"

    with pytest.raises(ValidationError, match="cannot pass with simulation evidence"):
        SocRolloutRehearsalRequest.model_validate(payload)


def test_plan_cannot_remove_an_owner_or_weaken_a_gate() -> None:
    missing_owner = _fixture_payload()
    missing_owner["plan"]["owners"].pop()
    with pytest.raises(ValidationError, match="at least 5 items"):
        SocRolloutRehearsalRequest.model_validate(missing_owner)

    weakened_gate = _fixture_payload()
    weakened_gate["plan"]["gates"][0]["required_for_stages"] = ["shadow"]
    with pytest.raises(ValidationError, match="cannot weaken its required stages"):
        SocRolloutRehearsalRequest.model_validate(weakened_gate)


def test_plan_requires_the_complete_ordered_rollback_procedure() -> None:
    payload = _fixture_payload()
    payload["plan"]["rollback"]["actions"].pop()

    with pytest.raises(ValidationError, match="at least 6 items"):
        SocRolloutRehearsalRequest.model_validate(payload)


def test_cli_writes_report_and_produces_stable_replay_diff(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "rehearsal.json"
    first_code = main(
        [
            "rollout",
            "rehearse",
            str(FIXTURE),
            "--output",
            str(output_path),
            "--pretty",
        ]
    )
    first_stdout = json.loads(capsys.readouterr().out)
    saved = load_soc_rollout_rehearsal_report(output_path)

    assert first_code == 0
    assert first_stdout["rehearsal_id"] == saved.rehearsal_id
    assert saved.engineering_rehearsal_passed is True

    second_code = main(
        [
            "rollout",
            "rehearse",
            str(FIXTURE),
            "--baseline-json",
            str(output_path),
            "--pretty",
        ]
    )
    replay = json.loads(capsys.readouterr().out)

    assert second_code == 0
    assert replay["diff"]["changed"] is False
    assert replay["real_stage_transition_count"] == 0
    assert replay["external_effect_count"] == 0
