#!/usr/bin/env python3
"""Build a labeled Zeus alert dataset from DAMS exports and the existing sample."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pickle
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from soc_agent.normalizers import normalize_alert_payload  # noqa: E402
from validation.compact_zeus.shared.restricted_dataframe_pickle import (  # noqa: E402
    load_dataframe_pickle,
)

DATASET_SCHEMA_VERSION = "soc.validation.dams_labeled_dataset.v2"
DEFAULT_ALERTS_DIR = (
    ROOT / "datas/source/dams_exports/EOA_EXP2026081800142/order_20260818160830348"
)
DEFAULT_LABELS_DIR = (
    ROOT / "datas/source/dams_exports/EOA_EXP2026081800144/order_20260818163115342"
)
DEFAULT_BASE_PICKLE = ROOT / "datas/source/full_alert_2026_month_forth_sample_200.pkl"
DEFAULT_OUTPUT = (
    ROOT / "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.pkl"
)
DEFAULT_MANIFEST = DEFAULT_OUTPUT.with_suffix(".manifest.json")

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

PROVENANCE_COLUMNS = [
    "dataset_schema_version",
    "sample_origin",
    "source_refs",
    "canonical_payload_sha256",
    "canonical_event_time",
    "canonical_event_time_source",
    "alert_source_updated_date",
    "alert_source_metadata",
    "operational_label_available",
    "operational_label_source_ref",
    "operational_label_record",
    "operational_label_method",
]

ALERT_REQUIRED_COLUMNS = frozenset(
    {
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
    }
)

LABEL_REQUIRED_COLUMNS = frozenset(
    {
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
    }
)

STATUS_LABELS = {
    "0": "已忽略",
    "1": "待审阅",
    "2": "退回中",
    "3": "待确认",
    "4": "处理中",
    "5": "待复核",
    "6": "待关闭",
    "9": "已关闭",
    "10": "编辑",
}

STATUS_GROUND_LABELS = {
    "0": "忽略",
    "9": "转交",
}

ALERT_METADATA_COLUMNS = (
    "id",
    "status",
    "failed_count",
    "created_date",
    "updated_date",
    "created_by",
    "updated_by",
    "session_id",
)


@dataclass(frozen=True)
class CsvRowLocator:
    path: Path
    row_number: int
    source_order: int
    updated_at: datetime

    @property
    def sort_key(self) -> tuple[datetime, int, int]:
        return (self.updated_at, self.source_order, self.row_number)


def build_dataset(
    *,
    alert_files: Sequence[Path],
    label_files: Sequence[Path],
    base_frame: pd.DataFrame,
    base_source_ref: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build one unique-alert dataset while retaining alerts without labels."""

    if not alert_files:
        raise ValueError("at least one DAMS alert CSV is required")
    _validate_base_frame(base_frame)

    alert_locators, alert_scan = _scan_latest_rows(
        alert_files,
        required_columns=ALERT_REQUIRED_COLUMNS,
        kind="alert",
    )
    label_locators, label_scan = _scan_latest_rows(
        label_files,
        required_columns=LABEL_REQUIRED_COLUMNS,
        kind="label",
    )

    base_ids = {int(value) for value in base_frame["alert_id"]}
    overlap_ids = sorted(base_ids.intersection(alert_locators))
    if overlap_ids:
        raise ValueError(
            "base PKL and DAMS alerts overlap; payload authority requires review for "
            f"alert_ids={overlap_ids[:20]}"
        )

    rows = _base_rows(base_frame, source_ref=base_source_ref)
    dams_rows = [
        _build_alert_row(row, source_ref=source_ref)
        for _, row, source_ref in _selected_rows(
            alert_files,
            alert_locators,
            required_columns=ALERT_REQUIRED_COLUMNS,
            kind="alert",
        )
    ]
    dams_rows.sort(key=lambda item: item["alert_id"])
    rows.extend(dams_rows)

    rows_by_id = {int(row["alert_id"]): row for row in rows}
    if len(rows_by_id) != len(rows):
        raise AssertionError("output row assembly produced duplicate alert IDs")

    joined_to_base = 0
    joined_to_dams = 0
    orphan_label_ids: list[int] = []
    ground_label_counts: Counter[str] = Counter()
    status_code_counts: Counter[str] = Counter()

    for alert_id, label_row, source_ref in _selected_rows(
        label_files,
        label_locators,
        required_columns=LABEL_REQUIRED_COLUMNS,
        kind="label",
    ):
        target = rows_by_id.get(alert_id)
        if target is None:
            orphan_label_ids.append(alert_id)
            continue
        _apply_operational_label(target, label_row, source_ref=source_ref)
        if alert_id in base_ids:
            joined_to_base += 1
        else:
            joined_to_dams += 1
        if target["ground_label"] is not None:
            ground_label_counts[str(target["ground_label"])] += 1
        status_code = _optional_text(label_row.get("status"))
        if status_code is not None:
            status_code_counts[status_code] += 1

    for row in rows:
        event_time = _canonical_event_time(row)
        row["canonical_event_time"] = event_time.isoformat()
        row["canonical_event_time_source"] = "soc.normalizer"

    rows.sort(
        key=lambda item: (
            datetime.fromisoformat(item["canonical_event_time"]),
            int(item["alert_id"]),
        )
    )

    dataset = pd.DataFrame(
        rows,
        columns=BASE_COLUMNS + PROVENANCE_COLUMNS,
        dtype=object,
    )
    dataset["alert_id"] = dataset["alert_id"].astype("int64")
    for column in dataset.columns:
        if column != "alert_id":
            dataset[column] = dataset[column].astype(object)
    dataset.columns = pd.Index(dataset.columns.tolist(), dtype=object)

    if dataset["alert_id"].duplicated().any():
        raise AssertionError("output dataset contains duplicate alert IDs")
    if len(dataset) != len(base_frame) + len(alert_locators):
        raise AssertionError(
            "output dataset row count does not preserve all unique alerts"
        )

    labeled_rows = int(dataset["operational_label_available"].eq(True).sum())  # noqa: E712
    report = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "alerts": {
            **alert_scan,
            "base_overlap_alert_ids": overlap_ids,
            "payload_json_parsed_rows": len(dams_rows),
            "payload_alert_id_match_rows": len(dams_rows),
        },
        "labels": {
            **label_scan,
            "joined_rows": joined_to_base + joined_to_dams,
            "joined_to_base_rows": joined_to_base,
            "joined_to_dams_rows": joined_to_dams,
            "orphan_rows": len(orphan_label_ids),
            "orphan_alert_ids": sorted(orphan_label_ids),
            "ground_label_counts": dict(sorted(ground_label_counts.items())),
            "status_code_counts": dict(sorted(status_code_counts.items())),
        },
        "base": {
            "rows": len(base_frame),
            "unique_alert_ids": int(base_frame["alert_id"].nunique()),
            "source_ref": base_source_ref,
        },
        "output": {
            "rows": len(dataset),
            "unique_alert_ids": int(dataset["alert_id"].nunique()),
            "labeled_rows": labeled_rows,
            "unlabeled_rows": len(dataset) - labeled_rows,
            "all_unique_alerts_preserved": True,
            "sort_order": "canonical_event_time_asc_alert_id_asc",
            "first_event_time": dataset.iloc[0]["canonical_event_time"],
            "last_event_time": dataset.iloc[-1]["canonical_event_time"],
        },
    }
    return dataset, report


