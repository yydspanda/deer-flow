from __future__ import annotations

import json
from pathlib import Path

from validation.compact_zeus.e2e.run_ten_alert_e2e import (
    DEFAULT_CASES,
    PINGAN_POLICY,
    PINGAN_POLICY_SKILL,
    _analysis_output_quality_findings,
    _execution_environment,
    _grounding_quality_findings,
    _model_usage_measurement,
    _role_verification_summary,
    _timing_measurement,
    _role_verification_quality_findings,
    _runtime_step_sequence_complete,
    _runtime_steps_acceptable,
    build_batch_command,
    build_paths,
    load_case_manifest,
    main,
)


def test_case_manifest_selects_ten_complete_alerts_and_excludes_known_gaps() -> None:
    manifest, cases = load_case_manifest(DEFAULT_CASES)

    alert_ids = [case.alert_id for case in cases]
    assert len(alert_ids) == 10
    assert "1965452" not in alert_ids
    assert "1965795" not in alert_ids
    assert {"2025642", "1980502"}.issubset(alert_ids)
    assert {item["alert_id"] for item in manifest["excluded"]} == {
        "1965452",
        "1965795",
    }


def test_batch_command_uses_exact_ids_and_production_service_boundaries(
    tmp_path: Path,
) -> None:
    _manifest, cases = load_case_manifest(DEFAULT_CASES)
    paths = build_paths(tmp_path / "output")
    command = build_batch_command(
        python_executable=Path("/tmp/python"),
        source=tmp_path / "source.pkl",
        paths=paths,
        cases=cases,
        model_name="deepseek-v4-flash",
        execute=True,
        resume=False,
    )

    selected_ids = [
        command[index + 1]
        for index, value in enumerate(command)
        if value == "--alert-id"
    ]
    assert selected_ids == [case.alert_id for case in cases]
    assert "--start-index" not in command
    assert "--limit" not in command
    assert "--persist" in command
    assert "--confirm-live" in command
    assert "--confirm-investigation" in command
    assert "--enrichment-composition" in command
    assert "--enrichment-extensions-config" in command


