from __future__ import annotations

import json
from pathlib import Path

import pytest

from soc_agent.cli import main
from soc_agent.contracts import AlertInput, AlertSourceType, AnalysisRun, AnalysisRunStatus, Verdict
from soc_agent.core import SocAnalysisService, SocNormalizationService
from soc_agent.core.runtime import analyze_alert
from soc_agent.normalizers import normalize_alert_payload

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"
MAPPINGS = Path(__file__).resolve().parents[1] / "samples" / "mappings"


def _sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def _analyze(payload: dict) -> AnalysisRun:
    return SocAnalysisService().analyze(payload)


def test_approved_scanner_returns_false_positive_candidate() -> None:
    payload = _sample("approved_scanner.json")
    run = _analyze(payload)

    assert run.status == AnalysisRunStatus.SUCCESS
    assert run.input_payload == payload
    assert run.input_hash is not None
    assert run.analysis is not None
    assert run.decision is not None
    assert run.analysis.verdict == Verdict.FALSE_POSITIVE
    assert run.decision.automation_allowed is False
    assert run.normalization_report is not None
    assert run.normalization_report.adapter == "generic"
    assert "detection.rule_code" in run.normalization_report.normalized_fields
    assert "entities.network.source_ip" in run.normalization_report.normalized_fields
    assert run.extraction_report is not None
    assert run.extraction_report.entity_counts["ip"] >= 2
    assert run.extraction_report.entity_counts["process"] == 1
    assert [step.step_name for step in run.steps] == [
        "normalize",
        "entity_extract",
        "analyze_stub",
        "schema_validate",
        "decide",
    ]
    assert all(step.status.value == "success" for step in run.steps)


def test_malicious_ioc_returns_true_positive_candidate() -> None:
    run = _analyze(_sample("malicious_ioc.json"))

    assert run.status == AnalysisRunStatus.SUCCESS
    assert run.analysis is not None
    assert run.analysis.verdict == Verdict.TRUE_POSITIVE
    assert run.analysis.confidence >= 0.9


def test_low_context_alert_needs_review() -> None:
    run = _analyze(_sample("unknown_low_context.json"))

    assert run.status == AnalysisRunStatus.NEEDS_REVIEW
    assert run.decision is not None
    assert run.decision.needs_review is True


def test_missing_fields_do_not_break_entity_extraction() -> None:
    run = _analyze(_sample("missing_fields.json"))

    assert run.status == AnalysisRunStatus.NEEDS_REVIEW
    assert run.entities is not None
    assert "missing optional field: rule_name" in run.entities.warnings
    assert run.normalization_report is not None
    assert "detection.rule_code_or_name" in run.normalization_report.missing_fields
    assert run.extraction_report is not None
    assert "process" in run.extraction_report.missing_entity_kinds


def test_alert_input_contract_rejects_flat_source_fields() -> None:
    with pytest.raises(ValueError):
        AlertInput.model_validate(
            {
                "alert_id": "ALT-FLAT-REJECTED-001",
                "rule_name": "Legacy Flat Rule",
                "source_ip": "10.0.1.10",
            }
        )


def test_nested_edr_alert_normalizes_detection_and_entities() -> None:
    alert = AlertInput.model_validate(
        {
            "alert_id": "ALT-NESTED-EDR-001",
            "source": {
                "source_type": "edr",
                "source_system": "pingan-edr",
                "vendor": "internal",
                "product": "endpoint-security",
            },
            "detection": {
                "rule_code": "EDR-SCAN-001",
                "rule_name": "Approved Scanner Process Execution",
            },
            "entities": {
                "network": {
                    "source_ip": "10.0.1.10",
                    "destination_ip": "10.0.2.20",
                    "dst_port": 443,
                },
                "process": {
                    "process_name": "SecurityScan",
                    "command_line": "SecurityScan --approved --target 10.0.2.20",
                },
                "user": {"username": "svc-security"},
                "host": {"host_name": "scanner-01"},
            },
            "classification": {"severity": "medium", "category": "process_execution"},
        }
    )

    assert alert.source.source_type == AlertSourceType.EDR
    assert alert.detection.rule_code == "EDR-SCAN-001"
    assert alert.detection.rule_name == "Approved Scanner Process Execution"
    assert alert.entities.network.source_ip == "10.0.1.10"
    assert alert.entities.process.process_name == "SecurityScan"

    normalized = normalize_alert_payload(alert.model_dump(mode="json"))
    assert normalized.detection.detection_key == "pingan-edr:rule_code:edr-scan-001"

    run = analyze_alert(normalized.model_dump(mode="json"))
    assert run.analysis is not None
    assert run.analysis.verdict == Verdict.FALSE_POSITIVE


