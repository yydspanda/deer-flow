#!/usr/bin/env python3
"""Audit PingAn NDR/APT and HIDS message fields against SOC contracts."""

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
    EvidenceLayer,
    ParsedRawMessageEvidence,
    SensitiveEvidenceMode,
)
from soc_agent.core.runtime import build_analysis_request_for_payload  # noqa: E402
from soc_agent.normalizers import normalize_alert_payload  # noqa: E402

SCHEMA_VERSION = "soc.validation.pingan_ndr_hids_field_audit.v3"
DEFAULT_CORPUS_PATH = (
    ROOT / "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"
)
DEFAULT_OUTPUT_PATH = (
    ROOT / "validation/compact_zeus/data/audits/pingan-ndr-hids-field-audit.json"
)

_SOURCE_TYPES = frozenset({AlertSourceType.NDR, AlertSourceType.HIDS})
_HIDS_NETWORK_EVENT_TYPES = frozenset({"bounce_shell", "honeypot", "malic_opera"})
_HIDS_FILE_EVENT_TYPES = frozenset(
    {"backdoor_diagnose", "backdoor_diagnose_win", "honey_file"}
)
_HIDS_PROCESS_FIELDS = frozenset(
    {
        "process_tree",
        "event_content",
        "process_chain",
        "pname",
        "process_name",
        "pid",
        "cmd",
        "ppname",
        "ppid",
        "pcmd",
    }
)
_HIGH_VALUE_FIELDS = {
    AlertSourceType.NDR: frozenset(
        {
            "access_time",
            "alarm_id",
            "alarm_sample",
            "alarm_sip",
            "agent",
            "api",
            "appid",
            "asset_group",
            "att_ck",
            "att_ck_all",
            "attack_chain",
            "attack_flag",
            "attack_method",
            "attack_result",
            "attack_sip",
            "attack_type",
            "attack_type_all",
            "confidence",
            "cookie",
            "code_language",
            "description",
            "detail_info",
            "device_ip",
            "dip",
            "dip_group",
            "dport",
            "dst_mac",
            "dolog_count",
            "dns_type",
            "file_md5",
            "file_name",
            "first_access_time",
            "hazard_level",
            "hazard_rating",
            "hit_content",
            "hit_field",
            "host",
            "host_md5",
            "host_state",
            "ioc",
            "is_white",
            "is_web_attack",
            "kill_chain",
            "kill_chain_all",
            "malicious_family",
            "method",
            "nid",
            "packet_data",
            "packet_size",
            "parameter",
            "pcap_url",
            "proto",
            "protocol_id",
            "public_date",
            "referer",
            "repeat_count",
            "req_body",
            "req_header",
            "rsp_body",
            "rsp_body_len",
            "rsp_content_length",
            "rsp_content_type",
            "rsp_header",
            "rsp_status",
            "rule_desc",
            "rule_id",
            "rule_name",
            "rule_labels",
            "rule_state",
            "rule_version",
            "rule_version_str",
            "severity",
            "sig_id",
            "sip",
            "sip_group",
            "sport",
            "src_mac",
            "site_app",
            "solution",
            "super_attack_chain",
            "type_chain",
            "tproto",
            "update_time",
            "uri",
            "victim",
            "victim_ip",
            "victim_type",
            "vuln_desc",
            "vuln_harm",
            "vuln_name",
            "vuln_type",
            "webrules_tag",
            "write_date",
            "xff",
            "x_forwarded_for",
            "xml_confidence",
        }
    ),
    AlertSourceType.HIDS: frozenset(
        {
            "access_permission",
            "action",
            "agent_id",
            "agent_ip",
            "atime",
            "backdoor_type",
            "cmd",
            "comment",
            "comid",
            "ctime",
            "datatime",
            "datatype",
            "discovery_time",
            "dst_ip",
            "event_content",
            "event_level",
            "event_name",
            "event_type",
            "execute_pname",
            "external_ip",
            "file_operation",
            "file_path",
            "file_rules",
            "file_type",
            "first_discovery_time",
            "gname",
            "group",
            "group_name",
            "hit_rule_name",
            "hit_rule_names",
            "host_name",
            "internal_ip",
            "item",
            "login_user",
            "log_type",
            "md5",
            "mtime",
            "name",
            "path",
            "pcmd",
            "pid",
            "pname",
            "port",
            "ppath",
            "ppid",
            "ppname",
            "process_chain",
            "process_name",
            "process_tree",
            "puname",
            "rule",
            "sha1",
            "sha256",
            "size",
            "src_ip",
            "src_port",
            "tty",
            "time",
            "type",
            "uname",
            "url",
        }
    ),
}
_TARGET_PATHS = {
    AlertSourceType.NDR: {
        "network.source_ip": ("entities", "network", "source_ip"),
        "network.destination_ip": ("entities", "network", "destination_ip"),
        "network.observations": ("entities", "network", "observations"),
        "http.observations": ("entities", "http", "observations"),
        "file.observations": ("entities", "file", "observations"),
        "detection.rule_name": ("detection", "rule_name"),
        "classification.category": ("classification", "category"),
    },
    AlertSourceType.HIDS: {
        "host.host_name": ("entities", "host", "host_name"),
        "host.host_id": ("entities", "host", "host_id"),
        "host.ip_addresses": ("entities", "host", "ip_addresses"),
        "process.observations": ("entities", "process", "observations"),
        "file.observations": ("entities", "file", "observations"),
        "network.observations": ("entities", "network", "observations"),
        "user.username": ("entities", "user", "username"),
        "detection.rule_name": ("detection", "rule_name"),
        "classification.category": ("classification", "category"),
    },
}


