from __future__ import annotations

import pytest

from scripts import soc_kafka_smoke


def test_smoke_daemon_command_supports_consume_mode() -> None:
    assert soc_kafka_smoke._daemon_command(mode="consume", database_url="sqlite:///soc.db") == [
        "daemon",
        "consume",
        "--database-url",
        "sqlite:///soc.db",
        "--max-records",
        "1",
        "--pretty",
    ]


def test_smoke_daemon_command_supports_run_mode() -> None:
    assert soc_kafka_smoke._daemon_command(mode="run", database_url="sqlite:///soc.db") == [
        "daemon",
        "run",
        "--database-url",
        "sqlite:///soc.db",
        "--max-loops",
        "1",
        "--idle-sleep-ms",
        "0",
        "--error-backoff-ms",
        "0",
        "--include-results",
        "--pretty",
    ]


def test_smoke_daemon_command_rejects_unknown_mode() -> None:
    with pytest.raises(SystemExit, match="Unsupported smoke mode"):
        soc_kafka_smoke._daemon_command(mode="other", database_url="sqlite:///soc.db")


def test_first_daemon_result_extracts_result() -> None:
    assert soc_kafka_smoke._first_daemon_result({"results": [{"status": "processed"}]}) == {"status": "processed"}


def test_first_daemon_result_rejects_missing_results() -> None:
    with pytest.raises(SystemExit, match="did not include results"):
        soc_kafka_smoke._first_daemon_result({"schema_version": "bad"})


def test_smoke_builds_versioned_alert_envelope_without_losing_raw_payload() -> None:
    raw = {
        "alert_id": "ALT-SMOKE-1",
        "source": {"source_type": "edr"},
        "severity": "high",
        "message": "raw source message",
    }

    envelope = soc_kafka_smoke._build_alert_envelope(raw, alert_id="ALT-SMOKE-1")

    assert envelope["schema_version"] == "soc.alert.raw.v1"
    assert envelope["source"] == "edr"
    assert envelope["alert_id"] == "ALT-SMOKE-1"
    assert envelope["dedup_key"] == "smoke:edr:ALT-SMOKE-1"
    assert envelope["raw"] == raw
