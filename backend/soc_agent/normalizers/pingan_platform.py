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
)

RAW_MESSAGE_FIELD = "message"


def is_pingan_platform_payload(payload: Mapping[str, Any]) -> bool:
    alert = payload.get("alert")
    return isinstance(alert, dict) and isinstance(alert.get("hitLog"), list)


def normalize_pingan_platform_payload(payload: Mapping[str, Any]) -> AlertInput:
    original = dict(payload)
    alert = _as_dict(original.get("alert"))
    hit_log_index, hit_log, raw_event_index, raw_event = _select_raw_event(alert)
    origin = _json_object(raw_event.get("_origin"))
    http_payload = _json_object(raw_event.get("payload"))
    soar_asset = _first_soar_asset(alert.get("soar"))

    source_type = _source_type(hit_log, raw_event)
    source_system = _first_str(hit_log, ("topic", "topicName")) or _first_str(raw_event, ("appname", "source"))
    product = _first_str(hit_log, ("topicName",)) or _first_str(raw_event, ("metadata__product__name",))

    canonical = {
        "schema_version": "soc.alert.v1",
        "alert_id": _first_str(alert, ("alertId", "alertCode")) or _first_str(raw_event, ("alarm_id", "finding__uid")),
        "source": {
            "source_type": source_type.value,
            "source_system": source_system,
            "product": product,
            "integration_name": "pingan_legacy_alert_platform",
        },
        "detection": {
            "rule_code": _first_str(hit_log, ("ruleCode",)) or _first_str(raw_event, ("str_rule_id", "rule_id")),
            "rule_name": _first_str(hit_log, ("ruleName",)) or _first_str(raw_event, ("finding__title", "str_title")),
            "rule_category": _first_str(alert, ("tertiaryType", "secondaryType")) or _first_str(raw_event, ("finding__type_name", "attack_type", "vuln_type")),
        },
        "event": {
            "event_id": _first_str(raw_event, ("alarm_id", "finding__uid", "str_unique_id", "logcloud_msgid")),
            "event_time": _first_str(raw_event, ("t_detect_time", "timestamp", "time", "access_time", "first_access_time")) or _first_str(alert, ("createAt",)),
            "received_at": _first_str(alert, ("createAt",)) or _first_str(raw_event, ("timestamp", "time")),
        },
        "classification": {
            "severity": _first_str(raw_event, ("severity", "risk_level", "hazard_rating", "threat_level")) or _first_str(alert, ("riskLevel",)),
            "category": _first_str(alert, ("tertiaryType", "secondaryType", "primaryType")) or _first_str(raw_event, ("finding__type_name", "attack_type", "vuln_type")),
            "tactic": _mitre_values(raw_event, prefix="TA"),
            "technique": _mitre_values(raw_event, prefix="T"),
            "labels": _labels(alert, hit_log, raw_event),
        },
        "entities": _entities(source_type, raw_event, origin, http_payload, soar_asset),
        "evidence": _evidence(alert, hit_log, raw_event),
        "extensions": {
            "legacy_platform": _legacy_platform_context(original, alert, hit_log, raw_event, soar_asset),
            "evidence_input_policy": _evidence_input_policy(hit_log_index, raw_event_index, raw_event),
        },
        "raw": original,
    }

    normalized = AlertInput.model_validate(_drop_none(canonical))
    normalized.detection.detection_key = normalized.detection.detection_key or _detection_key(normalized)
    return normalized