def build_ndr_hids_field_audit(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    source_alert_counts: Counter[str] = Counter()
    topic_counts: dict[str, Counter[str]] = defaultdict(Counter)
    parser_counts: dict[str, Counter[str]] = defaultdict(Counter)
    message_counts: Counter[str] = Counter()
    canonical_target_counts: dict[str, Counter[str]] = defaultdict(Counter)
    semantic_type_counts: dict[str, Counter[str]] = defaultdict(Counter)
    high_value_gap_counts: dict[str, Counter[str]] = defaultdict(Counter)
    field_presence: Counter[tuple[str, str]] = Counter()
    field_non_empty_presence: Counter[tuple[str, str]] = Counter()
    field_lane_counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    field_instance_lane_counts: Counter[str] = Counter(
        {
            "non_empty_typed_or_semantic": 0,
            "non_empty_not_typed_or_semantic": 0,
        }
    )
    high_value_instance_gaps: list[dict[str, str]] = []
    parsed_message_fallback_violations: list[dict[str, str]] = []
    raw_payload_mutation_count = 0
    ndr_network_expected = 0
    ndr_network_mapped = 0
    ndr_http_observation_count = 0
    ndr_file_expected = 0
    ndr_file_mapped = 0
    ndr_vendor_descriptor_count = 0
    ndr_threat_indicator_leak_count = 0
    hids_process_expected = 0
    hids_process_mapped = 0
    hids_file_expected = 0
    hids_file_mapped = 0
    hids_network_observation_count = 0
    hids_invalid_network_observation_count = 0
    hids_canonical_directional_network_count = 0
    hids_default_external_ip_leak_count = 0
    sample_ids: dict[str, list[str]] = defaultdict(list)

    for row in rows:
        payload = _alert_payload(row)
        input_hash = canonical_sha256(payload)
        alert = normalize_alert_payload(payload)
        source_type = alert.source.source_type
        if source_type not in _SOURCE_TYPES:
            continue

        source = source_type.value
        source_alert_counts[source] += 1
        sample_ids[source].append(alert.alert_id)
        topic_counts[source][alert.source.source_system or "unknown"] += 1
        request = build_analysis_request_for_payload(
            payload,
            sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
        )
        if canonical_sha256(payload) != input_hash:
            raw_payload_mutation_count += 1

        canonical = alert.model_dump(mode="json", exclude_none=True)
        for target, path in _TARGET_PATHS[source_type].items():
            if _has_value(_resolve_path(canonical, path)):
                canonical_target_counts[source][target] += 1
        for semantic in alert.extensions.get("source_field_semantics", []):
            semantic_type = str(semantic.get("semantic_type") or "unknown")
            semantic_type_counts[source][semantic_type] += 1
        for gap in request.evidence_coverage.high_value_gaps:
            high_value_gap_counts[source][gap.rule_id or gap.expected_target] += 1

        parsed_messages = [
            ParsedRawMessageEvidence.model_validate(item)
            for item in alert.extensions.get("parsed_raw_messages", [])
        ]
        if parsed_messages:
            policy = alert.extensions.get("evidence_input_policy")
            violation_reasons: list[str] = []
            if (
                not isinstance(policy, Mapping)
                or policy.get("name") != "raw_message_first"
            ):
                violation_reasons.append("evidence_policy_not_raw_message_first")
            if any(
                item.source_layer is EvidenceLayer.RAW_STRUCTURED
                for item in request.fact_reconstruction.role_claims
            ):
                violation_reasons.append("structured_role_claim")
            if any(
                item.source_layer is EvidenceLayer.RAW_STRUCTURED
                for item in request.fact_reconstruction.canonical_field_provenance
            ):
                violation_reasons.append("structured_canonical_provenance")
            if request.evidence_coverage.structured_field_paths:
                violation_reasons.append("structured_model_projection")
            if any(
                item.layer is EvidenceLayer.RAW_STRUCTURED
                for item in [
                    request.primary_evidence,
                    *request.supplementary_evidence,
                ]
                if item is not None
            ):
                violation_reasons.append("structured_bounded_evidence")
            parsed_message_fallback_violations.extend(
                {
                    "alert_id": alert.alert_id,
                    "source_type": source,
                    "reason": reason,
                }
                for reason in violation_reasons
            )
        message_counts[source] += len(parsed_messages)
        canonical_paths = set(request.evidence_coverage.canonical_source_paths)
        fact_paths = set(request.evidence_coverage.fact_source_paths)
        scenario_paths = set(request.evidence_coverage.scenario_source_paths)
        llm_paths = set(request.evidence_coverage.llm_projected_paths)
        sanitized_paths = set(request.evidence_coverage.llm_sanitized_paths)
        omitted_paths = {
            item.field_path for item in request.evidence_coverage.omissions
        }
        semantic_items = [
            item
            for item in alert.extensions.get("source_field_semantics", [])
            if isinstance(item, Mapping) and item.get("field_path")
        ]
        semantic_paths = {str(item["field_path"]) for item in semantic_items}
        reasoning_semantic_paths = {
            str(item["field_path"])
            for item in semantic_items
            if item.get("participates_in_reasoning") is True
        }
        excluded_semantic_paths = semantic_paths - reasoning_semantic_paths
        network_paths = {
            item.evidence_path for item in alert.entities.network.observations
        }
        file_paths = {item.evidence_path for item in alert.entities.file.observations}
        process_paths = {
            item.evidence_path for item in alert.entities.process.observations
        }

        for parsed in parsed_messages:
            parser_counts[source][parsed.parser_name] += 1
            evidence_path = f"{parsed.source_path}#parsed"
            fields = parsed.fields
            for path, value in _flatten_leaves(fields):
                key = (source, path)
                field_presence[key] += 1
                if _has_value(value):
                    field_non_empty_presence[key] += 1
                full_path = f"{evidence_path}.{path}"
                instance_lanes: set[str] = set()
                for lane, paths in (
                    ("canonical_provenance", canonical_paths),
                    ("fact", fact_paths),
                    ("scenario", scenario_paths),
                    ("llm", llm_paths),
                    ("semantic", semantic_paths),
                    ("sanitized", sanitized_paths),
                    ("omitted", omitted_paths),
                ):
                    if full_path in paths:
                        field_lane_counts[key][lane] += 1
                        instance_lanes.add(lane)
                typed_or_semantic = bool(
                    instance_lanes
                    & {"canonical_provenance", "fact", "scenario", "semantic"}
                )
                field_instance_lane_counts[
                    "typed_or_semantic"
                    if typed_or_semantic
                    else "not_typed_or_semantic"
                ] += 1
                if _has_value(value):
                    field_instance_lane_counts[
                        "non_empty_typed_or_semantic"
                        if typed_or_semantic
                        else "non_empty_not_typed_or_semantic"
                    ] += 1
                if "llm" in instance_lanes:
                    field_instance_lane_counts["llm_projected"] += 1
                if "omitted" in instance_lanes:
                    field_instance_lane_counts["llm_omitted"] += 1
                high_value_accounted = typed_or_semantic and (
                    bool(instance_lanes & {"canonical_provenance", "fact", "scenario"})
                    or "llm" in instance_lanes
                    or full_path in excluded_semantic_paths
                )
                if (
                    _has_value(value)
                    and _is_high_value_path(source_type, path)
                    and not high_value_accounted
                ):
                    high_value_instance_gaps.append(
                        {
                            "alert_id": alert.alert_id,
                            "source_type": source,
                            "field_path": full_path,
                        }
                    )

            if source_type is AlertSourceType.NDR:
                if any(_has_value(fields.get(name)) for name in ("sip", "dip")):
                    ndr_network_expected += 1
                    ndr_network_mapped += evidence_path in network_paths
                if _has_value(fields.get("file_name")) or _valid_digest(
                    fields.get("file_md5"), 32
                ):
                    ndr_file_expected += 1
                    ndr_file_mapped += evidence_path in file_paths
                if _has_value(fields.get("ioc")):
                    ndr_vendor_descriptor_count += 1
            else:
                event_type = _event_type(fields)
                if _has_hids_process_context(fields):
                    hids_process_expected += 1
                    hids_process_mapped += evidence_path in process_paths
                if event_type in _HIDS_FILE_EVENT_TYPES and (
                    _has_value(fields.get("file_path"))
                    or _valid_digest(fields.get("md5"), 32)
                    or _valid_digest(fields.get("sha1"), 40)
                    or _valid_digest(fields.get("sha256"), 64)
                ):
                    hids_file_expected += 1
                    hids_file_mapped += evidence_path in file_paths

        if source_type is AlertSourceType.NDR:
            ndr_http_observation_count += len(alert.entities.http.observations)
            ndr_threat_indicator_leak_count += len(alert.entities.threat.iocs)
        else:
            network = alert.entities.network
            if network.source_ip or network.destination_ip:
                hids_canonical_directional_network_count += 1
            if "1.1.1.1" in alert.entities.host.ip_addresses:
                hids_default_external_ip_leak_count += 1
            parsed_by_path = {
                f"{item.source_path}#parsed": item for item in parsed_messages
            }
            for observation in network.observations:
                hids_network_observation_count += 1
                parsed = parsed_by_path.get(observation.evidence_path)
                if (
                    parsed is None
                    or _event_type(parsed.fields) not in _HIDS_NETWORK_EVENT_TYPES
                ):
                    hids_invalid_network_observation_count += 1

    fields = [
        {
            "source_type": source,
            "path": path,
            "messages": count,
            "non_empty_messages": field_non_empty_presence[(source, path)],
            "lanes": {
                lane: field_lane_counts[(source, path)].get(lane, 0)
                for lane in (
                    "canonical_provenance",
                    "fact",
                    "scenario",
                    "semantic",
                    "llm",
                    "sanitized",
                    "omitted",
                )
            },
        }
        for (source, path), count in sorted(field_presence.items())
    ]
    checks = {
        "both_source_families_present": all(
            source_alert_counts.get(source.value, 0) > 0 for source in _SOURCE_TYPES
        ),
        "raw_payload_unchanged": raw_payload_mutation_count == 0,
        "high_value_mapping_gaps_empty": not any(high_value_gap_counts.values()),
        "high_value_instance_gaps_empty": not high_value_instance_gaps,
        "all_nonempty_fields_typed_or_semantic": (
            field_instance_lane_counts["non_empty_not_typed_or_semantic"] == 0
        ),
        "parsed_message_analysis_excludes_structured_fallback": (
            not parsed_message_fallback_violations
        ),
        "ndr_network_observations_complete": (
            ndr_network_mapped == ndr_network_expected
        ),
        "ndr_file_observations_complete": ndr_file_mapped == ndr_file_expected,
        "ndr_vendor_descriptor_not_promoted_to_ioc": (
            ndr_threat_indicator_leak_count == 0
        ),
        "hids_process_observations_complete": (
            hids_process_mapped == hids_process_expected
        ),
        "hids_file_observations_complete": hids_file_mapped == hids_file_expected,
        "hids_network_observations_are_event_scoped": (
            hids_invalid_network_observation_count == 0
        ),
        "hids_top_level_network_direction_remains_empty": (
            hids_canonical_directional_network_count == 0
        ),
        "hids_default_external_ip_not_promoted": (
            hids_default_external_ip_leak_count == 0
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "passed" if all(checks.values()) else "failed",
        "sensitive_values_included": False,
        "source_alert_counts": dict(sorted(source_alert_counts.items())),
        "sample_ids": dict(sorted(sample_ids.items())),
        "topic_counts": {
            source: dict(sorted(values.items()))
            for source, values in sorted(topic_counts.items())
        },
        "parsed_message_counts": dict(sorted(message_counts.items())),
        "parser_counts": {
            source: dict(sorted(values.items()))
            for source, values in sorted(parser_counts.items())
        },
        "canonical_target_coverage": {
            source: {
                target: {
                    "alerts": values.get(target, 0),
                    "coverage_ratio": _ratio(
                        values.get(target, 0), source_alert_counts[source]
                    ),
                }
                for target in _TARGET_PATHS[AlertSourceType(source)]
            }
            for source, values in sorted(canonical_target_counts.items())
        },
        "source_field_semantic_counts": {
            source: dict(sorted(values.items()))
            for source, values in sorted(semantic_type_counts.items())
        },
        "high_value_gap_counts": {
            source: dict(sorted(values.items()))
            for source, values in sorted(high_value_gap_counts.items())
        },
        "high_value_instance_gaps": high_value_instance_gaps,
        "parsed_message_fallback_violations": parsed_message_fallback_violations,
        "field_instance_lane_counts": dict(sorted(field_instance_lane_counts.items())),
        "ndr_observation_coverage": {
            "network_expected_messages": ndr_network_expected,
            "network_mapped_messages": ndr_network_mapped,
            "http_observations": ndr_http_observation_count,
            "file_expected_messages": ndr_file_expected,
            "file_mapped_messages": ndr_file_mapped,
            "vendor_descriptor_messages": ndr_vendor_descriptor_count,
            "threat_indicator_leaks": ndr_threat_indicator_leak_count,
        },
        "hids_observation_coverage": {
            "process_expected_messages": hids_process_expected,
            "process_mapped_messages": hids_process_mapped,
            "file_expected_messages": hids_file_expected,
            "file_mapped_messages": hids_file_mapped,
            "network_observations": hids_network_observation_count,
            "invalid_network_observations": hids_invalid_network_observation_count,
            "canonical_directional_network_alerts": hids_canonical_directional_network_count,
            "default_external_ip_leaks": hids_default_external_ip_leak_count,
        },
        "raw_payload_mutation_count": raw_payload_mutation_count,
        "checks": checks,
        "fields": fields,
    }


def _alert_payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    wrapper = row.get("alert_full_data")
    if not isinstance(wrapper, Mapping):
        raise ValueError("alert_full_data must be an object")
    payload = wrapper.get("alert_data")
    if not isinstance(payload, Mapping):
        raise ValueError("alert_full_data.alert_data must be an object")
    return payload


def _flatten_leaves(value: Any, path: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, Mapping):
        result: list[tuple[str, Any]] = []
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            result.extend(_flatten_leaves(item, child))
        return result
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value):
            result.extend(_flatten_leaves(item, f"{path}[{index}]"))
        return result
    return [(path, value)] if path else []


def _resolve_path(value: Any, path: tuple[str, ...]) -> Any:
    current = value
    for segment in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(segment)
    return current


def _event_type(fields: Mapping[str, Any]) -> str:
    return str(fields.get("event_type") or fields.get("datatype") or "").strip().lower()


def _has_hids_process_context(fields: Mapping[str, Any]) -> bool:
    explicit_fields = _HIDS_PROCESS_FIELDS - {"event_content"}
    if any(_has_value(fields.get(name)) for name in explicit_fields):
        return True
    event_content = str(fields.get("event_content") or "")
    return bool(
        re.search(
            r"[^\s()]+\(\d+\)(?:\s*(?:->|→|&gt;)\s*[^\s()]+\(\d+\))+", event_content
        )
    )


def _valid_digest(value: Any, length: int) -> bool:
    return bool(re.fullmatch(rf"[0-9A-Fa-f]{{{length}}}", str(value or "").strip()))


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _is_high_value_path(source_type: AlertSourceType, path: str) -> bool:
    """Match nested/array leaves against the reviewed source-field inventory."""

    leaf = re.sub(r"\[\d+\]$", "", path.rsplit(".", maxsplit=1)[-1])
    return leaf in _HIGH_VALUE_FIELDS[source_type]


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
    report = build_ndr_hids_field_audit(frame.to_dict(orient="records"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "source_alert_counts": report["source_alert_counts"],
                "parsed_message_counts": report["parsed_message_counts"],
                "output": str(args.output.resolve().relative_to(ROOT)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
