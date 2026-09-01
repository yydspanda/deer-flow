from __future__ import annotations

import json
import sqlite3
import stat
import zlib
from pathlib import Path

import pytest

from soc_agent.integrations.pingan.legacy_compat.request_preparation import (
    PingAnLegacyRequestPreparationError,
    prepare_pingan_legacy_live_request,
)
from soc_agent.utils.hashing import stable_hash


def test_prepare_live_request_reads_complete_alert_data_and_writes_private_file(
    tmp_path: Path,
) -> None:
    payload = _payload(alert_id="2457097", status="待审阅")
    index_path = _write_payload_store(tmp_path, payload)
    output_path = tmp_path / "private/task-request.local.json"

    report = prepare_pingan_legacy_live_request(
        alert_id="2457097",
        index_path=index_path,
        output_path=output_path,
        session_id="session-2457097",
    )

    request = json.loads(output_path.read_text(encoding="utf-8"))
    assert request == {
        "app_code": "zeus",
        "flow_id": "alert_agent",
        "session_id": "session-2457097",
        "alert_id": "2457097",
        "alert_data": payload,
    }
    assert request["alert_data"]["alert"]["hitLog"][0]["zeusRawLogs"][0]["message"] == '{"complete":true}'
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600
    assert report.alert_id == "2457097"
    assert report.snapshot_status == "待审阅"
    assert report.payload_sha256 == stable_hash(payload)
    assert report.payload_size_bytes > 0
    assert report.contains_business_payload is False
    assert "alert_data" not in report.model_dump(mode="json")


def test_prepare_live_request_rejects_non_pending_snapshot(
    tmp_path: Path,
) -> None:
    payload = _payload(alert_id="2457097", status="已忽略")
    index_path = _write_payload_store(tmp_path, payload)

    with pytest.raises(
        PingAnLegacyRequestPreparationError,
        match="not pending review",
    ):
        prepare_pingan_legacy_live_request(
            alert_id="2457097",
            index_path=index_path,
            output_path=tmp_path / "task-request.local.json",
        )


def test_prepare_live_request_rejects_alert_id_mismatch(
    tmp_path: Path,
) -> None:
    payload = _payload(alert_id="different-alert", status="待审阅")
    index_path = _write_payload_store(
        tmp_path,
        payload,
        indexed_alert_id="2457097",
    )

    with pytest.raises(
        PingAnLegacyRequestPreparationError,
        match="nested alert ID",
    ):
        prepare_pingan_legacy_live_request(
            alert_id="2457097",
            index_path=index_path,
            output_path=tmp_path / "task-request.local.json",
        )


def test_prepare_live_request_requires_explicit_overwrite(
    tmp_path: Path,
) -> None:
    payload = _payload(alert_id="2457097", status="待审阅")
    index_path = _write_payload_store(tmp_path, payload)
    output_path = tmp_path / "task-request.local.json"
    output_path.write_text("existing", encoding="utf-8")

    with pytest.raises(
        PingAnLegacyRequestPreparationError,
        match="already exists",
    ):
        prepare_pingan_legacy_live_request(
            alert_id="2457097",
            index_path=index_path,
            output_path=output_path,
        )

    prepare_pingan_legacy_live_request(
        alert_id="2457097",
        index_path=index_path,
        output_path=output_path,
        overwrite=True,
    )
    assert json.loads(output_path.read_text(encoding="utf-8"))["alert_id"] == ("2457097")


def _payload(*, alert_id: str, status: str) -> dict:
    return {
        "alert": {
            "alertId": alert_id,
            "status": status,
            "executeType": 0,
            "hitLog": [
                {
                    "zeusRawLogs": [
                        {
                            "message": '{"complete":true}',
                            "anotherImportantField": "preserved",
                        }
                    ]
                }
            ],
        },
        "relatedAlertList": [{"alertId": "related-1"}],
        "tenant_id": "pingan",
    }


def _write_payload_store(
    tmp_path: Path,
    payload: dict,
    *,
    indexed_alert_id: str | None = None,
) -> Path:
    alert_id = indexed_alert_id or str(payload["alert"]["alertId"])
    source_index = 7
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    payload_hash = stable_hash(payload)
    store_path = tmp_path / "corpus.workbench-payloads.sqlite"
    connection = sqlite3.connect(store_path)
    try:
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute(
            """
            CREATE TABLE payloads (
                alert_id TEXT PRIMARY KEY,
                source_index INTEGER NOT NULL,
                payload_hash TEXT NOT NULL,
                raw_size INTEGER NOT NULL,
                payload_zlib BLOB NOT NULL
            ) WITHOUT ROWID
            """
        )
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [
                ("schema_version", "soc.corpus_workbench_payload_store.v1"),
                ("source_sha256", "a" * 64),
                ("alert_count", "1"),
            ],
        )
        connection.execute(
            "INSERT INTO payloads VALUES (?, ?, ?, ?, ?)",
            (
                alert_id,
                source_index,
                payload_hash,
                len(encoded),
                sqlite3.Binary(zlib.compress(encoded)),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    store_sha256 = _sha256_file(store_path)
    index_path = tmp_path / "corpus.workbench-index.json"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": "soc.corpus_workbench_index.v3",
                "source": {"sha256": "a" * 64, "alert_count": 1},
                "payload_store": {
                    "schema_version": "soc.corpus_workbench_payload_store.v1",
                    "file_name": store_path.name,
                    "size_bytes": store_path.stat().st_size,
                    "sha256": store_sha256,
                },
                "cases": [
                    {
                        "alert_id": alert_id,
                        "source_index": source_index,
                        "payload_hash": payload_hash,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return index_path


def _sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()