def test_f5_waf_alert_without_rule_code_uses_rule_name_detection_key() -> None:
    alert = normalize_alert_payload(
        {
            "alert_id": "ALT-F5-WAF-001",
            "source": {
                "source_type": "f5",
                "source_system": "f5-asm-prod",
                "vendor": "F5",
                "product": "BIG-IP ASM",
            },
            "detection": {
                "rule_name": "SQL Injection Attempt",
                "rule_category": "waf_attack",
            },
            "entities": {
                "network": {
                    "source_ip": "203.0.113.10",
                    "destination_ip": "10.0.20.8",
                    "protocol": "https",
                    "domain": "app.example.com",
                },
                "http": {
                    "method": "GET",
                    "host": "app.example.com",
                    "path": "/search",
                    "url": "https://app.example.com/search?q=' OR 1=1",
                    "status_code": 403,
                    "user_agent": "curl/8.0",
                },
            },
            "classification": {"severity": "high", "category": "waf"},
        }
    )

    assert alert.detection.rule_code is None
    assert alert.detection.rule_name == "SQL Injection Attempt"
    assert alert.entities.network.domain == "app.example.com"
    assert alert.entities.http.url == "https://app.example.com/search?q=' OR 1=1"
    assert alert.detection.detection_key == "f5-asm-prod:rule_name:sql_injection_attempt"


def test_http_x_forwarded_for_header_alias_normalizes_to_canonical_field() -> None:
    alert = normalize_alert_payload(
        {
            "alert_id": "ALT-XFF-001",
            "source": {
                "source_type": "waf",
                "source_system": "f5-asm-prod",
            },
            "detection": {"rule_name": "Suspicious Forwarded Client"},
            "entities": {
                "http": {
                    "x-forwarded-for": "203.0.113.88",
                    "host": "app.example.com",
                    "url": "https://app.example.com/login",
                }
            },
        }
    )

    assert alert.entities.http.x_forwarded_for == "203.0.113.88"

    run = _analyze(alert.model_dump(mode="json"))
    assert run.entities is not None
    assert run.normalization_report is not None
    assert "entities.http.x_forwarded_for" in run.normalization_report.normalized_fields
    by_key = {mention.key: mention for mention in run.entities.mentions}
    assert by_key["ip:203.0.113.88"].role == "x_forwarded_for"
    assert by_key["ip:203.0.113.88"].evidence_path == "entities.http.x_forwarded_for"


def test_alert_without_rule_identifiers_gets_fingerprint_detection_key() -> None:
    alert = normalize_alert_payload(
        {
            "alert_id": "ALT-NIDS-LOWCONTEXT-001",
            "source_type": "nids",
            "source_system": "suricata",
            "source_ip": "192.0.2.10",
            "destination_ip": "192.0.2.20",
            "dst_port": 445,
            "protocol": "tcp",
        }
    )

    assert alert.source.source_type == AlertSourceType.NIDS
    assert alert.detection.rule_code is None
    assert alert.detection.rule_name is None
    assert alert.detection.detection_key is not None
    assert alert.detection.detection_key.startswith("suricata:fingerprint:")


def test_unknown_source_type_falls_back_to_other_without_breaking() -> None:
    alert = normalize_alert_payload(
        {
            "alert_id": "ALT-VENDOR-001",
            "source_type": "fortigate",
            "rule_id": 1002003,
            "rule_name": "Suspicious VPN Login",
            "source_ip": "198.51.100.10",
            "username": "alice",
            "severity": "medium",
        }
    )

    assert alert.source.source_type == AlertSourceType.OTHER
    assert alert.source.source_system == "fortigate"
    assert alert.detection.rule_code == "1002003"
    assert alert.detection.detection_key == "fortigate:rule_code:1002003"