def _scan_latest_rows(
    files: Sequence[Path],
    *,
    required_columns: frozenset[str],
    kind: str,
) -> tuple[dict[int, CsvRowLocator], dict[str, Any]]:
    latest: dict[int, CsvRowLocator] = {}
    raw_rows = 0
    duplicate_ids: set[int] = set()

    for source_order, path, row_number, row in _iter_csv_rows(
        files,
        required_columns=required_columns,
        kind=kind,
    ):
        raw_rows += 1
        alert_id = _parse_alert_id(
            row.get("alert_id"), source_ref=_row_ref(path, row_number)
        )
        updated_at = _parse_updated_at(
            row.get("updated_date"), source_ref=_row_ref(path, row_number)
        )
        locator = CsvRowLocator(
            path=path,
            row_number=row_number,
            source_order=source_order,
            updated_at=updated_at,
        )
        previous = latest.get(alert_id)
        if previous is not None:
            duplicate_ids.add(alert_id)
        if previous is None or locator.sort_key > previous.sort_key:
            latest[alert_id] = locator

    return latest, {
        "raw_rows": raw_rows,
        "latest_unique_rows": len(latest),
        "duplicate_alert_id_count": len(duplicate_ids),
        "duplicate_alert_ids": sorted(duplicate_ids),
    }


