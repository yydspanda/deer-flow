from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

from validation.compact_zeus.corpus.build_dams_labeled_dataset import (
    DATASET_SCHEMA_VERSION,
    build_dataset,
    write_dataset_atomic,
)
from validation.compact_zeus.shared.restricted_dataframe_pickle import (
    load_dataframe_pickle,
)
from soc_agent.demo.corpus_workbench import (
    CORPUS_WORKBENCH_INDEX_VERSION,
    _load_cases,
    _load_payload_from_store,
    build_corpus_workbench_index,
    corpus_workbench_payload_store_path,
)

BASE_COLUMNS = [
    "alert_id",
    "alert_full_data",
    "agent_response",
    "risk_level",
    "topic",
    "topic_name",
    "related_status_dict",
    "status",
    "ignore_reason",
    "predict_label",
    "action_label",
    "status_label",
    "ground_label",
    "execute_type",
    "primary_type",
    "secondary_type",
    "tertiary_type",
]

ALERT_COLUMNS = [
    "id",
    "alert_id",
    "flow_id",
    "alert_data",
    "agent_response",
    "status",
    "failed_count",
    "created_date",
    "updated_date",
    "created_by",
    "updated_by",
    "session_id",
    "app_code",
]

LABEL_COLUMNS = [
    "alert_id",
    "code",
    "status",
    "alert_time",
    "updated_date",
    "topic",
    "content",
    "模型研判结果",
    "模型处置结果",
    "忽略原因",
    "备注",
    "rn",
    "预警研判结果",
    "是否一致",
    "是否有处置结果",
]


def _payload(alert_id: int, *, marker: str) -> dict:
    return {
        "alert": {
            "alertId": str(alert_id),
            "createAt": f"2026-08-01 00:{alert_id:02d}:00",
            "executeType": "0",
            "riskLevel": "medium",
            "status": "待审阅",
            "primaryType": "入侵预警",
            "secondaryType": "安全合规",
            "tertiaryType": "网络攻击",
            "hitLog": [
                {
                    "topic": "sec_guard_apt",
                    "topicName": "360天眼APT",
                    "zeusRawLogs": [{"marker": marker}],
                }
            ],
        },
        "relatedAlertList": [],
        "opaque_vendor_section": {"must_survive": marker},
    }


def _alert_row(alert_id: int, *, marker: str, updated_date: str) -> dict[str, str]:
    return {
        "id": f"row-{marker}",
        "alert_id": str(alert_id),
        "flow_id": "alert_agent",
        "alert_data": json.dumps(_payload(alert_id, marker=marker), ensure_ascii=False),
        "agent_response": json.dumps({"historical": marker}, ensure_ascii=False),
        "status": "successed",
        "failed_count": "0",
        "created_date": "2026-08-01 00:00:00.000000",
        "updated_date": updated_date,
        "created_by": "fixture",
        "updated_by": "fixture",
        "session_id": f"session-{marker}",
        "app_code": "zeus",
    }


def _label_row(
    alert_id: int,
    *,
    verdict: str,
    status: str,
    updated_date: str,
) -> dict[str, str]:
    return {
        "alert_id": str(alert_id),
        "code": "RULE-1",
        "status": status,
        "alert_time": "2026-08-01 00:00:00.000",
        "updated_date": updated_date,
        "topic": "sec_guard_apt",
        "content": '{"operator_note":"reviewed"}',
        "模型研判结果": "忽略",
        "模型处置结果": "-1",
        "忽略原因": "误报-AI研判忽略",
        "备注": "fixture-note",
        "rn": "1",
        "预警研判结果": verdict,
        "是否一致": "1",
        "是否有处置结果": "0",
    }


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _base_frame() -> pd.DataFrame:
    payload = _payload(3, marker="base")
    row = {
        "alert_id": 3,
        "alert_full_data": {
            "app_code": "zeus",
            "flow_id": "alert_agent",
            "alert_id": "3",
            "alert_data": payload,
        },
        "agent_response": '{"historical":"base"}',
        "risk_level": "medium",
        "topic": "sec_guard_apt",
        "topic_name": "360天眼APT",
        "related_status_dict": Counter(),
        "status": "待审阅",
        "ignore_reason": None,
        "predict_label": None,
        "action_label": None,
        "status_label": None,
        "ground_label": None,
        "execute_type": "0",
        "primary_type": "入侵预警",
        "secondary_type": "安全合规",
        "tertiary_type": "网络攻击",
    }
    frame = pd.DataFrame([row], columns=BASE_COLUMNS, dtype=object)
    frame["alert_id"] = frame["alert_id"].astype("int64")
    return frame


