"""Normalize external alert payloads into canonical SOC contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from soc_agent.contracts import AlertInput, AlertSourceType
from soc_agent.normalizers.pingan_platform import is_pingan_platform_payload, normalize_pingan_platform_payload


def normalize_alert_payload(payload: Mapping[str, Any]) -> AlertInput:
    """Convert a loose external alert payload into canonical ``AlertInput``.

    This function is intentionally permissive. Vendor adapters can be added
    later, but the core contract stays canonical and strict.
    """

    if is_pingan_platform_payload(payload):
        return normalize_pingan_platform_payload(payload)

    raw_payload = dict(payload)
    normalized: dict[str, Any] = {
        "schema_version": str(raw_payload.get("schema_version") or "soc.alert.v1"),
        "tenant_id": raw_payload.get("tenant_id") or raw_payload.get("tenantId"),
        "alert_id": _first_str(raw_payload, ("alert_id", "alertId", "id", "event_id", "eventId")),
        "source": _merge_source(raw_payload),
        "detection": _merge_detection(raw_payload),
        "event": _merge_event(raw_payload),
        "classification": _merge_classification(raw_payload),
        "entities": _merge_entities(raw_payload),
        "evidence": raw_payload.get("evidence") or [],
        "extensions": _as_dict(raw_payload.get("extensions")),
        "raw": raw_payload,
    }

    alert = AlertInput.model_validate({key: value for key, value in normalized.items() if value is not None})
    alert.detection.detection_key = alert.detection.detection_key or build_detection_key(alert)
    return alert


def build_detection_key(alert: AlertInput) -> str:
    source = alert.source.source_system or alert.source.product or alert.source.vendor or alert.source.source_type.value or "unknown"
    source_part = _slug(source)
    if alert.detection.rule_code:
        return f"{source_part}:rule_code:{_slug(alert.detection.rule_code)}"
    if alert.detection.rule_name:
        return f"{source_part}:rule_name:{_slug(alert.detection.rule_name)}"
    if alert.detection.rule_category:
        return f"{source_part}:category:{_slug(alert.detection.rule_category)}"
    return f"{source_part}:fingerprint:{_short_hash(alert.raw)}"


def _merge_source(data: dict[str, Any]) -> dict[str, Any]:
    source = _as_dict(data.get("source"))
    for source_key, aliases in {
        "source_type": ("source_type", "sourceType", "data_source", "dataSource"),
        "source_system": ("source_system", "sourceSystem", "system", "system_name", "systemName"),
        "vendor": ("vendor",),
        "product": ("product",),
        "integration_name": ("integration_name", "integrationName"),
    }.items():
        _copy_first(source, source_key, data, aliases)

    source_type = source.get("source_type")
    if source_type and str(source_type).lower() not in {item.value for item in AlertSourceType}:
        source.setdefault("source_system", str(source_type))
        source["source_type"] = AlertSourceType.OTHER.value
    return source


def _merge_detection(data: dict[str, Any]) -> dict[str, Any]:
    detection = _as_dict(data.get("detection"))
    for detection_key, aliases in {
        "rule_code": (
            "rule_code",
            "ruleCode",
            "rule_id",
            "ruleId",
            "signature_id",
            "signatureId",
            "detector_id",
            "detectorId",
            "correlation_rule_id",
            "correlationRuleId",
        ),
        "rule_name": ("rule_name", "ruleName", "signature_name", "signatureName", "detection_name", "detectionName"),
        "rule_version": ("rule_version", "ruleVersion"),
        "rule_category": ("rule_category", "ruleCategory", "category", "alert_type", "alertType"),
        "detection_key": ("detection_key", "detectionKey"),
    }.items():
        _copy_first(detection, detection_key, data, aliases)

    for key in ("rule_code", "rule_name", "rule_version", "rule_category", "detection_key"):
        if detection.get(key) is not None:
            detection[key] = str(detection[key])
    return detection


def _merge_event(data: dict[str, Any]) -> dict[str, Any]:
    event = _as_dict(data.get("event"))
    for event_key, aliases in {
        "event_id": ("event_id", "eventId"),
        "event_time": ("event_time", "eventTime", "timestamp", "time", "@timestamp"),
        "received_at": ("received_at", "receivedAt"),
    }.items():
        _copy_first(event, event_key, data, aliases)
    if event.get("event_id") is not None:
        event["event_id"] = str(event["event_id"])
    return event


def _merge_classification(data: dict[str, Any]) -> dict[str, Any]:
    classification = _as_dict(data.get("classification"))
    for classification_key, aliases in {
        "severity": ("severity",),
        "category": ("category", "alert_type", "alertType"),
    }.items():
        _copy_first(classification, classification_key, data, aliases)
    return classification


def _merge_entities(data: dict[str, Any]) -> dict[str, Any]:
    entities = _as_dict(data.get("entities"))
    network = _as_dict(entities.get("network"))
    process = _as_dict(entities.get("process"))
    user = _as_dict(entities.get("user"))
    host = _as_dict(entities.get("host"))
    file_entity = _as_dict(entities.get("file"))
    http = _as_dict(entities.get("http"))
    threat = _as_dict(entities.get("threat"))

    for key, aliases in {
        "source_ip": ("source_ip", "sourceIp", "src_ip", "srcIp", "client_ip", "clientIp"),
        "destination_ip": ("destination_ip", "destinationIp", "dst_ip", "dstIp", "server_ip", "serverIp"),
        "src_port": ("src_port", "srcPort", "source_port", "sourcePort"),
        "dst_port": ("dst_port", "dstPort", "destination_port", "destinationPort"),
        "protocol": ("protocol",),
        "domain": ("domain", "dns_query", "dnsQuery"),
        "url": ("url", "request_url", "requestUrl"),
    }.items():
        _copy_first(network, key, data, aliases)

    for key, aliases in {
        "process_name": ("process_name", "processName", "process", "image_name", "imageName"),
        "process_path": ("process_path", "processPath", "image_path", "imagePath"),
        "command_line": ("command_line", "commandLine", "cmdline"),
        "parent_process_name": ("parent_process_name", "parentProcessName", "parent_process", "parentProcess"),
        "parent_command_line": ("parent_command_line", "parentCommandLine"),
    }.items():
        _copy_first(process, key, data, aliases)

    for key, aliases in {
        "username": ("username", "user", "user_name", "userName"),
        "user_id": ("user_id", "userId"),
        "um_account": ("um_account", "umAccount", "um", "um_id", "umId"),
        "src_user": ("src_user", "srcUser"),
        "dst_user": ("dst_user", "dstUser"),
    }.items():
        _copy_first(user, key, data, aliases)

    for key, aliases in {
        "host_name": ("host_name", "hostName", "hostname", "host"),
        "host_id": ("host_id", "hostId"),
        "asset_id": ("asset_id", "assetId"),
        "asset_group": ("asset_group", "assetGroup"),
    }.items():
        _copy_first(host, key, data, aliases)

    for key, aliases in {
        "file_name": ("file_name", "fileName"),
        "file_path": ("file_path", "filePath"),
        "sha256": ("sha256",),
        "sha1": ("sha1",),
        "md5": ("md5",),
    }.items():
        _copy_first(file_entity, key, data, aliases)

    for key, aliases in {
        "method": ("http_method", "httpMethod", "method"),
        "host": ("http_host", "httpHost", "host_header", "hostHeader"),
        "path": ("uri_path", "uriPath", "path"),
        "url": ("url", "request_url", "requestUrl"),
        "status_code": ("status_code", "statusCode", "http_status", "httpStatus"),
        "user_agent": ("user_agent", "userAgent"),
        "x_forwarded_for": ("x_forwarded_for", "xForwardedFor", "x-forwarded-for", "X-Forwarded-For", "xff", "XFF"),
    }.items():
        _copy_first(http, key, data, aliases)

    for key, aliases in {
        "campaign": ("campaign",),
        "threat_actor": ("threat_actor", "threatActor"),
        "malware_family": ("malware_family", "malwareFamily"),
    }.items():
        _copy_first(threat, key, data, aliases)

    entities["network"] = network
    entities["process"] = process
    entities["user"] = user
    entities["host"] = host
    entities["file"] = file_entity
    entities["http"] = http
    entities["threat"] = threat
    return entities


def _copy_first(target: dict[str, Any], target_key: str, source: dict[str, Any], aliases: tuple[str, ...]) -> None:
    if target.get(target_key) is not None:
        return
    for alias in aliases:
        if target.get(alias) is not None:
            target[target_key] = target[alias]
            return
    for alias in aliases:
        if source.get(alias) is not None:
            target[target_key] = source[alias]
            return


def _first_str(source: dict[str, Any], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        if source.get(alias) is not None:
            return str(source[alias])
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _slug(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def _short_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