def _evidence_input_policy(
    hit_log_index: int | None,
    raw_event_index: int | None,
    raw_event: dict[str, Any],
) -> dict[str, Any]:
    raw_event_path = _raw_event_path(hit_log_index, raw_event_index)
    if _has_raw_message(raw_event):
        message_path = f"{raw_event_path}.{RAW_MESSAGE_FIELD}"
        policy = EvidenceInputPolicy(
            name=EvidenceInputPolicyName.RAW_MESSAGE_FIRST,
            primary_input_path=message_path,
            fallback_input_path=raw_event_path,
            selected_input_path=message_path,
            selected_layer=EvidenceLayer.RAW_MESSAGE,
            ignore_processed_fields_for_reasoning=True,
            trust_level=EvidenceTrustLevel.HIGH,
        )
    else:
        policy = EvidenceInputPolicy(
            name=EvidenceInputPolicyName.STRUCTURED_FALLBACK,
            primary_input_path=raw_event_path,
            selected_input_path=raw_event_path,
            selected_layer=EvidenceLayer.RAW_STRUCTURED,
            fallback_reason="raw_message_missing",
            ignore_processed_fields_for_reasoning=False,
            trust_level=EvidenceTrustLevel.LOW,
        )
    return policy.model_dump(mode="json", exclude_none=True)


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
                "soar_asset_department": _first_str(soar_asset, ("strdeptname",)),
                "soar_asset_owner": _first_str(soar_asset, ("strusername",)),
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
    soar_asset: dict[str, Any],
) -> dict[str, Any]:
    req = _parse_request_line(_first_str(http_payload, ("req_header",)) or "")

    if source_type is AlertSourceType.EDR:
        network = {
            "source_ip": _first_str(raw_event, ("str_source_ip", "device__ip")),
            "destination_ip": _first_str(raw_event, ("str_attack_ip", "str_threat_value", "str_activity_id")),
            "protocol": _first_str(raw_event, ("proto", "protocol")),
        }
    else:
        network = {
            "source_ip": _first_str(raw_event, ("sip", "attack_sip", "src_addr", "source_ip")) or _first_str(origin, ("sip",)),
            "destination_ip": _first_str(raw_event, ("dip", "dst_addr", "alarm_sip")) or _first_str(origin, ("dip",)),
            "src_port": _first_str(raw_event, ("sport",)) or _first_str(origin, ("sport",)),
            "dst_port": _first_str(raw_event, ("dport",)) or _first_str(origin, ("dport",)),
            "protocol": _first_str(raw_event, ("proto", "labels_proto", "protocol")),
            "domain": _first_str(raw_event, ("host",)),
            "url": _first_str(origin, ("uri",)) or req.get("path"),
        }

    return {
        "network": network,
        "process": {
            "process_name": _first_str(raw_event, ("process__name", "str_process_short", "process__file__name", "str_suspicious_process_ancestor_short")),
            "process_path": _first_str(raw_event, ("process__file__path", "str_process_full", "str_suspicious_file")),
            "command_line": _first_str(raw_event, ("process__cmd_line", "str_cmd", "str_suspicious_process_ancestor_cmd", "process__ancestor__cmd_line")),
            "parent_process_name": _basename(_first_str(raw_event, ("process__parent_process__file__path", "str_parent_path_full"))),
            "parent_command_line": _first_str(raw_event, ("process__parent_process__cmd_line", "str_parent_cmd")),
        },
        "user": {
            "username": _first_str(raw_event, ("str_user_agent", "process__user__name", "str_user_process")) or _first_str(soar_asset, ("strusername",)),
            "user_id": _first_str(soar_asset, ("uiduserid",)),
            "um_account": _first_str(raw_event, ("um", "um_account", "umAccount", "str_um_account")),
        },
        "host": {
            "host_name": _first_str(raw_event, ("device__hostname", "str_source_host")) or _first_str(soar_asset, ("strdevname",)),
            "host_id": _first_str(raw_event, ("str_agent_id", "metadata__product__version")) or _first_str(soar_asset, ("uiddevrecordid",)),
            "asset_id": _first_str(raw_event, ("device__ip", "str_source_ip")) or _first_str(soar_asset, ("strdevip",)),
            "asset_group": _first_str(raw_event, ("device__org__ou_name", "str_dept_name", "dip_group", "asset_group")) or _first_str(soar_asset, ("strdeptname",)),
        },
        "file": {
            "file_name": _first_str(raw_event, ("process__file__name", "str_process_short")),
            "file_path": _first_str(raw_event, ("process__file__path", "str_process_full", "str_suspicious_file")),
            "md5": _first_str(raw_event, ("process__file__hashes__md5", "str_md5", "str_suspicious_file_md5", "host_md5")),
        },
        "http": {
            "method": req.get("method"),
            "host": _first_str(raw_event, ("host",)) or req.get("host"),
            "path": req.get("path") or _first_str(origin, ("uri",)),
            "url": _first_str(origin, ("uri",)) or req.get("path"),
            "status_code": _first_str(raw_event, ("rsp_status",)) or _first_str(origin, ("rsp_status",)),
            "x_forwarded_for": _first_str(raw_event, ("x_forwarded_for",)) or _first_str(origin, ("xff",)),
        },
        "threat": {
            "iocs": _dedupe(
                [
                    value
                    for value in [
                        _first_str(raw_event, ("ioc",)),
                        _first_str(raw_event, ("str_threat_value", "str_attack_ip")),
                        _first_str(raw_event, ("attack_sip", "sip")),
                    ]
                    if value
                ]
            )
        },
    }


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
    text = " ".join(
        value.lower()
        for value in [
            _first_str(hit_log, ("topic", "topicName")) or "",
            _first_str(raw_event, ("appname", "metadata__product__name", "skyeye_type")) or "",
        ]
    )
    if "edr" in text:
        return AlertSourceType.EDR
    if "waf" in text:
        return AlertSourceType.WAF
    if "apt" in text or "skyeye" in text or "天眼" in text:
        return AlertSourceType.NDR
    return AlertSourceType.OTHER


def _labels(alert: dict[str, Any], hit_log: dict[str, Any], raw_event: dict[str, Any]) -> dict[str, str]:
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
        data = _as_dict(_as_dict(_as_dict(item).get("data")).get("data"))
        rows = data.get("rows")
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return dict(rows[0])
    return {}


def _soar_asset_summary(value: dict[str, Any]) -> dict[str, Any]:
    return _drop_none(
        {
            "device_id": _first_str(value, ("uiddevrecordid", "strdevidentiy")),
            "device_name": _first_str(value, ("strdevname",)),
            "device_ip": _first_str(value, ("strdevip",)),
            "username": _first_str(value, ("strusername",)),
            "user_id": _first_str(value, ("uiduserid",)),
            "department": _first_str(value, ("strdeptname",)),
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


def _select_raw_event(alert: dict[str, Any]) -> tuple[int | None, dict[str, Any], int | None, dict[str, Any]]:
    first_candidate: tuple[int | None, dict[str, Any], int | None, dict[str, Any]] | None = None
    for hit_log_index, hit_log, raw_event_index, raw_event in _iter_raw_events(alert):
        candidate = (hit_log_index, hit_log, raw_event_index, raw_event)
        if first_candidate is None:
            first_candidate = candidate
        if _has_raw_message(raw_event):
            return candidate
    if first_candidate is not None:
        return first_candidate
    hit_log_index, hit_log = _first_hit_log(alert)
    return (hit_log_index, hit_log, None, {})


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
