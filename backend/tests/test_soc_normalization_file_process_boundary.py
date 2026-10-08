"""File findings cannot become execution identities through semantic review."""

import json

import pytest

from soc_agent.contracts import AlertInput
from soc_agent.llm.analyzer import LLMChatResponse
from soc_agent.llm.normalization import JsonLLMNormalizationReviewer, apply_normalization_changes
from soc_agent.normalizers import normalize_alert_payload

FILE_PATH = "/srv/site/checkConfig.php"
FILE_HASH = "a" * 64


class Client:
    def __init__(self, output):
        self.output = output

    def complete(self, messages, *, model_name):
        return LLMChatResponse(content=json.dumps(self.output), model_name=model_name)


def source(*, processes=(), process_source="message", marker=True):
    observations = []
    raw = {"message": f"file={FILE_PATH} sha256={FILE_HASH}"}
    if processes:
        raw[process_source] = raw.get(process_source, "") + " process php-fpm pid=40 user=root"
        observations = [
            {
                "observation_id": "direct-process",
                "evidence_path": process_source + "#parsed",
                "event_scope_id": process_source,
                "nodes": list(processes),
                "field_sources": {f"nodes[{i}].{key}": f"{process_source}#parsed.process[{i}].{key}" for i, node in enumerate(processes) for key in node},
            }
        ]
    return AlertInput(
        alert_id="file-process-boundary",
        raw=raw,
        entities={
            "file": {"observations": [{"observation_id": "detected-file", "evidence_path": "message#parsed.file", "relation": "observed_artifact", "file_path": FILE_PATH, "file_name": "checkConfig.php", "sha256": FILE_HASH}]},
            "process": {"observations": observations},
        },
        extensions={
            "evidence_input_policy": {"name": "raw_message_first", "selected_input_path": "message", "selected_layer": "raw_message", "trust_level": "high"},
            "source_field_semantics": [{"field_path": "message#parsed", "semantic_type": "file_detection_process_context", "meaning": "File detection does not establish execution identity."}] if marker else [],
        },
    )


def process_object(attributes, **kwargs):
    return {"id": "process-proposal", "kind": "process", "attributes": attributes, "source_quote": "model excerpt", **kwargs}


def review(alert, output, *, existing_path=None, omitted_semantics=False):
    reviewer = JsonLLMNormalizationReviewer(client=Client(output), model_name="synthetic")
    request = reviewer.prepare(alert)
    if omitted_semantics:
        request.source_semantics = []
    if existing_path:
        output["objects"][0]["existing_ref"] = next(key for key, item in request.object_catalog.items() if item["path"] == existing_path)
    report = reviewer.review(alert, request)
    return apply_normalization_changes(alert, request, report), report


@pytest.mark.parametrize("attributes", [{"process_name": "checkConfig.php"}, {"process_path": FILE_PATH}, {"process_id": 40, "process_name": "checkConfig.php", "sha256": FILE_HASH}])
def test_file_only_source_rejects_process_objects_and_keeps_file_supplements(attributes):
    alert = source()
    before = alert.model_dump_json()
    updated, report = review(
        alert,
        {
            "objects": [process_object(attributes), {"id": "file-proposal", "kind": "file", "attributes": {"file_path": FILE_PATH, "exists": True}, "source_quote": "model excerpt"}],
            "additional_facts": [{"name": "website", "value": "site", "meaning": "associated site", "source_quote": "model excerpt"}],
        },
        omitted_semantics=True,
    )
    assert updated.entities.process.observations == []
    assert updated.entities.process.process_name is None
    assert updated.entities.file.observations[0].exists is True
    assert updated.entities.supplementary_facts[0].value == "site"
    assert any("file_detection_process_identity_unverified" in issue for issue in report.issues)
    assert alert.model_dump_json() == before


@pytest.mark.parametrize("target,value", [("process_path", FILE_PATH), ("command_line", "php checkConfig.php"), ("process_id", "40")])
def test_file_only_source_rejects_legacy_scalar_process_backfill(target, value):
    updated, report = review(source(), {"facts": [{"target": "entities.process." + target, "value": value, "source_quote": "model excerpt", "reason": "claim"}]})
    assert getattr(updated.entities.process, target) is None
    assert not report.changes
    assert any("file_detection_process_identity_unverified" in issue for issue in report.issues)


@pytest.mark.parametrize("existing_path", [None, "entities.process.observations[0].nodes[0]"])
def test_mixed_file_and_process_evidence_keeps_real_process_and_supplements(existing_path):
    alert = source(processes=[{"process_id": 40, "process_name": "php-fpm", "process_path": "/usr/sbin/php-fpm", "command_line": "php-fpm --nodaemonize"}])
    updated, report = review(
        alert,
        {"objects": [process_object({"process_id": 40, "process_name": "php-fpm", "username": "root", "command_line": "invented"})]},
        existing_path=existing_path,
    )
    assert len(updated.entities.process.observations) == 1
    node = updated.entities.process.observations[0].nodes[0]
    assert node.username == "root"
    assert node.command_line == "php-fpm --nodaemonize"
    assert any("direct_process_fact_conflict" in issue for issue in report.issues)
    assert not any("file_detection_process_identity_unverified" in issue for issue in report.issues)


