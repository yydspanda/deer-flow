from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from soc_agent.cli import main
from soc_agent.contracts import (
    AlertSourceType,
    EvidenceLayer,
    EvidenceTrustLevel,
    MessageSchemaStatus,
    NestedJsonRepairStatus,
    RoleResolutionStatus,
    SensitiveEvidenceMode,
)
from soc_agent.core import SocAnalysisService, SocNormalizationService
from soc_agent.core.runtime import build_analysis_request_for_payload
from soc_agent.normalizers import normalize_alert_payload
from soc_agent.normalizers.pingan_messages import parse_pingan_raw_message


def _payload(*messages: str, topic: str, topic_name: str, raw_fields: dict | None = None) -> dict:
    return {
        "alert": {
            "alertId": "PINGAN-MESSAGE-001",
            "alertCode": "PIE-MESSAGE-001",
            "alertName": "PingAn parser fixture",
            "riskLevel": "high",
            "createAt": "2026-07-14T10:00:00+08:00",
            "hitLog": [
                {
                    "topic": topic,
                    "topicName": topic_name,
                    "ruleCode": "RPAADM_MESSAGE_001",
                    "ruleName": "Platform rule name",
                    "zeusRawLogs": [{**(raw_fields or {}), "message": message} for message in messages],
                }
            ],
        },
        "relatedAlertList": [],
    }


def test_pingan_apt_message_fields_override_zeus_structured_fields() -> None:
    message = 'skyeye SyslogClient[1]: 2026-07-14 10:00:00|!sensor|!alarm|!{"attack_type":"代码执行","sip":"30.1.1.10","dip":"30.2.2.20","attacker":"30.2.2.20","victim":"30.1.1.10","severity":8}'
    payload = _payload(
        message,
        topic="sec_guard_apt",
        topic_name="SkyEye APT",
        raw_fields={"sip": "198.51.100.10", "dip": "198.51.100.20"},
    )

    alert = normalize_alert_payload(payload)

    assert alert.raw == payload
    assert alert.entities.network.source_ip == "30.1.1.10"
    assert alert.entities.network.destination_ip == "30.2.2.20"
    parsed = alert.extensions["parsed_raw_messages"]
    assert parsed[0]["parser_name"] == "pingan_delimited_json"
    assert parsed[0]["fields"]["sip"] == "30.1.1.10"
    assert "message" not in parsed[0]

    run = SocAnalysisService().analyze(payload)
    assert run.input_payload == payload
    assert run.fact_reconstruction is not None
    resolutions = {item.role: item for item in run.fact_reconstruction.role_resolutions}
    assert resolutions["source"].selected_value == "30.1.1.10"
    assert resolutions["source"].status is RoleResolutionStatus.CONFLICTED
    assert resolutions["impacted_asset"].selected_value == "30.1.1.10"
    provenance = {item.canonical_path: item for item in run.fact_reconstruction.canonical_field_provenance}
    assert provenance["entities.network.source_ip"].selected_from.endswith("message#parsed.sip")
    assert provenance["entities.network.source_ip"].source_layer is EvidenceLayer.RAW_MESSAGE
    assert provenance["entities.network.source_ip"].alternative_values == ["198.51.100.10"]
    assert any(item.conflict_type == "source_candidate_conflict" for item in run.fact_reconstruction.conflict_reports)


def test_pingan_direct_json_message_uses_complete_json_parser_before_partial_kv() -> None:
    fields = {
        "sip": "30.1.1.10",
        "dip": "30.2.2.20",
        "event_type": "alert",
        "alert": {
            "category": "C2 communication",
            "metadata": {"attack_target": ["server"]},
        },
    }
    payload = _payload(
        json.dumps(fields, ensure_ascii=False),
        topic="ptp-nids",
        topic_name="NIDS",
        raw_fields={"sip": "198.51.100.10", "dip": "198.51.100.20"},
    )

    alert = normalize_alert_payload(payload)

    assert alert.source.source_type is AlertSourceType.NIDS
    assert alert.entities.network.source_ip == "30.1.1.10"
    assert alert.entities.network.destination_ip == "30.2.2.20"
    parsed = alert.extensions["parsed_raw_messages"][0]
    assert parsed["parser_name"] == "pingan_json_object"
    assert parsed["fields"] == fields
    assert parsed["header"] == {}
    assert alert.extensions["evidence_input_policy"]["name"] == "raw_message_first"


