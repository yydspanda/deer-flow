from __future__ import annotations

import json
from pathlib import Path

from soc_agent.integrations.pingan.legacy_compat.acceptance import (
    run_pingan_legacy_fake_acceptance,
)


def test_fake_acceptance_covers_http_runtime_recovery_and_callback(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "report.json"
    report = run_pingan_legacy_fake_acceptance(
        database_url=f"sqlite:///{tmp_path / 'soc.sqlite'}",
        report_path=report_path,
    )

    assert report["passed"] is True
    assert report["simulated"] is True
    assert len(report["jobs"]) == 2
    assert all(item["status"] == "completed" for item in report["jobs"])
    assert any("lease_expired_requeued" in item["event_types"] for item in report["jobs"])
    assert [item["outcome"] for item in report["callback_attempts"]] == [
        "delivered",
        "delivered",
    ]
    assert report["fixture"] == {
        "kind": "synthetic_protocol_alert",
        "version": "soc.pingan_legacy_fake_fixture.v1",
        "alert_id": "FAKE-LEGACY-ACCEPTANCE",
    }
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted["secrets_included"] is False
    assert "hitLog" not in report_path.read_text(encoding="utf-8")
    assert "legacy_demos" not in report_path.read_text(encoding="utf-8")


def test_fake_acceptance_sources_do_not_depend_on_local_alert_samples() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    sources = [
        backend_root / "scripts" / "soc_pingan_legacy_fake_acceptance.py",
        backend_root / "soc_agent" / "integrations" / "pingan" / "legacy_compat" / "acceptance.py",
    ]

    combined = "\n".join(path.read_text(encoding="utf-8") for path in sources)

    assert "legacy_demos" not in combined
    assert "--sample-path" not in combined
