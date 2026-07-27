"""Normalizer for legacy Ping An alert-platform payloads.

The legacy platform wraps source logs under ``alert.hitLog[].zeusRawLogs[]``
and enriches them with SOAR results. This adapter maps that envelope into the
canonical ``AlertInput`` shape while preserving the original payload in
``raw``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from soc_agent.contracts import (
    AlertInput,
    AlertSourceType,
    EvidenceInputPolicy,
    EvidenceInputPolicyName,
    EvidenceLayer,
    EvidenceTrustLevel,
    ParsedRawMessageEvidence,
)
from soc_agent.normalizers.pingan_edr import (
    build_edr_canonical_field_provenance,
    build_edr_file_observations,
    build_edr_process_observations,
    build_edr_source_field_semantics,
    edr_endpoint_addresses,
    edr_field_importance_rules,
    edr_mitre_values,
    edr_threat_indicators,
    first_edr_detail,
    validated_edr_digest,
)
from soc_agent.normalizers.pingan_evidence import build_pingan_fact_inputs
from soc_agent.normalizers.pingan_messages import parse_pingan_raw_message
from soc_agent.normalizers.pingan_siem import (
    build_siem_canonical_field_provenance,
    build_siem_entities,
    build_siem_source_field_semantics,
    siem_field_importance_rules,
)
from soc_agent.normalizers.pingan_threat_intel import (
    build_threat_intel_canonical_field_provenance,
    build_threat_intel_entities,
    build_threat_intel_source_field_semantics,
    threat_intel_category,
    threat_intel_field_importance_rules,
    threat_intel_mitre_values,
    threat_intel_severity,
)

RAW_MESSAGE_FIELD = "message"
_PINGAN_TOPIC_SOURCE_TYPES = {
    "ptp-nids": AlertSourceType.NIDS,
    "sec_guard_wb": AlertSourceType.THREAT_INTEL,
    "t_gbd_zeus_data": AlertSourceType.SIEM,
}
_PINGAN_TRUSTED_STRUCTURED_FALLBACK_TOPICS = frozenset({"t_gbd_zeus_data"})


def is_pingan_platform_payload(payload: Mapping[str, Any]) -> bool:
    alert = payload.get("alert")
    return isinstance(alert, dict) and isinstance(alert.get("hitLog"), list)


def normalize_pingan_platform_payload(payload: Mapping[str, Any]) -> AlertInput:
    original = dict(payload)
    alert = _as_dict(original.get("alert"))
    parsed_messages = _parse_raw_messages(alert)
    preferred_path = parsed_messages[0].source_path if parsed_messages else None
    hit_log_index, hit_log, raw_event_index, raw_event = _select_raw_event(alert, preferred_path=preferred_path)
    raw_event_path = _raw_event_path(hit_log_index, raw_event_index)
    primary_parsed = next((item for item in parsed_messages if item.source_path == f"{raw_event_path}.message"), None)
    parsed_fields = primary_parsed.fields if primary_parsed is not None else {}
    evidence_event = _merge_parsed_message(raw_event, primary_parsed)
    sensor_alert = _as_dict(parsed_fields.get("alert"))
    origin = _json_object(evidence_event.get("_origin"))
    http_payload = _json_object(evidence_event.get("payload"))
    soar_asset = _first_soar_asset(alert.get("soar"))

    source_type = _source_type(hit_log, evidence_event)
    primary_edr_detail = first_edr_detail(primary_parsed.fields) if source_type is AlertSourceType.EDR and primary_parsed is not None else None
    primary_edr_fields = primary_edr_detail[1] if primary_edr_detail else {}
    role_claims, scenario_signals = build_pingan_fact_inputs(
        alert,
        parsed_messages=parsed_messages,
        source_type=source_type,
        structured_fallback_trust=_structured_fallback_trust(hit_log),
    )
    source_system = _first_str(hit_log, ("topic", "topicName")) or _first_str(evidence_event, ("appname", "source"))
    product = _first_str(hit_log, ("topicName",)) or _first_str(evidence_event, ("metadata__product__name",))

    canonical = {
        "schema_version": "soc.alert.v1",
        "alert_id": _first_str(alert, ("alertId", "alertCode")) or _first_str(evidence_event, ("alarm_id", "finding__uid")),
        "source": {
            "source_type": source_type.value,
            "source_system": source_system,
            "product": product,
            "integration_name": "pingan_legacy_alert_platform",
        },
        "detection": {
            # Platform ruleCode is the stable alert identity. Sensor rule ids remain
            # available in parsed evidence because they use a different namespace.
            "rule_code": _first_str(hit_log, ("ruleCode",)) or _first_str(evidence_event, ("str_rule_id", "rule_id")),
            "rule_name": _first_str(sensor_alert, ("signature",))
            or _first_str(primary_edr_fields, ("rule_name", "rule_desc"))
            or _first_str(
                parsed_fields,
                ("finding__title", "str_title", "rule_name", "alert_describe"),
            )
            or _first_str(hit_log, ("ruleName",))
            or _first_str(raw_event, ("finding__title", "str_title")),
            "rule_category": _first_str(sensor_alert, ("category",))
            or (threat_intel_category(parsed_fields) if source_type is AlertSourceType.THREAT_INTEL else None)
            or _first_str(parsed_fields, ("finding__type_name", "attack_type", "vuln_type", "event_type"))
            or (_first_str(evidence_event, ("subtype",)) if source_type is AlertSourceType.SIEM else None)
            or _first_str(alert, ("tertiaryType", "secondaryType"))
            or _first_str(raw_event, ("finding__type_name", "attack_type", "vuln_type")),
        },
        "event": {
            "event_id": _first_str(evidence_event, ("alarm_id", "finding__uid", "str_unique_id", "logcloud_msgid")),
            "event_time": _first_str(
                evidence_event,
                (
                    "t_detect_time",
                    "timeStr",
                    "modeltime",
                    "timestamp",
                    "time",
                    "access_time",
                    "first_access_time",
                ),
            )
            or _first_str(alert, ("createAt",)),
            "received_at": _first_str(alert, ("createAt",)) or _first_str(evidence_event, ("timestamp", "time")),
        },
        "classification": {
            "severity": (threat_intel_severity(parsed_fields) if source_type is AlertSourceType.THREAT_INTEL else None)
            or _first_str(
                evidence_event,
                (
                    "severity",
                    "risk_level",
                    "hazard_rating",
                    "threat_level",
                    "event_level",
                ),
            )
            or _first_str(alert, ("riskLevel",)),
            "category": _first_str(sensor_alert, ("category",))
            or (threat_intel_category(parsed_fields) if source_type is AlertSourceType.THREAT_INTEL else None)
            or _first_str(parsed_fields, ("finding__type_name", "attack_type", "vuln_type", "event_name", "event_type"))
            or (_first_str(evidence_event, ("subtype",)) if source_type is AlertSourceType.SIEM else None)
            or _first_str(alert, ("tertiaryType", "secondaryType", "primaryType"))
            or _first_str(raw_event, ("finding__type_name", "attack_type", "vuln_type")),
            "tactic": _dedupe(
                [
                    *_mitre_values(evidence_event, prefix="TA"),
                    *(edr_mitre_values(parsed_messages, prefix="TA") if source_type is AlertSourceType.EDR else []),
                ]
            ),
            "technique": _dedupe(
                [
                    *_mitre_values(evidence_event, prefix="T"),
                    *(edr_mitre_values(parsed_messages, prefix="T") if source_type is AlertSourceType.EDR else []),
                    *(threat_intel_mitre_values(parsed_messages) if source_type is AlertSourceType.THREAT_INTEL else []),
                ]
            ),
            "labels": _labels(alert, hit_log, evidence_event),
        },
        "entities": _entities(
            source_type,
            evidence_event,
            origin,
            http_payload,
            primary_parsed,
            parsed_messages,
            raw_event_path,
        ),
        "evidence": _evidence(alert, hit_log, evidence_event),
        "extensions": {
            "legacy_platform": _legacy_platform_context(original, alert, hit_log, evidence_event, soar_asset),
            "parsed_raw_messages": [item.model_dump(mode="json", exclude_none=True) for item in parsed_messages],
            "role_claims": [item.model_dump(mode="json", exclude_none=True) for item in role_claims],
            "scenario_signals": [item.model_dump(mode="json", exclude_none=True) for item in scenario_signals],
            "field_importance_rules": _field_importance_rules(source_type),
            "source_field_semantics": _source_field_semantics(
                source_type,
                parsed_messages,
                fallback_fields=raw_event,
                fallback_path=raw_event_path,
            ),
            "analysis_context_coverage": _analysis_context_coverage(original, alert),
            "evidence_input_policy": _evidence_input_policy(
                hit_log_index,
                raw_event_index,
                raw_event,
                supplementary_input_paths=[path for path in _raw_message_paths(alert) if path != f"{raw_event_path}.message"],
                structured_fallback_trust=_structured_fallback_trust(hit_log),
            ),
        },
        "raw": original,
    }

    normalized = AlertInput.model_validate(_drop_none(canonical))
    provenance = _canonical_field_provenance(
        normalized,
        source_type=source_type,
        parsed_messages=parsed_messages,
        primary_parsed=primary_parsed,
        raw_event=raw_event,
        raw_event_path=raw_event_path,
        structured_fallback_trust=_structured_fallback_trust(hit_log),
    )
    if provenance:
        normalized.extensions["canonical_field_provenance"] = provenance
    normalized.detection.detection_key = normalized.detection.detection_key or _detection_key(normalized)
    return normalized


def _evidence_input_policy(
    hit_log_index: int | None,
    raw_event_index: int | None,
    raw_event: dict[str, Any],
    *,
    supplementary_input_paths: list[str] | None = None,
    structured_fallback_trust: EvidenceTrustLevel = EvidenceTrustLevel.LOW,
) -> dict[str, Any]:
    raw_event_path = _raw_event_path(hit_log_index, raw_event_index)
    if _has_raw_message(raw_event):
        message_path = f"{raw_event_path}.{RAW_MESSAGE_FIELD}"
        policy = EvidenceInputPolicy(
            name=EvidenceInputPolicyName.RAW_MESSAGE_FIRST,
            primary_input_path=message_path,
            fallback_input_path=raw_event_path,
            selected_input_path=message_path,
            supplementary_input_paths=supplementary_input_paths or [],
            selected_layer=EvidenceLayer.RAW_MESSAGE,
            ignore_processed_fields_for_reasoning=True,
            trust_level=EvidenceTrustLevel.HIGH,
        )
    else:
        policy = EvidenceInputPolicy(
            name=EvidenceInputPolicyName.STRUCTURED_FALLBACK,
            primary_input_path=raw_event_path,
            selected_input_path=raw_event_path,
            supplementary_input_paths=supplementary_input_paths or [],
            selected_layer=EvidenceLayer.RAW_STRUCTURED,
            fallback_reason="raw_message_missing",
            ignore_processed_fields_for_reasoning=False,
            trust_level=structured_fallback_trust,
        )
    return policy.model_dump(mode="json", exclude_none=True)


def _structured_fallback_trust(
    hit_log: Mapping[str, Any],
) -> EvidenceTrustLevel:
    topic = _first_str(hit_log, ("topic",))
    if topic and topic.strip().lower() in _PINGAN_TRUSTED_STRUCTURED_FALLBACK_TOPICS:
        return EvidenceTrustLevel.HIGH
    return EvidenceTrustLevel.LOW


def _legacy_platform_context(
    original: dict[str, Any],
    alert: dict[str, Any],
    hit_log: dict[str, Any],
    raw_event: dict[str, Any],
    soar_asset: dict[str, Any],
) -> dict[str, Any]:
    content_items = _content_items(alert.get("content"))
    soar_display_names = _soar_display_names(alert.get("soar"))
    return _drop_none(
        {
            "workflow": {
                "alert_code": _first_str(alert, ("alertCode",)),
                "alert_name": _first_str(alert, ("alertName",)),
                "execute_type": _first_str(alert, ("executeType",)),
                "status": _first_str(alert, ("status",)),
                "created_at": _first_str(alert, ("createAt",)),
                "process_actions": _dedupe([item["process_action"] for item in content_items if item.get("process_action")]),
                "handlers": _dedupe([item["user_name"] for item in content_items if item.get("user_name")]),
                "content_count": len(content_items),
            },
            "taxonomy": {
                "primary_type": _first_str(alert, ("primaryType",)),
                "secondary_type": _first_str(alert, ("secondaryType",)),
                "tertiary_type": _first_str(alert, ("tertiaryType",)),
                "tertiary_type_id": _first_str(alert, ("tertiaryTypeId",)),
                "profile_code": _first_str(alert, ("profileCode",)),
                "profile_name": _first_str(alert, ("profileName",)),
                "topic": _first_str(hit_log, ("topic",)),
                "topic_name": _first_str(hit_log, ("topicName",)),
            },
            "ownership": {
                "dst_bu_code": _first_str(raw_event, ("dst_BUcode",)),
                "dst_company": _first_str(raw_event, ("zeus_company_dst_name", "device__org__ou_name", "str_dept_name")),
                "asset_group": _first_str(raw_event, ("asset_group",)),
                "dip_group": _first_str(raw_event, ("dip_group",)),
                "industry": _first_str(raw_event, ("industry_sign",)),
                "soar_asset_department": _first_str(soar_asset, ("strdeptname", "department")),
                "soar_asset_owner": _first_str(soar_asset, ("strusername", "username")),
            },
            "sensor": {
                "source": _first_str(raw_event, ("source",)),
                "appname": _first_str(raw_event, ("appname",)),
                "device_ip": _first_str(raw_event, ("device_ip",)),
                "node_ip": _first_str(raw_event, ("node_ip",)),
                "idc_location": _first_str(raw_event, ("idc_location",)),
                "vlan_id": _first_str(raw_event, ("vlan_id",)),
                "vxlan_id": _first_str(raw_event, ("vxlan_id",)),
                "skyeye_type": _first_str(raw_event, ("skyeye_type",)),
                "skyeye_serial_num": _first_str(raw_event, ("skyeye_serial_num", "serial_num")),
            },
            "disposition": {
                "host_state": _first_str(raw_event, ("host_state",)),
                "rule_state": _first_str(raw_event, ("rule_state",)),
                "is_blocked": _boolish(_first_str(raw_event, ("is_blocked",))),
                "is_banned": _boolish(_first_str(raw_event, ("is_banned",))),
                "is_white": _boolish(_first_str(raw_event, ("is_white",))),
                "repeat_count": _intish(_first_str(raw_event, ("repeat_count", "i_count"))),
                "confidence": _first_str(raw_event, ("confidence",)),
                "hazard_level": _first_str(raw_event, ("hazard_level",)),
                "hazard_rating": _first_str(raw_event, ("hazard_rating",)),
                "threat_level": _first_str(raw_event, ("threat_level",)),
            },
            "correlation": {
                "alarm_id": _first_str(raw_event, ("alarm_id",)),
                "alert_hash": _first_str(raw_event, ("alert_hash",)),
                "logcloud_msgid": _first_str(raw_event, ("logcloud_msgid",)),
                "raw_event_count": len(hit_log.get("zeusRawLogs") or []),
                "related_alert_count": len(original.get("relatedAlertList") or []),
                "related_disposition_summary": _related_disposition_summary(original.get("relatedAlertList")),
                "soar_display_names": soar_display_names,
            },
            "soar": {
                "display_names": soar_display_names,
                "asset": _soar_asset_summary(soar_asset),
            },
        }
    )


def _entities(
    source_type: AlertSourceType,
    raw_event: dict[str, Any],
    origin: dict[str, Any],
    http_payload: dict[str, Any],
    parsed_message: ParsedRawMessageEvidence | None,
    parsed_messages: list[ParsedRawMessageEvidence],
    raw_event_path: str,
) -> dict[str, Any]:
    primary_fields = parsed_message.fields if parsed_message is not None else {}

    if source_type is AlertSourceType.THREAT_INTEL:
        return build_threat_intel_entities(
            parsed_messages,
            fallback_fields=raw_event,
        )
    if source_type is AlertSourceType.SIEM:
        return build_siem_entities(
            raw_event,
            evidence_path=raw_event_path,
        )

    def source_value(aliases: tuple[str, ...]) -> str | None:
        if source_type is AlertSourceType.EDR:
            value = _first_str(primary_fields, aliases)
            if value is not None:
                return value
        return _first_str(raw_event, aliases)

    req = _parse_request_line(_first_str(http_payload, ("req_header",)) or "")
    decoded_request_header = _decoded_request_header(parsed_message)
    decoded_headers = _as_dict(decoded_request_header.get("headers"))
    forwarded_chain = decoded_request_header.get("forwarded_chain")
    decoded_forwarded = forwarded_chain[0] if isinstance(forwarded_chain, list) and forwarded_chain else None
    process_chain = _process_chain(source_value(("event_content", "finding__desc", "str_desc")) or "")
    nids_http, _ = _nids_http_projection(
        raw_event,
        decoded_fields=(parsed_message.decoded_fields if parsed_message is not None else None),
    )
    edr_detail = first_edr_detail(parsed_message.fields) if source_type is AlertSourceType.EDR and parsed_message is not None else None
    edr_fields = edr_detail[1] if edr_detail else {}
    edr_action = _as_dict(edr_fields.get("action_detail"))
    edr_endpoint_ips = (
        _selected_edr_endpoint_addresses(
            parsed_messages,
            fallback_fields=raw_event,
        )
        if source_type is AlertSourceType.EDR
        else []
    )
    edr_iocs = (
        _selected_edr_threat_indicators(
            parsed_messages,
            fallback_fields=raw_event,
        )
        if source_type is AlertSourceType.EDR
        else []
    )

    if source_type is AlertSourceType.EDR:
        network = {
            # Endpoint identity and vendor attack-role fields do not establish
            # packet/session direction. Keep wire endpoints empty until the
            # source contract supplies explicit directional observations.
            "protocol": source_value(("proto", "protocol")),
        }
    elif source_type is AlertSourceType.HIDS:
        # A host alert's agent/internal IP identifies the impacted endpoint; it
        # must not be mislabeled as an attacker/source network role.
        network = {
            "protocol": _first_str(raw_event, ("proto", "protocol")),
        }
    else:
        network = {
            "source_ip": _first_str(raw_event, ("sip", "src_addr", "source_ip")) or _first_str(origin, ("sip",)),
            "destination_ip": _first_str(raw_event, ("dip", "dst_addr")) or _first_str(origin, ("dip",)),
            "src_port": _first_str(raw_event, ("sport",)) or _first_str(origin, ("sport",)),
            "dst_port": _first_str(raw_event, ("dport",)) or _first_str(origin, ("dport",)),
            "protocol": _first_str(raw_event, ("proto", "labels_proto", "protocol")),
            "application_protocol": _first_str(raw_event, ("app_proto",)),
            "direction": _first_str(raw_event, ("direction",)),
            "domain": nids_http.get("host") or _first_str(raw_event, ("host",)),
            "url": nids_http.get("url") or _first_str(origin, ("uri",)) or req.get("path"),
        }
    network["observations"] = _network_observations(source_type, parsed_messages)

    http = {
        "method": req.get("method"),
        "host": _first_str(raw_event, ("host",)) or req.get("host"),
        "path": req.get("path") or _first_str(origin, ("uri",)),
        "url": _first_str(origin, ("uri",)) or req.get("path"),
        "status_code": _first_str(raw_event, ("rsp_status",)) or _first_str(origin, ("rsp_status",)),
        "user_agent": _first_header_value(decoded_headers, "user-agent"),
        "x_forwarded_for": _first_str(raw_event, ("x_forwarded_for",)) or _first_str(origin, ("xff",)) or decoded_forwarded,
    }
    if source_type is AlertSourceType.NIDS:
        http = {**http, **nids_http}
    http["observations"] = _http_observations(source_type, parsed_messages)

    process_md5_value = source_value(
        (
            "process__file__hashes__md5",
            "str_md5",
            "str_suspicious_file_md5",
        ),
    ) or _first_str(edr_fields, ("process_md5",))
    process_sha256_value = source_value(
        (
            "process__file__hashes__sha256",
            "str_sha256",
            "str_suspicious_file_sha256",
        ),
    ) or _first_str(edr_fields, ("process_sha256",))
    process_md5 = validated_edr_digest(process_md5_value, expected_length=32) if source_type is AlertSourceType.EDR else process_md5_value
    process_sha256 = validated_edr_digest(process_sha256_value, expected_length=64) if source_type is AlertSourceType.EDR else process_sha256_value
    process = {
        "process_name": source_value(
            (
                "process__name",
                "str_process_short",
                "process__file__name",
                "str_suspicious_process_ancestor_short",
            ),
        )
        or _first_str(edr_fields, ("process_mame", "process_name"))
        or (process_chain[-1] if process_chain else None),
        "process_id": _intish(source_value(("process__pid", "str_process_id")) or _first_str(edr_fields, ("process_pid",))),
        "process_path": source_value(("process__file__path", "str_process_full", "str_suspicious_file")) or _first_str(edr_fields, ("process_path",)),
        "command_line": source_value(
            (
                "process__cmd_line",
                "str_cmd",
                "str_suspicious_process_ancestor_cmd",
                "process__ancestor__cmd_line",
            ),
        )
        or _first_str(edr_fields, ("command",)),
        "parent_process_name": _basename(source_value(("process__parent_process__file__path", "str_parent_path_full"))) or (process_chain[-2] if len(process_chain) >= 2 else None),
        "parent_command_line": source_value(("process__parent_process__cmd_line", "str_parent_cmd")),
        "md5": process_md5,
        "sha256": process_sha256,
        "observations": (build_edr_process_observations(parsed_messages) if source_type is AlertSourceType.EDR else _process_observations(parsed_messages)),
    }

    action_file_name = _first_str(edr_action, ("file_name",))
    action_file_path = _first_str(edr_action, ("file_path",))
    action_file_selected = bool(action_file_name or action_file_path)
    file_entity = {
        "file_name": action_file_name or source_value(("process__file__name", "str_process_short")) or _first_str(edr_fields, ("process_mame", "process_name")),
        "file_path": action_file_path or source_value(("process__file__path", "str_process_full", "str_suspicious_file")) or _first_str(edr_fields, ("process_path",)),
        "md5": None if action_file_selected else process_md5,
        "sha256": None if action_file_selected else process_sha256,
        "observations": (build_edr_file_observations(parsed_messages) if source_type is AlertSourceType.EDR else []),
    }

    return {
        "network": network,
        "process": process,
        "user": {
            # CMDB/SOAR owners describe asset ownership, not the event actor.
            "username": source_value(("str_user_agent", "process__user__name", "str_user_process")) or _first_str(edr_fields, ("process_user",)),
            "um_account": source_value(("um", "um_account", "umAccount", "str_um_account")),
        },
        "host": {
            "host_name": source_value(("host_name", "device__hostname", "str_source_host", "endpoint")),
            "host_id": source_value(("agent_id", "str_agent_id", "metadata__product__version")),
            "asset_id": (source_value(("agent_id", "str_agent_id")) or (edr_endpoint_ips[0] if edr_endpoint_ips else None)) if source_type is AlertSourceType.EDR else _first_str(raw_event, ("agent_id", "device__ip", "str_source_ip")),
            "asset_group": source_value(("device__org__ou_name", "str_dept_name", "dip_group", "asset_group")),
            "ip_addresses": edr_endpoint_ips
            if source_type is AlertSourceType.EDR
            else _dedupe(
                [
                    value
                    for value in [
                        _first_str(raw_event, ("agent_ip",)),
                        _first_str(raw_event, ("internal_ip", "device__ip", "str_source_ip")),
                        _usable_hids_external_ip(source_type, _first_str(raw_event, ("external_ip",))),
                    ]
                    if value
                ]
            ),
        },
        "file": file_entity,
        "http": http,
        "threat": {"iocs": edr_iocs if source_type is AlertSourceType.EDR else _dedupe([value for value in [_first_str(raw_event, ("ioc",))] if value])},
    }


def _selected_edr_endpoint_addresses(
    parsed_messages: list[ParsedRawMessageEvidence],
    *,
    fallback_fields: Mapping[str, Any],
) -> list[str]:
    for parsed in parsed_messages:
        values = edr_endpoint_addresses(parsed.fields)
        if values:
            return values
    return edr_endpoint_addresses(fallback_fields)


def _selected_edr_threat_indicators(
    parsed_messages: list[ParsedRawMessageEvidence],
    *,
    fallback_fields: Mapping[str, Any],
) -> list[str]:
    known_endpoint_addresses = _dedupe([value for parsed in parsed_messages for value in edr_endpoint_addresses(parsed.fields)] + edr_endpoint_addresses(fallback_fields))
    parsed_values = _dedupe(
        [
            value
            for parsed in parsed_messages
            for value in edr_threat_indicators(
                parsed.fields,
                known_endpoint_addresses=known_endpoint_addresses,
            )
        ]
    )
    if parsed_values:
        return parsed_values
    return edr_threat_indicators(
        fallback_fields,
        known_endpoint_addresses=known_endpoint_addresses,
    )


def _network_observations(
    source_type: AlertSourceType,
    parsed_messages: list[ParsedRawMessageEvidence],
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for parsed in parsed_messages:
        fields = parsed.fields
        if source_type in {AlertSourceType.EDR, AlertSourceType.HIDS}:
            continue
        source_ip = _first_str(fields, ("sip", "src_addr", "source_ip"))
        destination_ip = _first_str(fields, ("dip", "dst_addr"))
        decoded_request = _decoded_request_header(parsed)
        forwarded = decoded_request.get("forwarded_chain")
        observation = _drop_none(
            {
                "observation_id": f"network:{parsed.message_hash[:16]}",
                "evidence_path": f"{parsed.source_path}#parsed",
                "event_time": _first_str(parsed.header, ("timestamp", "event_time")) or _first_str(fields, ("timestamp", "time", "access_time", "first_access_time")),
                "source_ip": source_ip,
                "destination_ip": destination_ip,
                "src_port": _intish(_first_str(fields, ("sport", "src_port"))),
                "dst_port": _intish(_first_str(fields, ("dport", "dst_port"))),
                "protocol": _first_str(fields, ("proto", "labels_proto", "protocol")),
                "application_protocol": _first_str(fields, ("app_proto",)),
                "direction": _first_str(fields, ("direction",)),
                "community_id": _first_str(fields, ("community_id",)),
                "flow_id": fields.get("flow_id"),
                "sensor_source_ip": _nested_str(
                    fields,
                    ("alert", "source", "ip"),
                ),
                "sensor_source_port": _nested_int(
                    fields,
                    ("alert", "source", "port"),
                ),
                "sensor_target_ip": _nested_str(
                    fields,
                    ("alert", "target", "ip"),
                ),
                "sensor_target_port": _nested_int(
                    fields,
                    ("alert", "target", "port"),
                ),
                "sensor_source_zone": _nested_str(
                    fields,
                    ("alert", "source", "zone"),
                ),
                "sensor_target_zone": _nested_str(
                    fields,
                    ("alert", "target", "zone"),
                ),
                "bytes_to_server": _nested_int(fields, ("flow", "bytes_toserver")),
                "bytes_to_client": _nested_int(fields, ("flow", "bytes_toclient")),
                "packets_to_server": _nested_int(fields, ("flow", "pkts_toserver")),
                "packets_to_client": _nested_int(fields, ("flow", "pkts_toclient")),
                "forwarded_chain": [str(value) for value in forwarded] if isinstance(forwarded, list) else [],
            }
        )
        if source_ip or destination_ip or observation.get("forwarded_chain"):
            observations.append(observation)
    return observations


def _http_observations(
    source_type: AlertSourceType,
    parsed_messages: list[ParsedRawMessageEvidence],
) -> list[dict[str, Any]]:
    if source_type is not AlertSourceType.NIDS:
        return []
    observations: list[dict[str, Any]] = []
    for parsed in parsed_messages:
        projection, _ = _nids_http_projection(
            parsed.fields,
            decoded_fields=parsed.decoded_fields,
        )
        if not projection:
            continue
        observations.append(
            _drop_none(
                {
                    "observation_id": f"http:{parsed.message_hash[:16]}",
                    "evidence_path": f"{parsed.source_path}#parsed",
                    "event_time": _first_str(parsed.header, ("timestamp", "event_time"))
                    or _first_str(
                        parsed.fields,
                        ("timestamp", "time", "access_time", "first_access_time"),
                    ),
                    **projection,
                }
            )
        )
    return observations


def _nids_http_projection(
    fields: Mapping[str, Any],
    *,
    decoded_fields: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Project bounded HTTP transaction metadata while retaining bodies as evidence."""

    http = _as_dict(fields.get("http"))

    projection: dict[str, Any] = {}
    source_paths: dict[str, str] = {}
    decoded_request_headers = _decoded_nids_request_headers(decoded_fields)

    def add(name: str, value: Any, source_path: str) -> None:
        if value is None or value == "":
            return
        projection[name] = value
        source_paths[name] = source_path

    method = _first_str(http, ("http_method", "method"))
    host = _first_str(http, ("hostname", "host"))
    host_path = "http.hostname" if _first_str(http, ("hostname",)) else "http.host"
    if host is None:
        host, host_path = _nids_request_header(
            http,
            "host",
            decoded_request_headers=decoded_request_headers,
        )
    url = _first_str(http, ("url",))
    user_agent = _first_str(http, ("http_user_agent", "user_agent"))
    user_agent_path = "http.http_user_agent" if _first_str(http, ("http_user_agent",)) else "http.user_agent"
    if user_agent is None:
        user_agent, user_agent_path = _nids_request_header(
            http,
            "user-agent",
            decoded_request_headers=decoded_request_headers,
        )
    referer = _first_str(http, ("http_refer", "referer", "referrer"))
    referer_path = "http.http_refer" if _first_str(http, ("http_refer",)) else ("http.referer" if _first_str(http, ("referer",)) else "http.referrer")
    if referer is None:
        referer, referer_path = _nids_request_header(
            http,
            "referer",
            decoded_request_headers=decoded_request_headers,
        )
    x_forwarded_for = _first_str(http, ("xff", "x_forwarded_for"))
    xff_path = "http.xff" if _first_str(http, ("xff",)) else "http.x_forwarded_for"
    if x_forwarded_for is None:
        sensor_alert = _as_dict(fields.get("alert"))
        x_forwarded_for = _first_str(sensor_alert, ("xff",))
        xff_path = "alert.xff"
    if x_forwarded_for is None:
        x_forwarded_for, xff_path = _nids_request_header(
            http,
            "x-forwarded-for",
            decoded_request_headers=decoded_request_headers,
        )

    add("method", method, "http.http_method" if _first_str(http, ("http_method",)) else "http.method")
    add("host", host, host_path)
    add("path", url, "http.url")
    add("url", url, "http.url")
    add("protocol", _first_str(http, ("protocol",)), "http.protocol")
    add("port", _intish(_first_str(http, ("http_port", "port"))), "http.http_port" if _first_str(http, ("http_port",)) else "http.port")
    add("status_code", _intish(_first_str(http, ("status", "status_code"))), "http.status" if _first_str(http, ("status",)) else "http.status_code")
    add("user_agent", user_agent, user_agent_path)
    add("referer", referer, referer_path)
    add("x_forwarded_for", _first_forwarded_address(x_forwarded_for), xff_path)
    return projection, source_paths