def test_pingan_prefixed_json_parser_supports_edr_and_threat_intel() -> None:
    cases = [
        (
            "edr-core-xc",
            "信创EDR",
            "<14>Apr  4 18:32:30 guest EDR[123]: adv_threat_log : ",
            {"agent_id": "AGENT-001", "alert_id": "EDR-001", "details0": {"relation": 4}},
            AlertSourceType.EDR,
        ),
        (
            "sec_guard_wb",
            "微步威胁情报",
            "tdpv3-svc Threatbook[123]: ",
            {
                "direction": "outbound",
                "attacker": "30.1.1.10",
                "victim": "30.2.2.20",
                "net": {"src_ip": "30.1.1.10", "dest_ip": "30.2.2.20"},
            },
            AlertSourceType.THREAT_INTEL,
        ),
    ]

    for topic, topic_name, prefix, fields, expected_source_type in cases:
        alert = normalize_alert_payload(
            _payload(
                prefix + json.dumps(fields, ensure_ascii=False),
                topic=topic,
                topic_name=topic_name,
            )
        )
        parsed = alert.extensions["parsed_raw_messages"][0]

        assert alert.source.source_type is expected_source_type
        assert parsed["parser_name"] == "pingan_json_object"
        assert parsed["fields"] == fields
        assert parsed["header"] == {"prefix": prefix.strip()}


def test_pingan_json_parser_rejects_non_object_incomplete_and_trailing_payloads() -> None:
    for message in (
        '[{"sip":"30.1.1.10"}]',
        'prefix {"sip":"30.1.1.10"',
        'prefix {"sip":"30.1.1.10"} trailing',
        f'{"x" * 513}{{"sip":"30.1.1.10"}}',
    ):
        assert parse_pingan_raw_message(message, source_path="alert.hitLog[0].zeusRawLogs[0].message") is None


def test_pingan_no_message_keeps_structured_fallback_for_siem_model_alert() -> None:
    payload = _payload(
        topic="T_GBD_zeus_data",
        topic_name="AI分析模型-数据模型组",
    )
    payload["alert"]["hitLog"][0]["zeusRawLogs"] = [
        {
            "subtype": "suspicious_email",
            "computername": "mail-gateway",
            "rule_name": "Suspicious email model",
            "password": "original-password",
        },
        {
            "subtype": "must-not-enter-primary",
            "password": "second-password",
        },
    ]

    alert = normalize_alert_payload(payload)

    assert alert.source.source_type is AlertSourceType.SIEM
    assert alert.extensions["parsed_raw_messages"] == []
    assert alert.extensions["evidence_input_policy"] == {
        "name": "structured_fallback",
        "primary_input_path": "alert.hitLog[0].zeusRawLogs[0]",
        "selected_input_path": "alert.hitLog[0].zeusRawLogs[0]",
        "supplementary_input_paths": [],
        "selected_layer": "raw_structured",
        "fallback_reason": "raw_message_missing",
        "ignore_processed_fields_for_reasoning": False,
        "trust_level": "high",
    }
    assert alert.raw == payload

    request = build_analysis_request_for_payload(
        payload,
        sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
    )
    assert request.primary_evidence is not None
    assert request.primary_evidence.layer is EvidenceLayer.RAW_STRUCTURED
    assert request.primary_evidence.trust_level is EvidenceTrustLevel.HIGH
    assert request.primary_evidence.sensitive_evidence_mode is SensitiveEvidenceMode.FULL
    assert json.loads(request.primary_evidence.content) == payload["alert"]["hitLog"][0]["zeusRawLogs"][0]
    assert request.supplementary_evidence == []
    assert request.primary_evidence.sanitized_field_paths == []
    assert "alert.hitLog[0].zeusRawLogs[0].password" in request.evidence_coverage.structured_field_paths
    assert "alert.hitLog[0].zeusRawLogs[1].password" not in request.evidence_coverage.structured_field_paths
    assert request.evidence_coverage.llm_sanitized_paths == []

    redacted_request = build_analysis_request_for_payload(payload)
    assert redacted_request.primary_evidence is not None
    assert "original-password" not in redacted_request.primary_evidence.content
    assert "[REDACTED]" in redacted_request.primary_evidence.content


