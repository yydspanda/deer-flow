"""Read-only v2 matcher / current parser comparison; no model or Memory calls."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import html
import json
from pathlib import Path
import platform
import sqlite3
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from validation.compact_zeus.audits.normalization_assistance_trial import (  # noqa: E402
    LEGACY_QUOTED_KV_RE,
    check_quoted_kv,
    git_value,
    select_source,
    write_json,
)
from soc_agent.core.runtime import build_analysis_request_for_payload  # noqa: E402
from soc_agent.normalizers import normalize_alert_payload  # noqa: E402
from soc_agent.utils.hashing import stable_hash  # noqa: E402

DEFAULT_IDS = [
    "2448412",
    "2448580",
    "2450017",
    "2455416",
    "2459908",
    "2464210",
    "2466490",
    "2483048",
]


def compare_payload(payload: dict) -> dict:
    source = select_source(payload)
    message = source["text"]
    before = {
        key: html.unescape(value) for key, value in LEGACY_QUOTED_KV_RE.findall(message)
    }
    alert = normalize_alert_payload(payload)
    parsed = alert.extensions["parsed_raw_messages"][0]
    if parsed["parser_name"] != "pingan_quoted_kv" or not parsed.get("syntax_coverage"):
        raise ValueError(
            "audit requires the current quoted-KV parser and syntax coverage"
        )
    request = build_analysis_request_for_payload(payload)
    primary = (
        json.loads(request.primary_evidence.content) if request.primary_evidence else {}
    )
    after = parsed["fields"]
    changed = [
        key
        for key in sorted(before.keys() | after.keys())
        if (key in before) != (key in after) or before.get(key) != after.get(key)
    ]
    return {
        "alert_id": alert.alert_id,
        "source": source,
        "before_v2": {"fields": before, "inspection": check_quoted_kv(message)},
        "after": parsed,
        "changed_fields": changed,
        "changed_fields_visible_to_model": {
            key: primary.get("fields", {}).get(key) == after[key]
            for key in changed
            if key in after
        },
        "current_canonical": alert.entities.model_dump(mode="json"),
        "raw_preserved": alert.raw == payload,
        "interpretation": "Parser and bounded-input check only; not a full Runtime run, canonical completeness or fingerprint quality acceptance.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alert-ids", nargs="+", default=DEFAULT_IDS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= len(args.alert_ids) <= 20 or len(set(args.alert_ids)) != len(
        args.alert_ids
    ):
        parser.error("select 1..20 distinct alert IDs")
    started = time.monotonic()
    directory = ROOT / "validation/compact_zeus/data/corpus"
    index_path = directory / "full_alert_dams_labeled_merged.workbench-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    expected = {case["alert_id"]: case["payload_hash"] for case in index["cases"]}
    args.output.mkdir(parents=True, mode=0o700, exist_ok=False)
    cases, hashes = [], {}
    store_path = directory / "full_alert_dams_labeled_merged.workbench-payloads.sqlite"
    with sqlite3.connect(store_path.as_uri() + "?mode=ro", uri=True) as connection:
        for alert_id in args.alert_ids:
            row = connection.execute(
                "SELECT payload_zlib, payload_hash FROM payloads WHERE alert_id=?",
                (alert_id,),
            ).fetchone()
            if not row or expected.get(alert_id) != row[1]:
                raise ValueError(f"index/payload-store mismatch: {alert_id}")
            payload = json.loads(zlib.decompress(row[0]))
            if stable_hash(payload) != row[1]:
                raise ValueError(f"payload content mismatch: {alert_id}")
            result = compare_payload(payload)
            write_json(args.output / f"{alert_id}.parsing.json", result)
            cases.append(
                {
                    "alert_id": alert_id,
                    "changed_fields": result["changed_fields"],
                    "complete": result["after"]["syntax_coverage"]["complete"],
                    "raw_preserved": result["raw_preserved"],
                    "model_visible": result["changed_fields_visible_to_model"],
                }
            )
            hashes[alert_id] = row[1]
    summary = {
        "schema_version": "soc.quoted_kv_parser_audit.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "cases": cases,
        "new_model_calls": 0,
        "new_tokens": 0,
        "soc_database_writes": 0,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "code_commit": git_value("rev-parse", "HEAD"),
        "upstream_commit": git_value("rev-parse", "upstream/main"),
        "model": "not_called",
        "config_hash": stable_hash(
            {
                "parser": "pingan_quoted_kv",
                "version": "v3",
                "alert_ids": args.alert_ids,
                "source_selection": "single-message",
                "primary_evidence": "production_defaults",
            }
        ),
        "data_hash": stable_hash(hashes),
        "source_hashes": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [
                Path(__file__),
                ROOT / "backend/soc_agent/normalizers/pingan_messages.py",
                ROOT / "backend/soc_agent/contracts/schemas.py",
            ]
        },
        "hardware": platform.platform() + " / Python " + platform.python_version(),
        "command": sys.argv,
    }
    write_json(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