def test_user_um_account_normalizes_as_user_identity() -> None:
    alert = normalize_alert_payload(
        {
            "alert_id": "ALT-UM-001",
            "source_type": "iam",
            "source_system": "iam-audit",
            "rule_name": "Suspicious UM Login",
            "umAccount": "UM123456",
            "source_ip": "198.51.100.20",
        }
    )

    assert alert.entities.user.um_account == "UM123456"

    run = _analyze(alert.model_dump(mode="json"))
    assert run.entities is not None
    by_key = {mention.key: mention for mention in run.entities.mentions}
    assert by_key["user:UM123456"].kind == "user"
    assert by_key["user:UM123456"].role == "um_account"
    assert by_key["user:UM123456"].evidence_path == "entities.user.um_account"


def test_pingan_legacy_apt_alert_normalizes_platform_envelope() -> None:
    alert = normalize_alert_payload(_sample("pingan_legacy_apt.json"))

    assert alert.alert_id == "2026494"
    assert alert.source.source_type == AlertSourceType.NDR
    assert alert.source.source_system == "sec_guard_apt"
    assert alert.source.product == "360天眼APT"
    assert alert.detection.rule_code == "RPAADM_002635"
    assert alert.detection.rule_name == "告警日志【天眼APT】失败企图"
    assert alert.detection.detection_key == "sec_guard_apt:rule_code:rpaadm_002635"
    assert alert.classification.severity == "高危"
    assert alert.classification.category == "可疑操作行为"
    assert alert.classification.tactic == ["TA0001"]
    assert alert.classification.technique == ["T1190"]
    assert alert.entities.network.source_ip == "30.180.248.178"
    assert alert.entities.network.destination_ip == "30.185.76.75"
    assert alert.entities.http.host == "app.example.internal"
    assert alert.entities.http.status_code == 200
    legacy = alert.extensions["legacy_platform"]
    assert legacy["workflow"]["alert_code"] == "PIE-2026-127865"
    assert legacy["workflow"]["status"] == "待审阅"
    assert legacy["taxonomy"]["profile_code"] == "PPAADM_000890"
    assert legacy["ownership"]["dst_bu_code"] == "PA011"
    assert legacy["ownership"]["asset_group"] == "Example Business Unit"
    assert legacy["sensor"]["device_ip"] == "30.176.240.70"
    assert legacy["sensor"]["skyeye_type"] == "webids-webattack_dolog"
    assert legacy["disposition"]["host_state"] == "企图"
    assert legacy["disposition"]["is_blocked"] is True
    assert legacy["disposition"]["is_white"] is False
    assert legacy["disposition"]["repeat_count"] == 1
    assert legacy["correlation"]["alert_hash"] == "20260617_b4c266bf0241cb9f589d80036cc3c44a"

    run = _analyze(_sample("pingan_legacy_apt.json"))
    assert run.alert_id == "2026494"
    assert run.entities is not None
    assert "30.180.248.178" in run.entities.ips
    assert "30.185.76.75" in run.entities.ips
    assert "app.example.internal" in run.entities.domains
    assert run.entities.rule_codes == ["RPAADM_002635"]
    by_key = {mention.key: mention for mention in run.entities.mentions}
    assert by_key["ip:30.180.248.178"].role == "source_ip"
    assert by_key["ip:30.185.76.75"].role == "destination_ip"
    assert by_key["domain:app.example.internal"].kind == "domain"
    assert by_key["rule_code:RPAADM_002635"].evidence_path == "detection.rule_code"
    assert by_key["mitre:T1190"].role == "technique"