def test_pingan_structured_fallback_is_low_trust_outside_explicit_topic_allowlist() -> None:
    payload = _payload(
        topic="sec_guard_apt",
        topic_name="SkyEye APT",
    )
    payload["alert"]["hitLog"][0]["zeusRawLogs"] = [
        {
            "source_ip": "30.1.1.10",
            "password": "original-password",
        }
    ]

    alert = normalize_alert_payload(payload)
    policy = alert.extensions["evidence_input_policy"]

    assert policy["name"] == "structured_fallback"
    assert policy["trust_level"] == "low"

    request = build_analysis_request_for_payload(
        payload,
        sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
    )
    assert request.primary_evidence is not None
    assert request.primary_evidence.trust_level is EvidenceTrustLevel.LOW
    assert json.loads(request.primary_evidence.content) == payload["alert"]["hitLog"][0]["zeusRawLogs"][0]


def test_pingan_edr_comma_kv_message_populates_canonical_entities() -> None:
    message = (
        "<14>[SourceIP:30.99.16.122][AuditDB.tbl_ud_pe_threat_alert]"
        "str_source_ip=10.43.107.39,str_threat_value=30.162.29.85,"
        "str_title=横向移动,str_suspicious_file=C:\\Windows\\svchost.exe,"
        "str_agent_id=AGENT-001,str_process_short=svchost.exe,"
        "str_cmd=C:\\Windows\\svchost.exe -s RemoteRegistry,"
        "str_user_process=LOCAL SERVICE,str_source_host=HOST-001"
    )
    payload = _payload(message, topic="leagsoft-edr", topic_name="EDR")

    result = SocNormalizationService().inspect(payload)
    alert = result.alert

    assert alert.entities.network.source_ip == "10.43.107.39"
    assert alert.entities.network.destination_ip == "30.162.29.85"
    assert alert.entities.process.process_name == "svchost.exe"
    assert alert.entities.user.username == "LOCAL SERVICE"
    assert alert.entities.host.host_name == "HOST-001"
    assert result.entities.ips == ["10.43.107.39", "30.162.29.85"]
    request = build_analysis_request_for_payload(payload)
    resolutions = {item.role: item for item in request.fact_reconstruction.role_resolutions}
    assert resolutions["source"].selected_value == "10.43.107.39"
    assert resolutions["destination"].selected_value == "30.162.29.85"
    claims = {item.claim_id: item for item in request.fact_reconstruction.role_claims}
    source_claim = claims[resolutions["source"].supporting_claim_ids[0]]
    assert source_claim.source_layer is EvidenceLayer.RAW_MESSAGE