def _selected_rows(
    files: Sequence[Path],
    latest: Mapping[int, CsvRowLocator],
    *,
    required_columns: frozenset[str],
    kind: str,
) -> Iterable[tuple[int, dict[str, str], str]]:
    for source_order, path, row_number, row in _iter_csv_rows(
        files,
        required_columns=required_columns,
        kind=kind,
    ):
        alert_id = _parse_alert_id(
            row.get("alert_id"), source_ref=_row_ref(path, row_number)
        )
        locator = latest.get(alert_id)
        if locator is None:
            continue
        if (
            locator.path != path
            or locator.row_number != row_number
            or locator.source_order != source_order
        ):
            continue
        yield alert_id, row, _row_ref(path, row_number)


def _iter_csv_rows(
    files: Sequence[Path],
    *,
    required_columns: frozenset[str],
    kind: str,
) -> Iterable[tuple[int, Path, int, dict[str, str]]]:
    _maximize_csv_field_size_limit()
    for source_order, path in enumerate(files):
        if not path.is_file():
            raise FileNotFoundError(path)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = set(reader.fieldnames or [])
            missing = sorted(required_columns.difference(fieldnames))
            if missing:
                raise ValueError(f"{path}: missing {kind} CSV columns: {missing}")
            for row_number, row in enumerate(reader, start=2):
                yield source_order, path, row_number, dict(row)


def _build_alert_row(row: Mapping[str, str], *, source_ref: str) -> dict[str, Any]:
    alert_id = _parse_alert_id(row.get("alert_id"), source_ref=source_ref)
    raw_alert_data = row.get("alert_data")
    try:
        payload = json.loads(raw_alert_data or "")
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source_ref}: alert_data is invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise TypeError(f"{source_ref}: alert_data must decode to an object")

    alert = payload.get("alert")
    if not isinstance(alert, Mapping):
        raise ValueError(f"{source_ref}: alert_data.alert must be an object")
    payload_alert_id = _parse_alert_id(
        alert.get("alertId"), source_ref=f"{source_ref} alert.alertId"
    )
    if payload_alert_id != alert_id:
        raise ValueError(
            f"{source_ref}: payload alertId {payload_alert_id} != CSV alert_id {alert_id}"
        )

    hit_logs = alert.get("hitLog")
    first_hit = (
        hit_logs[0]
        if isinstance(hit_logs, list) and hit_logs and isinstance(hit_logs[0], Mapping)
        else {}
    )
    full_data = {
        "app_code": row.get("app_code"),
        "flow_id": row.get("flow_id"),
        "alert_id": str(alert_id),
        "alert_data": payload,
    }
    return {
        "alert_id": alert_id,
        "alert_full_data": full_data,
        "agent_response": row.get("agent_response"),
        "risk_level": alert.get("riskLevel"),
        "topic": first_hit.get("topic"),
        "topic_name": first_hit.get("topicName"),
        "related_status_dict": _related_statuses(payload),
        "status": alert.get("status"),
        "ignore_reason": None,
        "predict_label": None,
        "action_label": None,
        "status_label": None,
        "ground_label": None,
        "execute_type": alert.get("executeType"),
        "primary_type": alert.get("primaryType"),
        "secondary_type": alert.get("secondaryType"),
        "tertiary_type": alert.get("tertiaryType"),
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "sample_origin": "dams_export",
        "source_refs": [source_ref],
        "canonical_payload_sha256": _canonical_sha256(full_data),
        "canonical_event_time": None,
        "canonical_event_time_source": None,
        "alert_source_updated_date": row.get("updated_date"),
        "alert_source_metadata": {key: row.get(key) for key in ALERT_METADATA_COLUMNS},
        "operational_label_available": False,
        "operational_label_source_ref": None,
        "operational_label_record": None,
        "operational_label_method": None,
    }