def test_genuine_process_does_not_authorize_an_additional_file_as_process():
    alert = source(processes=[{"process_id": 40, "process_name": "php-fpm"}])
    updated, report = review(alert, {"objects": [process_object({"process_path": FILE_PATH, "sha256": FILE_HASH})]})
    assert updated.entities.process.observations == alert.entities.process.observations
    assert any("file_detection_process_identity_unverified" in issue for issue in report.issues)


def test_file_identity_cannot_fill_missing_identity_fields_of_a_real_process():
    alert = source(processes=[{"process_id": 40, "process_name": "php-fpm"}])
    updated, report = review(alert, {"objects": [process_object({"process_id": 40, "sha256": FILE_HASH, "process_path": FILE_PATH, "username": "root"})]})
    node = updated.entities.process.observations[0].nodes[0]
    assert node.sha256 is None
    assert node.process_path is None
    assert node.username == "root"
    assert any("file_artifact_is_not_process_identity" in issue for issue in report.issues)


def test_explicit_process_evidence_can_identify_the_same_detected_file():
    alert = source(processes=[{"process_id": 40, "process_name": "checkConfig.php", "process_path": FILE_PATH, "sha256": FILE_HASH}])
    updated, report = review(alert, {"objects": [process_object({"process_id": 40, "process_name": "checkConfig.php", "process_path": FILE_PATH, "sha256": FILE_HASH, "username": "root"})]})
    node = updated.entities.process.observations[0].nodes[0]
    assert node.process_path == FILE_PATH
    assert node.sha256 == FILE_HASH
    assert node.username == "root"
    assert not report.issues


@pytest.mark.parametrize("existing_path", [None, "entities.process.observations[0].nodes[0]"])
def test_real_adapter_explicit_process_accepts_source_bound_semantic_supplement(existing_path):
    fields = {
        "event_type": "webshell",
        "detail.path": FILE_PATH,
        "detail.sha256": FILE_HASH,
        "detail.uname": "deploy-owner",
        "detail.process_name": "php-fpm",
        "detail.pid": "420",
        "path": "/usr/sbin/php-fpm",
        "detail.cmd": "php-fpm --nodaemonize",
        "extra_process_user": "www-data",
    }
    alert = normalize_alert_payload({"alert": {"alertId": "synthetic-mixed-webshell", "hitLog": [{"topic": "security_qthids", "zeusRawLogs": [{"message": json.dumps(fields)}]}]}})
    assert alert.entities.process.observations[0].nodes[0].username is None
    updated, report = review(
        alert,
        {"objects": [{**process_object({"process_id": 420, "process_name": "php-fpm", "username": "www-data"}), "source_quote": '"extra_process_user": "www-data"'}]},
        existing_path=existing_path,
    )
    assert len(updated.entities.process.observations) == 1
    node = updated.entities.process.observations[0].nodes[0]
    assert node.username == "www-data"
    assert node.process_path == "/usr/sbin/php-fpm"
    assert node.command_line == "php-fpm --nodaemonize"
    assert node.sha256 is None
    assert updated.entities.file.observations[0].sha256 == FILE_HASH
    assert any(fact.name == "file_owner" and fact.value == "deploy-owner" and fact.subject_ref == "entities.file.observations[0]" for fact in updated.entities.supplementary_facts)
    assert not report.issues


def test_process_identity_from_another_source_cannot_authorize_backfill():
    alert = source(processes=[{"process_id": 40, "process_name": "php-fpm"}], process_source="other_message")
    updated, report = review(alert, {"objects": [process_object({"process_id": 40, "process_name": "php-fpm", "username": "root"})]})
    assert updated.entities.process.observations == alert.entities.process.observations
    assert any("file_detection_process_identity_unverified" in issue for issue in report.issues)


def test_same_name_processes_require_an_unambiguous_existing_identity():
    alert = source(processes=[{"process_id": 40, "process_name": "php-fpm"}, {"process_id": 41, "process_name": "php-fpm"}])
    updated, report = review(alert, {"objects": [process_object({"process_name": "php-fpm", "command_line": "invented", "sha256": FILE_HASH})]})
    assert updated.entities.process.observations == alert.entities.process.observations
    assert report.issues


def test_sources_without_file_detection_constraint_keep_existing_semantic_behavior():
    updated, report = review(source(marker=False), {"objects": [process_object({"process_name": "php-fpm", "process_id": 40})]})
    assert updated.entities.process.observations[0].nodes[0].process_name == "php-fpm"
    assert not report.issues