def test_pingan_hids_quoted_kv_message_extracts_host_ip_and_process_tree() -> None:
    message = (
        "2026-07-14T10:00:05+08:00 HOST-SENSOR qtAlert[679] "
        'datatype="web_command" agent_ip="30.232.21.35" host_name="work04" '
        'internal_ip="30.232.21.35" external_ip="1.1.1.1" agent_id="AGENT-HIDS-001" '
        'event_type="web_command" event_name="LinuxWeb命令执行" '
        'event_content="java进程发现异常执行行为，其进程树为：java(3065)-&gt;chattr(3287784)"'
    )
    payload = _payload(message, topic="security_qthids", topic_name="HIDS")

    result = SocNormalizationService().inspect(payload)
    alert = result.alert

    assert alert.entities.host.host_name == "work04"
    assert alert.entities.host.host_id == "AGENT-HIDS-001"
    assert alert.entities.host.ip_addresses == ["30.232.21.35"]
    assert alert.entities.process.process_name == "chattr"
    assert alert.entities.process.parent_process_name == "java"
    assert result.entities.ips == ["30.232.21.35"]
    assert result.entities.processes == ["chattr", "java"]
    assert "work04" in result.entities.hosts
    assert result.normalization_report.missing_fields == []
    request = build_analysis_request_for_payload(payload)
    resolutions = {item.role: item for item in request.fact_reconstruction.role_resolutions}
    assert resolutions["victim"].selected_value == "30.232.21.35"
    assert resolutions["impacted_asset"].selected_value == "30.232.21.35"
    assert any(item.scenario_type == "command_execution" for item in request.fact_reconstruction.scenario_hypotheses)
    assert alert.extensions["source_field_semantics"] == [
        {
            "field_path": "alert.hitLog[0].zeusRawLogs[0].message#parsed.external_ip",
            "semantic_type": "source_placeholder",
            "meaning": "vendor_default_value_not_observed_external_ip",
            "participates_in_entities": False,
            "participates_in_reasoning": False,
        }
    ]
    observation = alert.entities.process.observations[0]
    assert [(node.process_name, node.process_id) for node in observation.nodes] == [
        ("java", 3065),
        ("chattr", 3287784),
    ]


def test_pingan_multiple_messages_are_bounded_as_primary_and_supplementary() -> None:
    first = '2026-07-14T10:00:05+08:00 HOST-1 qtAlert[1] event_type="web_command" internal_ip="30.1.1.10" host_name="host-1"'
    second = '2026-07-14T10:01:05+08:00 HOST-1 qtAlert[2] event_type="web_command" internal_ip="30.1.1.10" host_name="host-1"'
    payload = _payload(first, second, topic="security_qthids", topic_name="HIDS")

    request = build_analysis_request_for_payload(payload)

    assert request.primary_evidence is not None
    assert request.primary_evidence.parser_name == "pingan_quoted_kv"
    assert "30.1.1.10" in request.primary_evidence.content
    assert len(request.supplementary_evidence) == 1
    assert request.supplementary_evidence[0].source_path.endswith("zeusRawLogs[1].message")


def test_supplementary_messages_remain_independent_network_observations() -> None:
    first = 'skyeye|!{"sip":"30.1.1.10","dip":"30.2.2.20","attack_type":"代码执行"}'
    second = 'skyeye|!{"sip":"30.1.1.11","dip":"30.2.2.20","attack_type":"代码执行"}'
    payload = _payload(first, second, topic="sec_guard_apt", topic_name="SkyEye APT")

    request = build_analysis_request_for_payload(payload)
    reconstruction = request.fact_reconstruction
    resolutions = {item.role: item for item in reconstruction.role_resolutions}

    assert resolutions["source"].status is RoleResolutionStatus.OBSERVED
    assert resolutions["source"].selected_value == "30.1.1.10"
    assert not any(item.conflict_type == "source_candidate_conflict" for item in reconstruction.conflict_reports)
    source_claims = [item for item in reconstruction.role_claims if item.role == "source"]
    assert {item.value for item in source_claims} == {"30.1.1.10", "30.1.1.11"}
    assert len({item.observation_scope for item in source_claims}) == 2
    assert [item.source_ip for item in request.canonical_entities.network.observations] == [
        "30.1.1.10",
        "30.1.1.11",
    ]