def test_build_dataset_keeps_unlabeled_alert_and_uses_latest_rows(
    tmp_path: Path,
) -> None:
    alert_file = tmp_path / "alerts" / "alerts.csv"
    label_file = tmp_path / "labels" / "labels.csv"
    _write_csv(
        alert_file,
        ALERT_COLUMNS,
        [
            _alert_row(1, marker="old", updated_date="2026-08-01 01:00:00.000000"),
            _alert_row(
                2, marker="unlabeled", updated_date="2026-08-01 02:00:00.000000"
            ),
            _alert_row(1, marker="new", updated_date="2026-08-01 03:00:00.000000"),
        ],
    )
    _write_csv(
        label_file,
        LABEL_COLUMNS,
        [
            _label_row(
                1,
                verdict="忽略",
                status="0",
                updated_date="2026-08-01 04:00:00.000",
            ),
            _label_row(
                999,
                verdict="转交",
                status="9",
                updated_date="2026-08-01 05:00:00.000",
            ),
        ],
    )

    dataset, report = build_dataset(
        alert_files=[alert_file],
        label_files=[label_file],
        base_frame=_base_frame(),
        base_source_ref="base.pkl",
    )

    assert dataset["alert_id"].tolist() == [1, 2, 3]
    assert dataset["canonical_event_time"].tolist() == sorted(
        dataset["canonical_event_time"].tolist()
    )
    assert set(dataset["canonical_event_time_source"]) == {"soc.normalizer"}
    assert dataset["alert_id"].is_unique
    by_id = dataset.set_index("alert_id")
    assert by_id.loc[1, "alert_full_data"]["alert_data"] == _payload(1, marker="new")
    assert by_id.loc[1, "agent_response"] == '{"historical": "new"}'
    assert by_id.loc[1, "operational_label_available"] is True
    assert by_id.loc[1, "ground_label"] == "忽略"
    assert by_id.loc[1, "status_label"] == "已忽略"
    assert by_id.loc[1, "ignore_reason"] == "误报-AI研判忽略"
    assert by_id.loc[1, "operational_label_record"]["备注"] == "fixture-note"
    assert by_id.loc[1, "alert_source_metadata"]["session_id"] == "session-new"
    assert by_id.loc[2, "operational_label_available"] is False
    assert by_id.loc[2, "ground_label"] is None
    assert by_id.loc[2, "alert_full_data"]["alert_data"] == _payload(
        2, marker="unlabeled"
    )
    assert by_id.loc[3, "sample_origin"] == "existing_sample_200"
    assert by_id.loc[3, "alert_full_data"] == _base_frame().iloc[0]["alert_full_data"]
    assert set(dataset["dataset_schema_version"]) == {DATASET_SCHEMA_VERSION}
    assert report["alerts"]["raw_rows"] == 3
    assert report["alerts"]["latest_unique_rows"] == 2
    assert report["labels"]["joined_rows"] == 1
    assert report["labels"]["orphan_alert_ids"] == [999]
    assert report["output"]["rows"] == 3
    assert report["output"]["labeled_rows"] == 1
    assert report["output"]["unlabeled_rows"] == 2
    assert report["output"]["sort_order"] == ("canonical_event_time_asc_alert_id_asc")


