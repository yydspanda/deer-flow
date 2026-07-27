#!/usr/bin/env python3
"""Audit how PingAn NIDS fields flow through the current SOC contracts."""

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

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    import_path = str(import_root)
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from validation.compact_zeus.compact_encoded_llm_context import (  # noqa: E402
    compact_encoded_spans,
)
from validation.compact_zeus.restricted_dataframe_pickle import (  # noqa: E402
    load_dataframe_pickle,
)

from soc_agent.contracts import (  # noqa: E402
    AlertSourceType,
    ParsedRawMessageEvidence,
    SensitiveEvidenceMode,
)
from soc_agent.core.runtime import build_analysis_request_for_payload  # noqa: E402
from soc_agent.normalizers import normalize_alert_payload  # noqa: E402

SCHEMA_VERSION = "soc.validation.pingan_nids_field_audit.v1"
DEFAULT_CORPUS_PATH = ROOT / "validation/compact_zeus/data/full_alert_validation_corpus.pkl"
DEFAULT_OUTPUT_PATH = ROOT / "validation/compact_zeus/data/pingan-nids-field-audit.json"

_TARGET_PATHS = {
    "network.source_ip": ("entities", "network", "source_ip"),
    "network.destination_ip": ("entities", "network", "destination_ip"),
    "network.src_port": ("entities", "network", "src_port"),
    "network.dst_port": ("entities", "network", "dst_port"),
    "network.protocol": ("entities", "network", "protocol"),
    "network.application_protocol": ("entities", "network", "application_protocol"),
    "network.direction": ("entities", "network", "direction"),
    "network.domain": ("entities", "network", "domain"),
    "network.url": ("entities", "network", "url"),
    "http.method": ("entities", "http", "method"),
    "http.host": ("entities", "http", "host"),
    "http.path": ("entities", "http", "path"),
    "http.url": ("entities", "http", "url"),
    "http.protocol": ("entities", "http", "protocol"),
    "http.port": ("entities", "http", "port"),
    "http.status_code": ("entities", "http", "status_code"),
    "http.user_agent": ("entities", "http", "user_agent"),
    "http.referer": ("entities", "http", "referer"),
    "http.x_forwarded_for": ("entities", "http", "x_forwarded_for"),
    "detection.rule_name": ("detection", "rule_name"),
    "detection.rule_category": ("detection", "rule_category"),
    "classification.category": ("classification", "category"),
}

_FIELD_GROUP_PREFIXES = {
    "five_tuple": (
        "sip",
        "dip",
        "sport",
        "dport",
        "proto",
        "src_ip",
        "dest_ip",
        "src_port",
        "dest_port",
    ),
    "flow_and_direction": (
        "app_proto",
        "direction",
        "community_id",
        "flow_id",
        "flow.",
        "stream",
        "tx_id",
        "vlan",
    ),
    "detection": ("alert.",),
    "http": (
        "http.",
        "headers.",
        "request_header_str",
        "response_header_str",
        "response_hqeader_str",
    ),
    "dns": ("dns.",),
    "query_context": ("query",),
    "tls": ("tls.",),
    "file": ("files", "fileinfo."),
    "payload": ("payload", "packet"),
}

_SEMANTIC_VALUE_PATHS = {
    "sensor_action": ("alert", "action"),
    "sensor_attack_result": ("alert", "attack_res"),
    "sensor_category": ("alert", "category"),
    "sensor_severity": ("alert", "severity"),
    "sensor_source_zone": ("alert", "source", "zone"),
    "sensor_target_zone": ("alert", "target", "zone"),
    "attack_type_code": ("attack_type_code",),
    "application_protocol": ("app_proto",),
    "traffic_direction": ("direction",),
}

_ENCODED_CONTEXT_FIELDS = (
    "request_header_str",
    "response_header_str",
    "response_hqeader_str",
    "payload",
    "packet",
)