def test_pingan_host_identity_digest_is_not_a_file_hash_or_network_ioc() -> None:
    message = 'skyeye|!{"sip":"30.1.1.10","dip":"30.2.2.20","attack_sip":"30.1.1.10","host_md5":"HOST-IDENTITY-DIGEST","attack_type":"代码执行"}'
    alert = normalize_alert_payload(_payload(message, topic="sec_guard_apt", topic_name="SkyEye APT"))

    assert alert.entities.file.md5 is None
    assert alert.entities.threat.iocs == []
    semantics = alert.extensions["source_field_semantics"]
    assert semantics[0]["meaning"] == "host_identity_digest_not_file_hash"


def test_relative_http_paths_and_filenames_are_not_extracted_as_domains() -> None:
    message = 'skyeye|!{"sip":"30.1.1.10","dip":"30.2.2.20","host":"app.example.com","_origin":"{\\"uri\\":\\"/news.html?file=fireworks123.php&src=shell.txt\\"}"}'
    result = SocNormalizationService().inspect(_payload(message, topic="sec_guard_apt", topic_name="SkyEye APT"))

    assert result.entities.domains == ["app.example.com"]
    assert not {"news.html", "fireworks123.php", "shell.txt"} & set(result.entities.domains)


def test_unparsed_raw_message_is_preserved_and_does_not_upgrade_fallback_trust() -> None:
    payload = _payload(
        "opaque proprietary message",
        topic="sec_guard_apt",
        topic_name="APT",
        raw_fields={"sip": "30.1.1.10", "dip": "30.2.2.20"},
    )
    original = deepcopy(payload)

    run = SocAnalysisService().analyze(payload)

    assert run.input_payload == original
    assert run.fact_reconstruction is not None
    assert "selected raw message has no deterministic parser output" in run.fact_reconstruction.warnings
    resolutions = {item.role: item for item in run.fact_reconstruction.role_resolutions}
    claims = {item.claim_id: item for item in run.fact_reconstruction.role_claims}
    source_claim = claims[resolutions["source"].supporting_claim_ids[0]]
    assert source_claim.source_layer is EvidenceLayer.RAW_STRUCTURED
    assert source_claim.evidence_trust is EvidenceTrustLevel.MEDIUM
    assert run.llm_analysis_request is not None
    assert run.llm_analysis_request.primary_evidence is not None
    assert run.llm_analysis_request.primary_evidence.content == "opaque proprietary message"
    assert run.llm_analysis_request.primary_evidence.parser_name is None
    assert run.normalization_report is not None
    assert run.normalization_report.message_schemas[0].status is MessageSchemaStatus.UNSUPPORTED
    assert "unsupported message schema" in " ".join(run.normalization_report.warnings)
    assert run.llm_analysis_request.evidence_coverage.message_schemas[0].status is MessageSchemaStatus.UNSUPPORTED


def test_reverse_shell_resolves_network_and_security_roles_without_false_mismatch() -> None:
    message = (
        'skyeye SyslogClient[1]: 2026-07-14 10:00:00|!sensor|!alarm|!{"rule_name":"发现反弹SHELL行为（Linux）",'
        '"detail_info":"发现反弹了一个shell到远程主机上","sip":"30.116.114.150","dip":"30.174.29.44",'
        '"attacker":"30.174.29.44","victim":"30.116.114.150"}'
    )
    payload = _payload(message, topic="sec_guard_apt", topic_name="SkyEye APT")

    run = SocAnalysisService().analyze(payload)

    assert run.fact_reconstruction is not None
    reconstruction = run.fact_reconstruction
    assert any(item.scenario_type == "reverse_connection" for item in reconstruction.scenario_hypotheses)
    resolutions = {item.role: item for item in reconstruction.role_resolutions}
    assert resolutions["source"].selected_value == "30.116.114.150"
    assert resolutions["destination"].selected_value == "30.174.29.44"
    assert resolutions["attacker"].selected_value == "30.174.29.44"
    assert resolutions["victim"].selected_value == "30.116.114.150"
    assert resolutions["impacted_asset"].selected_value == "30.116.114.150"
    conflict_types = {item.conflict_type for item in reconstruction.conflict_reports}
    assert "attacker_source_mismatch" not in conflict_types
    assert "victim_destination_mismatch" not in conflict_types
    assert "reverse_connection_attacker_destination_mismatch" not in conflict_types
    assert "reverse_connection_victim_source_mismatch" not in conflict_types
    assert resolutions["impacted_asset"].automation_allowed is False


