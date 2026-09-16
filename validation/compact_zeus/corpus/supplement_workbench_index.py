"""Reuse frozen DEV corpus cases when appending independently verified rows."""

from __future__ import annotations

import json
import shutil
import sqlite3
import zlib
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from soc_agent.demo.corpus_workbench import (
    _build_cases_from_frame,
    _case_index_record,
    _case_readiness,
    _load_cases,
    _resolve_payload_store,
)
from validation.compact_zeus.corpus.build_dams_labeled_dataset import (
    _sha256_file,
    write_dataset_atomic,
)


def build_supplemented_index(
    existing_path: Path,
    output_path: Path,
    additions: pd.DataFrame,
    *,
    index_path: Path | None = None,
) -> Path:
    """Caller must verify unchanged old rows and chronological PKL order first."""
    if existing_path.resolve() == output_path.resolve():
        raise ValueError("supplemented index must be staged separately")
    old_index = existing_path.with_suffix(".workbench-index.json")
    old_document = json.loads(old_index.read_text(encoding="utf-8"))
    cases = _load_cases(existing_path, source_sha256=_sha256_file(existing_path))
    store, expected_hash = _resolve_payload_store(old_index)
    if _sha256_file(store) != expected_hash:
        raise ValueError("existing payload store hash mismatch")
    target = index_path or output_path.with_suffix(".workbench-index.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    supplement = target.parent / ".supplement-cases.pkl"
    try:
        write_dataset_atomic(additions, supplement)
        new_cases = _build_cases_from_frame(supplement)
    finally:
        supplement.unlink(missing_ok=True)
    if set(cases) & set(new_cases):
        raise ValueError("index supplements must not replace existing alert IDs")
    ordered = sorted(
        [*cases.values(), *new_cases.values()],
        key=lambda case: (case.observed_at_value.astimezone(UTC), int(case.alert_id)),
    )
    group_counts = Counter(case.group_id for case in ordered)
    window_counts = Counter(case.window_id for case in ordered)
    finalized = [
        replace(
            case,
            source_index=index,
            sequence_number=index + 1,
            group_alert_count=group_counts[case.group_id],
            window_alert_count=window_counts[case.window_id],
            readiness=_case_readiness(
                case,
                group_alert_count=group_counts[case.group_id],
                window_alert_count=window_counts[case.window_id],
            ),
        )
        for index, case in enumerate(ordered)
    ]
    new_hash = _sha256_file(output_path)
    target_store = target.parent / (output_path.stem + ".workbench-payloads.sqlite")
    temporary = target_store.with_name("." + target_store.name + ".tmp")
    shutil.copyfile(store, temporary)
    try:
        connection = sqlite3.connect(temporary)
        try:
            with connection:
                connection.execute("PRAGMA journal_mode=DELETE")
                for case in finalized:
                    if case.alert_id not in new_cases:
                        connection.execute(
                            "UPDATE payloads SET source_index=? WHERE alert_id=?",
                            (case.source_index, case.alert_id),
                        )
                        continue
                    encoded = json.dumps(
                        case.payload, ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                    connection.execute(
                        "INSERT INTO payloads VALUES (?, ?, ?, ?, ?)",
                        (
                            case.alert_id,
                            case.source_index,
                            case.payload_hash,
                            len(encoded),
                            zlib.compress(encoded, level=6),
                        ),
                    )
                connection.executemany(
                    "UPDATE metadata SET value=? WHERE key=?",
                    [(new_hash, "source_sha256"), (str(len(finalized)), "alert_count")],
                )
        finally:
            connection.close()
        temporary.replace(target_store)
    finally:
        temporary.unlink(missing_ok=True)
    document = {
        **old_document,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "file_name": output_path.name,
            "size_bytes": output_path.stat().st_size,
            "sha256": new_hash,
            "alert_count": len(finalized),
        },
        "payload_store": {
            **old_document["payload_store"],
            "file_name": target_store.name,
            "size_bytes": target_store.stat().st_size,
            "sha256": _sha256_file(target_store),
        },
        "cases": [_case_index_record(case) for case in finalized],
    }
    temporary_index = target.with_name("." + target.name + ".tmp")
    temporary_index.write_text(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary_index.replace(target)
    return target