def test_pingan_legacy_edr_alert_normalizes_platform_envelope() -> None:
    alert = normalize_alert_payload(_sample("pingan_legacy_edr.json"))

    assert alert.alert_id == "1965810"
    assert alert.source.source_type == AlertSourceType.EDR
    assert alert.source.source_system == "leagsoft-edr"
    assert alert.detection.rule_code == "RPAADM_002583"
    assert alert.detection.rule_name == "【联软edr】横向移动"
    assert alert.classification.severity == "High"
    assert alert.classification.category == "可疑横向移动"
    assert alert.classification.tactic == ["TA0008"]
    assert alert.classification.technique == ["T1021"]
    assert alert.entities.network.source_ip == "10.43.107.39"
    assert alert.entities.network.destination_ip == "30.162.29.85"
    assert alert.entities.host.host_name == "HOST-L12267.example.local"
    assert alert.entities.user.username == "analyst001"
    assert alert.entities.user.user_id == "S-1-5-21-example"
    assert alert.entities.process.process_name == "svchost.exe"
    assert alert.entities.process.parent_process_name == "services.exe"
    assert alert.entities.file.md5 == "7B88D0896FBF43469A9959D59824A514"
    legacy = alert.extensions["legacy_platform"]
    assert legacy["taxonomy"]["topic"] == "leagsoft-edr"
    assert legacy["soar"]["display_names"] == ["IP查询-SOAR"]
    assert legacy["soar"]["asset"]["device_name"] == "HOST-L12267.example.local"
    assert legacy["soar"]["asset"]["username"] == "analyst001"

    run = _analyze(_sample("pingan_legacy_edr.json"))
    assert run.alert_id == "1965810"
    assert run.entities is not None
    assert run.entities.ips == ["10.43.107.39", "30.162.29.85"]
    assert "svchost.exe" in run.entities.processes
    assert "services.exe" in run.entities.processes
    assert "analyst001" in run.entities.users
    assert "S-1-5-21-example" in run.entities.users
    assert "HOST-L12267.example.local" in run.entities.hosts
    by_key = {mention.key: mention for mention in run.entities.mentions}
    assert by_key["process:svchost.exe"].role == "process_name"
    assert by_key["process:services.exe"].role == "parent_process_name"
    assert by_key["user:analyst001"].role == "username"
    assert by_key["user:S-1-5-21-example"].role == "user_id"
    assert by_key["host:HOST-L12267.example.local"].role == "host_name"
    assert by_key["file_hash:7B88D0896FBF43469A9959D59824A514"].role == "md5"


def test_cli_analyze_file_outputs_json(capsys) -> None:
    exit_code = main(["analyze", str(SAMPLES / "approved_scanner.json")])

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["alert_id"] == "ALT-SAMPLE-FP-001"
    assert payload["analysis"]["verdict"] == "false_positive"


def test_cli_normalize_inspect_outputs_reports_without_analysis(capsys) -> None:
    exit_code = main(["normalize", "inspect", str(SAMPLES / "pingan_legacy_edr.json")])

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["schema_version"] == "soc.normalization_inspection.v1"
    assert payload["alert"]["alert_id"] == "1965810"
    assert payload["alert"]["source"]["source_type"] == "edr"
    assert payload["entities"]["ips"] == ["10.43.107.39", "30.162.29.85"]
    assert payload["normalization_report"]["adapter"] == "pingan_platform"
    assert "entities.user.user_id" in payload["normalization_report"]["normalized_fields"]
    assert payload["extraction_report"]["entity_counts"]["user"] >= 2
    assert "analysis" not in payload
    assert "decision" not in payload


def test_mapping_normalize_inspect_maps_simple_vendor_payload() -> None:
    result = SocNormalizationService().inspect(
        _sample("mapped_waf.json"),
        mapping_path=MAPPINGS / "sample_waf.yaml",
    )

    assert result.alert.alert_id == "WAF-2026-0001"
    assert result.alert.source.source_type == AlertSourceType.WAF
    assert result.alert.source.source_system == "sample-waf"
    assert result.alert.detection.rule_name == "SQL Injection Attempt"
    assert result.alert.detection.detection_key == "sample-waf:rule_name:sql_injection_attempt"
    assert result.alert.classification.severity == "high"
    assert result.alert.entities.network.source_ip == "203.0.113.77"
    assert result.alert.entities.network.destination_ip == "10.10.20.8"
    assert result.alert.entities.http.x_forwarded_for == "198.51.100.23"
    assert result.normalization_report.adapter == "mapping:sample-waf"
    assert result.normalization_report.unmapped_field_count == 0
    assert "entities.http.x_forwarded_for" in result.normalization_report.normalized_fields

    by_key = {mention.key: mention for mention in result.entities.mentions}
    assert by_key["ip:198.51.100.23"].role == "x_forwarded_for"
    assert by_key["url:https://app.example.com/search?q=' or 1=1"].role == "http_url"