def test_delimited_json_parser_decodes_supported_nested_json_and_http_headers() -> None:
    message = (
        'skyeye|!{"sip":"30.1.1.10","dip":"30.2.2.20","rule_labels":"{\\"kind\\":\\"web\\"}",'
        '"payload":{"req_body":"{\\"username\\":\\"alice\\",\\"password\\":\\"secret\\"}",'
        '"rsp_body":"{\\"token\\":\\"secret-token\\",\\"ok\\":true}",'
        '"req_header":"GET / HTTP/1.1\\r\\nHost: app.example.com\\r\\nUser-Agent: TestAgent/1.0\\r\\n'
        'X-Forwarded-For: 198.51.100.1, 10.0.0.2\\r\\nCookie: sid=secret\\r\\n\\r\\n"}}'
    )
    alert = normalize_alert_payload(_payload(message, topic="sec_guard_apt", topic_name="SkyEye APT"))

    parsed = alert.extensions["parsed_raw_messages"][0]
    assert parsed["fields"]["rule_labels"] == '{"kind":"web"}'
    assert parsed["decoded_fields"]["rule_labels"] == {"kind": "web"}
    assert parsed["decoded_fields"]["payload"]["req_body"]["password"] == "secret"
    assert parsed["decoded_fields"]["payload"]["rsp_body"]["token"] == "secret-token"
    request_header = parsed["decoded_fields"]["payload"]["req_header"]
    assert request_header["forwarded_chain"] == ["198.51.100.1", "10.0.0.2"]
    assert request_header["headers"]["cookie"] == ["sid=secret"]
    assert alert.entities.http.user_agent == "TestAgent/1.0"
    assert alert.entities.http.x_forwarded_for == "198.51.100.1"

    request = build_analysis_request_for_payload(alert.model_dump(mode="json"))
    assert request.primary_evidence is not None
    json.loads(request.primary_evidence.content)
    assert "secret-token" not in request.primary_evidence.content
    assert "[REDACTED]" in request.primary_evidence.content
    assert "decoded_fields" in request.primary_evidence.content
    coverage = request.evidence_coverage
    assert coverage.message_schemas[0].status is MessageSchemaStatus.RECOGNIZED
    assert not coverage.high_value_gaps
    assert any(path.endswith("#parsed.payload.req_header") for path in coverage.llm_sanitized_paths)
    assert set(coverage.llm_projected_paths) == set(request.primary_evidence.projected_field_paths)
    assert not set(request.primary_evidence.omitted_field_paths) & set(request.primary_evidence.projected_field_paths)

    full_request = build_analysis_request_for_payload(
        alert.model_dump(mode="json"),
        sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
    )
    assert full_request.primary_evidence is not None
    assert "secret-token" in full_request.primary_evidence.content
    assert full_request.primary_evidence.sanitized_field_paths == []


def test_deferred_related_and_soar_context_is_explicit_in_coverage() -> None:
    payload = _payload(
        'skyeye|!{"sip":"30.1.1.10","dip":"30.2.2.20","attack_type":"代码执行"}',
        topic="sec_guard_apt",
        topic_name="SkyEye APT",
    )
    payload["relatedAlertList"] = [{"alertId": "RELATED-1"}]
    payload["alert"]["soar"] = [{"displayName": "asset lookup", "data": {"data": []}}]

    request = build_analysis_request_for_payload(payload)
    omissions = {(item.field_path, item.reason) for item in request.evidence_coverage.omissions}

    assert ("raw.relatedAlertList", "external_context_deferred_to_investigation") in omissions
    assert ("raw.alert.soar", "external_context_deferred_to_investigation") in omissions


