"""PingAn EDR-specific projections for nested endpoint evidence."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping, Sequence
from typing import Any

from soc_agent.contracts import (
    AlertInput,
    AlertSourceType,
    EvidenceLayer,
    EvidenceTrustLevel,
    ParsedRawMessageEvidence,
)

_DETAIL_KEY_RE = re.compile(r"details(?P<index>\d+)$", re.IGNORECASE)
_ENDPOINT_IP_ALIASES = (
    "str_source_ip",
    "device__ip",
    "agent_ip",
    "internal_ip",
    "iplist",
)
_EXPLICIT_IOC_ALIASES = ("ioc", "str_ioc_value")


def first_edr_detail(
    fields: Mapping[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    records = _detail_records(fields)
    return records[0] if records else None


def edr_mitre_values(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
    *,
    prefix: str,
) -> list[str]:
    values: list[str] = []
    for parsed in parsed_messages:
        for _, detail in _detail_records(parsed.fields):
            values.extend(
                _mitre_ids(
                    _first_str(detail, ("attck_id", "attack_id")) or "",
                    prefix=prefix,
                )
            )
    return _dedupe(values)


def validated_edr_digest(
    value: str | None,
    *,
    expected_length: int,
) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not re.fullmatch(rf"[0-9A-Fa-f]{{{expected_length}}}", normalized):
        return None
    return normalized


def edr_ip_addresses(value: Any) -> list[str]:
    candidates = value if isinstance(value, list) else re.split(r"[,;\s]+", str(value or ""))
    result: list[str] = []
    for candidate in candidates:
        text = str(candidate).strip()
        if not text:
            continue
        try:
            normalized = str(ipaddress.ip_address(text))
        except ValueError:
            continue
        if normalized not in result:
            result.append(normalized)
    return result


def edr_endpoint_addresses(fields: Mapping[str, Any]) -> list[str]:
    """Return endpoint identities without assigning a wire-network role."""

    values: list[str] = []
    for alias in _ENDPOINT_IP_ALIASES:
        values.extend(edr_ip_addresses(fields.get(alias)))
    return _dedupe(values)


def edr_attacker_candidates(
    fields: Mapping[str, Any],
    *,
    known_endpoint_addresses: Sequence[str] = (),
) -> list[str]:
    """Return valid vendor attack-IP assertions that are not the endpoint itself."""

    endpoint_addresses = {
        *edr_endpoint_addresses(fields),
        *known_endpoint_addresses,
    }
    return [value for value in edr_ip_addresses(fields.get("str_attack_ip")) if value not in endpoint_addresses]


def edr_threat_indicators(
    fields: Mapping[str, Any],
    *,
    known_endpoint_addresses: Sequence[str] = (),
) -> list[str]:
    """Project only explicit IOCs and validated remote attack-IP candidates."""

    values = list(
        edr_attacker_candidates(
            fields,
            known_endpoint_addresses=known_endpoint_addresses,
        )
    )
    for alias in _EXPLICIT_IOC_ALIASES:
        values.extend(_explicit_indicator_values(fields.get(alias)))
    return _dedupe(values)


def build_edr_process_observations(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for parsed in parsed_messages:
        details = _detail_records(parsed.fields)
        for detail_name, detail in details:
            nodes = _process_nodes(detail)
            if not nodes:
                continue
            observations.append(
                _drop_none(
                    {
                        "observation_id": (f"process:{parsed.message_hash[:12]}:{detail_name}"),
                        "evidence_path": (f"{parsed.source_path}#parsed.{detail_name}"),
                        "event_time": _first_str(
                            detail,
                            (
                                "action_time ",
                                "action_time",
                                "process_create_time",
                            ),
                        ),
                        "host_name": _first_str(
                            parsed.fields,
                            ("host_name", "endpoint"),
                        ),
                        "nodes": nodes,
                    }
                )
            )
        if details:
            continue

        flat_node = _flat_process_node(parsed.fields)
        if flat_node is None:
            continue
        observations.append(
            _drop_none(
                {
                    "observation_id": f"process:{parsed.message_hash[:16]}",
                    "evidence_path": f"{parsed.source_path}#parsed",
                    "event_time": _first_str(
                        parsed.header,
                        ("timestamp", "event_time"),
                    )
                    or _first_str(
                        parsed.fields,
                        ("timestamp", "time", "access_time", "first_access_time"),
                    ),
                    "host_name": _first_str(
                        parsed.fields,
                        ("host_name", "device__hostname", "str_source_host", "endpoint"),
                    ),
                    "nodes": [flat_node],
                }
            )
        )
    return observations


def build_edr_file_observations(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for parsed in parsed_messages:
        for detail_name, detail in _detail_records(parsed.fields):
            action_detail = _as_dict(detail.get("action_detail"))
            file_name = _first_str(action_detail, ("file_name",))
            file_path = _first_str(action_detail, ("file_path",))
            if not file_name and not file_path:
                continue
            observations.append(
                _drop_none(
                    {
                        "observation_id": (f"file:{parsed.message_hash[:12]}:{detail_name}"),
                        "evidence_path": (f"{parsed.source_path}#parsed.{detail_name}.action_detail"),
                        "relation": "endpoint_action_target",
                        "event_time": _first_str(
                            detail,
                            ("action_time ", "action_time"),
                        ),
                        "process_id": _intish(_first_str(detail, ("process_pid",))),
                        "file_name": file_name,
                        "file_path": file_path,
                        "exists": _boolish(action_detail.get("is_exist")),
                    }
                )
            )
    return observations


def edr_field_importance_rules() -> list[dict[str, Any]]:
    definitions = (
        (
            "pingan.edr.endpoint_ip",
            [
                "parsed.str_source_ip",
                "parsed.device__ip",
                "parsed.agent_ip",
                "parsed.internal_ip",
                "parsed.iplist*",
            ],
            "entities.host.ip_addresses",
            "critical",
        ),
        (
            "pingan.edr.endpoint_name",
            ["parsed.host_name", "parsed.endpoint"],
            "entities.host.host_name",
            "high",
        ),
        (
            "pingan.edr.endpoint_id",
            ["parsed.agent_id"],
            "entities.host.host_id",
            "high",
        ),
        (
            "pingan.edr.process_observation",
            [
                "parsed.details*.process_mame",
                "parsed.details*.process_name",
                "parsed.details*.process_pid",
                "parsed.details*.process_path",
                "parsed.details*.command",
                "parsed.details*.process_user",
                "parsed.details*.process_md5",
                "parsed.details*.process_sha256",
            ],
            "entities.process.observations",
            "critical",
        ),
        (
            "pingan.edr.child_process_observation",
            ["parsed.details*.action_detail.child_*"],
            "entities.process.observations",
            "critical",
        ),
        (
            "pingan.edr.file_observation",
            [
                "parsed.details*.action_detail.file_name",
                "parsed.details*.action_detail.file_path",
            ],
            "entities.file.observations",
            "high",
        ),
        (
            "pingan.edr.rule_name",
            [
                "parsed.details*.rule_name",
                "parsed.details*.rule_desc",
                "parsed.alert_describe",
            ],
            "detection.rule_name",
            "critical",
        ),
    )
    return [
        {
            "rule_id": rule_id,
            "source_patterns": source_patterns,
            "expected_target": expected_target,
            "importance": importance,
            "source_types": [AlertSourceType.EDR.value],
            "reason": f"PingAn EDR evidence should populate {expected_target}",
        }
        for rule_id, source_patterns, expected_target, importance in definitions
    ]


def build_edr_source_field_semantics(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
    *,
    known_endpoint_addresses: Sequence[str] = (),
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    all_endpoint_addresses = _dedupe(list(known_endpoint_addresses) + [value for parsed in parsed_messages for value in edr_endpoint_addresses(parsed.fields)])
    for parsed in parsed_messages:
        base_path = f"{parsed.source_path}#parsed"
        for alias in _ENDPOINT_IP_ALIASES:
            if not edr_ip_addresses(parsed.fields.get(alias)):
                continue
            observations.append(
                _semantic(
                    f"{base_path}.{alias}",
                    "endpoint_identity",
                    "endpoint_address_identifies_the_observed_host_not_a_wire_source_or_confirmed_victim",
                    entities=True,
                )
            )

        attack_ip = _first_str(parsed.fields, ("str_attack_ip",))
        if attack_ip:
            has_remote_candidate = bool(
                edr_attacker_candidates(
                    parsed.fields,
                    known_endpoint_addresses=all_endpoint_addresses,
                )
            )
            observations.append(
                _semantic(
                    f"{base_path}.str_attack_ip",
                    "vendor_attack_ip_assertion",
                    "vendor_attack_ip_is_a_tentative_security_role_or_remote_peer_candidate_not_a_wire_destination",
                    entities=has_remote_candidate,
                )
            )

        if _first_str(parsed.fields, ("str_threat_value",)):
            observations.append(
                _semantic(
                    f"{base_path}.str_threat_value",
                    "polymorphic_vendor_threat_value",
                    "vendor_threat_value_may_be_an_ip_identifier_or_digest_shaped_value_and_is_not_a_network_endpoint_without_an_explicit_type_contract",
                )
            )

        if _first_str(parsed.fields, ("str_activity_id",)):
            observations.append(
                _semantic(
                    f"{base_path}.str_activity_id",
                    "vendor_activity_identifier",
                    "activity_identifier_is_preserved_for_audit_and_correlation_but_is_not_an_ip_hash_or_security_role",
                    reasoning=False,
                )
            )

        for detail_name, detail in _detail_records(parsed.fields):
            detail_path = f"{base_path}.{detail_name}"
            for field_name, expected_length in (
                ("process_md5", 32),
                ("process_sha256", 64),
            ):
                value = _first_str(detail, (field_name,))
                if (
                    value
                    and validated_edr_digest(
                        value,
                        expected_length=expected_length,
                    )
                    is None
                ):
                    observations.append(
                        {
                            "field_path": f"{detail_path}.{field_name}",
                            "semantic_type": "invalid_process_hash",
                            "meaning": ("value_does_not_match_the_declared_digest_shape_and_is_not_an_entity"),
                            "participates_in_entities": False,
                            "participates_in_reasoning": False,
                        }
                    )

            attack_id = _first_str(detail, ("attck_id", "attack_id"))
            if attack_id:
                source_name = "attck_id" if "attck_id" in detail else "attack_id"
                observations.append(
                    {
                        "field_path": f"{detail_path}.{source_name}",
                        "semantic_type": "vendor_mitre_classification",
                        "meaning": ("vendor_attack_mapping_is_classification_context_not_proof_that_the_technique_succeeded"),
                        "participates_in_entities": False,
                        "participates_in_reasoning": True,
                    }
                )

            action_detail = _as_dict(detail.get("action_detail"))
            if not action_detail:
                continue
            action_path = f"{detail_path}.action_detail"
            if _first_str(action_detail, ("child_name", "child_path")):
                observations.append(
                    _semantic(
                        action_path,
                        "endpoint_child_process_observation",
                        "child_process_fields_are_observed_process_context_not_maliciousness_or_success_truth",
                        entities=True,
                    )
                )
            if _first_str(action_detail, ("file_name", "file_path")):
                observations.append(
                    _semantic(
                        action_path,
                        "endpoint_file_action_target",
                        "file_fields_identify_an_action_target_not_proof_of_maliciousness_or_success",
                        entities=True,
                    )
                )
            if any(str(key).lower().startswith("registry_") for key in action_detail):
                observations.append(
                    _semantic(
                        action_path,
                        "endpoint_registry_action_context",
                        "registry_fields_are_action_context_not_file_entities_or_detection_truth",
                    )
                )
            if any(str(key).lower().startswith("task_") for key in action_detail):
                observations.append(
                    _semantic(
                        action_path,
                        "endpoint_scheduled_task_context",
                        "scheduled_task_fields_are_action_context_not_automatic_maliciousness_truth",
                    )
                )
            if "is_exist" in action_detail:
                observations.append(
                    _semantic(
                        f"{action_path}.is_exist",
                        "endpoint_artifact_existence",
                        "reported_existence_describes_sensor_state_not_maliciousness_or_attack_success",
                        entities=True,
                    )
                )
    return observations


def build_edr_canonical_field_provenance(
    alert: AlertInput,
    *,
    parsed_messages: Sequence[ParsedRawMessageEvidence],
    primary_parsed: ParsedRawMessageEvidence | None,
) -> list[dict[str, Any]]:
    provenance: list[dict[str, Any]] = []
    if primary_parsed is not None:
        _append_primary_provenance(provenance, alert, primary_parsed)
    _append_endpoint_ip_provenance(provenance, alert, parsed_messages)
    _append_threat_ioc_provenance(provenance, alert, parsed_messages)
    _append_mitre_provenance(provenance, alert, parsed_messages)
    _append_observation_provenance(provenance, alert, parsed_messages)
    return provenance


def _append_primary_provenance(
    provenance: list[dict[str, Any]],
    alert: AlertInput,
    parsed: ParsedRawMessageEvidence,
) -> None:
    fields = parsed.fields
    primary_detail = first_edr_detail(fields)
    detail_name, detail = primary_detail if primary_detail is not None else (None, {})
    action_detail = _as_dict(detail.get("action_detail"))

    def detail_path(*aliases: str) -> str | None:
        selected = _first_present_path(detail, aliases)
        return f"{detail_name}.{selected}" if detail_name and selected else None

    direct_sources = {
        "entities.host.host_name": _first_present_path(
            fields,
            ("host_name", "device__hostname", "str_source_host", "endpoint"),
        ),
        "entities.host.host_id": _first_present_path(
            fields,
            ("agent_id", "str_agent_id", "metadata__product__version"),
        ),
        "entities.host.asset_id": _first_present_path(
            fields,
            (
                "agent_id",
                "str_agent_id",
                "str_source_ip",
                "device__ip",
                "agent_ip",
                "internal_ip",
                "iplist",
            ),
        ),
        "entities.process.process_name": _first_present_path(
            fields,
            (
                "process__name",
                "str_process_short",
                "process__file__name",
                "str_suspicious_process_ancestor_short",
            ),
        )
        or detail_path("process_mame", "process_name"),
        "entities.process.process_id": _first_present_path(
            fields,
            ("process__pid", "str_process_id"),
        )
        or detail_path("process_pid"),
        "entities.process.process_path": _first_present_path(
            fields,
            ("process__file__path", "str_process_full", "str_suspicious_file"),
        )
        or detail_path("process_path"),
        "entities.process.command_line": _first_present_path(
            fields,
            (
                "process__cmd_line",
                "str_cmd",
                "str_suspicious_process_ancestor_cmd",
                "process__ancestor__cmd_line",
            ),
        )
        or detail_path("command"),
        "entities.process.md5": _first_present_path(
            fields,
            (
                "process__file__hashes__md5",
                "str_md5",
                "str_suspicious_file_md5",
            ),
        )
        or detail_path("process_md5"),
        "entities.process.sha256": _first_present_path(
            fields,
            (
                "process__file__hashes__sha256",
                "str_sha256",
                "str_suspicious_file_sha256",
            ),
        )
        or detail_path("process_sha256"),
        "entities.user.username": _first_present_path(
            fields,
            ("str_user_agent", "process__user__name", "str_user_process"),
        )
        or detail_path("process_user"),
        "detection.rule_name": detail_path("rule_name", "rule_desc")
        or _first_present_path(
            fields,
            ("finding__title", "str_title", "rule_name", "alert_describe"),
        ),
    }
    for canonical_path, source_path in direct_sources.items():
        _append_provenance(
            provenance,
            canonical_path=canonical_path,
            selected_value=_resolve_alert_path(alert, canonical_path),
            parsed=parsed,
            source_path=source_path,
        )

    action_file_selected = bool(_first_str(action_detail, ("file_name", "file_path")))
    file_sources = {
        "entities.file.file_name": (
            f"{detail_name}.action_detail.file_name"
            if detail_name and _first_str(action_detail, ("file_name",))
            else _first_present_path(
                fields,
                ("process__file__name", "str_process_short"),
            )
            or detail_path("process_mame", "process_name")
        ),
        "entities.file.file_path": (
            f"{detail_name}.action_detail.file_path"
            if detail_name and _first_str(action_detail, ("file_path",))
            else _first_present_path(
                fields,
                ("process__file__path", "str_process_full", "str_suspicious_file"),
            )
            or detail_path("process_path")
        ),
        "entities.file.md5": (None if action_file_selected else direct_sources["entities.process.md5"]),
        "entities.file.sha256": (None if action_file_selected else direct_sources["entities.process.sha256"]),
    }
    for canonical_path, source_path in file_sources.items():
        _append_provenance(
            provenance,
            canonical_path=canonical_path,
            selected_value=_resolve_alert_path(alert, canonical_path),
            parsed=parsed,
            source_path=source_path,
        )


def _append_endpoint_ip_provenance(
    provenance: list[dict[str, Any]],
    alert: AlertInput,
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> None:
    for index, value in enumerate(alert.entities.host.ip_addresses):
        source = _endpoint_ip_source(parsed_messages, value)
        if source is None:
            continue
        parsed, source_path = source
        _append_provenance(
            provenance,
            canonical_path=f"entities.host.ip_addresses[{index}]",
            selected_value=value,
            parsed=parsed,
            source_path=source_path,
        )


def _append_threat_ioc_provenance(
    provenance: list[dict[str, Any]],
    alert: AlertInput,
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> None:
    for index, value in enumerate(alert.entities.threat.iocs):
        source = _threat_indicator_source(parsed_messages, value)
        if source is None:
            continue
        parsed, source_path = source
        _append_provenance(
            provenance,
            canonical_path=f"entities.threat.iocs[{index}]",
            selected_value=value,
            parsed=parsed,
            source_path=source_path,
        )


def _append_mitre_provenance(
    provenance: list[dict[str, Any]],
    alert: AlertInput,
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> None:
    for prefix, values in (
        ("TA", alert.classification.tactic),
        ("T", alert.classification.technique),
    ):
        for index, value in enumerate(values):
            source = _mitre_source(parsed_messages, value=value, prefix=prefix)
            if source is None:
                continue
            parsed, source_path = source
            canonical_name = "tactic" if prefix == "TA" else "technique"
            _append_provenance(
                provenance,
                canonical_path=f"classification.{canonical_name}[{index}]",
                selected_value=value,
                parsed=parsed,
                source_path=source_path,
            )


def _append_observation_provenance(
    provenance: list[dict[str, Any]],
    alert: AlertInput,
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> None:
    process_by_evidence = {item.evidence_path: (index, item) for index, item in enumerate(alert.entities.process.observations)}
    file_by_evidence = {item.evidence_path: (index, item) for index, item in enumerate(alert.entities.file.observations)}
    for parsed in parsed_messages:
        details = _detail_records(parsed.fields)
        if details:
            for detail_name, detail in details:
                process_item = process_by_evidence.get(f"{parsed.source_path}#parsed.{detail_name}")
                if process_item is not None:
                    observation_index, observation = process_item
                    node_sources = _process_node_sources(detail_name, detail)
                    for node_index, node in enumerate(observation.nodes):
                        for field_name, source_path in node_sources[node_index].items():
                            _append_provenance(
                                provenance,
                                canonical_path=(f"entities.process.observations[{observation_index}].nodes[{node_index}].{field_name}"),
                                selected_value=getattr(node, field_name),
                                parsed=parsed,
                                source_path=source_path,
                            )

                file_item = file_by_evidence.get(f"{parsed.source_path}#parsed.{detail_name}.action_detail")
                if file_item is not None:
                    observation_index, observation = file_item
                    action_detail = _as_dict(detail.get("action_detail"))
                    for field_name in ("file_name", "file_path", "is_exist"):
                        canonical_name = "exists" if field_name == "is_exist" else field_name
                        _append_provenance(
                            provenance,
                            canonical_path=(f"entities.file.observations[{observation_index}].{canonical_name}"),
                            selected_value=getattr(observation, canonical_name),
                            parsed=parsed,
                            source_path=(f"{detail_name}.action_detail.{field_name}" if field_name in action_detail else None),
                        )
            continue

        process_item = process_by_evidence.get(f"{parsed.source_path}#parsed")
        if process_item is None:
            continue
        observation_index, observation = process_item
        for node_index, node in enumerate(observation.nodes):
            sources = {
                "process_name": _first_present_path(
                    parsed.fields,
                    ("process__name", "str_process_short", "process__file__name"),
                ),
                "process_id": _first_present_path(
                    parsed.fields,
                    ("process__pid", "str_process_id"),
                ),
                "process_path": _first_present_path(
                    parsed.fields,
                    ("process__file__path", "str_process_full", "str_suspicious_file"),
                ),
                "command_line": _first_present_path(
                    parsed.fields,
                    ("process__cmd_line", "str_cmd", "process__ancestor__cmd_line"),
                ),
                "username": _first_present_path(
                    parsed.fields,
                    ("process__user__name", "str_user_process"),
                ),
                "md5": _first_present_path(
                    parsed.fields,
                    (
                        "process__file__hashes__md5",
                        "str_md5",
                        "str_suspicious_file_md5",
                    ),
                ),
                "sha256": _first_present_path(
                    parsed.fields,
                    (
                        "process__file__hashes__sha256",
                        "str_sha256",
                        "str_suspicious_file_sha256",
                    ),
                ),
            }
            for field_name, source_path in sources.items():
                _append_provenance(
                    provenance,
                    canonical_path=(f"entities.process.observations[{observation_index}].nodes[{node_index}].{field_name}"),
                    selected_value=getattr(node, field_name),
                    parsed=parsed,
                    source_path=source_path,
                )


def _detail_records(
    fields: Mapping[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    records: list[tuple[int, str, dict[str, Any]]] = []
    for key, value in fields.items():
        match = _DETAIL_KEY_RE.fullmatch(str(key))
        if match is None or not isinstance(value, Mapping):
            continue
        records.append((int(match.group("index")), str(key), dict(value)))
    return [(key, value) for _, key, value in sorted(records)]


def _process_nodes(detail: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = dict(detail)
    nodes: list[dict[str, Any]] = []
    process_name = _first_str(values, ("process_mame", "process_name"))
    if process_name:
        nodes.append(
            _drop_none(
                {
                    "process_name": process_name,
                    "process_id": _intish(_first_str(values, ("process_pid",))),
                    "process_path": _first_str(values, ("process_path",)),
                    "command_line": _first_str(values, ("command",)),
                    "username": _first_str(values, ("process_user",)),
                    "md5": validated_edr_digest(
                        _first_str(values, ("process_md5",)),
                        expected_length=32,
                    ),
                    "sha256": validated_edr_digest(
                        _first_str(values, ("process_sha256",)),
                        expected_length=64,
                    ),
                }
            )
        )
    action_detail = _as_dict(detail.get("action_detail"))
    child_name = _first_str(action_detail, ("child_name",))
    if child_name:
        nodes.append(
            _drop_none(
                {
                    "process_name": child_name,
                    "process_id": _intish(_first_str(action_detail, ("child_pid",))),
                    "process_path": _first_str(action_detail, ("child_path",)),
                    "command_line": _first_str(
                        action_detail,
                        ("child_commandline",),
                    ),
                }
            )
        )
    return nodes


def _flat_process_node(fields: Mapping[str, Any]) -> dict[str, Any] | None:
    process_name = _first_str(
        fields,
        ("process__name", "str_process_short", "process__file__name"),
    )
    if process_name is None:
        return None
    return _drop_none(
        {
            "process_name": process_name,
            "process_id": _intish(
                _first_str(fields, ("process__pid", "str_process_id")),
            ),
            "process_path": _first_str(
                fields,
                ("process__file__path", "str_process_full", "str_suspicious_file"),
            ),
            "command_line": _first_str(
                fields,
                ("process__cmd_line", "str_cmd", "process__ancestor__cmd_line"),
            ),
            "username": _first_str(
                fields,
                ("process__user__name", "str_user_process"),
            ),
            "md5": validated_edr_digest(
                _first_str(
                    fields,
                    (
                        "process__file__hashes__md5",
                        "str_md5",
                        "str_suspicious_file_md5",
                    ),
                ),
                expected_length=32,
            ),
            "sha256": validated_edr_digest(
                _first_str(
                    fields,
                    (
                        "process__file__hashes__sha256",
                        "str_sha256",
                        "str_suspicious_file_sha256",
                    ),
                ),
                expected_length=64,
            ),
        }
    )


def _process_node_sources(
    detail_name: str,
    detail: Mapping[str, Any],
) -> list[dict[str, str | None]]:
    sources: list[dict[str, str | None]] = []
    process_name_source = _first_present_path(
        detail,
        ("process_mame", "process_name"),
    )
    if process_name_source:
        sources.append(
            {
                "process_name": f"{detail_name}.{process_name_source}",
                "process_id": f"{detail_name}.process_pid",
                "process_path": f"{detail_name}.process_path",
                "command_line": f"{detail_name}.command",
                "username": f"{detail_name}.process_user",
                "md5": f"{detail_name}.process_md5",
                "sha256": f"{detail_name}.process_sha256",
            }
        )
    action_detail = _as_dict(detail.get("action_detail"))
    if _first_str(action_detail, ("child_name",)):
        sources.append(
            {
                "process_name": f"{detail_name}.action_detail.child_name",
                "process_id": f"{detail_name}.action_detail.child_pid",
                "process_path": f"{detail_name}.action_detail.child_path",
                "command_line": f"{detail_name}.action_detail.child_commandline",
            }
        )
    return sources


def _endpoint_ip_source(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
    value: str,
) -> tuple[ParsedRawMessageEvidence, str] | None:
    for parsed in parsed_messages:
        for alias in _ENDPOINT_IP_ALIASES:
            if value in edr_ip_addresses(parsed.fields.get(alias)):
                return parsed, alias
    return None


def _threat_indicator_source(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
    value: str,
) -> tuple[ParsedRawMessageEvidence, str] | None:
    known_endpoint_addresses = _dedupe([endpoint for parsed in parsed_messages for endpoint in edr_endpoint_addresses(parsed.fields)])
    for parsed in parsed_messages:
        for alias in _EXPLICIT_IOC_ALIASES:
            if value in _explicit_indicator_values(parsed.fields.get(alias)):
                return parsed, alias
    for parsed in parsed_messages:
        if value in edr_attacker_candidates(
            parsed.fields,
            known_endpoint_addresses=known_endpoint_addresses,
        ):
            return parsed, "str_attack_ip"
    return None


def _mitre_source(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
    *,
    value: str,
    prefix: str,
) -> tuple[ParsedRawMessageEvidence, str] | None:
    direct_aliases = ("str_tactic_id", "finding__attack__tactic_id") if prefix == "TA" else ("str_technique_id", "finding__attack__technique_id")
    for parsed in parsed_messages:
        direct_path = _first_present_path(parsed.fields, direct_aliases)
        if direct_path and value in _mitre_ids(
            _first_str(parsed.fields, (direct_path,)) or "",
            prefix=prefix,
        ):
            return parsed, direct_path
        for detail_name, detail in _detail_records(parsed.fields):
            detail_value = _first_str(detail, ("attck_id", "attack_id")) or ""
            if value in _mitre_ids(detail_value, prefix=prefix):
                source_name = "attck_id" if "attck_id" in detail else "attack_id"
                return parsed, f"{detail_name}.{source_name}"
    return None


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
            "selected_from": f"{parsed.source_path}#parsed.{source_path}",
            "source_layer": EvidenceLayer.RAW_MESSAGE.value,
            "trust_level": EvidenceTrustLevel.HIGH.value,
            "selection_reason": "pingan_raw_message_mapping",
        }
    )


def _resolve_alert_path(alert: AlertInput, path: str) -> Any:
    current: Any = alert
    for segment in path.split("."):
        if "[" in segment:
            name, raw_index = segment.rstrip("]").split("[", 1)
            current = getattr(current, name, None)
            if not isinstance(current, list) or not raw_index.isdigit():
                return None
            index = int(raw_index)
            current = current[index] if index < len(current) else None
        else:
            current = getattr(current, segment, None)
        if current is None:
            return None
    return current


def _first_present_path(
    fields: Mapping[str, Any],
    aliases: tuple[str, ...],
) -> str | None:
    for alias in aliases:
        if _first_str(fields, (alias,)) is not None:
            return alias
    return None


def _first_str(
    source: Mapping[str, Any],
    aliases: tuple[str, ...],
) -> str | None:
    for alias in aliases:
        value = source.get(alias)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _mitre_ids(value: str, *, prefix: str) -> list[str]:
    pattern = r"\bTA\d{4}\b" if prefix == "TA" else r"\bT\d{4}(?:\.\d{3})?\b"
    return re.findall(pattern, value)


def _semantic(
    field_path: str,
    semantic_type: str,
    meaning: str,
    *,
    entities: bool = False,
    reasoning: bool = True,
) -> dict[str, Any]:
    return {
        "field_path": field_path,
        "semantic_type": semantic_type,
        "meaning": meaning,
        "participates_in_entities": entities,
        "participates_in_reasoning": reasoning,
    }


def _explicit_indicator_values(value: Any) -> list[str]:
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_explicit_indicator_values(item))
        return _dedupe(result)
    text = str(value or "").strip()
    if not text:
        return []
    parts = [item for item in re.split(r"[,;\s]+", text) if item]
    ip_values = edr_ip_addresses(text)
    if ip_values and len(ip_values) == len(parts):
        return ip_values
    return [text]


def _boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    return None


def _intish(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


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
    "edr_attacker_candidates",
    "build_edr_canonical_field_provenance",
    "build_edr_file_observations",
    "build_edr_process_observations",
    "build_edr_source_field_semantics",
    "edr_endpoint_addresses",
    "edr_field_importance_rules",
    "edr_ip_addresses",
    "edr_mitre_values",
    "edr_threat_indicators",
    "first_edr_detail",
    "validated_edr_digest",
]
