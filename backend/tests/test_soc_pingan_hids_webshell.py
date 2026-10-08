from __future__ import annotations

import pytest

from soc_agent.contracts import ParsedRawMessageEvidence
from soc_agent.normalizers import normalize_alert_payload
from soc_agent.pipeline.field_importance import EvidenceFieldImportanceRegistry

SOURCE = "alert.hitLog[0].zeusRawLogs[0].message"
FILE_PATH = "/srv/example/checkConfig.php"
FILE_MD5 = "a" * 32
FILE_SHA256 = "b" * 64


def _fields(**extra: str) -> dict[str, str]:
    return {
        "event_type": "webshell",
        "datatype": "webshell",
        "event_name": "Web后门",
        "event_level": "danger",
        "event_content": f"主机192.0.2.10存在web后门，文件路径为：{FILE_PATH}",
        "host_name": "synthetic-web",
        "internal_ip": "192.0.2.10",
        "type": "monitor",
        "detail.path": FILE_PATH,
        "detail.md5": FILE_MD5,
        "detail.sha256": FILE_SHA256,
        "detail.uname": "deploy-owner",
        "detail.gname": "deploy-group",
        "detail.website": "example-app",
        "detail.file_type": "php",
        "detail.size": "4.54KB",
        "detail.access_permission": "rwxr-xr-x",
        **extra,
    }


def _alert(*messages: dict[str, str]):
    return normalize_alert_payload({"alert": {"alertId": "synthetic-webshell", "hitLog": [{"topic": "security_qthids", "zeusRawLogs": [{"message": " ".join(f'{key}="{value}"' for key, value in fields.items())} for fields in messages]}]}})


def _gaps(alert):
    parsed = [ParsedRawMessageEvidence.model_validate(item) for item in alert.extensions["parsed_raw_messages"]]
    return EvidenceFieldImportanceRegistry.for_alert(alert).find_gaps(alert, {item.source_path: item for item in parsed})


@pytest.mark.parametrize("mode", ["monitor", "scan"])
def test_webshell_artifact_does_not_invent_an_executing_process(mode: str) -> None:
    alert = _alert(_fields(type=mode))

    assert not any(value for value in alert.entities.process.model_dump().values())
    assert alert.entities.user.username is None
    assert alert.entities.file.file_path == FILE_PATH
    artifact = alert.entities.file.observations[0]
    assert (artifact.file_name, artifact.file_path, artifact.md5, artifact.sha256) == ("checkConfig.php", FILE_PATH, FILE_MD5, FILE_SHA256)
    assert artifact.process_id is None
    detection = alert.entities.detections[0]
    assert detection.kind == "file_detection"
    assert detection.subject_refs == ["entities.file.observations[0]"]
    assert detection.reported_result != "danger"
    assert not _gaps(alert)


def test_webshell_file_fields_cannot_contaminate_a_real_php_process() -> None:
    alert = _alert(_fields(**{"detail.process_name": "php-fpm", "detail.pid": "420", "path": "/usr/sbin/php-fpm", "detail.cmd": "php-fpm --nodaemonize", "uname": "web-process"}))

    assert alert.entities.process.process_name == "php-fpm"
    assert alert.entities.process.process_id == 420
    assert alert.entities.process.process_path == "/usr/sbin/php-fpm"
    assert alert.entities.process.md5 is None
    assert alert.entities.process.sha256 is None
    node = alert.entities.process.observations[0].nodes[0]
    assert node.username == "web-process"
    assert node.md5 is None and node.sha256 is None
    assert alert.entities.file.observations[0].file_path == FILE_PATH
    assert alert.entities.file.observations[0].md5 == FILE_MD5
    assert alert.entities.file.observations[0].process_id is None
    assert alert.entities.detections[0].subject_refs == ["entities.file.observations[0]"]
    sources = alert.entities.process.observations[0].field_sources
    assert sources == {
        "nodes[0].process_name": f"{SOURCE}#parsed.detail.process_name",
        "nodes[0].process_id": f"{SOURCE}#parsed.detail.pid",
        "nodes[0].process_path": f"{SOURCE}#parsed.path",
        "nodes[0].command_line": f"{SOURCE}#parsed.detail.cmd",
        "nodes[0].username": f"{SOURCE}#parsed.uname",
    }


def test_webshell_file_fields_have_exact_provenance_and_owner_binding() -> None:
    alert = _alert(_fields())
    provenance = {item["canonical_path"]: item["selected_from"] for item in alert.extensions["canonical_field_provenance"]}

    for target, source in {"file_name": "detail.path", "file_path": "detail.path", "md5": "detail.md5", "sha256": "detail.sha256"}.items():
        assert provenance[f"entities.file.{target}"] == f"{SOURCE}#parsed.{source}"
        assert provenance[f"entities.file.observations[0].{target}"] == f"{SOURCE}#parsed.{source}"
    owner = next(item for item in alert.entities.supplementary_facts if item.name == "file_owner")
    assert owner.value == "deploy-owner"
    assert owner.subject_ref == "entities.file.observations[0]"
    semantics = alert.extensions["source_field_semantics"]
    assert not any(item["semantic_type"] == "endpoint_process_observation" for item in semantics)
    assert any(item["semantic_type"] == "file_detection_process_context" for item in semantics)