def test_malformed_nested_bodies_use_accepted_repair_or_sanitized_string_fallback() -> None:
    request_body = '{"domain":"guanbi","******","******","vendor":"pingan_ad"}'
    response_body = '{"uIdToken":"secret-token-without-closing-quote'
    # Build valid outer JSON while deliberately retaining malformed JSON strings inside it.
    message = "skyeye|!" + json.dumps(
        {
            "sip": "30.1.1.10",
            "dip": "30.2.2.20",
            "payload": {"req_body": request_body, "rsp_body": response_body},
        }
    )
    payload = _payload(message, topic="sec_guard_apt", topic_name="SkyEye APT")

    alert = normalize_alert_payload(payload)
    parsed = alert.extensions["parsed_raw_messages"][0]
    assert parsed["fields"]["payload"]["req_body"] == request_body
    assert parsed["fields"]["payload"]["rsp_body"] == response_body
    assert "payload" not in parsed["decoded_fields"]
    assert "req_body" not in parsed["repaired_fields"].get("payload", {})
    assert parsed["repaired_fields"]["payload"]["rsp_body"]["uIdToken"] == "secret-token-without-closing-quote"
    observations = {item["field_path"]: item for item in parsed["repair_observations"]}
    assert observations["payload.req_body"]["status"] == NestedJsonRepairStatus.REJECTED
    assert observations["payload.rsp_body"]["status"] == NestedJsonRepairStatus.ACCEPTED
    assert "nested JSON decode failed: payload.req_body" in parsed["warnings"]
    assert "nested JSON decode failed: payload.rsp_body" in parsed["warnings"]
    assert "nested JSON repair rejected: payload.req_body" in parsed["warnings"]
    assert "nested JSON repair accepted: payload.rsp_body" in parsed["warnings"]

    analysis_request = build_analysis_request_for_payload(payload)
    assert analysis_request.primary_evidence is not None
    content = analysis_request.primary_evidence.content
    assert "guanbi" in content
    assert "pingan_ad" in content
    assert "uIdToken" in content
    assert "secret-token-without-closing-quote" not in content
    assert "[REDACTED]" in content
    bounded = json.loads(content)
    assert bounded["fields"]["payload"]["rsp_body"] == "[SEE repaired_fields]"
    assert "guanbi" in bounded["fields"]["payload"]["req_body"]
    assert bounded["repaired_fields"]["payload"]["rsp_body"]["uIdToken"] == "[REDACTED]"
    coverage = analysis_request.evidence_coverage
    assert coverage.message_schemas[0].status is MessageSchemaStatus.DEGRADED
    assert any(path.endswith("#parsed.payload.req_body") for path in coverage.llm_projected_paths)
    assert any(path.endswith("#repaired.payload.rsp_body.uIdToken") for path in coverage.repaired_field_paths)
    assert any(item.reason == "sanitized_string_fallback" for item in coverage.omissions)
    assert any(item.reason == "replaced_by_repaired_projection" for item in coverage.omissions)
    assert "degraded message schema" in " ".join(coverage.warnings)


def test_recoverable_nested_json_is_exposed_as_repaired_projection() -> None:
    request_body = '{"username":"alice","enabled":true,}'
    message = "skyeye|!" + json.dumps(
        {
            "sip": "30.1.1.10",
            "dip": "30.2.2.20",
            "payload": {"req_body": request_body},
        }
    )
    payload = _payload(message, topic="sec_guard_apt", topic_name="SkyEye APT")

    request = build_analysis_request_for_payload(payload)
    assert request.primary_evidence is not None
    bounded = json.loads(request.primary_evidence.content)
    assert bounded["fields"]["payload"]["req_body"] == "[SEE repaired_fields]"
    assert bounded["repaired_fields"]["payload"]["req_body"] == {
        "enabled": True,
        "username": "alice",
    }
    observation = bounded["repair_observations"][0]
    assert observation["field_path"] == "payload.req_body"
    assert observation["status"] == NestedJsonRepairStatus.ACCEPTED


