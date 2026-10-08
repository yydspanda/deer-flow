"""File-only detection evidence does not imply a missing execution event."""

from __future__ import annotations

import pytest

from soc_agent.core.runtime import inspect_alert_normalization


def _payload() -> dict:
    return {
        "alert_id": "file-inspection",
        "source": {"source_type": "hids", "source_system": "synthetic-sensor"},
        "detection": {"rule_code": "FILE-1", "rule_name": "File finding"},
        "entities": {
            "file": {"observations": [{"observation_id": "file-1", "evidence_path": "message#file", "relation": "observed_artifact", "file_path": "/srv/web/config.php"}]},
            "detections": [{"observation_id": "finding-1", "evidence_path": "message#finding", "event_scope_id": "message", "kind": "file_detection", "name": "File finding", "subject_refs": ["entities.file.observations[0]"]}],
        },
    }


def test_file_only_detection_does_not_report_missing_process() -> None:
    result = inspect_alert_normalization(_payload())
    assert result.entities.processes == []
    assert "no process entity extracted" not in result.entities.warnings
    assert "process" not in result.extraction_report.missing_entity_kinds


@pytest.mark.parametrize("case", ["no_detection", "unbound_file", "unresolved_process", "mixed_detection", "cross_source_file", "blank_file"])
def test_file_context_does_not_hide_an_expected_process_gap(case: str) -> None:
    payload = _payload()
    entities = payload["entities"]
    if case == "no_detection":
        entities["detections"] = []
    elif case == "unbound_file":
        entities["detections"][0]["subject_refs"] = []
    elif case == "unresolved_process":
        entities["process"] = {"command_line": "php /srv/web/config.php"}
    elif case == "cross_source_file":
        entities["file"]["observations"][0]["evidence_path"] = "other_message#file"
    elif case == "blank_file":
        entities["file"]["observations"][0]["file_path"] = " "
    else:
        entities["detections"].append({"observation_id": "exec-1", "evidence_path": "message#exec", "event_scope_id": "message", "kind": "process_execution", "name": "Execution", "subject_refs": ["entities.process"]})
    result = inspect_alert_normalization(payload)
    assert "no process entity extracted" in result.entities.warnings
    assert "process" in result.extraction_report.missing_entity_kinds