def _nids_request_header(
    http: Mapping[str, Any],
    target_name: str,
    *,
    decoded_request_headers: Mapping[str, Any],
) -> tuple[str | None, str]:
    headers = http.get("request_headers")
    if isinstance(headers, list):
        for index, item in enumerate(headers):
            if not isinstance(item, Mapping):
                continue
            name = item.get("name")
            value = item.get("value")
            if isinstance(name, str) and name.strip().lower() == target_name.lower() and value is not None and str(value).strip():
                return str(value).strip(), f"http.request_headers[{index}].value"
    for name, value in decoded_request_headers.items():
        if str(name).strip().lower() == target_name.lower() and value is not None and str(value).strip():
            return str(value).strip(), f"decoded.request_header_str.{name}"
    return None, f"http.request_headers.{target_name}"


def _decoded_nids_request_headers(
    decoded_fields: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(decoded_fields, Mapping):
        return {}
    value = decoded_fields.get("request_header_str")
    return dict(value) if isinstance(value, Mapping) else {}


def _first_forwarded_address(value: str | None) -> str | None:
    if value is None:
        return None
    first = value.split(",", 1)[0].strip()
    return first or None


def _canonical_field_provenance(
    alert: AlertInput,
    *,
    source_type: AlertSourceType,
    parsed_messages: list[ParsedRawMessageEvidence],
    primary_parsed: ParsedRawMessageEvidence | None,
    raw_event: Mapping[str, Any],
    raw_event_path: str,
    structured_fallback_trust: EvidenceTrustLevel,
) -> list[dict[str, Any]]:
    if source_type is AlertSourceType.EDR:
        return build_edr_canonical_field_provenance(
            alert,
            parsed_messages=parsed_messages,
            primary_parsed=primary_parsed,
        )
    if source_type is AlertSourceType.THREAT_INTEL:
        return build_threat_intel_canonical_field_provenance(
            alert,
            parsed_messages=parsed_messages,
        )
    if source_type is AlertSourceType.SIEM:
        return build_siem_canonical_field_provenance(
            alert,
            fields=raw_event,
            evidence_path=raw_event_path,
            trust=structured_fallback_trust,
        )
    if source_type is not AlertSourceType.NIDS:
        return []

    provenance: list[dict[str, Any]] = []
    if primary_parsed is not None:
        fields = primary_parsed.fields
        sensor_alert = _as_dict(fields.get("alert"))
        network_sources = {
            "source_ip": _first_present_path(fields, ("sip", "src_addr", "source_ip")),
            "destination_ip": _first_present_path(fields, ("dip", "dst_addr")),
            "src_port": _first_present_path(fields, ("sport", "src_port")),
            "dst_port": _first_present_path(fields, ("dport", "dst_port")),
            "protocol": _first_present_path(fields, ("proto", "labels_proto", "protocol")),
            "application_protocol": _first_present_path(fields, ("app_proto",)),
            "direction": _first_present_path(fields, ("direction",)),
        }
        for field_name, source_path in network_sources.items():
            _append_provenance(
                provenance,
                canonical_path=f"entities.network.{field_name}",
                selected_value=getattr(alert.entities.network, field_name),
                parsed=primary_parsed,
                source_path=source_path,
            )

        nids_http, http_sources = _nids_http_projection(
            fields,
            decoded_fields=primary_parsed.decoded_fields,
        )
        for field_name, source_path in http_sources.items():
            _append_provenance(
                provenance,
                canonical_path=f"entities.http.{field_name}",
                selected_value=getattr(alert.entities.http, field_name),
                parsed=primary_parsed,
                source_path=source_path,
            )
        if nids_http.get("host"):
            _append_provenance(
                provenance,
                canonical_path="entities.network.domain",
                selected_value=alert.entities.network.domain,
                parsed=primary_parsed,
                source_path=http_sources.get("host"),
            )
        if nids_http.get("url"):
            _append_provenance(
                provenance,
                canonical_path="entities.network.url",
                selected_value=alert.entities.network.url,
                parsed=primary_parsed,
                source_path=http_sources.get("url"),
            )

        _append_provenance(
            provenance,
            canonical_path="detection.rule_name",
            selected_value=alert.detection.rule_name,
            parsed=primary_parsed,
            source_path="alert.signature" if _first_str(sensor_alert, ("signature",)) else None,
        )
        category_path = "alert.category" if _first_str(sensor_alert, ("category",)) else None
        _append_provenance(
            provenance,
            canonical_path="detection.rule_category",
            selected_value=alert.detection.rule_category,
            parsed=primary_parsed,
            source_path=category_path,
        )
        _append_provenance(
            provenance,
            canonical_path="classification.category",
            selected_value=alert.classification.category,
            parsed=primary_parsed,
            source_path=category_path,
        )
    network_by_evidence = {item.evidence_path: (index, item) for index, item in enumerate(alert.entities.network.observations)}
    http_by_evidence = {item.evidence_path: (index, item) for index, item in enumerate(alert.entities.http.observations)}
    for parsed in parsed_messages:
        evidence_path = f"{parsed.source_path}#parsed"
        network_item = network_by_evidence.get(evidence_path)
        if network_item is not None:
            observation_index, observation = network_item
            network_sources = {
                "source_ip": _first_present_path(parsed.fields, ("sip", "src_addr", "source_ip")),
                "destination_ip": _first_present_path(parsed.fields, ("dip", "dst_addr")),
                "src_port": _first_present_path(parsed.fields, ("sport", "src_port")),
                "dst_port": _first_present_path(parsed.fields, ("dport", "dst_port")),
                "protocol": _first_present_path(parsed.fields, ("proto", "labels_proto", "protocol")),
                "application_protocol": _first_present_path(parsed.fields, ("app_proto",)),
                "direction": _first_present_path(parsed.fields, ("direction",)),
                "community_id": _first_present_path(parsed.fields, ("community_id",)),
                "flow_id": _first_present_path(parsed.fields, ("flow_id",)),
                "sensor_source_ip": "alert.source.ip",
                "sensor_source_port": "alert.source.port",
                "sensor_target_ip": "alert.target.ip",
                "sensor_target_port": "alert.target.port",
                "sensor_source_zone": "alert.source.zone",
                "sensor_target_zone": "alert.target.zone",
                "bytes_to_server": "flow.bytes_toserver",
                "bytes_to_client": "flow.bytes_toclient",
                "packets_to_server": "flow.pkts_toserver",
                "packets_to_client": "flow.pkts_toclient",
            }
            for field_name, source_path in network_sources.items():
                _append_provenance(
                    provenance,
                    canonical_path=f"entities.network.observations[{observation_index}].{field_name}",
                    selected_value=getattr(observation, field_name),
                    parsed=parsed,
                    source_path=source_path,
                )

        http_item = http_by_evidence.get(evidence_path)
        if http_item is not None:
            observation_index, observation = http_item
            _, http_sources = _nids_http_projection(
                parsed.fields,
                decoded_fields=parsed.decoded_fields,
            )
            for field_name, source_path in http_sources.items():
                _append_provenance(
                    provenance,
                    canonical_path=f"entities.http.observations[{observation_index}].{field_name}",
                    selected_value=getattr(observation, field_name),
                    parsed=parsed,
                    source_path=source_path,
                )
    return provenance


def _append_provenance(
    target: list[dict[str, Any]],
    *,
    canonical_path: str,
    selected_value: Any,
    parsed: ParsedRawMessageEvidence,
    source_path: str | None,
) -> None:
    if selected_value is None or selected_value == "" or not source_path:
        return
    target.append(
        {
            "canonical_path": canonical_path,
            "selected_value": str(selected_value),
            "selected_from": (f"{parsed.source_path}#{source_path}" if source_path.startswith(("decoded.", "repaired.", "parsed.")) else f"{parsed.source_path}#parsed.{source_path}"),
            "source_layer": EvidenceLayer.RAW_MESSAGE.value,
            "trust_level": EvidenceTrustLevel.HIGH.value,
            "selection_reason": "pingan_raw_message_mapping",
        }
    )


def _first_present_path(
    fields: Mapping[str, Any],
    aliases: tuple[str, ...],
) -> str | None:
    for alias in aliases:
        if _first_str(dict(fields), (alias,)) is not None:
            return alias
    return None


def _field_importance_rules(
    source_type: AlertSourceType,
) -> list[dict[str, Any]]:
    if source_type is AlertSourceType.EDR:
        return edr_field_importance_rules()
    if source_type is AlertSourceType.THREAT_INTEL:
        return threat_intel_field_importance_rules()
    if source_type is AlertSourceType.SIEM:
        return siem_field_importance_rules()
    if source_type is not AlertSourceType.NIDS:
        return []
    definitions = (
        ("pingan.nids.source_ip", ["parsed.sip"], "entities.network.source_ip", "critical"),
        ("pingan.nids.destination_ip", ["parsed.dip"], "entities.network.destination_ip", "critical"),
        ("pingan.nids.src_port", ["parsed.sport"], "entities.network.src_port", "high"),
        ("pingan.nids.dst_port", ["parsed.dport"], "entities.network.dst_port", "high"),
        ("pingan.nids.protocol", ["parsed.proto"], "entities.network.protocol", "high"),
        ("pingan.nids.application_protocol", ["parsed.app_proto"], "entities.network.application_protocol", "high"),
        ("pingan.nids.direction", ["parsed.direction"], "entities.network.direction", "high"),
        ("pingan.nids.signature", ["parsed.alert.signature"], "detection.rule_name", "critical"),
        ("pingan.nids.category", ["parsed.alert.category"], "detection.rule_category", "high"),
        ("pingan.nids.http.method", ["parsed.http.http_method"], "entities.http.method", "high"),
        ("pingan.nids.http.host", ["parsed.http.hostname"], "entities.http.host", "high"),
        ("pingan.nids.http.url", ["parsed.http.url"], "entities.http.url", "high"),
        ("pingan.nids.http.status", ["parsed.http.status"], "entities.http.status_code", "high"),
        ("pingan.nids.http.user_agent", ["parsed.http.http_user_agent"], "entities.http.user_agent", "high"),
        (
            "pingan.nids.http.xff",
            ["parsed.http.xff", "parsed.alert.xff"],
            "entities.http.x_forwarded_for",
            "critical",
        ),
    )
    return [
        {
            "rule_id": rule_id,
            "source_patterns": source_patterns,
            "expected_target": expected_target,
            "importance": importance,
            "source_types": [AlertSourceType.NIDS.value],
            "reason": f"PingAn NIDS evidence should populate {expected_target}",
        }
        for rule_id, source_patterns, expected_target, importance in definitions
    ]


def _process_observations(parsed_messages: list[ParsedRawMessageEvidence]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for parsed in parsed_messages:
        fields = parsed.fields
        tree_text = (
            _first_str(
                fields,
                ("process_tree", "detail_process_tree", "event_content", "finding__desc", "str_desc"),
            )
            or ""
        )
        nodes = _process_nodes(tree_text)
        if not nodes:
            process_name = _first_str(fields, ("process__name", "str_process_short", "process__file__name"))
            if process_name:
                nodes = [
                    _drop_none(
                        {
                            "process_name": process_name,
                            "process_path": _first_str(fields, ("process__file__path", "str_process_full", "str_suspicious_file")),
                            "command_line": _first_str(fields, ("process__cmd_line", "str_cmd", "process__ancestor__cmd_line")),
                            "username": _first_str(fields, ("process__user__name", "str_user_process")),
                        }
                    )
                ]
        if not nodes:
            continue
        observations.append(
            _drop_none(
                {
                    "observation_id": f"process:{parsed.message_hash[:16]}",
                    "evidence_path": f"{parsed.source_path}#parsed",
                    "event_time": _first_str(parsed.header, ("timestamp", "event_time")) or _first_str(fields, ("timestamp", "time", "access_time", "first_access_time")),
                    "host_name": _first_str(fields, ("host_name", "device__hostname", "str_source_host")),
                    "nodes": nodes,
                }
            )
        )
    return observations


def _process_nodes(value: str) -> list[dict[str, Any]]:
    return [{"process_name": name, "process_id": int(process_id)} for name, process_id in re.findall(r"([A-Za-z0-9_.-]+)\((\d+)\)", value)]


def _decoded_request_header(parsed_message: ParsedRawMessageEvidence | None) -> dict[str, Any]:
    if parsed_message is None:
        return {}
    payload = parsed_message.decoded_fields.get("payload")
    if not isinstance(payload, Mapping):
        return {}
    request_header = payload.get("req_header")
    return dict(request_header) if isinstance(request_header, Mapping) else {}


def _usable_hids_external_ip(source_type: AlertSourceType, value: str | None) -> str | None:
    if source_type is AlertSourceType.HIDS and value == "1.1.1.1":
        return None
    return value


def _source_field_semantics(
    source_type: AlertSourceType,
    parsed_messages: list[ParsedRawMessageEvidence],
    *,
    fallback_fields: Mapping[str, Any],
    fallback_path: str,
) -> list[dict[str, Any]]:
    if source_type is AlertSourceType.THREAT_INTEL:
        return build_threat_intel_source_field_semantics(parsed_messages)
    if source_type is AlertSourceType.SIEM:
        return build_siem_source_field_semantics(
            fallback_fields,
            evidence_path=fallback_path,
        )
    observations = (
        build_edr_source_field_semantics(
            parsed_messages,
            known_endpoint_addresses=edr_endpoint_addresses(fallback_fields),
        )
        if source_type is AlertSourceType.EDR
        else []
    )
    for parsed in parsed_messages:
        base_path = f"{parsed.source_path}#parsed"
        if source_type is AlertSourceType.HIDS and _first_str(parsed.fields, ("external_ip",)) == "1.1.1.1":
            observations.append(
                {
                    "field_path": f"{base_path}.external_ip",
                    "semantic_type": "source_placeholder",
                    "meaning": "vendor_default_value_not_observed_external_ip",
                    "participates_in_entities": False,
                    "participates_in_reasoning": False,
                }
            )
        if _first_str(parsed.fields, ("host_md5",)):
            observations.append(
                {
                    "field_path": f"{base_path}.host_md5",
                    "semantic_type": "host_identity_digest",
                    "meaning": "host_identity_digest_not_file_hash",
                    "participates_in_entities": False,
                    "participates_in_reasoning": False,
                }
            )
        if source_type is AlertSourceType.NIDS:
            if _nested_str(parsed.fields, ("alert", "action")):
                observations.append(
                    {
                        "field_path": f"{base_path}.alert.action",
                        "semantic_type": "sensor_enforcement_action",
                        "meaning": "allowed_means_sensor_did_not_block_not_that_the_attack_succeeded",
                        "participates_in_entities": False,
                        "participates_in_reasoning": True,
                    }
                )
            if _nested_str(parsed.fields, ("alert", "attack_res")):
                observations.append(
                    {
                        "field_path": f"{base_path}.alert.attack_res",
                        "semantic_type": "vendor_sensor_result_code",
                        "meaning": "uninterpreted_vendor_code_not_detection_truth",
                        "participates_in_entities": False,
                        "participates_in_reasoning": False,
                    }
                )
            if _nested_value(parsed.fields, ("http", "status")) is not None:
                observations.append(
                    {
                        "field_path": f"{base_path}.http.status",
                        "semantic_type": "http_response_status",
                        "meaning": "response_status_is_not_proof_of_exploit_success",
                        "participates_in_entities": True,
                        "participates_in_reasoning": True,
                    }
                )
            if _nested_str(parsed.fields, ("alert", "source", "ip")):
                observations.append(
                    {
                        "field_path": f"{base_path}.alert.source.ip",
                        "semantic_type": "sensor_rule_relative_endpoint",
                        "meaning": "sensor_source_is_not_automatically_network_source_or_attacker",
                        "participates_in_entities": False,
                        "participates_in_reasoning": True,
                    }
                )
            if _nested_str(parsed.fields, ("alert", "target", "ip")):
                observations.append(
                    {
                        "field_path": f"{base_path}.alert.target.ip",
                        "semantic_type": "sensor_rule_relative_endpoint",
                        "meaning": "sensor_target_is_not_automatically_network_destination_or_victim",
                        "participates_in_entities": False,
                        "participates_in_reasoning": True,
                    }
                )
            if _first_str(parsed.fields, ("query",)):
                observations.append(
                    {
                        "field_path": f"{base_path}.query",
                        "semantic_type": "sensor_query_context",
                        "meaning": "query_is_not_dns_without_explicit_protocol_evidence",
                        "participates_in_entities": False,
                        "participates_in_reasoning": True,
                    }
                )
            if isinstance(parsed.fields.get("files"), list) and parsed.fields["files"]:
                observations.append(
                    {
                        "field_path": f"{base_path}.files",
                        "semantic_type": "sensor_transaction_file_metadata",
                        "meaning": "transaction_file_metadata_is_not_proof_of_endpoint_file_write",
                        "participates_in_entities": False,
                        "participates_in_reasoning": True,
                    }
                )
    return observations


def _analysis_context_coverage(original: dict[str, Any], alert: dict[str, Any]) -> dict[str, Any]:
    deferred_sources: list[dict[str, str]] = []
    if original.get("relatedAlertList"):
        deferred_sources.append(
            {
                "field_path": "raw.relatedAlertList",
                "reason": "external_context_deferred_to_investigation",
            }
        )
    if alert.get("soar"):
        deferred_sources.append(
            {
                "field_path": "raw.alert.soar",
                "reason": "external_context_deferred_to_investigation",
            }
        )
    if alert.get("content"):
        deferred_sources.append(
            {
                "field_path": "raw.alert.content",
                "reason": "workflow_context_deferred_to_investigation",
            }
        )
    return {"deferred_sources": deferred_sources}


def _related_disposition_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, list):
        return {}
    actor_counts = {"human": 0, "automation": 0, "unknown": 0}
    reason_counts: dict[str, int] = {}
    disposition_count = 0
    for related in value:
        if not isinstance(related, Mapping):
            continue
        related_alert = _as_dict(related.get("alert")) or dict(related)
        content = related_alert.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, Mapping):
                continue
            disposition_count += 1
            username = str(item.get("userName") or "").strip().lower()
            actor_kind = "automation" if username == "zeusai" else ("human" if username else "unknown")
            actor_counts[actor_kind] += 1
            for reason in _disposition_reasons(item.get("content")):
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return {
        "related_alert_count": len(value),
        "disposition_count": disposition_count,
        "actor_kind_counts": actor_counts,
        "reason_counts": reason_counts,
        "decision_input_allowed": False,
    }


