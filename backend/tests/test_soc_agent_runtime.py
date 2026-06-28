from __future__ import annotations

import json
from pathlib import Path

import pytest

from soc_agent.cli import main
from soc_agent.contracts import AlertInput, AlertSourceType, AnalysisRun, AnalysisRunStatus, Verdict
from soc_agent.core import SocAnalysisService
from soc_agent.core.runtime import analyze_alert
from soc_agent.normalizers import normalize_alert_payload

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


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


def test_cli_analyze_file_outputs_json(capsys) -> None:
    exit_code = main(["analyze", str(SAMPLES / "approved_scanner.json")])

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["alert_id"] == "ALT-SAMPLE-FP-001"
    assert payload["analysis"]["verdict"] == "false_positive"


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
