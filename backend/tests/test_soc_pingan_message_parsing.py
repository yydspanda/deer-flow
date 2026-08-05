from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from soc_agent.cli import main
from soc_agent.contracts import (
    AlertSourceType,
    DecisionEvidenceState,
    DecisionReviewReason,
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
from soc_agent.pipeline.analysis_context import project_analysis_context


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


def test_pingan_adapter_preserves_trusted_ingress_tenant() -> None:
    payload = _payload(
        'sensor SyslogClient[1]: 2026-07-14 10:00:00|!sensor|!alarm|!{"sip":"30.1.1.10"}',
        topic="sec_guard_apt",
        topic_name="SkyEye APT",
    )
    payload["tenant_id"] = "pingan"

    alert = normalize_alert_payload(payload)
    request = build_analysis_request_for_payload(payload)

    assert alert.tenant_id == "pingan"
    assert request.tenant_id == "pingan"
    assert alert.raw["tenant_id"] == "pingan"


def test_pingan_apt_parsed_message_excludes_zeus_structured_fields_from_analysis() -> None:
    message = 'skyeye SyslogClient[1]: 2026-07-14 10:00:00|!sensor|!alarm|!{"attack_type":"代码执行","sip":"30.1.1.10","dip":"30.2.2.20","attacker":"30.2.2.20","victim":"30.1.1.10","severity":8}'
    payload = _payload(
        message,
        topic="sec_guard_apt",
        topic_name="SkyEye APT",
        raw_fields={
            "sip": "198.51.100.10",
            "dip": "198.51.100.20",
            "host": "processed-only.example.test",
            "attack_type": "外层加工分类",
        },
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
    assert resolutions["source"].status is RoleResolutionStatus.OBSERVED
    assert resolutions["impacted_asset"].selected_value == "30.1.1.10"
    provenance = {item.canonical_path: item for item in run.fact_reconstruction.canonical_field_provenance}
    assert provenance["entities.network.source_ip"].selected_from.endswith("message#parsed.sip")
    assert provenance["entities.network.source_ip"].source_layer is EvidenceLayer.RAW_MESSAGE
    assert provenance["entities.network.source_ip"].alternative_values == []
    assert alert.entities.http.host is None
    assert alert.classification.category == "代码执行"
    assert not any(item.source_layer is EvidenceLayer.RAW_STRUCTURED for item in run.fact_reconstruction.role_claims)
    assert not any(item.conflict_type == "source_candidate_conflict" for item in run.fact_reconstruction.conflict_reports)
    assert run.llm_analysis_request is not None
    bounded_content = "\n".join(
        item.content
        for item in [
            run.llm_analysis_request.primary_evidence,
            *run.llm_analysis_request.supplementary_evidence,
        ]
        if item is not None
    )
    assert "198.51.100.10" not in bounded_content
    assert "processed-only.example.test" not in bounded_content
    projected_context = json.dumps(
        project_analysis_context(run.llm_analysis_request),
        ensure_ascii=False,
    )
    assert "198.51.100.10" not in projected_context
    assert "processed-only.example.test" not in projected_context
    assert "外层加工分类" not in projected_context


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


def test_pingan_nids_projects_session_http_sensor_and_provenance_without_verdict_inference() -> None:
    fields = {
        "timestamp": "2026-07-14T10:00:00+08:00",
        "sip": "198.51.100.10",
        "sport": "43123",
        "dip": "10.20.30.40",
        "dport": "8080",
        "proto": "TCP",
        "app_proto": "http",
        "direction": "to_server",
        "community_id": "1:test-community",
        "flow_id": 123456,
        "flow": {
            "bytes_toserver": 1400,
            "bytes_toclient": 3200,
            "pkts_toserver": 4,
            "pkts_toclient": 6,
        },
        "payload": ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/" * 5),
        "query": "10.20.30.40",
        "files": [
            {
                "filename": "/cgi-bin/execute",
                "gaps": False,
                "size": 42,
                "state": "CLOSED",
                "stored": False,
                "tx_id": 7,
            }
        ],
        "alert": {
            "action": "allowed",
            "attack_res": "1",
            "category": "代码执行",
            "severity": "4",
            "signature": "Apache HTTP Server 远程命令执行",
            "signature_id": 50002002,
            "source": {"ip": "198.51.100.10", "port": 43123, "zone": "外网"},
            "target": {"ip": "10.20.30.40", "port": 8080, "zone": "内网"},
        },
        "http": {
            "http_method": "POST",
            "hostname": "app.example.internal",
            "url": "/cgi-bin/execute",
            "protocol": "HTTP/1.1",
            "http_port": 8080,
            "status": 200,
            "http_user_agent": "NidsFixture/1.0",
            "http_refer": "https://portal.example.internal/",
            "xff": "203.0.113.8, 10.0.0.2",
            "http_request_body": "command=id",
            "http_response_body": "uid=1000",
        },
    }
    payload = _payload(
        json.dumps(fields, ensure_ascii=False),
        topic="ptp-nids",
        topic_name="NIDS",
    )

    request = build_analysis_request_for_payload(payload)
    alert = normalize_alert_payload(payload)

    assert alert.raw == payload
    assert alert.detection.rule_code == "RPAADM_MESSAGE_001"
    assert alert.detection.rule_name == "Apache HTTP Server 远程命令执行"
    assert alert.detection.rule_category == "代码执行"
    assert alert.classification.severity == "high"
    assert alert.classification.labels["sensor_action"] == "allowed"
    assert alert.classification.labels["sensor_attack_result"] == "1"
    assert alert.classification.labels["sensor_severity"] == "4"

    network = alert.entities.network
    assert (
        network.source_ip,
        network.src_port,
        network.destination_ip,
        network.dst_port,
        network.protocol,
    ) == ("198.51.100.10", 43123, "10.20.30.40", 8080, "TCP")
    assert network.application_protocol == "http"
    assert network.direction == "to_server"
    assert network.domain == "app.example.internal"
    assert network.url == "/cgi-bin/execute"
    assert len(network.observations) == 1
    observation = network.observations[0]
    assert observation.community_id == "1:test-community"
    assert observation.flow_id == 123456
    assert observation.sensor_source_ip == "198.51.100.10"
    assert observation.sensor_source_port == 43123
    assert observation.sensor_target_ip == "10.20.30.40"
    assert observation.sensor_target_port == 8080
    assert observation.sensor_source_zone == "外网"
    assert observation.sensor_target_zone == "内网"
    assert observation.bytes_to_server == 1400
    assert observation.bytes_to_client == 3200
    assert observation.packets_to_server == 4
    assert observation.packets_to_client == 6

    http = alert.entities.http
    assert http.method == "POST"
    assert http.host == "app.example.internal"
    assert http.path == "/cgi-bin/execute"
    assert http.url == "/cgi-bin/execute"
    assert http.protocol == "HTTP/1.1"
    assert http.port == 8080
    assert http.status_code == 200
    assert http.user_agent == "NidsFixture/1.0"
    assert http.referer == "https://portal.example.internal/"
    assert http.x_forwarded_for == "203.0.113.8"
    assert len(http.observations) == 1
    assert http.observations[0].evidence_path.endswith("message#parsed")

    assert any(item.scenario_type == "command_execution" for item in request.fact_reconstruction.scenario_hypotheses)
    provenance = {item.canonical_path: item for item in request.fact_reconstruction.canonical_field_provenance}
    assert provenance["entities.network.src_port"].selected_from.endswith("message#parsed.sport")
    assert provenance["entities.network.application_protocol"].selected_from.endswith("message#parsed.app_proto")
    assert provenance["entities.http.method"].selected_from.endswith("message#parsed.http.http_method")
    assert provenance["detection.rule_name"].selected_from.endswith("message#parsed.alert.signature")
    assert not request.evidence_coverage.high_value_gaps
    assert any(path.endswith("#parsed.http.http_request_body") for path in request.evidence_coverage.parsed_field_paths)
    assert alert.entities.network.domain != fields["query"]
    assert request.primary_evidence is not None
    assert fields["payload"] not in request.primary_evidence.content
    encoded_omission = request.primary_evidence.encoded_span_omissions[0]
    assert f"<ENCODED:base64_like:320:sha256={encoded_omission.sha256[:12]}:OMITTED>" in request.primary_evidence.content
    assert encoded_omission.field_path.endswith("message#parsed.payload")
    assert request.evidence_coverage.llm_compacted_encoded_paths == [request.primary_evidence.encoded_span_omissions[0].field_path]
    prompt_context = project_analysis_context(request)
    assert "encoded_span_omissions" not in prompt_context["evidence"]["primary_evidence"]
    assert prompt_context["evidence"]["coverage"]["compacted_encoded_count"] == 1
    semantics = {item["field_path"].split("#parsed.", 1)[-1]: item for item in alert.extensions["source_field_semantics"]}
    assert semantics["alert.action"]["meaning"] == ("allowed_means_sensor_did_not_block_not_that_the_attack_succeeded")
    assert semantics["alert.attack_res"]["participates_in_reasoning"] is False
    attack_result_path = next(path for path in request.primary_evidence.omitted_field_paths if path.endswith("message#parsed.alert.attack_res"))
    assert request.primary_evidence.omission_reasons[attack_result_path] == ("adapter_excluded_from_reasoning")
    assert semantics["http.status"]["meaning"] == ("response_status_is_not_proof_of_exploit_success")
    assert semantics["query"]["meaning"] == ("query_is_not_dns_without_explicit_protocol_evidence")
    assert semantics["files"]["meaning"] == ("transaction_file_metadata_is_not_proof_of_endpoint_file_write")
    assert alert.entities.file.file_name is None
    assert alert.entities.file.file_path is None

    changed_sensor_outcome = deepcopy(payload)
    changed_fields = deepcopy(fields)
    changed_fields["alert"]["action"] = "blocked"
    changed_fields["alert"]["attack_res"] = "0"
    changed_fields["http"]["status"] = 500
    changed_sensor_outcome["alert"]["hitLog"][0]["zeusRawLogs"][0]["message"] = json.dumps(changed_fields, ensure_ascii=False)
    baseline_run = SocAnalysisService().analyze(payload)
    changed_run = SocAnalysisService().analyze(changed_sensor_outcome)
    assert baseline_run.analysis.verdict == changed_run.analysis.verdict


def test_pingan_nids_http_header_array_is_a_canonical_fallback() -> None:
    fields = {
        "sip": "198.51.100.10",
        "sport": "43123",
        "dip": "10.20.30.40",
        "dport": "8080",
        "proto": "TCP",
        "alert": {
            "category": "命令注入",
            "signature": "通用命令执行 linux id",
            "source": {"ip": "10.20.30.40", "port": 8080},
            "target": {"ip": "198.51.100.10", "port": 43123},
        },
        "http": {
            "request_headers": [
                {"name": "Host", "value": "header.example.internal"},
                {"name": "User-Agent", "value": "HeaderAgent/2.0"},
                {
                    "name": "X-Forwarded-For",
                    "value": "203.0.113.9, 10.0.0.3",
                },
            ]
        },
    }

    request = build_analysis_request_for_payload(
        _payload(
            json.dumps(fields, ensure_ascii=False),
            topic="ptp-nids",
            topic_name="NIDS",
        )
    )

    assert request.canonical_entities.http.host == "header.example.internal"
    assert request.canonical_entities.http.user_agent == "HeaderAgent/2.0"
    assert request.canonical_entities.http.x_forwarded_for == "203.0.113.9"
    assert request.canonical_entities.network.source_ip == "198.51.100.10"
    assert request.canonical_entities.network.observations[0].sensor_source_ip == "10.20.30.40"
    provenance = {item.canonical_path: item for item in request.fact_reconstruction.canonical_field_provenance}
    assert provenance["entities.http.host"].selected_from.endswith("message#parsed.http.request_headers[0].value")
    assert provenance["entities.http.user_agent"].selected_from.endswith("message#parsed.http.request_headers[1].value")
    assert provenance["entities.http.x_forwarded_for"].selected_from.endswith("message#parsed.http.request_headers[2].value")


def test_pingan_nids_json_header_string_is_decoded_redacted_and_mapped() -> None:
    fields = {
        "sip": "198.51.100.10",
        "sport": "43123",
        "dip": "10.20.30.40",
        "dport": "8080",
        "proto": "TCP",
        "alert": {
            "category": "命令注入",
            "signature": "通用命令执行 linux id",
        },
        "request_header_str": json.dumps(
            {
                "Host": "decoded.example.internal",
                "User-Agent": "DecodedAgent/3.0",
                "X-Forwarded-For": "203.0.113.10, 10.0.0.4",
                "Cookie": "sid=must-not-enter-redacted-context",
            }
        ),
        "response_header_str": json.dumps(
            {
                "Content-Type": "text/plain",
                "Set-Cookie": "session=must-not-enter-redacted-context",
            }
        ),
    }
    payload = _payload(
        json.dumps(fields, ensure_ascii=False),
        topic="ptp-nids",
        topic_name="NIDS",
    )

    alert = normalize_alert_payload(payload)
    parsed = alert.extensions["parsed_raw_messages"][0]
    assert parsed["decoded_fields"]["request_header_str"]["Host"] == ("decoded.example.internal")
    assert parsed["decoded_fields"]["response_header_str"]["Content-Type"] == ("text/plain")
    assert alert.entities.http.host == "decoded.example.internal"
    assert alert.entities.http.user_agent == "DecodedAgent/3.0"
    assert alert.entities.http.x_forwarded_for == "203.0.113.10"

    request = build_analysis_request_for_payload(payload)
    assert request.primary_evidence is not None
    bounded = json.loads(request.primary_evidence.content)
    assert bounded["fields"]["request_header_str"] == "[SEE decoded_fields]"
    assert bounded["fields"]["response_header_str"] == "[SEE decoded_fields]"
    assert bounded["decoded_fields"]["request_header_str"]["Cookie"] == "[REDACTED]"
    assert bounded["decoded_fields"]["response_header_str"]["Set-Cookie"] == "[REDACTED]"
    assert "must-not-enter-redacted-context" not in request.primary_evidence.content
    provenance = {item.canonical_path: item for item in request.fact_reconstruction.canonical_field_provenance}
    assert provenance["entities.http.host"].selected_from.endswith("message#decoded.request_header_str.Host")
    assert any(path.endswith("#decoded.request_header_str.Host") for path in request.evidence_coverage.decoded_field_paths)
    assert any(item.field_path.endswith("#parsed.request_header_str") and item.reason == "replaced_by_decoded_projection" for item in request.evidence_coverage.omissions)


def test_pingan_nids_multiple_messages_remain_independent_http_sessions() -> None:
    first = {
        "sip": "198.51.100.10",
        "sport": "43123",
        "dip": "10.20.30.40",
        "dport": "8080",
        "proto": "TCP",
        "app_proto": "http",
        "alert": {"category": "代码执行", "signature": "Web RCE"},
        "http": {
            "http_method": "POST",
            "hostname": "app.example.internal",
            "url": "/first",
        },
    }
    second = {
        **first,
        "sport": "43124",
        "http": {
            "http_method": "GET",
            "hostname": "app.example.internal",
            "url": "/second",
        },
    }
    request = build_analysis_request_for_payload(
        _payload(
            json.dumps(first, ensure_ascii=False),
            json.dumps(second, ensure_ascii=False),
            topic="ptp-nids",
            topic_name="NIDS",
        )
    )

    assert request.canonical_entities.network.src_port == 43123
    assert [item.src_port for item in request.canonical_entities.network.observations] == [43123, 43124]
    assert request.canonical_entities.http.path == "/first"
    assert [item.path for item in request.canonical_entities.http.observations] == ["/first", "/second"]
    assert len(request.supplementary_evidence) == 1
    canonical_paths = {item.canonical_path for item in request.fact_reconstruction.canonical_field_provenance}
    assert "entities.network.observations[1].src_port" in canonical_paths
    assert "entities.http.observations[1].path" in canonical_paths


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


def test_pingan_threat_intel_separates_wire_roles_and_provider_roles() -> None:
    fields = {
        "timeStr": "2026-04-01T12:21:32+08:00",
        "direction": "out",
        "machine": "172.28.253.5",
        "external_ip": "30.198.71.231",
        "attacker": "30.198.71.231",
        "victim": "172.28.253.5",
        "is_black_ip": False,
        "net": {
            "src_ip": "172.28.253.5",
            "dest_ip": "30.198.71.231",
            "src_port": 6449,
            "dest_port": 80,
            "proto": "TCP",
            "type": "http",
        },
        "assets": {
            "ip": "172.16.0.0/12",
            "section": "服务器",
        },
        "threat": {
            "id": "provider-record-id",
            "severity": 3,
            "phase": "control",
            "level": "attack",
            "type": "mining",
            "name": "CoinMiner挖矿木马",
            "ioc": "provider-record-id-is-not-an-ioc",
            "tag": ["Mitre: T1496: Resource Hijacking"],
            "msg": "检测到门罗币挖矿登录操作。",
            "result": "success",
        },
    }
    payload = _payload(
        "tdpv3-svc Threatbook[123]: " + json.dumps(fields, ensure_ascii=False),
        topic="sec_guard_wb",
        topic_name="微步威胁情报",
        raw_fields={
            "external_ip": "198.51.100.8",
            "net_src_ip": "198.51.100.9",
        },
    )

    request = build_analysis_request_for_payload(payload)
    alert = normalize_alert_payload(payload)

    assert alert.entities.network.source_ip == "172.28.253.5"
    assert alert.entities.network.destination_ip == "30.198.71.231"
    assert alert.entities.network.src_port == 6449
    assert alert.entities.network.dst_port == 80
    assert alert.entities.network.application_protocol == "http"
    assert len(alert.entities.network.observations) == 1
    assert alert.entities.host.ip_addresses == ["172.28.253.5"]
    assert "172.16.0.0/12" not in alert.entities.host.ip_addresses
    assert alert.entities.threat.iocs == ["30.198.71.231"]
    assert alert.entities.threat.malware_family == "CoinMiner挖矿木马"
    assert alert.classification.severity == "3"
    assert alert.classification.category == "mining"
    assert alert.classification.technique == ["T1496"]

    claims = request.fact_reconstruction.role_claims
    by_role = {role: [item for item in claims if item.role == role] for role in {item.role for item in claims}}
    assert by_role["source"][0].value == "172.28.253.5"
    assert by_role["source"][0].claim_type.value == "observation"
    assert by_role["attacker"][0].value == "30.198.71.231"
    assert by_role["attacker"][0].claim_type.value == "vendor_assertion"
    assert by_role["victim"][0].value == "172.28.253.5"
    assert all(item.source_layer is EvidenceLayer.RAW_MESSAGE for item in claims)

    semantics = {item["semantic_type"]: item for item in alert.extensions["source_field_semantics"]}
    assert semantics["asset_scope_expression"]["participates_in_entities"] is False
    assert semantics["upstream_reputation_assertion"]["participates_in_reasoning"] is True
    assert "not proof" in semantics["provider_detection_result"]["meaning"]
    provenance = {item.canonical_path: item for item in request.fact_reconstruction.canonical_field_provenance}
    assert provenance["entities.network.source_ip"].selected_from.endswith("message#parsed.net.src_ip")
    assert provenance["entities.threat.iocs[0]"].selected_from.endswith("message#parsed.external_ip")
    assert request.evidence_coverage.high_value_gaps == []


def test_pingan_siem_suspicious_email_projects_typed_email_without_actor_inference() -> None:
    payload = _payload(
        topic="T_GBD_zeus_data",
        topic_name="AI分析模型-数据模型组",
    )
    payload["alert"]["hitLog"][0]["zeusRawLogs"] = [
        {
            "subtype": "suspicious_email",
            "email_id": "email-001",
            "modeltime": "2026-04-01 08:28:41",
            "User": "system",
            "from": '["Sender@QQ.COM"]',
            "to": '["analyst@pingan.com.cn"]',
            "cc": "[]",
            "subject": "平安人员信息",
            "url": '["https://example.test/login"]',
            "attachment": '{"平安银行.xlsx": "c168ea293c92389e66ee0ad3dd1ddcf0"}',
            "Phishing_type": "class_5",
            "llm_score": "80分",
            "llm_ans": '["upstream model narrative"]',
            "text": "mail body remains bounded evidence",
        }
    ]

    alert = normalize_alert_payload(payload)
    request = build_analysis_request_for_payload(payload)

    assert alert.entities.email is not None
    assert alert.entities.email.message_id == "email-001"
    assert alert.entities.email.sender_addresses == ["Sender@QQ.COM"]
    assert alert.entities.email.recipient_addresses == ["analyst@pingan.com.cn"]
    assert alert.entities.email.subject == "平安人员信息"
    assert alert.entities.email.links == ["https://example.test/login"]
    assert alert.entities.email.attachment_names == ["平安银行.xlsx"]
    assert alert.entities.user.username is None
    assert request.extracted_entities.emails == [
        "sender@qq.com",
        "analyst@pingan.com.cn",
    ]
    assert request.extracted_entities.domains == [
        "qq.com",
        "pingan.com.cn",
        "example.test",
    ]
    assert request.extracted_entities.urls == ["https://example.test/login"]
    assert request.fact_reconstruction.role_claims == []

    semantics = {item["semantic_type"]: item for item in alert.extensions["source_field_semantics"]}
    assert semantics["pipeline_service_identity"]["participates_in_entities"] is False
    assert semantics["upstream_model_narrative"]["participates_in_reasoning"] is True
    assert "not calibrated" in semantics["upstream_uncalibrated_model_score"]["meaning"]
    provenance = {item.canonical_path: item for item in request.fact_reconstruction.canonical_field_provenance}
    assert provenance["entities.email.sender_addresses[0]"].source_layer is EvidenceLayer.RAW_STRUCTURED
    assert provenance["entities.email.sender_addresses[0]"].trust_level is EvidenceTrustLevel.HIGH


def test_pingan_siem_machine_copy_projects_host_candidates_without_network_direction() -> None:
    payload = _payload(
        topic="T_GBD_zeus_data",
        topic_name="AI分析模型-数据模型组",
    )
    payload["alert"]["hitLog"][0]["zeusRawLogs"] = [
        {
            "subtype": "standard_machine_copy",
            "modeltime": "2026-04-01 03:18:07",
            "computername": "PBNJ-D0174",
            "agg_ip": "['10.121.176.162', '10.121.49.87']",
            "winlogbeat_event_data_ipaddress": "10.121.49.87",
            "if_cross": "交叉",
            "sorted_timestamp_str": "['2026-03-31T00:28:57.226Z']",
        },
        {
            "subtype": "standard_machine_copy",
            "computername": "PBNJ-D0174",
            "agg_ip": "['10.121.176.162', '10.121.49.87']",
            "winlogbeat_event_data_ipaddress": "10.121.176.162",
        },
    ]

    alert = normalize_alert_payload(payload)
    request = build_analysis_request_for_payload(payload)

    assert alert.entities.host.host_name == "PBNJ-D0174"
    assert alert.entities.host.ip_addresses == ["10.121.176.162", "10.121.49.87"]
    assert alert.entities.network.source_ip is None
    assert alert.entities.network.destination_ip is None
    assert alert.entities.network.observations == []
    assert request.extracted_entities.hosts == ["PBNJ-D0174"]
    assert set(request.extracted_entities.ips) == {
        "10.121.176.162",
        "10.121.49.87",
    }
    impacted_claims = [item for item in request.fact_reconstruction.role_claims if item.role == "impacted_asset"]
    assert len(impacted_claims) == 1
    assert {item.value for item in impacted_claims} == {"PBNJ-D0174"}
    assert all(item.evidence_trust is EvidenceTrustLevel.HIGH for item in impacted_claims)
    assert not {item.role for item in request.fact_reconstruction.role_claims} & {"source", "destination", "attacker", "victim"}


def test_pingan_unknown_siem_subtype_keeps_evidence_without_guessing_entities() -> None:
    payload = _payload(
        topic="T_GBD_zeus_data",
        topic_name="AI分析模型-数据模型组",
    )
    payload["alert"]["hitLog"][0]["zeusRawLogs"] = [
        {
            "subtype": "new_unmapped_model",
            "computername": "must-not-map",
            "from": '["must-not-map@example.test"]',
        }
    ]

    alert = normalize_alert_payload(payload)
    request = build_analysis_request_for_payload(payload)

    assert alert.entities.email is None
    assert alert.entities.host.host_name is None
    assert alert.entities.network.source_ip is None
    assert request.fact_reconstruction.role_claims == []
    assert request.primary_evidence is not None
    assert "new_unmapped_model" in request.primary_evidence.content
    assert {(item.rule_id, item.expected_target) for item in request.evidence_coverage.high_value_gaps} == {
        (
            "pingan.siem.email.sender",
            "entities.email.sender_addresses",
        ),
        (
            "pingan.siem.machine.host",
            "entities.host.host_name",
        ),
    }
    assert alert.extensions["source_field_semantics"] == [
        {
            "field_path": "alert.hitLog[0].zeusRawLogs[0].subtype",
            "semantic_type": "unsupported_siem_subtype",
            "meaning": "unknown SIEM subtype remains bounded source evidence; the adapter does not infer entities or roles",
            "participates_in_entities": False,
            "participates_in_reasoning": True,
        }
    ]


def test_pingan_edr_nested_mitre_aliases_do_not_leak_to_other_source_types() -> None:
    fields = {
        "details0": {
            "attck_id": "TA0003,T1053.005",
            "process_mame": "not-an-endpoint-process.exe",
        }
    }

    alert = normalize_alert_payload(
        _payload(
            json.dumps(fields),
            topic="sec_guard_wb",
            topic_name="Threat Intel",
        )
    )

    assert alert.source.source_type is AlertSourceType.THREAT_INTEL
    assert alert.classification.tactic == []
    assert alert.classification.technique == []
    assert alert.entities.process.process_name is None


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
    activity_id = "a" * 32
    message = (
        "<14>[SourceIP:30.99.16.122][AuditDB.tbl_ud_pe_threat_alert]"
        f"str_source_ip=10.43.107.39,str_attack_ip=30.162.29.85,"
        f"str_threat_value={activity_id},str_activity_id={activity_id},"
        "str_title=横向移动,str_suspicious_file=C:\\Windows\\svchost.exe,"
        "str_agent_id=AGENT-001,str_process_short=svchost.exe,"
        "str_cmd=C:\\Windows\\svchost.exe -s RemoteRegistry,"
        "str_user_process=LOCAL SERVICE,str_source_host=HOST-001"
    )
    payload = _payload(
        message,
        topic="leagsoft-edr",
        topic_name="EDR",
        raw_fields={"device__ip": "198.51.100.10"},
    )

    result = SocNormalizationService().inspect(payload)
    alert = result.alert

    assert alert.entities.network.source_ip is None
    assert alert.entities.network.destination_ip is None
    assert alert.entities.network.observations == []
    assert alert.entities.process.process_name == "svchost.exe"
    assert alert.entities.user.username == "LOCAL SERVICE"
    assert alert.entities.host.host_name == "HOST-001"
    assert alert.entities.host.ip_addresses == ["10.43.107.39"]
    assert alert.entities.threat.iocs == ["30.162.29.85"]
    assert len(alert.entities.process.observations) == 1
    flat_process = alert.entities.process.observations[0]
    assert flat_process.evidence_path.endswith("message#parsed")
    assert flat_process.nodes[0].process_name == "svchost.exe"
    assert flat_process.nodes[0].process_path == "C:\\Windows\\svchost.exe"
    assert flat_process.nodes[0].command_line == "C:\\Windows\\svchost.exe -s RemoteRegistry"
    assert flat_process.nodes[0].username == "LOCAL SERVICE"
    assert result.entities.ips == ["10.43.107.39", "30.162.29.85"]
    assert activity_id.upper() not in {item.value for item in result.entities.mentions}

    request = build_analysis_request_for_payload(payload)
    resolutions = {item.role: item for item in request.fact_reconstruction.role_resolutions}
    assert resolutions["source"].status is RoleResolutionStatus.UNRESOLVED
    assert resolutions["destination"].status is RoleResolutionStatus.UNRESOLVED
    assert resolutions["attacker"].status is RoleResolutionStatus.TENTATIVE
    assert resolutions["attacker"].selected_value == "30.162.29.85"
    assert resolutions["impacted_asset"].selected_value == "10.43.107.39"
    assert resolutions["impacted_asset"].status is RoleResolutionStatus.TENTATIVE

    claims = request.fact_reconstruction.role_claims
    attacker_claim = next(item for item in claims if item.role == "attacker")
    assert attacker_claim.source_layer is EvidenceLayer.RAW_MESSAGE
    assert attacker_claim.evidence_path.endswith("message#parsed.str_attack_ip")
    assert not any(item.role in {"source", "destination"} for item in claims)
    assert not any(item.evidence_path.endswith((".str_threat_value", ".str_activity_id")) for item in claims)

    provenance = {item.canonical_path: item for item in request.fact_reconstruction.canonical_field_provenance}
    assert "entities.network.source_ip" not in provenance
    assert "entities.network.destination_ip" not in provenance
    assert provenance["entities.host.ip_addresses[0]"].selected_from.endswith("message#parsed.str_source_ip")
    assert provenance["entities.threat.iocs[0]"].selected_from.endswith("message#parsed.str_attack_ip")

    semantics = {item["semantic_type"] for item in alert.extensions["source_field_semantics"]}
    assert {
        "endpoint_identity",
        "polymorphic_vendor_threat_value",
        "vendor_activity_identifier",
        "vendor_attack_ip_assertion",
    } <= semantics


def test_pingan_edr_self_attack_alias_and_digest_shaped_values_do_not_invent_network_roles() -> None:
    digest_shaped_id = "b" * 32
    message = (
        "<14>[SourceIP:30.99.16.122][AuditDB.tbl_ud_pe_threat_alert]"
        "str_source_ip=10.181.175.69,str_attack_ip=10.181.175.69,"
        f"str_threat_value={digest_shaped_id},str_activity_id={digest_shaped_id},"
        "str_title=GalaxyLab_T1003-SAM-Dumping,str_source_host=HOST-SAM-001"
    )

    result = SocNormalizationService().inspect(_payload(message, topic="leagsoft-edr", topic_name="EDR"))
    alert = result.alert

    assert alert.entities.network.source_ip is None
    assert alert.entities.network.destination_ip is None
    assert alert.entities.threat.iocs == []
    assert result.entities.ips == ["10.181.175.69"]
    assert digest_shaped_id.upper() not in {item.value for item in result.entities.mentions}

    request = build_analysis_request_for_payload(alert.raw)
    assert not any(item.role in {"source", "destination", "attacker"} for item in request.fact_reconstruction.role_claims)


def test_pingan_edr_parsed_message_does_not_use_outer_endpoint_identity() -> None:
    message = "<14>[SourceIP:30.99.16.122][AuditDB.tbl_ud_pe_threat_alert]str_attack_ip=10.181.175.69,str_title=Endpoint-only split evidence"
    payload = _payload(
        message,
        topic="leagsoft-edr",
        topic_name="EDR",
        raw_fields={"device__ip": "10.181.175.69"},
    )

    result = SocNormalizationService().inspect(payload)
    alert = result.alert

    assert alert.entities.host.ip_addresses == []
    assert alert.entities.network.source_ip is None
    assert alert.entities.network.destination_ip is None
    assert alert.entities.threat.iocs == ["10.181.175.69"]

    request = build_analysis_request_for_payload(payload)
    assert not any(item.role in {"source", "destination"} for item in request.fact_reconstruction.role_claims)
    attacker = next(item for item in request.fact_reconstruction.role_claims if item.role == "attacker")
    assert attacker.value == "10.181.175.69"
    assert attacker.evidence_path.endswith("message#parsed.str_attack_ip")
    assert all(item.source_layer is EvidenceLayer.RAW_MESSAGE for item in request.fact_reconstruction.role_claims)
    attack_semantic = next(item for item in alert.extensions["source_field_semantics"] if item["semantic_type"] == "vendor_attack_ip_assertion")
    assert attack_semantic["participates_in_entities"] is True


def test_pingan_edr_nested_details_preserve_endpoint_process_and_action_observations() -> None:
    valid_md5 = "a" * 32
    valid_sha256 = "b" * 64
    fields = {
        "agent_id": "AGENT-XC-001",
        "alert_describe": "计划任务与子进程异常",
        "endpoint": "XC-ENDPOINT-001",
        "iplist": "10.20.30.40,not-an-ip",
        "details0": {
            "attck_id": "TA0003,T1053.005",
            "command": "C:\\Windows\\System32\\cmd.exe /c whoami",
            "process_mame": "cmd.exe",
            "process_md5": valid_md5,
            "process_path": "C:\\Windows\\System32\\cmd.exe",
            "process_pid": "1200",
            "process_sha256": valid_sha256,
            "process_user": "SYSTEM",
            "rule_name": "Scheduled task child process",
            "action_detail": {
                "child_commandline": "powershell.exe -nop",
                "child_name": "powershell.exe",
                "child_path": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
                "child_pid": "1201",
            },
        },
        "details1": {
            "command": "reg.exe add HKCU\\Software\\Example",
            "process_mame": "reg.exe",
            "process_md5": "21",
            "process_path": "C:\\Windows\\System32\\reg.exe",
            "process_pid": "1300",
            "process_sha256": "21",
            "process_user": "analyst",
            "rule_desc": "Registry and file action",
            "action_detail": {
                "file_name": "payload.dll",
                "file_path": "C:\\Temp\\payload.dll",
                "is_exist": "true",
                "registry_key": "HKCU\\Software\\Example",
                "task_name": "Updater",
            },
        },
    }
    payload = _payload(
        json.dumps(fields, ensure_ascii=False),
        topic="edr-core-xc",
        topic_name="信创EDR",
    )
    original = deepcopy(payload)

    result = SocNormalizationService().inspect(payload)
    alert = result.alert

    assert payload == original
    assert alert.raw == original
    assert alert.entities.host.host_name == "XC-ENDPOINT-001"
    assert alert.entities.host.host_id == "AGENT-XC-001"
    assert alert.entities.host.ip_addresses == ["10.20.30.40"]
    assert alert.entities.network.source_ip is None
    assert alert.entities.network.destination_ip is None
    assert alert.entities.process.process_name == "cmd.exe"
    assert alert.entities.process.process_id == 1200
    assert alert.entities.process.md5 == valid_md5
    assert alert.entities.process.sha256 == valid_sha256
    assert alert.classification.tactic == ["TA0003"]
    assert alert.classification.technique == ["T1053.005"]

    observations = alert.entities.process.observations
    assert len(observations) == 2
    assert [node.process_name for node in observations[0].nodes] == [
        "cmd.exe",
        "powershell.exe",
    ]
    assert observations[1].nodes[0].process_name == "reg.exe"
    assert observations[1].nodes[0].md5 is None
    assert observations[1].nodes[0].sha256 is None
    assert observations[1].evidence_path.endswith("message#parsed.details1")

    file_observation = alert.entities.file.observations[0]
    assert file_observation.file_name == "payload.dll"
    assert file_observation.file_path == "C:\\Temp\\payload.dll"
    assert file_observation.exists is True
    assert file_observation.evidence_path.endswith("message#parsed.details1.action_detail")

    request = build_analysis_request_for_payload(payload)
    claims = request.fact_reconstruction.role_claims
    assert any(item.role == "impacted_asset" and item.value == "10.20.30.40" for item in claims)
    assert any(item.role == "victim" and item.value == "10.20.30.40" for item in claims)
    assert not any(item.role in {"source", "destination"} and item.evidence_path.endswith(".iplist") for item in claims)
    provenance = {item.canonical_path: item for item in request.fact_reconstruction.canonical_field_provenance}
    assert provenance["entities.process.observations[1].nodes[0].process_name"].selected_from.endswith("message#parsed.details1.process_mame")
    assert "entities.process.observations[1].nodes[0].md5" not in provenance
    assert request.evidence_coverage.high_value_gaps == []
    semantics = {item["semantic_type"] for item in alert.extensions["source_field_semantics"]}
    assert {
        "endpoint_child_process_observation",
        "endpoint_file_action_target",
        "endpoint_registry_action_context",
        "endpoint_scheduled_task_context",
        "invalid_process_hash",
        "vendor_mitre_classification",
    } <= semantics
    assert {"cmd.exe", "powershell.exe", "reg.exe"} <= set(result.entities.processes)
    assert valid_md5.upper() in {item.value for item in result.entities.mentions}
    assert "21" not in {item.value for item in result.entities.mentions}


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
    semantics = {item["semantic_type"]: item for item in alert.extensions["source_field_semantics"]}
    assert {
        "source_placeholder",
        "host_event_taxonomy",
        "endpoint_process_observation",
    } <= semantics.keys()
    assert semantics["source_placeholder"]["participates_in_reasoning"] is False
    assert request.primary_evidence is not None
    assert "1.1.1.1" not in request.primary_evidence.content
    placeholder_path = next(path for path in request.primary_evidence.omitted_field_paths if path.endswith("message#parsed.external_ip"))
    assert request.primary_evidence.omission_reasons[placeholder_path] == ("adapter_excluded_from_reasoning")
    observation = alert.entities.process.observations[0]
    assert [(node.process_name, node.process_id) for node in observation.nodes] == [
        ("java", 3065),
        ("chattr", 3287784),
    ]


def test_pingan_hids_preserves_parent_pid_without_inventing_parent_name() -> None:
    message = 'qtAlert datatype="backdoor_diagnose_win" internal_ip="30.1.1.20" host_name="endpoint-20" pname="net1.exe" pid="9980" ppid="5160" cmd="net1 localgroup Administrators example\\user /add"'
    payload = _payload(message, topic="security_qthids", topic_name="HIDS")

    request = build_analysis_request_for_payload(payload)
    process = request.canonical_entities.process

    assert process.parent_process_name is None
    assert process.parent_process_id == 5160
    assert process.observations[0].parent_process_id == 5160
    provenance = {item.canonical_path: item for item in request.fact_reconstruction.canonical_field_provenance}
    assert provenance["entities.process.parent_process_id"].selected_from.endswith("message#parsed.ppid")
    assert provenance["entities.process.observations[0].parent_process_id"].selected_from.endswith("message#parsed.ppid")


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


def test_pingan_high_value_fields_survive_full_supplementary_message_limit() -> None:
    messages = [
        "skyeye|!"
        + json.dumps(
            {
                "sip": f"30.1.1.{index + 1}",
                "dip": "30.2.2.20",
                "attack_type": "网络行为",
                "rule_id": f"RULE-{index + 1}",
            },
            ensure_ascii=False,
        )
        for index in range(5)
    ]
    messages.extend(
        "skyeye|!"
        + json.dumps(
            {
                "sip": f"30.1.1.{index + 1}",
                "dip": "30.2.2.20",
                "attack_type": "网络行为",
                "hit_content": "late-high-value-marker",
            },
            ensure_ascii=False,
        )
        for index in range(5, 11)
    )
    payload = _payload(
        *messages,
        topic="sec_guard_apt",
        topic_name="SkyEye APT",
    )

    request = build_analysis_request_for_payload(payload)

    assert len(request.supplementary_evidence) == 4
    highlight = next(item for item in request.evidence_highlights if item.value == "late-high-value-marker")
    assert highlight.semantic_type == "sensor_match_excerpt"
    assert highlight.occurrence_count == 6
    assert len(highlight.evidence_paths) == 5
    assert highlight.evidence_paths_truncated is True
    highlighted_paths = {f"alert.hitLog[0].zeusRawLogs[{index}].message#parsed.hit_content" for index in range(5, 11)}
    assert highlighted_paths <= set(request.evidence_coverage.llm_projected_paths)
    assert not highlighted_paths & {item.field_path for item in request.evidence_coverage.omissions}
    projected = project_analysis_context(request)
    assert projected["evidence"]["highlights"][0]["schema_version"] == ("soc.bounded_evidence_highlight.v2")


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


def test_pingan_ndr_preserves_file_observations_without_promoting_vendor_ioc() -> None:
    first = 'skyeye|!{"sip":"30.1.1.10","dip":"30.2.2.20","attack_type":"代码执行","ioc":"19023-发现反弹SHELL行为（Linux）"}'
    second = 'skyeye|!{"sip":"30.1.1.10","dip":"30.2.2.20","file_name":"/tmp/payload.jsp","file_md5":"0123456789abcdef0123456789abcdef"}'
    payload = _payload(first, second, topic="sec_guard_apt", topic_name="SkyEye APT")

    request = build_analysis_request_for_payload(payload)
    alert = request.canonical_entities

    assert alert.file.file_name == "payload.jsp"
    assert len(alert.file.observations) == 1
    assert alert.file.observations[0].relation == "observed_artifact"
    assert alert.threat.iocs == []
    semantics = {item["semantic_type"] for item in normalize_alert_payload(payload).extensions["source_field_semantics"]}
    assert "vendor_detection_descriptor" in semantics
    provenance = {item.canonical_path: item for item in request.fact_reconstruction.canonical_field_provenance}
    assert provenance["entities.file.file_name"].selected_from.endswith("zeusRawLogs[1].message#parsed.file_name")
    assert request.evidence_coverage.high_value_gaps == []


def test_pingan_ndr_marks_reviewed_detection_fields_as_upstream_assertions() -> None:
    message = 'skyeye|!{"sip":"30.1.1.10","dip":"30.2.2.20","rule_name":"成功失陷","rule_desc":"弱口令登录检测","attack_type":"弱口令","host_state":"攻击成功","rule_labels":"{\\"category\\":\\"弱口令\\"}"}'

    alert = normalize_alert_payload(_payload(message, topic="sec_guard_apt", topic_name="SkyEye APT"))
    semantics = {item["field_path"].split("#", 1)[-1]: item for item in alert.extensions["source_field_semantics"]}

    assert semantics["parsed.rule_name"]["semantic_type"] == ("provider_detection_rule_name_assertion")
    assert semantics["parsed.attack_type"]["semantic_type"] == ("provider_detection_classification_assertion")
    assert semantics["parsed.host_state"]["semantic_type"] == ("provider_detection_outcome_assertion")
    assert semantics["parsed.host_state"]["participates_in_reasoning"] is True
    assert semantics["parsed.rule_labels"]["semantic_type"] == ("provider_detection_rule_label_assertion")
    assert semantics["decoded.rule_labels"]["semantic_type"] == ("provider_detection_rule_label_assertion")


def test_pingan_hids_keeps_network_direction_event_scoped() -> None:
    first = 'qtAlert event_type="bounce_shell" internal_ip="30.3.3.30" external_ip="1.1.1.1" host_name="host-30" agent_id="agent-30" pname="bash" pid="100" cmd="bash -i" dst_ip="198.51.100.9" port="4444"'
    second = 'qtAlert event_type="honey_file" internal_ip="30.3.3.30" host_name="host-30" uname="app" process_chain="java(10)->touch(11)" file_path="/srv/decoy.txt" md5="0123456789abcdef0123456789abcdef"'
    payload = _payload(first, second, topic="security_qthids", topic_name="HIDS")

    request = build_analysis_request_for_payload(payload)
    alert = request.canonical_entities

    assert alert.network.source_ip is None
    assert alert.network.destination_ip is None
    assert len(alert.network.observations) == 1
    observation = alert.network.observations[0]
    assert observation.source_ip == "30.3.3.30"
    assert observation.destination_ip == "198.51.100.9"
    assert observation.direction == "outbound"
    assert alert.host.ip_addresses == ["30.3.3.30"]
    assert alert.user.username == "app"
    assert alert.file.file_path == "/srv/decoy.txt"
    provenance = {item.canonical_path: item for item in request.fact_reconstruction.canonical_field_provenance}
    assert provenance["entities.user.username"].selected_from.endswith("zeusRawLogs[1].message#parsed.uname")
    assert provenance["entities.file.file_name"].selected_from.endswith("zeusRawLogs[1].message#parsed.file_path")
    assert request.evidence_coverage.high_value_gaps == []


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
    assert source_claim.evidence_trust is EvidenceTrustLevel.LOW
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
    highlight_paths = {path for item in request.evidence_highlights for path in item.evidence_paths}
    assert set(coverage.llm_projected_paths) == {
        *request.primary_evidence.projected_field_paths,
        *highlight_paths,
    }
    assert not set(request.primary_evidence.omitted_field_paths) & set(request.primary_evidence.projected_field_paths)

    full_request = build_analysis_request_for_payload(
        alert.model_dump(mode="json"),
        sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
    )
    assert full_request.primary_evidence is not None
    assert "secret-token" in full_request.primary_evidence.content
    assert full_request.primary_evidence.sanitized_field_paths == []
    assert full_request.evidence_coverage.llm_sanitized_paths == []
    assert full_request.evidence_coverage.counts["llm_sanitized_count"] == 0


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
    assert coverage.message_schemas[0].status is MessageSchemaStatus.RECOGNIZED
    assert coverage.message_schemas[0].warnings == parsed["warnings"]
    assert any(path.endswith("#parsed.payload.req_body") for path in coverage.llm_projected_paths)
    assert any(path.endswith("#repaired.payload.rsp_body.uIdToken") for path in coverage.repaired_field_paths)
    assert any(item.reason == "sanitized_string_fallback" for item in coverage.omissions)
    assert any(item.reason == "replaced_by_repaired_projection" for item in coverage.omissions)
    assert "degraded message schema" not in " ".join(coverage.warnings)

    run = SocAnalysisService().analyze(payload)
    assert run.decision is not None
    assert run.decision.evidence_state is DecisionEvidenceState.PARTIAL
    assert DecisionReviewReason.DEGRADED_MESSAGE_SCHEMA not in run.decision.review_reasons
    assert DecisionReviewReason.TRUNCATED_ANALYSIS_EVIDENCE not in run.decision.review_reasons


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