def _disposition_reasons(value: Any) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    reasons: list[str] = []
    content = parsed.get("content") if isinstance(parsed, Mapping) else None
    if not isinstance(content, list):
        return reasons
    for item in content:
        if not isinstance(item, Mapping):
            continue
        field_name = str(item.get("field_cn") or "")
        if "原因" not in field_name:
            continue
        text = re.sub(r"<[^>]+>", "", str(item.get("field_content") or "")).strip()
        if text:
            reasons.append(text)
    return _dedupe(reasons)


def _first_header_value(headers: Mapping[str, Any], name: str) -> str | None:
    value = headers.get(name)
    if isinstance(value, list):
        return next((str(item).strip() for item in value if str(item).strip()), None)
    return str(value).strip() if value is not None and str(value).strip() else None


def _evidence(alert: dict[str, Any], hit_log: dict[str, Any], raw_event: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = [
        {
            "source": "legacy_alert",
            "description": "旧预警平台告警名称",
            "value": _first_str(alert, ("alertName",)),
        },
        {
            "source": "rule",
            "description": "命中的规则",
            "value": _first_str(hit_log, ("ruleName",)) or _first_str(raw_event, ("finding__title", "str_title")),
        },
    ]
    description = _first_str(raw_event, ("finding__desc", "str_desc", "vuln_desc", "detail_info"))
    if description:
        evidence.append({"source": "raw_event", "description": "原始日志描述", "value": description})
    return [item for item in evidence if item.get("value") is not None]


def _source_type(hit_log: dict[str, Any], raw_event: dict[str, Any]) -> AlertSourceType:
    topic = _first_str(hit_log, ("topic",)) or ""
    explicit_type = _PINGAN_TOPIC_SOURCE_TYPES.get(topic.strip().lower())
    if explicit_type is not None:
        return explicit_type

    text = " ".join(
        value.lower()
        for value in [
            topic,
            _first_str(hit_log, ("topicName",)) or "",
            _first_str(raw_event, ("appname", "metadata__product__name", "skyeye_type")) or "",
        ]
    )
    if "threat_intel" in text or "threat intel" in text or "threatbook" in text or "威胁情报" in text:
        return AlertSourceType.THREAT_INTEL
    if "nids" in text:
        return AlertSourceType.NIDS
    if "ndr" in text:
        return AlertSourceType.NDR
    if "edr" in text:
        return AlertSourceType.EDR
    if "hids" in text:
        return AlertSourceType.HIDS
    if "waf" in text:
        return AlertSourceType.WAF
    if "apt" in text or "skyeye" in text or "天眼" in text:
        return AlertSourceType.NDR
    return AlertSourceType.OTHER


def _labels(alert: dict[str, Any], hit_log: dict[str, Any], raw_event: dict[str, Any]) -> dict[str, str]:
    sensor_alert = _as_dict(raw_event.get("alert"))
    labels = {
        "alert_code": _first_str(alert, ("alertCode",)),
        "alert_name": _first_str(alert, ("alertName",)),
        "primary_type": _first_str(alert, ("primaryType",)),
        "secondary_type": _first_str(alert, ("secondaryType",)),
        "tertiary_type": _first_str(alert, ("tertiaryType",)),
        "profile_code": _first_str(alert, ("profileCode",)),
        "profile_name": _first_str(alert, ("profileName",)),
        "topic": _first_str(hit_log, ("topic",)),
        "topic_name": _first_str(hit_log, ("topicName",)),
        "attack_type": _first_str(raw_event, ("attack_type", "finding__type_name")),
        "host_state": _first_str(raw_event, ("host_state",)),
        "sensor_action": _first_str(sensor_alert, ("action",)),
        "sensor_attack_result": _first_str(sensor_alert, ("attack_res",)),
        "sensor_severity": _first_str(sensor_alert, ("severity",)),
        "sensor_signature_id": _first_str(sensor_alert, ("signature_id",)),
    }
    return {key: value for key, value in labels.items() if value is not None}


def _mitre_values(raw_event: dict[str, Any], *, prefix: str) -> list[str]:
    values = [
        _first_str(raw_event, ("str_tactic_id", "finding__attack__tactic_id")) if prefix == "TA" else None,
        _first_str(raw_event, ("str_technique_id", "finding__attack__technique_id")) if prefix == "T" else None,
        *_mitre_ids(_first_str(raw_event, ("att_ck",)) or "", prefix=prefix),
    ]
    return _dedupe([value for value in values if value])


def _mitre_ids(value: str, *, prefix: str) -> list[str]:
    pattern = r"\bTA\d{4}\b" if prefix == "TA" else r"\bT\d{4}(?:\.\d{3})?\b"
    return re.findall(pattern, value)


def _first_soar_asset(value: Any) -> dict[str, Any]:
    if not isinstance(value, list):
        return {}
    for item in value:
        nested_data = _as_dict(_as_dict(item).get("data")).get("data")
        if isinstance(nested_data, list) and nested_data and isinstance(nested_data[0], dict):
            return dict(nested_data[0])
        data = _as_dict(nested_data)
        rows = data.get("rows")
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return dict(rows[0])
    return {}


def _soar_asset_summary(value: dict[str, Any]) -> dict[str, Any]:
    return _drop_none(
        {
            "device_id": _first_str(value, ("uiddevrecordid", "strdevidentiy")),
            "device_name": _first_str(value, ("strdevname", "hostName")),
            "device_ip": _first_str(value, ("strdevip", "hostIp")),
            "username": _first_str(value, ("strusername", "username")),
            "user_id": _first_str(value, ("uiduserid",)),
            "department": _first_str(value, ("strdeptname", "department")),
            "company": _first_str(value, ("company",)),
            "cluster": _first_str(value, ("clusterName",)),
            "os": _first_str(value, ("stros",)),
            "device_type": _first_str(value, ("strdevtype",)),
            "status": _first_str(value, ("status", "idevstatus")),
        }
    )


def _soar_display_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return _dedupe([str(item["displayName"]) for item in value if isinstance(item, dict) and item.get("displayName")])


def _content_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        items.append(
            _drop_none(
                {
                    "process": item.get("process"),
                    "status": item.get("status"),
                    "process_action": item.get("processAction"),
                    "user_name": item.get("userName"),
                    "created_at": item.get("createAt"),
                    "updated_at": item.get("updateAt"),
                }
            )
        )
    return items


def _parse_request_line(req_header: str) -> dict[str, str]:
    if not req_header:
        return {}
    result: dict[str, str] = {}
    first_line = req_header.splitlines()[0] if req_header.splitlines() else ""
    parts = first_line.split()
    if len(parts) >= 2:
        result["method"] = parts[0]
        result["path"] = parts[1]
    for line in req_header.splitlines()[1:]:
        if line.lower().startswith("host:"):
            result["host"] = line.split(":", 1)[1].strip()
            break
    return result


def _detection_key(alert: AlertInput) -> str:
    source = alert.source.source_system or alert.source.product or alert.source.source_type.value or "unknown"
    source_part = source.strip().lower().replace(" ", "_")
    if alert.detection.rule_code:
        return f"{source_part}:rule_code:{alert.detection.rule_code.strip().lower().replace(' ', '_')}"
    if alert.detection.rule_name:
        return f"{source_part}:rule_name:{alert.detection.rule_name.strip().lower().replace(' ', '_')}"
    return f"{source_part}:alert:{alert.alert_id}"


def _first_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return dict(item)
    return {}


def _select_raw_event(
    alert: dict[str, Any],
    *,
    preferred_path: str | None = None,
) -> tuple[int | None, dict[str, Any], int | None, dict[str, Any]]:
    first_candidate: tuple[int | None, dict[str, Any], int | None, dict[str, Any]] | None = None
    for hit_log_index, hit_log, raw_event_index, raw_event in _iter_raw_events(alert):
        candidate = (hit_log_index, hit_log, raw_event_index, raw_event)
        if first_candidate is None:
            first_candidate = candidate
        if preferred_path == f"{_raw_event_path(hit_log_index, raw_event_index)}.message":
            return candidate
        if preferred_path is None and _has_raw_message(raw_event):
            return candidate
    if first_candidate is not None:
        return first_candidate
    hit_log_index, hit_log = _first_hit_log(alert)
    return (hit_log_index, hit_log, None, {})


def _parse_raw_messages(alert: dict[str, Any]) -> list[ParsedRawMessageEvidence]:
    parsed: list[ParsedRawMessageEvidence] = []
    for hit_log_index, _, raw_event_index, raw_event in _iter_raw_events(alert):
        message = raw_event.get(RAW_MESSAGE_FIELD)
        if not isinstance(message, str) or not message.strip():
            continue
        source_path = f"{_raw_event_path(hit_log_index, raw_event_index)}.{RAW_MESSAGE_FIELD}"
        result = parse_pingan_raw_message(message, source_path=source_path)
        if result is not None:
            parsed.append(result)
    return parsed


def _raw_message_paths(alert: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for hit_log_index, _, raw_event_index, raw_event in _iter_raw_events(alert):
        if _has_raw_message(raw_event):
            paths.append(f"{_raw_event_path(hit_log_index, raw_event_index)}.{RAW_MESSAGE_FIELD}")
    return paths


def _merge_parsed_message(
    raw_event: dict[str, Any],
    parsed: ParsedRawMessageEvidence | None,
) -> dict[str, Any]:
    if parsed is None:
        return dict(raw_event)
    # Parsed raw-message fields override duplicate Zeus structured fields.
    # Header values fill gaps but never replace payload fields.
    return {**raw_event, **parsed.header, **parsed.fields}


def _iter_raw_events(alert: dict[str, Any]) -> list[tuple[int, dict[str, Any], int, dict[str, Any]]]:
    hit_logs = alert.get("hitLog")
    if not isinstance(hit_logs, list):
        return []
    events: list[tuple[int, dict[str, Any], int, dict[str, Any]]] = []
    for hit_log_index, hit_log_item in enumerate(hit_logs):
        if not isinstance(hit_log_item, dict):
            continue
        hit_log = dict(hit_log_item)
        raw_logs = hit_log.get("zeusRawLogs")
        if not isinstance(raw_logs, list):
            continue
        for raw_event_index, raw_event_item in enumerate(raw_logs):
            if isinstance(raw_event_item, dict):
                events.append((hit_log_index, hit_log, raw_event_index, dict(raw_event_item)))
    return events


def _first_hit_log(alert: dict[str, Any]) -> tuple[int | None, dict[str, Any]]:
    hit_logs = alert.get("hitLog")
    if not isinstance(hit_logs, list):
        return (None, {})
    for hit_log_index, hit_log_item in enumerate(hit_logs):
        if isinstance(hit_log_item, dict):
            return (hit_log_index, dict(hit_log_item))
    return (None, {})


def _raw_event_path(hit_log_index: int | None, raw_event_index: int | None) -> str:
    if hit_log_index is None or raw_event_index is None:
        return "alert.hitLog[].zeusRawLogs[]"
    return f"alert.hitLog[{hit_log_index}].zeusRawLogs[{raw_event_index}]"


def _process_chain(value: str) -> list[str]:
    if not value:
        return []
    names = re.findall(r"([A-Za-z0-9_.-]+)\(\d+\)", value)
    return _dedupe(names)


def _first_str(source: dict[str, Any], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        value = source.get(alias)
        if value is not None and value != "":
            return str(value)
    return None


def _has_raw_message(source: dict[str, Any]) -> bool:
    value = source.get(RAW_MESSAGE_FIELD)
    return value is not None and value != ""


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _nested_value(
    source: Mapping[str, Any],
    path: tuple[str, ...],
) -> Any:
    current: Any = source
    for segment in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(segment)
    return current


def _nested_str(
    source: Mapping[str, Any],
    path: tuple[str, ...],
) -> str | None:
    value = _nested_value(source, path)
    if value is None or value == "":
        return None
    return str(value)


def _nested_int(
    source: Mapping[str, Any],
    path: tuple[str, ...],
) -> int | None:
    value = _nested_value(source, path)
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _basename(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("/", "\\").rsplit("\\", 1)[-1]


def _boolish(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    return None


def _intish(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value