def _base_rows(frame: pd.DataFrame, *, source_ref: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for original in frame.to_dict(orient="records"):
        alert_id = int(original["alert_id"])
        row = {column: original.get(column) for column in BASE_COLUMNS}
        row.update(
            {
                "dataset_schema_version": DATASET_SCHEMA_VERSION,
                "sample_origin": "existing_sample_200",
                "source_refs": [f"{source_ref}#alert_id={alert_id}"],
                "canonical_payload_sha256": _canonical_sha256(row["alert_full_data"]),
                "canonical_event_time": None,
                "canonical_event_time_source": None,
                "alert_source_updated_date": None,
                "alert_source_metadata": None,
                "operational_label_available": _base_has_label(row),
                "operational_label_source_ref": None,
                "operational_label_record": None,
                "operational_label_method": (
                    "existing_sample" if _base_has_label(row) else None
                ),
            }
        )
        rows.append(row)
    return rows


def _canonical_event_time(row: Mapping[str, Any]) -> datetime:
    alert_id = _parse_alert_id(row.get("alert_id"), source_ref="assembled dataset")
    wrapper = row.get("alert_full_data")
    if not isinstance(wrapper, Mapping):
        raise ValueError(f"alert {alert_id} has no alert_full_data object")
    payload = wrapper.get("alert_data")
    if not isinstance(payload, Mapping):
        raise ValueError(f"alert {alert_id} has no alert_full_data.alert_data object")
    event_time = normalize_alert_payload(dict(payload)).event.event_time
    if event_time is None:
        raise ValueError(f"alert {alert_id} has no canonical event_time")
    if event_time.utcoffset() is None:
        raise ValueError(f"alert {alert_id} canonical event_time is timezone-naive")
    return event_time


def _apply_operational_label(
    row: dict[str, Any],
    label: Mapping[str, str],
    *,
    source_ref: str,
) -> None:
    status_code = _optional_text(label.get("status"))
    exported_ground_label = _optional_text(label.get("预警研判结果"))
    if exported_ground_label is not None:
        ground_label = exported_ground_label
        method = "exported_triage_result"
    else:
        ground_label = STATUS_GROUND_LABELS.get(status_code or "")
        method = "status_code_mapping" if ground_label is not None else None

    row["ignore_reason"] = _optional_text(label.get("忽略原因"))
    row["predict_label"] = _optional_text(label.get("模型研判结果"))
    row["action_label"] = _optional_text(label.get("模型处置结果"))
    row["status_label"] = STATUS_LABELS.get(status_code or "")
    row["ground_label"] = ground_label
    row["operational_label_available"] = True
    row["operational_label_source_ref"] = source_ref
    row["operational_label_record"] = dict(label)
    row["operational_label_method"] = method
    row["source_refs"] = [*row["source_refs"], source_ref]


def _base_has_label(row: Mapping[str, Any]) -> bool:
    return any(
        not _is_missing(row.get(column))
        for column in (
            "ignore_reason",
            "predict_label",
            "action_label",
            "status_label",
            "ground_label",
        )
    )


def _validate_base_frame(frame: pd.DataFrame) -> None:
    missing = sorted(set(BASE_COLUMNS).difference(frame.columns))
    if missing:
        raise ValueError(f"base DataFrame missing columns: {missing}")
    if frame["alert_id"].isna().any():
        raise ValueError("base DataFrame contains null alert_id")
    if frame["alert_id"].duplicated().any():
        raise ValueError("base DataFrame contains duplicate alert_id")
    for row_index, value in enumerate(frame["alert_full_data"]):
        if not isinstance(value, dict):
            raise TypeError(f"base row {row_index}: alert_full_data must be an object")


def _related_statuses(payload: Mapping[str, Any]) -> Counter[str]:
    reasons: list[str] = []
    related_alerts = payload.get("relatedAlertList")
    if not isinstance(related_alerts, list):
        return Counter()
    for related_alert in related_alerts:
        if not isinstance(related_alert, Mapping):
            continue
        content = related_alert.get("content")
        if not isinstance(content, list):
            continue
        for content_item in content:
            if not isinstance(content_item, Mapping):
                continue
            raw_content = content_item.get("content")
            if not isinstance(raw_content, str):
                continue
            try:
                parsed = json.loads(raw_content)
            except json.JSONDecodeError:
                continue
            entries = parsed.get("content") if isinstance(parsed, Mapping) else None
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if (
                    not isinstance(entry, Mapping)
                    or entry.get("field_cn") != "忽略原因"
                ):
                    continue
                reason = _optional_text(entry.get("field_content"))
                if reason is not None:
                    reasons.append(f"忽略原因-{reason}")
    return Counter(reasons)


def write_dataset_atomic(frame: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f".{output_path.name}.tmp")
    try:
        frame.to_pickle(temp_path, protocol=pickle.HIGHEST_PROTOCOL)
        temp_path.replace(output_path)
    finally:
        temp_path.unlink(missing_ok=True)

    reloaded = load_dataframe_pickle(output_path)
    if not frame.equals(reloaded):
        raise AssertionError("restricted-unpickler round trip changed DAMS dataset")


def write_manifest_atomic(manifest: Mapping[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f".{output_path.name}.tmp")
    try:
        temp_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(output_path)
    finally:
        temp_path.unlink(missing_ok=True)


def build_manifest(
    *,
    alert_files: Sequence[Path],
    label_files: Sequence[Path],
    base_path: Path,
    output_path: Path,
    workbench_index_path: Path,
    payload_store_path: Path,
    report: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": DATASET_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "builder": _file_identity(Path(__file__)),
        "inputs": {
            "alert_csvs": [_file_identity(path) for path in alert_files],
            "label_csvs": [_file_identity(path) for path in label_files],
            "base_pickle": _file_identity(base_path),
        },
        "build": dict(report),
        "output": {
            **_file_identity(output_path),
            "rows": report["output"]["rows"],
            "unique_alert_ids": report["output"]["unique_alert_ids"],
            "workbench_index": _file_identity(workbench_index_path),
            "workbench_payload_store": _file_identity(payload_store_path),
        },
        "claim_boundaries": [
            "Every unique DAMS alert is retained even when no label row matches.",
            "The newest updated_date wins independently within alert and label exports.",
            "Rows are ordered by timezone-aware canonical alert event time, then alert ID; export row order is never used as chronology.",
            "DAMS pre-alert payload JSON and historical agent_response are preserved.",
            "预警研判结果 is an operational workflow label, not independent analyst truth.",
            "模型研判结果 is historical model output and never becomes ground truth.",
            "A label without alert payload is reported as orphan and never fabricates an alert.",
            "The existing sample PKL is preserved and must not overlap DAMS alert IDs without review.",
        ],
    }


def _file_identity(path: Path) -> dict[str, Any]:
    return {
        "path": _relative_path(path),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, Counter):
        return dict(value)
    if _is_missing(value):
        return None
    raise TypeError(f"value is not JSON serializable: {type(value)!r}")


def _parse_alert_id(value: Any, *, source_ref: str) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source_ref}: invalid alert_id {value!r}") from exc


def _parse_updated_at(value: Any, *, source_ref: str) -> datetime:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{source_ref}: updated_date is empty")
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{source_ref}: invalid updated_date {text!r}") from exc


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if not pd.api.types.is_scalar(value):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _maximize_csv_field_size_limit() -> None:
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10


def _row_ref(path: Path, row_number: int) -> str:
    return f"{_relative_path(path)}#row={row_number}"


def _relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _csv_files(directory: Path) -> list[Path]:
    files = sorted(directory.glob("*.csv"))
    if not files:
        raise ValueError(f"no CSV files found in {directory}")
    return files


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alerts-dir", type=Path, default=DEFAULT_ALERTS_DIR)
    parser.add_argument("--labels-dir", type=Path, default=DEFAULT_LABELS_DIR)
    parser.add_argument("--base-pickle", type=Path, default=DEFAULT_BASE_PICKLE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--workbench-index",
        type=Path,
        default=None,
        help="compact DEV workbench index; defaults beside --output",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    alert_files = _csv_files(args.alerts_dir)
    label_files = _csv_files(args.labels_dir)
    base_frame = load_dataframe_pickle(args.base_pickle)
    dataset, report = build_dataset(
        alert_files=alert_files,
        label_files=label_files,
        base_frame=base_frame,
        base_source_ref=_relative_path(args.base_pickle),
    )
    write_dataset_atomic(dataset, args.output)
    from soc_agent.demo.corpus_workbench import (
        build_corpus_workbench_index,
        corpus_workbench_payload_store_path,
    )

    workbench_index = build_corpus_workbench_index(
        args.output,
        output_path=args.workbench_index,
    )
    payload_store = corpus_workbench_payload_store_path(
        args.output,
        index_path=workbench_index,
    )
    manifest = build_manifest(
        alert_files=alert_files,
        label_files=label_files,
        base_path=args.base_pickle,
        output_path=args.output,
        workbench_index_path=workbench_index,
        payload_store_path=payload_store,
        report=report,
    )
    write_manifest_atomic(manifest, args.manifest)
    print(
        json.dumps(
            {
                "status": "completed",
                "output": _relative_path(args.output),
                "manifest": _relative_path(args.manifest),
                "workbench_index": _relative_path(workbench_index),
                "workbench_payload_store": _relative_path(payload_store),
                **report["output"],
                "orphan_label_rows": report["labels"]["orphan_rows"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