def test_nested_json_repair_enforces_field_specific_root_type() -> None:
    message = "skyeye|!" + json.dumps(
        {
            "sip": "30.1.1.10",
            "dip": "30.2.2.20",
            "rule_labels": '["web",]',
        }
    )

    alert = normalize_alert_payload(_payload(message, topic="sec_guard_apt", topic_name="SkyEye APT"))
    parsed = alert.extensions["parsed_raw_messages"][0]
    observation = parsed["repair_observations"][0]

    assert observation["field_path"] == "rule_labels"
    assert observation["status"] == NestedJsonRepairStatus.REJECTED
    assert "root type is not allowed" in observation["reason"]
    assert "rule_labels" not in parsed["repaired_fields"]


def test_normalization_drift_flags_message_fingerprint_not_in_accepted_baseline() -> None:
    baseline_payload = _payload(
        'skyeye|!{"sip":"30.1.1.10","dip":"30.2.2.20","attack_type":"恶意外联"}',
        topic="sec_guard_apt",
        topic_name="SkyEye APT",
    )
    baseline_report = SocNormalizationService().drift([("baseline.json", baseline_payload)])
    accepted_fingerprints = set(baseline_report.schema_fingerprint_counts)
    assert accepted_fingerprints
    assert baseline_report.schema_baseline_applied is False

    changed_payload = _payload(
        'skyeye|!{"sip":"30.1.1.10","dip":"30.2.2.20","attack_type":"恶意外联","new_vendor_context":{"campaign":"test"}}',
        topic="sec_guard_apt",
        topic_name="SkyEye APT",
    )
    report = SocNormalizationService().drift(
        [("changed.json", changed_payload)],
        known_schema_fingerprints=accepted_fingerprints,
    )

    assert report.schema_baseline_applied is True
    assert report.known_schema_fingerprint_count == len(accepted_fingerprints)
    assert report.novel_schema_fingerprint_counts
    assert report.samples[0].novel_schema_fingerprints
    assert [sample.path for sample in report.suspicious_samples] == ["changed.json"]


def test_cli_normalization_drift_accepts_prior_report_as_schema_baseline(
    tmp_path: Path,
    capsys,
) -> None:
    baseline_payload = _payload(
        'skyeye|!{"sip":"30.1.1.10","dip":"30.2.2.20","attack_type":"恶意外联"}',
        topic="sec_guard_apt",
        topic_name="SkyEye APT",
    )
    baseline_sample = tmp_path / "baseline-sample.json"
    baseline_sample.write_text(json.dumps(baseline_payload, ensure_ascii=False), encoding="utf-8")
    assert main(["normalize", "drift", str(baseline_sample)]) == 0
    baseline_report = tmp_path / "baseline-report.json"
    baseline_report.write_text(capsys.readouterr().out, encoding="utf-8")

    changed_payload = _payload(
        'skyeye|!{"sip":"30.1.1.10","dip":"30.2.2.20","attack_type":"恶意外联","new_vendor_context":{"campaign":"test"}}',
        topic="sec_guard_apt",
        topic_name="SkyEye APT",
    )
    changed_sample = tmp_path / "changed-sample.json"
    changed_sample.write_text(json.dumps(changed_payload, ensure_ascii=False), encoding="utf-8")
    assert (
        main(
            [
                "normalize",
                "drift",
                str(changed_sample),
                "--schema-baseline",
                str(baseline_report),
            ]
        )
        == 0
    )

    report = json.loads(capsys.readouterr().out)
    assert report["schema_baseline_applied"] is True
    assert report["novel_schema_fingerprint_counts"]
    assert report["suspicious_samples"][0]["path"] == str(changed_sample)