def test_mixed_messages_keep_files_and_detection_refs_in_their_own_source() -> None:
    alert = _alert(
        {"event_type": "web_command", "pname": "bash", "pid": "12", "cmd": "bash example.sh"},
        _fields(),
        _fields(**{"detail.path": "/srv/another/config.php", "detail.sha256": "c" * 64}),
    )

    assert len(alert.entities.process.observations) == 1
    assert [item.file_path for item in alert.entities.file.observations] == [FILE_PATH, "/srv/another/config.php"]
    assert [item.subject_refs for item in alert.entities.detections] == [["entities.file.observations[0]"], ["entities.file.observations[1]"]]
    assert alert.entities.detections[0].event_scope_id.endswith("zeusRawLogs[1].message")
    assert alert.entities.detections[1].event_scope_id.endswith("zeusRawLogs[2].message")
    provenance = {item["canonical_path"]: item["selected_from"] for item in alert.extensions["canonical_field_provenance"]}
    assert provenance["entities.file.file_path"] == "alert.hitLog[0].zeusRawLogs[1].message#parsed.detail.path"
    assert provenance["entities.file.observations[1].sha256"] == "alert.hitLog[0].zeusRawLogs[2].message#parsed.detail.sha256"


def test_real_process_missing_observation_still_reports_a_critical_gap() -> None:
    alert = _alert(_fields(**{"detail.process_name": "php-fpm", "detail.pid": "420"}))
    alert.entities.process.observations = []
    assert any(item.rule_id == "pingan.hids.process_observation" for item in _gaps(alert))


def test_webshell_missing_file_observation_reports_the_file_gap() -> None:
    alert = _alert(_fields())
    alert.entities.file.observations = []
    gaps = _gaps(alert)
    assert any(item.rule_id == "pingan.hids.file_observation" for item in gaps)
    assert not any(item.rule_id == "pingan.hids.process_observation" for item in gaps)


def test_file_description_parentheses_do_not_create_a_process_tree() -> None:
    alert = _alert(_fields(event_content="检测文件 /srv/example/checkConfig.php(420) 存在风险。"))
    assert alert.entities.process.observations == []
    assert alert.entities.process.process_name is None


def test_process_image_detail_path_does_not_require_a_file_observation() -> None:
    alert = _alert({"event_type": "web_command", "detail.pname": "php-fpm", "detail.pid": "420", "detail.path": "/usr/sbin/php-fpm", "detail.md5": FILE_MD5})
    assert alert.entities.process.process_path == "/usr/sbin/php-fpm"
    assert alert.entities.process.md5 == FILE_MD5
    assert not any(item.rule_id == "pingan.hids.file_observation" for item in _gaps(alert))


def test_explicit_indexed_process_retains_its_own_user_and_path() -> None:
    alert = _alert(
        _fields(
            **{
                "detail.process_name": "php-fpm",
                "detail.pid": "420",
                "detail.proctree.0.proc_name": "php-fpm",
                "detail.proctree.0.proc_pid": "420",
                "detail.proctree.0.proc_path": "/usr/sbin/php-fpm",
                "detail.proctree.0.proc_cmd": "php-fpm --nodaemonize",
                "detail.proctree.0.proc_euidUsername": "www",
                "process__file__hashes__md5": "d" * 32,
            }
        )
    )
    assert alert.entities.process.process_name == "php-fpm"
    assert alert.entities.process.process_path == "/usr/sbin/php-fpm"
    assert alert.entities.process.md5 == "d" * 32
    assert alert.entities.process.observations[0].nodes[0].username == "www"
    assert alert.entities.file.md5 == FILE_MD5


def test_file_owner_cannot_supply_a_different_message_process_user_provenance() -> None:
    alert = _alert(_fields(), {"event_type": "web_command", "pname": "bash", "pid": "12", "uname": "deploy-owner"})
    assert alert.entities.user.username == "deploy-owner"
    provenance = {item["canonical_path"]: item["selected_from"] for item in alert.extensions["canonical_field_provenance"]}
    assert provenance["entities.user.username"] == "alert.hitLog[0].zeusRawLogs[1].message#parsed.uname"


def test_explicit_process_path_binds_its_source_without_claiming_a_literal_name() -> None:
    alert = _alert(_fields(**{"process__file__path": "/usr/sbin/php-fpm"}))
    observation = alert.entities.process.observations[0]
    assert observation.nodes[0].process_name == "php-fpm"
    assert observation.field_sources == {"nodes[0].process_path": f"{SOURCE}#parsed.process__file__path"}