def test_label_latest_row_wins_and_raw_label_fields_are_preserved(
    tmp_path: Path,
) -> None:
    alert_file = tmp_path / "alerts.csv"
    label_file = tmp_path / "labels.csv"
    _write_csv(
        alert_file,
        ALERT_COLUMNS,
        [_alert_row(10, marker="only", updated_date="2026-08-01 01:00:00")],
    )
    old_label = _label_row(
        10,
        verdict="忽略",
        status="0",
        updated_date="2026-08-01 02:00:00",
    )
    new_label = _label_row(
        10,
        verdict="转交",
        status="5",
        updated_date="2026-08-01 03:00:00",
    )
    new_label["content"] = "new-content"
    _write_csv(label_file, LABEL_COLUMNS, [old_label, new_label])

    dataset, report = build_dataset(
        alert_files=[alert_file],
        label_files=[label_file],
        base_frame=pd.DataFrame(columns=BASE_COLUMNS),
        base_source_ref="base.pkl",
    )

    row = dataset.iloc[0]
    assert row["ground_label"] == "转交"
    assert row["status_label"] == "待复核"
    assert row["operational_label_record"]["content"] == "new-content"
    assert report["labels"]["raw_rows"] == 2
    assert report["labels"]["latest_unique_rows"] == 1


def test_build_dataset_rejects_alert_id_mismatch(tmp_path: Path) -> None:
    alert_file = tmp_path / "alerts.csv"
    row = _alert_row(20, marker="bad", updated_date="2026-08-01 01:00:00")
    row["alert_data"] = json.dumps(_payload(21, marker="bad"))
    _write_csv(alert_file, ALERT_COLUMNS, [row])

    with pytest.raises(ValueError, match="payload alertId 21 != CSV alert_id 20"):
        build_dataset(
            alert_files=[alert_file],
            label_files=[],
            base_frame=pd.DataFrame(columns=BASE_COLUMNS),
            base_source_ref="base.pkl",
        )


def test_dataset_round_trips_through_restricted_unpickler(tmp_path: Path) -> None:
    alert_file = tmp_path / "alerts.csv"
    _write_csv(
        alert_file,
        ALERT_COLUMNS,
        [_alert_row(30, marker="roundtrip", updated_date="2026-08-01 01:00:00")],
    )
    dataset, _ = build_dataset(
        alert_files=[alert_file],
        label_files=[],
        base_frame=pd.DataFrame(columns=BASE_COLUMNS),
        base_source_ref="base.pkl",
    )
    output = tmp_path / "dataset.pkl"

    write_dataset_atomic(dataset, output)

    assert load_dataframe_pickle(output).equals(dataset)


def test_workbench_index_preserves_canonical_order_without_payloads(
    tmp_path: Path,
) -> None:
    alert_file = tmp_path / "alerts.csv"
    _write_csv(
        alert_file,
        ALERT_COLUMNS,
        [
            _alert_row(2, marker="later", updated_date="2026-08-01 02:00:00"),
            _alert_row(1, marker="earlier", updated_date="2026-08-01 01:00:00"),
        ],
    )
    dataset, _ = build_dataset(
        alert_files=[alert_file],
        label_files=[],
        base_frame=_base_frame(),
        base_source_ref="base.pkl",
    )
    output = tmp_path / "dataset.pkl"
    write_dataset_atomic(dataset, output)

    index_path = build_corpus_workbench_index(output)
    payload_store = corpus_workbench_payload_store_path(
        output,
        index_path=index_path,
    )
    document = json.loads(index_path.read_text(encoding="utf-8"))
    cases = _load_cases(output, index_path=index_path)

    assert document["schema_version"] == CORPUS_WORKBENCH_INDEX_VERSION
    assert document["sort_order"] == "canonical_event_time_asc_alert_id_asc"
    assert document["memory_profile"]["aggregation_window_seconds"] == 30 * 24 * 60 * 60
    assert [item["alert_id"] for item in document["cases"]] == ["1", "2", "3"]
    assert all("payload" not in item for item in document["cases"])
    assert document["payload_store"]["file_name"] == payload_store.name
    assert payload_store.is_file()
    assert list(cases) == ["1", "2", "3"]
    assert all(item.payload is None for item in cases.values())
    assert (
        _load_payload_from_store(payload_store, cases["1"])["alert"]["alertId"] == "1"
    )