def test_plan_only_is_read_only_and_exposes_fixed_cohort(
    tmp_path: Path,
    capsys,
) -> None:
    source = tmp_path / "source.pkl"
    source.write_bytes(b"plan-only-does-not-read-source")

    exit_code = main(
        [
            "--source",
            str(source),
            "--output-root",
            str(tmp_path / "output"),
            "--python-executable",
            str(Path(__file__).resolve().parents[3] / "backend/.venv/bin/python"),
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["runtime_live_model_call_count"] == 10
    assert output["role_verifier_enabled"] is False
    assert output["maximum_role_verifier_call_count"] == 0
    assert output["maximum_primary_output_retry_call_count"] == 10
    assert output["maximum_policy_advisor_call_count"] == 10
    assert output["maximum_total_live_model_call_count"] == 30
    assert output["provider_mode"] == "simulated_read_only"
    assert output["alert_ids"][-2:] == ["2025642", "1980502"]
    assert "--plan-only" in output["batch_command"]
    assert output["batch_command"][0].endswith("backend/.venv/bin/python")
    assert not (tmp_path / "output").exists()


def test_plan_can_explicitly_enable_second_pass_role_verifier(
    tmp_path: Path,
    capsys,
) -> None:
    source = tmp_path / "source.pkl"
    source.write_bytes(b"plan-only-does-not-read-source")

    exit_code = main(
        [
            "--source",
            str(source),
            "--output-root",
            str(tmp_path / "output"),
            "--python-executable",
            str(Path(__file__).resolve().parents[3] / "backend/.venv/bin/python"),
            "--role-verifier",
            "enabled",
            "--role-verifier-model",
            "deepseek-v4-pro",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["role_verifier_enabled"] is True
    assert output["role_verifier_model_name"] == "deepseek-v4-pro"
    assert output["role_verifier_minimum_confidence"] == 0.35
    assert output["maximum_role_verifier_call_count"] == 10
    assert output["maximum_role_verifier_output_retry_call_count"] == 10
    assert output["maximum_total_live_model_call_count"] == 50
    assert not (tmp_path / "output").exists()


def test_plan_exposes_pingan_policy_without_synthetic_automation(
    tmp_path: Path,
    capsys,
) -> None:
    source = tmp_path / "source.pkl"
    source.write_bytes(b"plan-only-does-not-read-source")

    exit_code = main(
        [
            "--source",
            str(source),
            "--output-root",
            str(tmp_path / "output"),
            "--python-executable",
            str(Path(__file__).resolve().parents[3] / "backend/.venv/bin/python"),
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["tenant_policy"].endswith("tenant-disposition-v2.json")
    assert output["tenant_policy_skill"].endswith("disposition/SKILL.md")
    assert output["synthetic_automation_policy_installed"] is False
    assert output["real_external_action_call_count"] == 0
    assert not (tmp_path / "output").exists()


def test_execution_environment_enables_pingan_policy_skill_only() -> None:
    python_executable = Path("/tmp/python")
    environment = _execution_environment(python_executable)

    assert environment["SOC_TENANT_POLICY_ENABLED"] == "true"
    assert environment["SOC_TENANT_DISPOSITION_POLICY_PATH"] == str(PINGAN_POLICY)
    assert environment["SOC_TENANT_POLICY_ADVISOR_MODE"] == "llm"
    assert environment["SOC_TENANT_POLICY_SKILL_PATH"] == str(PINGAN_POLICY_SKILL)
    assert "SOC_AUTOMATION_POLICY_PATH" not in environment
    assert environment["SOC_ROLE_VERIFIER_ENABLED"] == "false"
    assert environment["SOC_LLM_OUTPUT_RETRY_ATTEMPTS"] == "1"
    assert "SOC_ROLE_VERIFIER_MODEL" not in environment


def test_execution_environment_can_pin_role_verifier() -> None:
    environment = _execution_environment(
        Path("/tmp/python"),
        role_verifier_enabled=True,
        role_verifier_model_name="deepseek-v4-pro",
        role_verifier_minimum_confidence=0.7,
    )

    assert environment["SOC_ROLE_VERIFIER_ENABLED"] == "true"
    assert environment["SOC_ROLE_VERIFIER_MODEL"] == "deepseek-v4-pro"
    assert environment["SOC_ROLE_VERIFIER_MIN_CONFIDENCE"] == "0.7"


def test_grounding_quality_findings_separate_safety_pass_from_model_quality() -> None:
    assert _grounding_quality_findings(
        {
            "total_count": 18,
            "grounded_count": 0,
            "ungrounded_count": 18,
            "description_leakage_count": 0,
            "reasoning_ungrounded_count": 2,
        }
    ) == [
        "no_analyzer_evidence_grounded",
        "18_analyzer_evidence_items_rejected",
        "2_analysis_reasoning_items_rejected",
    ]


def test_analysis_output_quality_findings_report_only_degradation() -> None:
    assert _analysis_output_quality_findings({"status": "accepted"}) == []
    assert _analysis_output_quality_findings({"status": "repaired"}) == []
    assert _analysis_output_quality_findings(
        {
            "status": "degraded",
            "degraded_sections": ["role_adjudication"],
        }
    ) == ["analysis_output_degraded:role_adjudication"]
    assert _analysis_output_quality_findings({"status": "deterministic_fallback"}) == [
        "analysis_output_used_deterministic_fallback"
    ]


def test_role_verifier_runtime_sequence_accepts_conditional_call() -> None:
    baseline = [
        "normalize",
        "entity_extract",
        "fact_reconstruct",
        "build_analysis_input",
        "skill_context",
        "reference_catalog",
        "analyze_llm",
        "schema_validate",
        "evidence_grounding",
        "decide",
    ]
    assert _runtime_step_sequence_complete(
        baseline,
        role_verifier_enabled=False,
        triggered=False,
    )
    assert _runtime_step_sequence_complete(
        [*baseline[:-1], "role_verification_gate", "decide"],
        role_verifier_enabled=True,
        triggered=False,
    )
    assert _runtime_step_sequence_complete(
        [
            *baseline[:-1],
            "role_verification_gate",
            "verify_roles_llm",
            "decide",
        ],
        role_verifier_enabled=True,
        triggered=True,
    )


def test_optional_role_verifier_failure_is_acceptable_but_reported() -> None:
    steps = [
        {"step_name": "normalize", "status": "success"},
        {
            "step_name": "verify_roles_llm",
            "status": "failed",
            "metadata": {"optional": True, "fail_closed": True},
        },
        {"step_name": "decide", "status": "success"},
    ]

    assert _runtime_steps_acceptable(steps)
    assert _role_verification_quality_findings({"status": "unavailable"}) == [
        "role_verifier_unavailable"
    ]


def test_role_verifier_summary_separates_projected_from_reviewed_claims() -> None:
    base = {
        "pipeline_version": "soc-runtime-v2",
        "role_verification_trigger": {
            "triggered": False,
            "claim_count": 3,
            "reasons": [],
        },
        "steps": [],
        "provider_request_journals": [],
    }

    not_triggered = _role_verification_summary(base)
    assert not_triggered["projected_candidate_claim_count"] == 3
    assert not_triggered["atomic_claim_count"] == 0

    triggered = _role_verification_summary(
        {
            **base,
            "role_verification_trigger": {
                "triggered": True,
                "claim_count": 3,
                "reasons": ["primary_role_unresolved"],
            },
        }
    )
    assert triggered["projected_candidate_claim_count"] == 3
    assert triggered["atomic_claim_count"] == 3


def test_model_usage_measurement_separates_lanes_and_marks_missing_usage() -> None:
    measurement = _model_usage_measurement(
        [
            {
                "alert_id": "alert-1",
                "model_calls": {
                    "primary": {
                        "provider_call_count": 1,
                        "usage": {"input_tokens": 100, "total_tokens": 120},
                    },
                    "tenant_policy_advisor": {
                        "present": True,
                        "provider_call_count": 1,
                        "usage": {"input_tokens": 30, "total_tokens": 35},
                    },
                },
                "role_verification": {
                    "call": {
                        "provider_call_count": 2,
                        "usage": {"input_tokens": 50, "total_tokens": 60},
                    }
                },
            },
            {
                "alert_id": "alert-2",
                "model_calls": {
                    "primary": {
                        "provider_call_count": 1,
                        "usage": {"input_tokens": 90, "total_tokens": 110},
                    },
                    "tenant_policy_advisor": {
                        "present": True,
                        "provider_call_count": 1,
                        "usage": {},
                    },
                },
                "role_verification": {"call": {"provider_call_count": 0, "usage": {}}},
            },
        ]
    )

    assert measurement["status"] == "partial"
    assert measurement["is_lower_bound"] is True
    assert measurement["provider_invocation_count"] == 6
    assert measurement["incomplete_case_count"] == 1
    assert measurement["incomplete_lane_count"] == 1
    assert measurement["incomplete_lanes"] == [
        {"alert_id": "alert-2", "lane": "tenant_policy_advisor"}
    ]
    assert measurement["observed_usage"] == {
        "input_tokens": 270,
        "total_tokens": 325,
    }
    assert measurement["cost_measurement"]["status"] == "not_measured"


def test_timing_measurement_aggregates_steps_and_end_to_end_totals() -> None:
    measurement = _timing_measurement(
        [
            {
                "timing": {
                    "runtime_total_duration_ms": 80,
                    "end_to_end_total_duration_ms": 120.5,
                    "runtime_steps": [
                        {"step_name": "normalize", "duration_ms": 10},
                        {"step_name": "analyze_llm", "duration_ms": 60},
                    ],
                }
            },
            {
                "timing": {
                    "runtime_total_duration_ms": 100,
                    "end_to_end_total_duration_ms": 150.5,
                    "runtime_steps": [
                        {"step_name": "normalize", "duration_ms": 20},
                        {"step_name": "analyze_llm", "duration_ms": 70},
                    ],
                }
            },
        ]
    )

    assert measurement["end_to_end_total_duration_ms"] == 271.0
    assert measurement["end_to_end_average_duration_ms"] == 135.5
    assert measurement["runtime_total_duration_ms"] == 180.0
    assert measurement["runtime_step_duration_totals_ms"] == {
        "analyze_llm": 130.0,
        "normalize": 30.0,
    }