def test_cli_normalize_inspect_supports_mapping_file(capsys) -> None:
    exit_code = main(
        [
            "normalize",
            "inspect",
            str(SAMPLES / "mapped_waf.json"),
            "--mapping",
            str(MAPPINGS / "sample_waf.yaml"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["alert"]["alert_id"] == "WAF-2026-0001"
    assert payload["alert"]["source"]["source_type"] == "waf"
    assert payload["alert"]["entities"]["http"]["x_forwarded_for"] == "198.51.100.23"
    assert payload["normalization_report"]["adapter"] == "mapping:sample-waf"
    assert payload["extraction_report"]["entity_counts"]["ip"] >= 3
    assert "analysis" not in payload
    assert "decision" not in payload


def test_normalize_drift_aggregates_runtime_reports() -> None:
    report = SocNormalizationService().drift(
        [
            ("approved_scanner.json", _sample("approved_scanner.json")),
            ("missing_fields.json", _sample("missing_fields.json")),
        ]
    )

    assert report.sample_count == 2
    assert report.success_count == 2
    assert report.failure_count == 0
    assert report.adapter_counts == {"generic": 2}
    assert report.source_type_counts["edr"] == 2
    assert report.missing_field_counts["detection.rule_code_or_name"] == 1
    assert report.entity_kind_counts["ip"] >= 2
    assert report.warning_counts["missing optional field: rule_name"] == 1
    assert [sample.path for sample in report.suspicious_samples] == ["missing_fields.json"]


def test_cli_normalize_drift_supports_mapping_file(capsys) -> None:
    exit_code = main(
        [
            "normalize",
            "drift",
            str(SAMPLES / "mapped_waf.json"),
            "--mapping",
            str(MAPPINGS / "sample_waf.yaml"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["schema_version"] == "soc.normalization_drift_report.v1"
    assert payload["sample_count"] == 1
    assert payload["success_count"] == 1
    assert payload["adapter_counts"] == {"mapping:sample-waf": 1}
    assert payload["source_type_counts"] == {"waf": 1}
    assert payload["entity_kind_counts"]["ip"] == 3
    assert payload["suspicious_samples"] == []
    assert payload["samples"][0]["alert_id"] == "WAF-2026-0001"


def test_cli_normalize_drift_supports_recent_persisted_runs(tmp_path: Path, capsys) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'soc.db'}"

    assert main(["db", "upgrade", "--database-url", database_url]) == 0
    capsys.readouterr()

    assert main(["analyze", str(SAMPLES / "approved_scanner.json"), "--persist", "--database-url", database_url]) == 0
    capsys.readouterr()
    assert main(["analyze", str(SAMPLES / "missing_fields.json"), "--persist", "--database-url", database_url]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "normalize",
                "drift",
                "--recent-runs",
                "--limit",
                "1",
                "--database-url",
                database_url,
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["schema_version"] == "soc.normalization_drift_report.v1"
    assert payload["sample_count"] == 1
    assert payload["success_count"] == 1
    assert payload["samples"][0]["path"].startswith("run:")
    assert payload["samples"][0]["run_id"] is not None
    assert payload["missing_field_counts"]["detection.rule_code_or_name"] == 1


def test_cli_persist_show_and_replay(tmp_path: Path, capsys) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'soc.db'}"

    assert main(["db", "upgrade", "--database-url", database_url]) == 0
    capsys.readouterr()

    assert main(["analyze", str(SAMPLES / "approved_scanner.json"), "--persist", "--database-url", database_url]) == 0
    captured = capsys.readouterr()
    original = json.loads(captured.out)

    assert main(["show", original["run_id"], "--database-url", database_url]) == 0
    captured = capsys.readouterr()
    shown = json.loads(captured.out)
    assert shown["run_id"] == original["run_id"]
    assert shown["input_hash"] == original["input_hash"]

    assert main(["replay", original["run_id"], "--database-url", database_url]) == 0
    captured = capsys.readouterr()
    replayed = json.loads(captured.out)
    assert replayed["run_id"] != original["run_id"]
    assert replayed["replay_of_run_id"] == original["run_id"]

    assert (
        main(
            [
                "correct",
                original["run_id"],
                "--verdict",
                "true_positive",
                "--reason",
                "Analyst confirmed malicious follow-up activity.",
                "--database-url",
                database_url,
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    corrected = json.loads(captured.out)
    assert corrected["decision"]["verdict"] == "true_positive"
    assert corrected["corrections"][0]["previous_verdict"] == "false_positive"
    assert corrected["corrections"][0]["candidate_knowledge_status"] == "pending_review"


def test_cli_list_outputs_persisted_alert_summaries(tmp_path: Path, capsys) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'soc.db'}"

    assert main(["db", "upgrade", "--database-url", database_url]) == 0
    capsys.readouterr()

    assert main(["analyze", str(SAMPLES / "pingan_legacy_apt.json"), "--persist", "--database-url", database_url]) == 0
    capsys.readouterr()
    assert main(["analyze", str(SAMPLES / "pingan_legacy_edr.json"), "--persist", "--database-url", database_url]) == 0
    captured = capsys.readouterr()
    edr_run = json.loads(captured.out)

    assert main(["list", "--database-url", database_url, "--limit", "10"]) == 0
    captured = capsys.readouterr()
    summaries = json.loads(captured.out)
    by_alert_id = {summary["alert_id"]: summary for summary in summaries}

    assert set(by_alert_id) == {"2026494", "1965810"}
    assert by_alert_id["2026494"]["source_type"] == "ndr"
    assert by_alert_id["2026494"]["rule_code"] == "RPAADM_002635"
    assert "ip:30.180.248.178" in by_alert_id["2026494"]["entity_keys"]
    assert "domain:app.example.internal" in by_alert_id["2026494"]["entity_keys"]
    assert by_alert_id["1965810"]["source_type"] == "edr"
    assert by_alert_id["1965810"]["rule_code"] == "RPAADM_002583"
    assert "process:svchost.exe" in by_alert_id["1965810"]["entity_keys"]

    assert (
        main(
            [
                "correct",
                edr_run["run_id"],
                "--verdict",
                "true_positive",
                "--reason",
                "Confirmed malicious lateral movement.",
                "--database-url",
                database_url,
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["list", "--database-url", database_url]) == 0
    captured = capsys.readouterr()
    summaries = json.loads(captured.out)
    corrected = next(summary for summary in summaries if summary["alert_id"] == "1965810")
    assert corrected["verdict"] == "true_positive"
    assert corrected["needs_review"] is False


def test_cli_review_queue_lists_and_closes_items(tmp_path: Path, capsys) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'soc.db'}"

    assert main(["db", "upgrade", "--database-url", database_url]) == 0
    capsys.readouterr()

    assert main(["analyze", str(SAMPLES / "pingan_legacy_apt.json"), "--persist", "--database-url", database_url]) == 0
    capsys.readouterr()

    assert main(["review", "list", "--database-url", database_url]) == 0
    captured = capsys.readouterr()
    open_items = json.loads(captured.out)
    assert len(open_items) == 1
    item = open_items[0]
    assert item["alert_id"] == "2026494"
    assert item["status"] == "open"
    assert item["priority"] == "high"
    assert item["reason"] == "summary.needs_review"
    assert item["rule_code"] == "RPAADM_002635"

    assert main(["review", "context", item["queue_id"], "--database-url", database_url]) == 0
    captured = capsys.readouterr()
    context = json.loads(captured.out)
    assert context["queue_item"]["queue_id"] == item["queue_id"]
    assert context["run"]["run_id"] == item["run_id"]
    assert context["summary"]["alert_id"] == "2026494"
    assert context["audit_records"][0]["action"] == "analysis"
    assert context["audit_records"][0]["run_id"] == item["run_id"]
    assert context["similar_alerts"] == []

    assert (
        main(
            [
                "review",
                "close",
                item["queue_id"],
                "--reason",
                "Reviewed in CLI queue.",
                "--database-url",
                database_url,
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    closed = json.loads(captured.out)
    assert closed["status"] == "closed"
    assert closed["close_reason"] == "Reviewed in CLI queue."

    assert main(["review", "list", "--database-url", database_url]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []

    assert main(["review", "list", "--status", "closed", "--database-url", database_url]) == 0
    captured = capsys.readouterr()
    closed_items = json.loads(captured.out)
    assert len(closed_items) == 1
    assert closed_items[0]["queue_id"] == item["queue_id"]
