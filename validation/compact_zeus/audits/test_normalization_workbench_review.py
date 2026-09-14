"""Offline validation harness controls, not real extraction-quality evidence."""

import copy
import json

import pytest

from soc_agent.contracts import AlertInput
from soc_agent.llm.analyzer import LLMChatResponse
from soc_agent.llm.normalization import JsonLLMNormalizationReviewer
from validation.compact_zeus.audits.normalization_workbench_review import (
    inspect_frozen_review,
)


class Client:
    def complete(self, messages, *, model_name):
        return LLMChatResponse(
            model_name=model_name,
            content=json.dumps(
                {
                    "objects": [
                        {
                            "id": "f1",
                            "kind": "file",
                            "attributes": {"file_path": "D:/sample.exe"},
                            "source_quote": "sample.exe",
                        }
                    ],
                    "events": [
                        {
                            "kind": "file_detection",
                            "subject_refs": ["f1"],
                            "name": "Family.A",
                            "category": "Trojan",
                            "source_quote": "Trojan",
                        }
                    ],
                }
            ),
        )


def fixture():
    alert = AlertInput(
        alert_id="synthetic",
        source={
            "integration_name": "pingan_legacy_alert_platform",
            "source_type": "edr",
        },
        detection={"detection_key": "synthetic:rule:1"},
        raw={"message": 'file="D:/sample.exe" virus="Trojan"', "nullable": None},
        extensions={
            "evidence_input_policy": {
                "name": "raw_message_first",
                "selected_input_path": "message",
                "selected_layer": "raw_message",
                "trust_level": "high",
            }
        },
    )
    reviewer = JsonLLMNormalizationReviewer(
        client=Client(), model_name="synthetic", mode="shadow"
    )
    request = reviewer.prepare(alert)
    report = reviewer.review(alert, request)
    assert report.status == "shadow"
    run = {
        "run_id": "RUN-synthetic",
        "normalized_alert": alert.model_dump(mode="json"),
        "normalization_assist_request": request.model_dump(mode="json"),
        "normalization_assistance": report.model_dump(mode="json"),
    }
    bundle = {
        "alert_id": "synthetic",
        "run_id": run["run_id"],
        "input_hash": "synthetic-hash",
        "artifacts": [
            {"artifact_id": key, "payload": value}
            for key, value in {
                "decision-lineage": {},
                "output-validation": {},
                "run-manifest": {"run_identity": {}},
            }.items()
        ],
    }
    return run, bundle


def test_complete_model_projection_and_serializable_profile(tmp_path):
    run, bundle = fixture()
    original = copy.deepcopy(run)
    row = inspect_frozen_review(bundle, tmp_path, persisted_run=run)
    assert row["model_input_present"] == 2
    assert row["after_fingerprint"]
    assert row["after_strength"] == ["strong"]
    assert row["raw_preserved"]
    assert run == original
    saved = json.loads((tmp_path / "offline-consumer-projections.json").read_text())
    assert saved["supplemented_v6"]["profile"]


def test_identity_and_frozen_hash_are_verified(tmp_path):
    run, bundle = fixture()
    bundle["run_id"] = "RUN-other"
    with pytest.raises(ValueError, match="identity mismatch"):
        inspect_frozen_review(bundle, tmp_path, persisted_run=run)
    bundle["run_id"] = run["run_id"]
    run["normalization_assistance"]["request_hash"] = "tampered"
    with pytest.raises(ValueError, match="matching request hash"):
        inspect_frozen_review(bundle, tmp_path, persisted_run=run)
