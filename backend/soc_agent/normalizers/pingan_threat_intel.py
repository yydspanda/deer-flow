"""PingAn ThreatBook projections with separate session and security-role semantics."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from soc_agent.contracts import (
    AlertInput,
    AlertSourceType,
    EvidenceLayer,
    EvidenceTrustLevel,
    ParsedRawMessageEvidence,
    ScenarioSignal,
)

_MITRE_TECHNIQUE_RE = re.compile(r"\bT\d{4}(?:\.\d{3})?\b", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"^(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$")
_FILE_HASH_RE = re.compile(r"^(?:[0-9A-Fa-f]{32}|[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$")


def build_threat_intel_entities(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
    *,
    fallback_fields: Mapping[str, Any],
) -> dict[str, Any]:
    primary_fields = parsed_messages[0].fields if parsed_messages else fallback_fields
    sessions = [observation for parsed in parsed_messages if (observation := _network_observation(parsed)) is not None]
    primary_session = threat_intel_session(primary_fields)
    primary_http = _http_projection(primary_fields)
    host_addresses = _host_addresses(primary_fields)
    indicators = threat_intel_indicators(primary_fields)
    threat = _as_dict(primary_fields.get("threat"))
    assets = _as_dict(primary_fields.get("assets"))

    return {
        "network": {
            **primary_session,
            "observations": sessions,
        },
        "process": {"observations": []},
        "user": {},
        "host": {
            "asset_group": _first_str(assets, ("group_name", "section")),
            "ip_addresses": host_addresses,
        },
        "file": {"observations": []},
        "http": primary_http,
        "threat": {
            "iocs": indicators,
            "malware_family": _first_str(threat, ("name",)),
        },
    }


def threat_intel_session(fields: Mapping[str, Any]) -> dict[str, Any]:
    net = _as_dict(fields.get("net"))
    dns = _as_dict(net.get("dns"))
    http = _as_dict(net.get("http"))
    flow = _as_dict(net.get("flow"))
    return _drop_none(
        {
            "source_ip": _valid_ip(_first_str(net, ("src_ip", "real_src_ip"))),
            "destination_ip": _valid_ip(_first_str(net, ("dest_ip",))),
            "src_port": _port(net.get("src_port")),
            "dst_port": _port(net.get("dest_port")),
            "protocol": _first_str(net, ("proto",)),
            "application_protocol": _first_str(flow, ("app_proto",)) or _first_str(net, ("type",)),
            "direction": _first_str(fields, ("direction",)),
            "domain": _first_str(dns, ("rrname",)) or _first_str(http, ("reqs_host",)),
            "url": _first_str(http, ("url",)),
        }
    )


def threat_intel_indicators(fields: Mapping[str, Any]) -> list[str]:
    return _dedupe([value for _, value in _indicator_candidates_with_paths(fields)])


def threat_intel_mitre_values(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> list[str]:
    values: list[str] = []
    for parsed in parsed_messages:
        threat = _as_dict(parsed.fields.get("threat"))
        tags = threat.get("tag")
        candidates = tags if isinstance(tags, list) else [tags]
        for candidate in candidates:
            values.extend(match.upper() for match in _MITRE_TECHNIQUE_RE.findall(str(candidate or "")))
    return _dedupe(values)


def threat_intel_category(fields: Mapping[str, Any]) -> str | None:
    threat = _as_dict(fields.get("threat"))
    return _first_str(threat, ("type", "phase"))


def threat_intel_severity(fields: Mapping[str, Any]) -> str | None:
    threat = _as_dict(fields.get("threat"))
    return _first_str(threat, ("severity", "level"))


def build_threat_intel_scenario_signals(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> list[ScenarioSignal]:
    signals: list[ScenarioSignal] = []
    seen: set[tuple[str, str]] = set()
    for parsed in parsed_messages:
        threat = _as_dict(parsed.fields.get("threat"))
        base_path = f"{parsed.source_path}#parsed.threat"
        candidates: list[tuple[str, Any]] = [
            ("name", threat.get("name")),
            ("msg", threat.get("msg")),
            ("type", threat.get("type")),
            ("phase", threat.get("phase")),
        ]
        tags = threat.get("tag")
        if isinstance(tags, list):
            candidates.extend((f"tag[{index}]", value) for index, value in enumerate(tags))
        for relative_path, value in candidates:
            text = str(value or "").strip()
            evidence_path = f"{base_path}.{relative_path}"
            key = (text, evidence_path)
            if not text or key in seen:
                continue
            seen.add(key)
            signals.append(
                ScenarioSignal(
                    text=text,
                    evidence_path=evidence_path,
                    source_layer=EvidenceLayer.RAW_MESSAGE,
                    evidence_trust=EvidenceTrustLevel.HIGH,
                )
            )
    return signals


def build_threat_intel_source_field_semantics(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for parsed in parsed_messages:
        base_path = f"{parsed.source_path}#parsed"
        fields = parsed.fields
        if _as_dict(fields.get("net")):
            observations.append(
                _semantic(
                    f"{base_path}.net",
                    "observed_network_session",
                    "net fields describe wire/session endpoints and remain separate from attacker/victim security roles",
                    entities=True,
                )
            )
        for name in ("attacker", "victim"):
            if _first_str(fields, (name,)):
                observations.append(
                    _semantic(
                        f"{base_path}.{name}",
                        "vendor_security_role_assertion",
                        "attacker/victim is a product security-role assertion and does not redefine wire direction",
                        entities=True,
                    )
                )
        if _first_str(fields, ("external_ip",)):
            observations.append(
                _semantic(
                    f"{base_path}.external_ip",
                    "external_peer_indicator",
                    "external_ip identifies the external peer and may be an IOC candidate when shape-valid",
                    entities=True,
                )
            )
        assets = _as_dict(fields.get("assets"))
        if _first_str(assets, ("ip",)):
            observations.append(
                _semantic(
                    f"{base_path}.assets.ip",
                    "asset_scope_expression",
                    "assets.ip may be a CIDR or range describing asset scope and must not become a host IP",
                )
            )
        if "is_black_ip" in fields:
            observations.append(
                _semantic(
                    f"{base_path}.is_black_ip",
                    "upstream_reputation_assertion",
                    "false means the provider did not label the peer as black; it does not negate behavioral detection evidence",
                    reasoning=True,
                )
            )
        threat = _as_dict(fields.get("threat"))
        for field_name, semantic_type, meaning in (
            (
                "result",
                "provider_detection_result",
                "provider result describes its detection outcome and is not proof that an attack or exploit succeeded",
            ),
            (
                "level",
                "provider_threat_level",
                "provider threat level is source taxonomy and not Runtime confidence",
            ),
            (
                "severity",
                "provider_severity_score",
                "provider severity is source scoring and not calibrated Runtime confidence",
            ),
            (
                "id",
                "provider_threat_identifier",
                "provider threat identifier is correlation metadata, not an IOC by string shape",
            ),
            (
                "tag",
                "provider_classification_tags",
                "provider tags may contribute typed classifications such as MITRE techniques",
            ),
        ):
            if threat.get(field_name) not in (None, "", []):
                observations.append(
                    _semantic(
                        f"{base_path}.threat.{field_name}",
                        semantic_type,
                        meaning,
                        reasoning=True,
                    )
                )
    return observations


def threat_intel_field_importance_rules() -> list[dict[str, Any]]:
    definitions = (
        ("pingan.threat_intel.source_ip", ["parsed.net.src_ip"], "entities.network.source_ip", "critical"),
        ("pingan.threat_intel.destination_ip", ["parsed.net.dest_ip"], "entities.network.destination_ip", "critical"),
        ("pingan.threat_intel.source_port", ["parsed.net.src_port"], "entities.network.src_port", "high"),
        ("pingan.threat_intel.destination_port", ["parsed.net.dest_port"], "entities.network.dst_port", "high"),
        ("pingan.threat_intel.protocol", ["parsed.net.proto"], "entities.network.protocol", "high"),
        (
            "pingan.threat_intel.application_protocol",
            ["parsed.net.flow.app_proto", "parsed.net.type"],
            "entities.network.application_protocol",
            "high",
        ),
        ("pingan.threat_intel.direction", ["parsed.direction"], "entities.network.direction", "high"),
        ("pingan.threat_intel.host_ip", ["parsed.machine", "parsed.victim"], "entities.host.ip_addresses", "critical"),
        (
            "pingan.threat_intel.asset_group",
            ["parsed.assets.group_name", "parsed.assets.section"],
            "entities.host.asset_group",
            "high",
        ),
        ("pingan.threat_intel.external_ioc", ["parsed.external_ip", "parsed.attacker"], "entities.threat.iocs", "critical"),
        ("pingan.threat_intel.malware", ["parsed.threat.name"], "entities.threat.malware_family", "high"),
        ("pingan.threat_intel.mitre", ["parsed.threat.tag*"], "classification.technique", "high"),
        (
            "pingan.threat_intel.severity",
            ["parsed.threat.severity", "parsed.threat.level"],
            "classification.severity",
            "high",
        ),
        (
            "pingan.threat_intel.category",
            ["parsed.threat.type", "parsed.threat.phase"],
            "classification.category",
            "high",
        ),
    )
    return [
        {
            "rule_id": rule_id,
            "source_patterns": patterns,
            "expected_target": target,
            "importance": importance,
            "source_types": [AlertSourceType.THREAT_INTEL.value],
            "reason": f"PingAn threat-intelligence evidence should populate {target}",
        }
        for rule_id, patterns, target, importance in definitions
    ]


def build_threat_intel_canonical_field_provenance(
    alert: AlertInput,
    *,
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> list[dict[str, Any]]:
    if not parsed_messages:
        return []
    provenance: list[dict[str, Any]] = []
    primary = parsed_messages[0]
    primary_fields = primary.fields
    primary_sources = {
        "entities.network.source_ip": "net.src_ip",
        "entities.network.destination_ip": "net.dest_ip",
        "entities.network.src_port": "net.src_port",
        "entities.network.dst_port": "net.dest_port",
        "entities.network.protocol": "net.proto",
        "entities.network.application_protocol": _first_present_path(
            primary_fields,
            ("net.flow.app_proto", "net.type"),
        ),
        "entities.network.direction": "direction",
        "entities.network.domain": _first_present_path(
            primary_fields,
            ("net.dns.rrname", "net.http.reqs_host"),
        ),
        "entities.network.url": "net.http.url",
        "entities.host.asset_group": _first_present_path(
            primary_fields,
            ("assets.group_name", "assets.section"),
        ),
        "entities.threat.malware_family": "threat.name",
        "classification.severity": _first_present_path(
            primary_fields,
            ("threat.severity", "threat.level"),
        ),
        "classification.category": _first_present_path(
            primary_fields,
            ("threat.type", "threat.phase"),
        ),
        "entities.http.method": "net.http.method",
        "entities.http.host": "net.http.reqs_host",
        "entities.http.url": "net.http.url",
        "entities.http.protocol": "net.http.protocol",
        "entities.http.status_code": "net.http.status",
        "entities.http.user_agent": "net.http.reqs_user_agent",
    }
    for canonical_path, relative_path in primary_sources.items():
        _append_provenance(
            provenance,
            canonical_path=canonical_path,
            selected_value=_resolve_alert_path(alert, canonical_path),
            parsed=primary,
            relative_path=relative_path,
        )

    for index, value in enumerate(alert.entities.host.ip_addresses):
        source = _find_value_source(parsed_messages, value, ("machine", "victim"))
        if source:
            parsed, relative_path = source
            _append_provenance(
                provenance,
                canonical_path=f"entities.host.ip_addresses[{index}]",
                selected_value=value,
                parsed=parsed,
                relative_path=relative_path,
            )
    for index, value in enumerate(alert.entities.threat.iocs):
        source = _find_indicator_source(parsed_messages, value)
        if source:
            parsed, relative_path = source
            _append_provenance(
                provenance,
                canonical_path=f"entities.threat.iocs[{index}]",
                selected_value=value,
                parsed=parsed,
                relative_path=relative_path,
            )
    for index, value in enumerate(alert.classification.technique):
        source = _find_mitre_source(parsed_messages, value)
        if source:
            parsed, relative_path = source
            _append_provenance(
                provenance,
                canonical_path=f"classification.technique[{index}]",
                selected_value=value,
                parsed=parsed,
                relative_path=relative_path,
            )

    for observation_index, observation in enumerate(alert.entities.network.observations):
        parsed = next(
            (item for item in parsed_messages if observation.evidence_path == f"{item.source_path}#parsed.net"),
            None,
        )
        if parsed is None:
            continue
        for field_name, relative_path in (
            ("source_ip", "net.src_ip"),
            ("destination_ip", "net.dest_ip"),
            ("src_port", "net.src_port"),
            ("dst_port", "net.dest_port"),
            ("protocol", "net.proto"),
            (
                "application_protocol",
                _first_present_path(
                    parsed.fields,
                    ("net.flow.app_proto", "net.type"),
                ),
            ),
            ("direction", "direction"),
        ):
            _append_provenance(
                provenance,
                canonical_path=f"entities.network.observations[{observation_index}].{field_name}",
                selected_value=getattr(observation, field_name),
                parsed=parsed,
                relative_path=relative_path,
            )
    return provenance


def _network_observation(
    parsed: ParsedRawMessageEvidence,
) -> dict[str, Any] | None:
    session = threat_intel_session(parsed.fields)
    if not session.get("source_ip") and not session.get("destination_ip"):
        return None
    return {
        "observation_id": f"network:{parsed.message_hash[:16]}",
        "evidence_path": f"{parsed.source_path}#parsed.net",
        "event_time": _first_str(parsed.fields, ("timeStr",)) or _first_str(parsed.header, ("timestamp", "event_time")),
        **session,
    }


def _http_projection(fields: Mapping[str, Any]) -> dict[str, Any]:
    net = _as_dict(fields.get("net"))
    http = _as_dict(net.get("http"))
    status = _intish(http.get("status"))
    if status == 0:
        status = None
    return _drop_none(
        {
            "method": _first_str(http, ("method",)),
            "host": _first_str(http, ("reqs_host",)),
            "url": _first_str(http, ("url",)),
            "protocol": _first_str(http, ("protocol",)),
            "status_code": status,
            "user_agent": _first_str(http, ("reqs_user_agent",)),
            "observations": [],
        }
    )


def _host_addresses(fields: Mapping[str, Any]) -> list[str]:
    return _dedupe(
        [
            value
            for candidate in (
                _first_str(fields, ("machine",)),
                _first_str(fields, ("victim",)),
            )
            if (value := _valid_ip(candidate)) is not None
        ]
    )


def _find_value_source(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
    value: str,
    paths: Sequence[str],
) -> tuple[ParsedRawMessageEvidence, str] | None:
    for parsed in parsed_messages:
        for path in paths:
            if str(_resolve_mapping_path(parsed.fields, path) or "").strip() == value:
                return parsed, path
    return None


def _find_indicator_source(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
    value: str,
) -> tuple[ParsedRawMessageEvidence, str] | None:
    for parsed in parsed_messages:
        for relative_path, candidate in _indicator_candidates_with_paths(parsed.fields):
            if candidate == value:
                return parsed, relative_path
    return None


def _indicator_candidates_with_paths(
    fields: Mapping[str, Any],
) -> list[tuple[str, str]]:
    threat = _as_dict(fields.get("threat"))
    result: list[tuple[str, str]] = []
    explicit = _valid_ioc(_first_str(threat, ("ioc",)))
    if explicit:
        result.append(("threat.ioc", explicit))
    for path, candidate in (
        ("external_ip", _first_str(fields, ("external_ip",))),
        ("attacker", _first_str(fields, ("attacker",))),
    ):
        normalized = _valid_ip(candidate)
        if normalized:
            result.append((path, normalized))
    return result


def _find_mitre_source(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
    value: str,
) -> tuple[ParsedRawMessageEvidence, str] | None:
    for parsed in parsed_messages:
        threat = _as_dict(parsed.fields.get("threat"))
        tags = threat.get("tag")
        if not isinstance(tags, list):
            continue
        for index, tag in enumerate(tags):
            if value in (match.upper() for match in _MITRE_TECHNIQUE_RE.findall(str(tag or ""))):
                return parsed, f"threat.tag[{index}]"
    return None


def _append_provenance(
    target: list[dict[str, Any]],
    *,
    canonical_path: str,
    selected_value: Any,
    parsed: ParsedRawMessageEvidence,
    relative_path: str | None,
) -> None:
    if selected_value is None or selected_value == "" or not relative_path:
        return
    target.append(
        {
            "canonical_path": canonical_path,
            "selected_value": str(selected_value),
            "selected_from": f"{parsed.source_path}#parsed.{relative_path}",
            "source_layer": EvidenceLayer.RAW_MESSAGE.value,
            "trust_level": EvidenceTrustLevel.HIGH.value,
            "selection_reason": "PingAn threat-intelligence adapter selected typed message evidence",
            "alternative_values": [],
        }
    )


def _semantic(
    field_path: str,
    semantic_type: str,
    meaning: str,
    *,
    entities: bool = False,
    reasoning: bool = False,
) -> dict[str, Any]:
    return {
        "field_path": field_path,
        "semantic_type": semantic_type,
        "meaning": meaning,
        "participates_in_entities": entities,
        "participates_in_reasoning": reasoning,
    }


def _resolve_alert_path(alert: AlertInput, path: str) -> Any:
    current: Any = alert
    for segment in path.split("."):
        current = getattr(current, segment, None)
        if current is None:
            return None
    return current


def _resolve_mapping_path(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for segment in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(segment)
    return current


def _first_present_path(
    value: Mapping[str, Any],
    paths: Sequence[str],
) -> str | None:
    for path in paths:
        candidate = _resolve_mapping_path(value, path)
        if candidate is None or isinstance(candidate, (dict, list, bool)):
            continue
        if str(candidate).strip():
            return path
    return None


def _valid_ip(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _valid_ioc(value: str | None) -> str | None:
    if value is None:
        return None
    if normalized_ip := _valid_ip(value):
        return normalized_ip
    candidate = value.strip()
    if _FILE_HASH_RE.fullmatch(candidate):
        return candidate.upper()
    parsed = urlsplit(candidate)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        return candidate
    normalized_domain = candidate.lower().rstrip(".")
    return normalized_domain if _DOMAIN_RE.fullmatch(normalized_domain) else None


def _port(value: Any) -> int | None:
    parsed = _intish(value)
    return parsed if parsed is not None and 0 <= parsed <= 65535 else None


def _intish(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_str(value: Mapping[str, Any], aliases: Sequence[str]) -> str | None:
    for alias in aliases:
        candidate = value.get(alias)
        if candidate is None or isinstance(candidate, (dict, list, bool)):
            continue
        text = str(candidate).strip()
        if text:
            return text
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


__all__ = [
    "build_threat_intel_canonical_field_provenance",
    "build_threat_intel_entities",
    "build_threat_intel_scenario_signals",
    "build_threat_intel_source_field_semantics",
    "threat_intel_field_importance_rules",
    "threat_intel_category",
    "threat_intel_indicators",
    "threat_intel_mitre_values",
    "threat_intel_severity",
    "threat_intel_session",
]
