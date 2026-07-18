from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import generate_soc_context_validation as context_validation
from scripts.generate_soc_normalization_maintenance_validation import _write_runtime_steps
from scripts.generate_soc_runtime_validation_report import _write_hardening_artifact


def test_runtime_validation_generator_writes_input_adapter_contract(tmp_path: Path) -> None:
    source_path = Path("samples/alerts/pingan_legacy_apt.json")
    payload = json.loads(source_path.read_text(encoding="utf-8"))

    _write_runtime_steps(
        [(source_path, payload)],
        validation_root=tmp_path,
    )

    artifact = json.loads((tmp_path / "step-01-input-adapter/pingan_legacy_apt.step-01.json").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / "step-01-input-adapter/manifest.json").read_text(encoding="utf-8"))

    assert artifact["schema_version"] == "soc.runtime_validation.step01.v1"
    assert artifact["adapter"]["name"] == "pingan_platform"
    assert artifact["adapter"]["source"]["source_type"] == "ndr"
    assert artifact["evidence_input_policy"]["name"] == "structured_fallback"
    assert artifact["raw_message_inventory"] == []
    assert manifest["artifact_count"] == 1
    assert manifest["entries"][0]["status"] == "passed"


def test_context_validation_generator_writes_append_only_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        context_validation,
        "_run_shadow_case",
        lambda **kwargs: {
            "source": f"datas/{kwargs['name']}.json",
            "artifact": f"{kwargs['name']}.step-12.json",
            "match_status": "exact",
        },
    )

    context_validation.generate(
        source_dir=tmp_path / "samples",
        output_root=tmp_path / "validation",
    )

    lifecycle = json.loads((tmp_path / "validation/step-11-governed-context/manifest.json").read_text(encoding="utf-8"))
    shadow = json.loads((tmp_path / "validation/step-12-authorization-shadow/manifest.json").read_text(encoding="utf-8"))

    assert lifecycle["status"] == "passed"
    assert lifecycle["assertions"] == {
        "append_only_versions": True,
        "proposed_then_active": True,
        "active_query_returns_latest_only": True,
    }
    assert shadow["status"] == "passed"
    assert shadow["boundary"]["changes_detection_truth"] is False
    assert shadow["boundary"]["authorizes_response_action"] is False


def test_runtime_hardening_passes_only_when_rejected_evidence_forces_safe_review(
    tmp_path: Path,
) -> None:
    run = {
        "run_id": "RUN-TEST",
        "runtime_failure": None,
        "steps": [
            {"step_name": "schema_validate", "status": "success"},
            {"step_name": "evidence_grounding", "status": "success"},
        ],
        "analysis_evidence_grounding": {
            "total_count": 2,
            "grounded_count": 1,
            "ungrounded_count": 1,
            "warnings": ["1 analyzer evidence item could not be grounded"],
        },
        "decision": {
            "verdict": "needs_review",
            "confidence_is_calibrated": False,
            "policy_version": "soc.decision_policy.v2",
            "evidence_state": "degraded",
            "needs_review": True,
            "review_reasons": ["ungrounded_analysis_evidence"],
            "automation_allowed": False,
        },
    }

    status = _write_hardening_artifact(tmp_path, run)
    artifact = json.loads((tmp_path / "step-08-runtime-hardening/apt-1965449.step-08.json").read_text(encoding="utf-8"))

    assert status == "passed"
    assert artifact["analysis_quality_status"] == "degraded"
    assert artifact["assertions"]["ungrounded_evidence_forces_safe_review"] is True

    run["decision"]["automation_allowed"] = True
    assert _write_hardening_artifact(tmp_path, run) == "failed"