def build_nids_field_audit(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    field_alert_presence: Counter[str] = Counter()
    field_message_presence: Counter[str] = Counter()
    field_non_empty_alert_presence: Counter[str] = Counter()
    field_non_empty_message_presence: Counter[str] = Counter()
    field_types: dict[str, Counter[str]] = defaultdict(Counter)
    top_level_alert_presence: Counter[str] = Counter()
    top_level_message_presence: Counter[str] = Counter()
    parser_counts: Counter[str] = Counter()
    schema_fingerprint_counts: Counter[str] = Counter()
    topic_counts: Counter[str] = Counter()
    signal_message_values: dict[str, Counter[str]] = defaultdict(Counter)
    semantic_message_values: dict[str, Counter[str]] = defaultdict(Counter)
    encoded_context_shapes: dict[str, Counter[str]] = defaultdict(Counter)
    zone_direction_counts: Counter[str] = Counter()
    messages_per_alert: Counter[int] = Counter()
    sensor_signatures: set[str] = set()
    representative_sample_ids: dict[str, list[str]] = defaultdict(list)
    multi_signature_alerts = 0
    multi_five_tuple_alerts = 0
    mixed_direction_alerts = 0
    canonical_target_counts: Counter[str] = Counter()
    role_status_counts: dict[str, Counter[str]] = defaultdict(Counter)
    path_lane_alert_counts: dict[str, Counter[str]] = defaultdict(Counter)
    path_lane_message_counts: dict[str, Counter[str]] = defaultdict(Counter)
    field_group_alert_counts: Counter[str] = Counter()
    high_value_gap_counts: Counter[str] = Counter()
    five_tuple_complete = 0
    scenario_hypothesis_alerts = 0
    network_observation_alerts = 0
    http_observation_alerts = 0
    network_observation_count = 0
    http_observation_count = 0
    llm_compacted_encoded_alerts = 0
    llm_compacted_encoded_spans = 0
    llm_compacted_encoded_kinds: Counter[str] = Counter()
    parsed_message_count = 0
    sample_ids: list[str] = []

    for row in rows:
        payload = _alert_payload(row)
        alert = normalize_alert_payload(payload)
        if alert.source.source_type is not AlertSourceType.NIDS:
            continue

        sample_ids.append(alert.alert_id)
        topic_counts[alert.source.source_system or "unknown"] += 1
        request = build_analysis_request_for_payload(
            payload,
            sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
        )
        coverage = request.evidence_coverage
        bounded_evidence = [item for item in [request.primary_evidence, *request.supplementary_evidence] if item is not None]
        compacted = [omission for evidence in bounded_evidence for omission in evidence.encoded_span_omissions]
        if compacted:
            llm_compacted_encoded_alerts += 1
            llm_compacted_encoded_spans += len(compacted)
            llm_compacted_encoded_kinds.update(item.kind for item in compacted)
        canonical_dump = alert.model_dump(mode="json", exclude_none=True)
        for target, path in _TARGET_PATHS.items():
            if _has_value(_resolve_path(canonical_dump, path)):
                canonical_target_counts[target] += 1

        network = alert.entities.network
        if all(
            _has_value(value)
            for value in (
                network.source_ip,
                network.destination_ip,
                network.src_port,
                network.dst_port,
                network.protocol,
            )
        ):
            five_tuple_complete += 1
        if request.fact_reconstruction.scenario_hypotheses:
            scenario_hypothesis_alerts += 1
        if alert.entities.network.observations:
            network_observation_alerts += 1
            network_observation_count += len(alert.entities.network.observations)
        if alert.entities.http.observations:
            http_observation_alerts += 1
            http_observation_count += len(alert.entities.http.observations)
        for resolution in request.fact_reconstruction.role_resolutions:
            role_status_counts[resolution.role][resolution.status.value] += 1
        for gap in coverage.high_value_gaps:
            high_value_gap_counts[gap.rule_id or gap.expected_target] += 1

        parsed_messages = [ParsedRawMessageEvidence.model_validate(item) for item in alert.extensions.get("parsed_raw_messages", [])]
        parsed_message_count += len(parsed_messages)
        messages_per_alert[len(parsed_messages)] += 1
        if len(parsed_messages) > 1:
            _append_sample_id(
                representative_sample_ids,
                "multiple_messages",
                alert.alert_id,
            )
        for observation in coverage.message_schemas:
            parser_counts[observation.parser_name or "unsupported"] += 1
            if observation.schema_fingerprint:
                schema_fingerprint_counts[observation.schema_fingerprint] += 1

        canonical_paths = set(coverage.canonical_source_paths)
        fact_paths = set(coverage.fact_source_paths)
        scenario_paths = set(coverage.scenario_source_paths)
        llm_paths = set(coverage.llm_projected_paths)
        alert_field_paths: set[str] = set()
        alert_non_empty_field_paths: set[str] = set()
        alert_top_level_names: set[str] = set()
        alert_present_groups: set[str] = set()
        alert_lanes_by_path: dict[str, set[str]] = defaultdict(set)
        alert_signatures: set[str] = set()
        alert_five_tuples: set[tuple[str, str, str, str, str]] = set()
        alert_directions: set[str] = set()
        for parsed in parsed_messages:
            if isinstance(parsed.fields.get("http"), Mapping) and parsed.fields["http"]:
                _append_sample_id(
                    representative_sample_ids,
                    "structured_http",
                    alert.alert_id,
                )
            elif _has_value(parsed.fields.get("request_header_str")):
                _append_sample_id(
                    representative_sample_ids,
                    "header_string_only",
                    alert.alert_id,
                )
            if _has_value(parsed.fields.get("query")):
                _append_sample_id(
                    representative_sample_ids,
                    "query_context",
                    alert.alert_id,
                )
            for top_level_name in parsed.fields:
                normalized_name = str(top_level_name)
                top_level_message_presence[normalized_name] += 1
                alert_top_level_names.add(normalized_name)
            for path, value in _flatten_leaves(parsed.fields):
                field_message_presence[path] += 1
                alert_field_paths.add(path)
                field_types[path][_type_name(value)] += 1
                value_is_present = _has_value(value)
                if value_is_present:
                    field_non_empty_message_presence[path] += 1
                    alert_non_empty_field_paths.add(path)
                full_path = f"{parsed.source_path}#parsed.{path}"
                if full_path in canonical_paths:
                    path_lane_message_counts[path]["canonical_provenance"] += 1
                    alert_lanes_by_path[path].add("canonical_provenance")
                if full_path in fact_paths:
                    path_lane_message_counts[path]["fact"] += 1
                    alert_lanes_by_path[path].add("fact")
                if full_path in scenario_paths:
                    path_lane_message_counts[path]["scenario"] += 1
                    alert_lanes_by_path[path].add("scenario")
                if full_path in llm_paths:
                    path_lane_message_counts[path]["llm"] += 1
                    alert_lanes_by_path[path].add("llm")
                if value_is_present:
                    for group, prefixes in _FIELD_GROUP_PREFIXES.items():
                        if any(path == prefix or path.startswith(prefix) for prefix in prefixes):
                            alert_present_groups.add(group)
            for signal_name in ("event_type", "app_proto", "direction"):
                value = parsed.fields.get(signal_name)
                if _has_value(value):
                    signal_message_values[signal_name][str(value)] += 1
            for semantic_name, semantic_path in _SEMANTIC_VALUE_PATHS.items():
                value = _resolve_path(parsed.fields, semantic_path)
                if _has_value(value):
                    semantic_message_values[semantic_name][str(value)] += 1
            for field_name in _ENCODED_CONTEXT_FIELDS:
                if field_name in parsed.fields:
                    encoded_context_shapes[field_name][_encoded_context_shape(parsed.fields[field_name])] += 1
            signature = _resolve_path(parsed.fields, ("alert", "signature"))
            if _has_value(signature):
                sensor_signatures.add(str(signature))
                alert_signatures.add(str(signature))
            five_tuple = tuple(str(parsed.fields.get(name) or "") for name in ("sip", "sport", "dip", "dport", "proto"))
            if all(five_tuple):
                alert_five_tuples.add(five_tuple)
            direction = parsed.fields.get("direction")
            if _has_value(direction):
                alert_directions.add(str(direction))
            source_zone = _resolve_path(parsed.fields, ("alert", "source", "zone"))
            target_zone = _resolve_path(parsed.fields, ("alert", "target", "zone"))
            if _has_value(source_zone) and _has_value(target_zone) and _has_value(direction):
                zone_direction_counts[f"{source_zone}->{target_zone}|{direction}"] += 1
        multi_signature_alerts += int(len(alert_signatures) > 1)
        multi_five_tuple_alerts += int(len(alert_five_tuples) > 1)
        mixed_direction_alerts += int(len(alert_directions) > 1)
        for path in alert_field_paths:
            field_alert_presence[path] += 1
        for path in alert_non_empty_field_paths:
            field_non_empty_alert_presence[path] += 1
        for top_level_name in alert_top_level_names:
            top_level_alert_presence[top_level_name] += 1
        for group in alert_present_groups:
            field_group_alert_counts[group] += 1
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
        alert_lane_counts = path_lane_alert_counts[path]
        message_lane_counts = path_lane_message_counts[path]
        field_rows.append(
            {
                "path": path,
                "alerts": alert_count,
                "alert_coverage_ratio": _ratio(alert_count, sample_count),
                "non_empty_alerts": field_non_empty_alert_presence[path],
                "non_empty_alert_coverage_ratio": _ratio(
                    field_non_empty_alert_presence[path],
                    sample_count,
                ),
                "messages": message_count,
                "message_coverage_ratio": _ratio(
                    message_count,
                    parsed_message_count,
                ),
                "non_empty_messages": field_non_empty_message_presence[path],
                "non_empty_message_coverage_ratio": _ratio(
                    field_non_empty_message_presence[path],
                    parsed_message_count,
                ),
                "types": dict(sorted(field_types[path].items())),
                "lanes": {
                    lane: {
                        "alerts": alert_lane_counts.get(lane, 0),
                        "alert_ratio_when_present": _ratio(
                            alert_lane_counts.get(lane, 0),
                            alert_count,
                        ),
                        "messages": message_lane_counts.get(lane, 0),
                        "message_ratio_when_present": _ratio(
                            message_lane_counts.get(lane, 0),
                            message_count,
                        ),
                    }
                    for lane in ("canonical_provenance", "fact", "scenario", "llm")
                },
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "sensitive_values_included": False,
        "encoded_context_policy": {
            "implementation": "backend/soc_agent/pipeline/encoded_context.py",
            "validation_entrypoint": "validation/compact_zeus/compact_encoded_llm_context.py",
            "scope": "LLM projection only",
            "decoding": False,
            "raw_payload_preserved": True,
        },
        "sample_count": sample_count,
        "sample_ids": sample_ids,
        "topic_counts": dict(sorted(topic_counts.items())),
        "parsed_message_count": parsed_message_count,
        "messages_per_alert": {str(message_count): alert_count for message_count, alert_count in sorted(messages_per_alert.items())},
        "parser_counts": dict(sorted(parser_counts.items())),
        "schema_fingerprint_counts": dict(sorted(schema_fingerprint_counts.items())),
        "top_level_alert_presence": dict(
            sorted(
                top_level_alert_presence.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        "top_level_message_presence": dict(
            sorted(
                top_level_message_presence.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        "field_group_alert_counts": {
            group: {
                "alerts": field_group_alert_counts.get(group, 0),
                "coverage_ratio": _ratio(field_group_alert_counts.get(group, 0), sample_count),
            }
            for group in _FIELD_GROUP_PREFIXES
        },
        "signal_message_values": {name: dict(sorted(values.items(), key=lambda item: (-item[1], item[0]))) for name, values in sorted(signal_message_values.items())},
        "semantic_message_values": {name: dict(sorted(values.items(), key=lambda item: (-item[1], item[0]))) for name, values in sorted(semantic_message_values.items())},
        "encoded_context_field_shapes": {name: dict(sorted(values.items())) for name, values in sorted(encoded_context_shapes.items())},
        "llm_encoded_compaction": {
            "alerts": llm_compacted_encoded_alerts,
            "spans": llm_compacted_encoded_spans,
            "kinds": dict(sorted(llm_compacted_encoded_kinds.items())),
        },
        "zone_direction_message_counts": dict(
            sorted(
                zone_direction_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        "distinct_sensor_signature_count": len(sensor_signatures),
        "multi_observation_alert_counts": {
            "multiple_signatures": multi_signature_alerts,
            "multiple_five_tuples": multi_five_tuple_alerts,
            "mixed_directions": mixed_direction_alerts,
        },
        "representative_sample_ids": {cohort: values for cohort, values in sorted(representative_sample_ids.items())},
        "canonical_target_coverage": {
            target: {
                "alerts": canonical_target_counts.get(target, 0),
                "coverage_ratio": _ratio(canonical_target_counts.get(target, 0), sample_count),
            }
            for target in _TARGET_PATHS
        },
        "five_tuple_complete": {
            "alerts": five_tuple_complete,
            "coverage_ratio": _ratio(five_tuple_complete, sample_count),
        },
        "observation_coverage": {
            "network": {
                "alerts": network_observation_alerts,
                "observations": network_observation_count,
                "coverage_ratio": _ratio(network_observation_alerts, sample_count),
            },
            "http": {
                "alerts": http_observation_alerts,
                "observations": http_observation_count,
                "coverage_ratio": _ratio(http_observation_alerts, sample_count),
            },
        },
        "scenario_hypothesis_alerts": {
            "alerts": scenario_hypothesis_alerts,
            "coverage_ratio": _ratio(scenario_hypothesis_alerts, sample_count),
        },
        "role_status_counts": {role: dict(sorted(values.items())) for role, values in sorted(role_status_counts.items())},
        "high_value_gap_counts": dict(sorted(high_value_gap_counts.items())),
        "fields": field_rows,
    }


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
    leaves: list[tuple[str, Any]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            leaves.extend(_flatten_leaves(item, path=(*path, str(key))))
        return leaves
    if isinstance(value, list):
        for index, item in enumerate(value):
            leaves.extend(_flatten_leaves(item, path=(*path, index)))
        return leaves
    leaves.append((_format_path(path), value))
    return leaves


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


def _encoded_context_shape(value: Any) -> str:
    if not isinstance(value, str):
        return "non_string"
    stripped = value.strip()
    if not stripped:
        return "empty"
    _, omissions = compact_encoded_spans(value)
    if omissions:
        return "encoded_span_compacted"
    if stripped == "{}":
        return "empty_json_object"
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return "json_object"
    if re.search(
        r"(?im)^(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+\S+\s+HTTP/",
        stripped,
    ):
        return "http_request_text"
    if re.search(r"(?im)^HTTP/\d(?:\.\d)?\s+\d{3}\b", stripped):
        return "http_response_text"
    return "plain_text"


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
    report = build_nids_field_audit(frame.to_dict(orient="records"))
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
                "output": str(args.output.relative_to(ROOT)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
