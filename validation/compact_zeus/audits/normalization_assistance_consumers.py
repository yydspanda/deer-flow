"""Offline consumer audit, not an automatic normalizer or model quality benchmark.

Replay one saved model answer and add literal test controls for selected real
single-message alerts. Never infer verdicts, generate Memory, or modify stores.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import hashlib
from itertools import combinations
import json
import ntpath
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
    build_comparison,
    check_quoted_kv,
    decode_extraction_response,
    git_value,
    merge_facts,
    select_source,
    write_json,
)
from soc_agent.integrations.pingan.memory import PingAnSocMemoryProfile  # noqa: E402
from soc_agent.normalizers import normalize_alert_payload  # noqa: E402
from soc_agent.utils.hashing import stable_hash  # noqa: E402

# Reviewed literal test inputs only. Production and model extraction never import this map.
CONTROL_FIELDS = {
    "hostname": "entities.host.host_name",
    "client_ip": "entities.host.ip_addresses",
    "proc_path": "entities.process.process_path",
    "cmdline": "entities.process.command_line",
    "file_path": "entities.file.file_path",
}
INSPECT_FIELDS = (*CONTROL_FIELDS, "virus_name", "virus_type", "defense_type", "result")
DEFAULT_IDS = (
    "2448412",
    "2448580",
    "2450017",
    "2455416",
    "2459908",
    "2464210",
    "2466490",
    "2483048",
)


def literal_control_facts(source: dict) -> list[dict]:
    matches = list(LEGACY_QUOTED_KV_RE.finditer(source["text"]))
    counts = Counter(match.group(1) for match in matches)
    if any(counts[key] > 1 for key in CONTROL_FIELDS):
        raise ValueError("duplicate fields are not supported by literal test controls")
    facts = []
    for match in matches:
        key, value = match.group(1, 2)
        if key not in CONTROL_FIELDS or not value:
            continue
        if key in {"proc_path", "file_path"} and not ntpath.isabs(value):
            continue
        facts.append(
            {
                "target": CONTROL_FIELDS[key],
                "value": value,
                "source_ref": "X-1",
                "source_quote": match.group(0),
            }
        )
    return facts


def review_case(payload: dict, *, saved_facts: list[dict] | None = None) -> dict:
    started = time.monotonic()
    source = select_source(payload)
    before = normalize_alert_payload(payload)
    proposals = literal_control_facts(source) if saved_facts is None else saved_facts
    after, merge = merge_facts(before, source, proposals)
    comparison, downstream = build_comparison(before, after)
    request = downstream["after"]["request"]
    facets = downstream["after"]["facets"]
    spec = PingAnSocMemoryProfile().build_applicability(
        consensus_facets=facets,
        strong_anchor_facets=facets,
    )
    primary = (
        json.loads(request.primary_evidence.content) if request.primary_evidence else {}
    )
    source_fields: dict[str, list[str]] = {}
    for match in LEGACY_QUOTED_KV_RE.finditer(source["text"]):
        if match.group(1) in INSPECT_FIELDS:
            source_fields.setdefault(match.group(1), []).append(match.group(2))
    destinations = []
    for key, values in source_fields.items():
        for value in values:
            targets = [
                f["target"]
                for f in merge["accepted"]
                if f["target"] == CONTROL_FIELDS.get(key) and f["value"] == value
            ]
            destinations.append(
                {
                    "source_field": key,
                    "value": value,
                    "in_primary_evidence": primary.get("fields", {}).get(key) == value,
                    "canonical_targets": targets,
                }
            )
    return {
        "alert_id": before.alert_id,
        "extraction_origin": "literal_test_control_not_llm"
        if saved_facts is None
        else "frozen_model_response",
        "source": source,
        "payload_hash": stable_hash(payload),
        "source_fields": source_fields,
        "inspection": check_quoted_kv(source["text"]),
        "merge": merge,
        "comparison": comparison,
        "canonical": after.model_dump(mode="json"),
        "bounded_request": request.model_dump(mode="json"),
        "facets": facets,
        "required_scope": spec.required_facets if spec else None,
        "field_destinations": destinations,
        "raw_preserved": before.raw == after.raw,
        "new_model_calls": 0,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
    }


def compare_cases(left: dict, right: dict) -> dict:
    lf = left["facets"].get("behavior_fingerprint", [])
    rf = right["facets"].get("behavior_fingerprint", [])
    differing = sorted(
        key
        for key in INSPECT_FIELDS
        if left["source_fields"].get(key) != right["source_fields"].get(key)
    )
    same_fingerprint = bool(lf) and lf == rf
    return {
        "alert_ids": [left["alert_id"], right["alert_id"]],
        "same_nonempty_fingerprint": same_fingerprint,
        "same_required_scope": bool(left["required_scope"])
        and left["required_scope"] == right["required_scope"],
        "different_source_fields": differing,
        "detector_or_file_difference_unrepresented": same_fingerprint
        and bool(
            set(differing) & {"file_path", "virus_name", "virus_type", "defense_type"}
        ),
        "memory_retrieval_executed": False,
        "interpretation": "Feature sensitivity only; not a risk label or proof that an actual Memory changed a verdict.",
    }


def run_audit(
    *, index_path: Path, store_path: Path, attempt: Path, output: Path, ids: list[str]
) -> dict:
    if not 1 <= len(ids) <= 20 or len(ids) != len(set(ids)):
        raise ValueError("select 1..20 distinct alert IDs")
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    started = time.monotonic()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    indexed = {case["alert_id"]: case for case in index["cases"]}
    saved_source = json.loads((attempt / "01-source.json").read_text(encoding="utf-8"))
    saved_before = json.loads(
        (attempt / "02-adapter-before.json").read_text(encoding="utf-8")
    )
    saved_response = json.loads(
        (attempt / "05-llm-response.json").read_text(encoding="utf-8")
    )
    saved_id = str(saved_before["alert_id"])
    parsed = decode_extraction_response(
        saved_response["content"], saved_response["metadata"]
    )
    cases = []
    with sqlite3.connect(store_path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        for alert_id in ids:
            if alert_id not in indexed:
                raise ValueError(f"alert not in frozen index: {alert_id}")
            row = db.execute(
                "SELECT payload_zlib, payload_hash FROM payloads WHERE alert_id=?",
                (alert_id,),
            ).fetchone()
            if row is None or row[1] != indexed[alert_id]["payload_hash"]:
                raise ValueError(f"index/payload-store mismatch: {alert_id}")
            payload = json.loads(zlib.decompress(row[0]))
            proposals = None
            if alert_id == saved_id:
                if select_source(payload) != saved_source["source"]:
                    raise ValueError(
                        "saved model answer belongs to a different raw message"
                    )
                proposals = parsed.facts
            case = review_case(payload, saved_facts=proposals)
            cases.append(case)
            write_json(output / f"{alert_id}.consumers.json", case)
    pairs = [compare_cases(left, right) for left, right in combinations(cases, 2)]
    blind_spots = [
        pair
        for pair in pairs
        if pair["detector_or_file_difference_unrepresented"]
        and pair["same_required_scope"]
    ]
    cohort = [
        case
        for case in index["cases"]
        if case.get("detection_key") == indexed[ids[0]].get("detection_key")
    ]
    summary = {
        "schema_version": "soc.normalization_assistance.consumer_audit.v1",
        "case_count": len(cases),
        "frozen_model_response_replays": sum(
            c["extraction_origin"] == "frozen_model_response" for c in cases
        ),
        "literal_test_controls": sum(
            c["extraction_origin"] == "literal_test_control_not_llm" for c in cases
        ),
        "new_model_calls": 0,
        "new_tokens": 0,
        "soc_database_writes": 0,
        "cohort_index_count": len(cohort),
        "cohort_without_fingerprint": sum(
            not c.get("behavior_fingerprint") for c in cohort
        ),
        "raw_preserved": all(c["raw_preserved"] for c in cases),
        "same_scope_distinct_detector_or_file_pairs": blind_spots,
        "quality_gate": "blocked_by_consumer_blind_spots"
        if blind_spots
        else "not_established",
        "online_apply_approved": False,
        "cases": [
            {
                "alert_id": c["alert_id"],
                "origin": c["extraction_origin"],
                "before": c["comparison"]["before"]["behavior_fingerprint"],
                "after": c["comparison"]["after"]["behavior_fingerprint"],
                "components": c["comparison"]["after"]["behavior_component_core"],
                "accepted_facts": len(c["merge"]["accepted"]),
                "duration_ms": c["duration_ms"],
            }
            for c in cases
        ],
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
    }
    write_json(output / "pairs.json", pairs)
    write_json(output / "summary.json", summary)
    write_json(
        output / "manifest.json",
        {
            "created_at": datetime.now(UTC).isoformat(),
            "code_commit": git_value("rev-parse", "HEAD"),
            "upstream_commit": git_value("rev-parse", "upstream/main"),
            "script_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "helper_hash": hashlib.sha256(
                Path(__file__)
                .with_name("normalization_assistance_trial.py")
                .read_bytes()
            ).hexdigest(),
            "data_hash": stable_hash({c["alert_id"]: c["payload_hash"] for c in cases}),
            "index_hash": hashlib.sha256(index_path.read_bytes()).hexdigest(),
            "config_hash": stable_hash(index["memory_profile"]),
            "memory_profile": index["memory_profile"],
            "model": "no new calls; one frozen model answer and explicit literal controls",
            "source_model_experiment": json.loads(
                (attempt / "00-experiment.json").read_text(encoding="utf-8")
            ),
            "saved_response_hash": hashlib.sha256(
                (attempt / "05-llm-response.json").read_bytes()
            ).hexdigest(),
            "command": sys.argv,
            "hardware": platform.platform(),
            "python": platform.python_version(),
            "metrics": {
                k: summary[k]
                for k in (
                    "case_count",
                    "new_model_calls",
                    "new_tokens",
                    "soc_database_writes",
                    "duration_ms",
                )
            },
            "interpretation": "Literal controls test consumers only. They are not additional successful LLM extractions or security labels.",
        },
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    base = ROOT / "validation/compact_zeus/data/corpus"
    parser.add_argument(
        "--index",
        type=Path,
        default=base / "full_alert_dams_labeled_merged.workbench-index.json",
    )
    parser.add_argument(
        "--payload-store",
        type=Path,
        default=base / "full_alert_dams_labeled_merged.workbench-payloads.sqlite",
    )
    parser.add_argument("--saved-attempt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--alert-ids", nargs="+", default=list(DEFAULT_IDS))
    args = parser.parse_args()
    summary = run_audit(
        index_path=args.index,
        store_path=args.payload_store,
        attempt=args.saved_attempt,
        output=args.output,
        ids=args.alert_ids,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
