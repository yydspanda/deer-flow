#!/usr/bin/env python3
"""Audit how PingAn EDR fields flow through the current SOC contracts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    import_path = str(import_root)
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from validation.compact_zeus.corpus.build_alert_validation_corpus import (  # noqa: E402
    canonical_sha256,
)
from validation.compact_zeus.shared.restricted_dataframe_pickle import (  # noqa: E402
    load_dataframe_pickle,
)

from soc_agent.contracts import (  # noqa: E402
    AlertSourceType,
    ParsedRawMessageEvidence,
    SensitiveEvidenceMode,
)
from soc_agent.core.runtime import build_analysis_request_for_payload  # noqa: E402
from soc_agent.normalizers import normalize_alert_payload  # noqa: E402

SCHEMA_VERSION = "soc.validation.pingan_edr_field_audit.v1"
DEFAULT_CORPUS_PATH = (
    ROOT / "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"
)
DEFAULT_OUTPUT_PATH = (
    ROOT / "validation/compact_zeus/data/audits/pingan-edr-field-audit.json"
)

_DETAIL_KEY_RE = re.compile(r"details(?P<index>\d+)$", re.IGNORECASE)
_HEX_HASH_LENGTHS = {"process_md5": 32, "process_sha256": 64}
_TARGET_PATHS = {
    "detection.rule_name": ("detection", "rule_name"),
    "classification.tactic": ("classification", "tactic"),
    "classification.technique": ("classification", "technique"),
    "host.host_name": ("entities", "host", "host_name"),
    "host.host_id": ("entities", "host", "host_id"),
    "host.ip_addresses": ("entities", "host", "ip_addresses"),
    "process.process_name": ("entities", "process", "process_name"),
    "process.process_id": ("entities", "process", "process_id"),
    "process.process_path": ("entities", "process", "process_path"),
    "process.command_line": ("entities", "process", "command_line"),
    "process.md5": ("entities", "process", "md5"),
    "process.sha256": ("entities", "process", "sha256"),
    "user.username": ("entities", "user", "username"),
    "file.file_name": ("entities", "file", "file_name"),
    "file.file_path": ("entities", "file", "file_path"),
    "file.md5": ("entities", "file", "md5"),
    "file.sha256": ("entities", "file", "sha256"),
    "network.source_ip": ("entities", "network", "source_ip"),
    "network.destination_ip": ("entities", "network", "destination_ip"),
}


def build_edr_field_audit(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    topic_counts: Counter[str] = Counter()
    parser_counts: Counter[str] = Counter()
    messages_per_alert: Counter[int] = Counter()
    canonical_target_counts: Counter[str] = Counter()
    high_value_gap_counts: Counter[str] = Counter()
    field_alert_presence: Counter[str] = Counter()
    field_message_presence: Counter[str] = Counter()
    field_non_empty_alert_presence: Counter[str] = Counter()
    field_non_empty_message_presence: Counter[str] = Counter()
    field_types: dict[str, Counter[str]] = defaultdict(Counter)
    path_lane_alert_counts: dict[str, Counter[str]] = defaultdict(Counter)
    path_lane_message_counts: dict[str, Counter[str]] = defaultdict(Counter)
    detail_action_alert_counts: Counter[str] = Counter()
    detail_action_record_counts: Counter[str] = Counter()
    invalid_hash_counts: Counter[str] = Counter()
    valid_hash_counts: Counter[str] = Counter()
    representative_sample_ids: dict[str, list[str]] = defaultdict(list)
    sample_ids: list[str] = []
    parsed_message_count = 0
    detail_record_count = 0
    detail_message_count = 0
    detail_alert_count = 0
    process_observation_alerts = 0
    process_observation_count = 0
    process_node_count = 0
    file_observation_alerts = 0
    file_observation_count = 0
    directional_network_alerts = 0
    directional_network_observation_count = 0
    scenario_hypothesis_alerts = 0
    raw_payload_mutation_count = 0

    for row in rows:
        payload = _alert_payload(row)
        input_hash = canonical_sha256(payload)
        alert = normalize_alert_payload(payload)
        if alert.source.source_type is not AlertSourceType.EDR:
            continue

        sample_ids.append(alert.alert_id)
        topic = alert.source.source_system or "unknown"
        topic_counts[topic] += 1
        _append_sample_id(representative_sample_ids, f"topic:{topic}", alert.alert_id)
        request = build_analysis_request_for_payload(
            payload,
            sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
        )
        if canonical_sha256(payload) != input_hash:
            raw_payload_mutation_count += 1

        canonical_dump = alert.model_dump(mode="json", exclude_none=True)
        for target, path in _TARGET_PATHS.items():
            if _has_value(_resolve_path(canonical_dump, path)):
                canonical_target_counts[target] += 1
        for gap in request.evidence_coverage.high_value_gaps:
            high_value_gap_counts[gap.rule_id or gap.expected_target] += 1
        if request.fact_reconstruction.scenario_hypotheses:
            scenario_hypothesis_alerts += 1

        process_observations = alert.entities.process.observations
        if process_observations:
            process_observation_alerts += 1
            process_observation_count += len(process_observations)
            process_node_count += sum(len(item.nodes) for item in process_observations)
        file_observations = getattr(alert.entities.file, "observations", [])
        if file_observations:
            file_observation_alerts += 1
            file_observation_count += len(file_observations)
        network = alert.entities.network
        if network.source_ip or network.destination_ip:
            directional_network_alerts += 1
        directional_network_observation_count += len(network.observations)

        parsed_messages = [
            ParsedRawMessageEvidence.model_validate(item)
            for item in alert.extensions.get("parsed_raw_messages", [])
        ]
        parsed_message_count += len(parsed_messages)
        messages_per_alert[len(parsed_messages)] += 1
        if not parsed_messages:
            _append_sample_id(
                representative_sample_ids, "no_parsed_message", alert.alert_id
            )
        if len(parsed_messages) > 1:
            _append_sample_id(
                representative_sample_ids, "multiple_messages", alert.alert_id
            )
        for parsed in parsed_messages:
            parser_counts[parsed.parser_name] += 1

        canonical_paths = set(request.evidence_coverage.canonical_source_paths)
        fact_paths = set(request.evidence_coverage.fact_source_paths)
        scenario_paths = set(request.evidence_coverage.scenario_source_paths)
        llm_paths = set(request.evidence_coverage.llm_projected_paths)
        alert_field_paths: set[str] = set()
        alert_non_empty_paths: set[str] = set()
        alert_lanes_by_path: dict[str, set[str]] = defaultdict(set)
        alert_action_kinds: set[str] = set()
        alert_has_details = False

        for parsed in parsed_messages:
            detail_records = _detail_records(parsed.fields)
            if detail_records:
                detail_message_count += 1
                detail_record_count += len(detail_records)
                alert_has_details = True
                _append_sample_id(
                    representative_sample_ids, "nested_details", alert.alert_id
                )
            for detail_path, detail in detail_records:
                action_detail = detail.get("action_detail")
                kinds = _action_kinds(action_detail)
                for kind in kinds:
                    detail_action_record_counts[kind] += 1
                    alert_action_kinds.add(kind)
                    _append_sample_id(
                        representative_sample_ids,
                        f"action:{kind}",
                        alert.alert_id,
                    )
                for hash_field, expected_length in _HEX_HASH_LENGTHS.items():
                    value = detail.get(hash_field)
                    if not _has_value(value):
                        continue
                    if _is_hex_digest(value, expected_length=expected_length):
                        valid_hash_counts[hash_field] += 1
                    else:
                        invalid_hash_counts[hash_field] += 1

            for path, value in _flatten_leaves(parsed.fields):
                field_message_presence[path] += 1
                field_types[path][_type_name(value)] += 1
                alert_field_paths.add(path)
                if _has_value(value):
                    field_non_empty_message_presence[path] += 1
                    alert_non_empty_paths.add(path)
                full_path = f"{parsed.source_path}#parsed.{path}"
                for lane, lane_paths in (
                    ("canonical_provenance", canonical_paths),
                    ("fact", fact_paths),
                    ("scenario", scenario_paths),
                    ("llm", llm_paths),
                ):
                    if full_path in lane_paths:
                        path_lane_message_counts[path][lane] += 1
                        alert_lanes_by_path[path].add(lane)

        if alert_has_details:
            detail_alert_count += 1
        for kind in alert_action_kinds:
            detail_action_alert_counts[kind] += 1
        for path in alert_field_paths:
            field_alert_presence[path] += 1
        for path in alert_non_empty_paths:
            field_non_empty_alert_presence[path] += 1
        for path, lanes in alert_lanes_by_path.items():
            for lane in lanes:
                path_lane_alert_counts[path][lane] += 1

    sample_count = len(sample_ids)
    field_rows = []
    for path, alert_count in sorted(
        field_alert_presence.items(),
        key=lambda item: (-item[1], item[0]),
    ):
        message_count = field_message_presence[path]
        alert_lanes = path_lane_alert_counts[path]
        message_lanes = path_lane_message_counts[path]
        field_rows.append(
            {
                "path": path,
                "alerts": alert_count,
                "alert_coverage_ratio": _ratio(alert_count, sample_count),
                "non_empty_alerts": field_non_empty_alert_presence[path],
                "messages": message_count,
                "non_empty_messages": field_non_empty_message_presence[path],
                "types": dict(sorted(field_types[path].items())),
                "lanes": {
                    lane: {
                        "alerts": alert_lanes.get(lane, 0),
                        "messages": message_lanes.get(lane, 0),
                    }
                    for lane in (
                        "canonical_provenance",
                        "fact",
                        "scenario",
                        "llm",
                    )
                },
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "sensitive_values_included": False,
        "sample_count": sample_count,
        "sample_ids": sample_ids,
        "topic_counts": dict(sorted(topic_counts.items())),
        "parsed_message_count": parsed_message_count,
        "parser_counts": dict(sorted(parser_counts.items())),
        "messages_per_alert": {
            str(message_count): alert_count
            for message_count, alert_count in sorted(messages_per_alert.items())
        },
        "nested_detail_coverage": {
            "alerts": detail_alert_count,
            "messages": detail_message_count,
            "records": detail_record_count,
            "action_alert_counts": dict(sorted(detail_action_alert_counts.items())),
            "action_record_counts": dict(sorted(detail_action_record_counts.items())),
            "valid_hash_counts": dict(sorted(valid_hash_counts.items())),
            "invalid_hash_counts": dict(sorted(invalid_hash_counts.items())),
        },
        "canonical_target_coverage": {
            target: {
                "alerts": canonical_target_counts.get(target, 0),
                "coverage_ratio": _ratio(
                    canonical_target_counts.get(target, 0),
                    sample_count,
                ),
            }
            for target in _TARGET_PATHS
        },
        "observation_coverage": {
            "process": {
                "alerts": process_observation_alerts,
                "observations": process_observation_count,
                "nodes": process_node_count,
            },
            "file": {
                "alerts": file_observation_alerts,
                "observations": file_observation_count,
            },
            "directional_network": {
                "alerts": directional_network_alerts,
                "observations": directional_network_observation_count,
            },
        },
        "scenario_hypothesis_alerts": {
            "alerts": scenario_hypothesis_alerts,
            "coverage_ratio": _ratio(scenario_hypothesis_alerts, sample_count),
        },
        "high_value_gap_counts": dict(sorted(high_value_gap_counts.items())),
        "raw_payload_mutation_count": raw_payload_mutation_count,
        "representative_sample_ids": {
            cohort: values
            for cohort, values in sorted(representative_sample_ids.items())
        },
        "fields": field_rows,
    }


def _detail_records(fields: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    records: list[tuple[int, str, Mapping[str, Any]]] = []
    for key, value in fields.items():
        match = _DETAIL_KEY_RE.fullmatch(str(key))
        if match is not None and isinstance(value, Mapping):
            records.append((int(match.group("index")), str(key), value))
    return [(key, value) for _, key, value in sorted(records)]


def _action_kinds(value: Any) -> set[str]:
    if not isinstance(value, Mapping):
        return set()
    keys = {str(key).strip().lower() for key in value}
    kinds: set[str] = set()
    if any(key.startswith("child_") for key in keys):
        kinds.add("child_process")
    if {"file_name", "file_path"} & keys:
        kinds.add("file")
    if any(key.startswith("registry_") for key in keys):
        kinds.add("registry")
    if any(key.startswith("task_") for key in keys):
        kinds.add("scheduled_task")
    return kinds or {"other"}


def _is_hex_digest(value: Any, *, expected_length: int) -> bool:
    return isinstance(value, str) and bool(
        re.fullmatch(rf"[0-9A-Fa-f]{{{expected_length}}}", value.strip())
    )


def _append_sample_id(
    target: dict[str, list[str]],
    cohort: str,
    alert_id: str,
    *,
    limit: int = 5,
) -> None:
    values = target[cohort]
    if alert_id not in values and len(values) < limit:
        values.append(alert_id)


def _alert_payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    wrapper = row.get("alert_full_data")
    if not isinstance(wrapper, Mapping):
        raise ValueError("alert_full_data must be an object")
    payload = wrapper.get("alert_data")
    if not isinstance(payload, Mapping):
        raise ValueError("alert_full_data.alert_data must be an object")
    return payload


def _flatten_leaves(
    value: Any,
    *,
    path: tuple[str | int, ...] = (),
) -> list[tuple[str, Any]]:
    if isinstance(value, Mapping):
        return [
            leaf
            for key, item in value.items()
            for leaf in _flatten_leaves(item, path=(*path, str(key)))
        ]
    if isinstance(value, list):
        return [
            leaf
            for index, item in enumerate(value)
            for leaf in _flatten_leaves(item, path=(*path, index))
        ]
    return [(_format_path(path), value)]


def _format_path(path: tuple[str | int, ...]) -> str:
    result = ""
    for segment in path:
        if isinstance(segment, int):
            result += f"[{segment}]"
        else:
            result += f".{segment}" if result else segment
    return result


def _resolve_path(value: Any, path: tuple[str, ...]) -> Any:
    current = value
    for segment in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(segment)
    return current


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def _ratio(value: int, total: int) -> float:
    return round(value / total, 4) if total else 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    frame = load_dataframe_pickle(args.corpus)
    report = build_edr_field_audit(frame.to_dict(orient="records"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "sample_count": report["sample_count"],
                "field_count": len(report["fields"]),
                "output": str(args.output.resolve().relative_to(ROOT)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
