"""PingAn HIDS projections for host, process, file, and event-scoped network evidence."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePath
from typing import Any

from soc_agent.contracts import (
    AlertInput,
    AlertSourceType,
    EvidenceLayer,
    EvidenceTrustLevel,
    ParsedRawMessageEvidence,
)

_DEFAULT_EXTERNAL_IP = "1.1.1.1"
_ENDPOINT_IP_ALIASES = (
    "internal_ip",
    "agent_ip",
    "device__ip",
    "str_source_ip",
    "external_ip",
)
_PROCESS_NAME_ALIASES = ("pname", "process_name")
_PROCESS_ID_ALIASES = ("pid", "process__pid", "str_process_id")
_PROCESS_PATH_ALIASES = ("path", "process__file__path", "str_process_full")
_PROCESS_COMMAND_ALIASES = ("cmd", "process__cmd_line", "str_cmd")
_PROCESS_USER_ALIASES = ("uname", "process__user__name")
_SESSION_USER_ALIASES = ("login_user",)
_PARENT_NAME_ALIASES = ("ppname",)
_PARENT_ID_ALIASES = ("ppid",)
_PARENT_PATH_ALIASES = ("ppath",)
_PARENT_COMMAND_ALIASES = ("pcmd",)
_PARENT_USER_ALIASES = ("puname",)
_TREE_ALIASES = ("process_tree", "event_content")
_FILE_EVENT_TYPES = frozenset(
    {
        "backdoor_diagnose",
        "backdoor_diagnose_win",
        "honey_file",
    }
)


def hids_endpoint_sources(fields: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Return distinct endpoint identities with their first exact source alias."""

    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for alias in _ENDPOINT_IP_ALIASES:
        for value in _ip_values(fields.get(alias)):
            if alias == "external_ip" and value == _DEFAULT_EXTERNAL_IP:
                continue
            if value in seen:
                continue
            seen.add(value)
            result.append((value, alias))
    return result


def hids_endpoint_addresses(fields: Mapping[str, Any]) -> list[str]:
    return [value for value, _ in hids_endpoint_sources(fields)]


def hids_primary_process(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> dict[str, Any]:
    """Return a canonical process summary without discarding full observations."""

    if not parsed_messages:
        return {}
    parsed = parsed_messages[0]
    fields = parsed.fields
    observations = build_hids_process_observations([parsed])
    nodes = observations[0].get("nodes", []) if observations else []
    explicit_pid = _intish(_first_str(fields, _PROCESS_ID_ALIASES))
    explicit_name = _first_str(fields, _PROCESS_NAME_ALIASES)
    selected = _matching_node(nodes, process_id=explicit_pid, process_name=explicit_name)
    if selected is None and nodes:
        selected = nodes[-1]
    explicit_parent = _explicit_parent_node(fields)
    parent = explicit_parent or _parent_for_node(nodes, selected)
    return _drop_none(
        {
            "process_name": selected.get("process_name") if selected else explicit_name,
            "process_id": selected.get("process_id") if selected else explicit_pid,
            "process_path": selected.get("process_path") if selected else _first_str(fields, _PROCESS_PATH_ALIASES),
            "command_line": selected.get("command_line") if selected else _first_str(fields, _PROCESS_COMMAND_ALIASES),
            "parent_process_name": parent.get("process_name") if parent else _first_str(fields, _PARENT_NAME_ALIASES),
            "parent_process_id": parent.get("process_id") if parent else _intish(_first_str(fields, _PARENT_ID_ALIASES)),
            "parent_command_line": parent.get("command_line") if parent else _first_str(fields, _PARENT_COMMAND_ALIASES),
            "md5": selected.get("md5") if selected else _validated_digest(_first_str(fields, ("md5",)), expected_length=32),
            "sha256": selected.get("sha256") if selected else _validated_digest(_first_str(fields, ("sha256",)), expected_length=64),
        }
    )


def hids_primary_file(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> dict[str, Any]:
    observations = build_hids_file_observations(parsed_messages)
    if not observations:
        return {}
    item = observations[0]
    return _drop_none(
        {
            "file_name": item.get("file_name"),
            "file_path": item.get("file_path"),
            "md5": item.get("md5"),
            "sha1": item.get("sha1"),
            "sha256": item.get("sha256"),
        }
    )


def hids_primary_username(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> str | None:
    for parsed in parsed_messages:
        username = _first_str(parsed.fields, _PROCESS_USER_ALIASES)
        if username:
            return username
    return None


def build_hids_process_observations(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for parsed in parsed_messages:
        fields = parsed.fields
        nodes = _tree_nodes(fields)
        explicit = _explicit_process_node(fields)
        parent = _explicit_parent_node(fields)
        if nodes:
            nodes = _enrich_nodes(nodes, explicit=explicit, parent=parent)
        else:
            nodes = [item for item in (parent, explicit) if item]
        nodes = _dedupe_nodes(nodes)
        if not nodes:
            continue
        observations.append(
            _drop_none(
                {
                    "observation_id": f"process:{parsed.message_hash[:16]}",
                    "event_scope_id": f"process:{parsed.message_hash[:16]}",
                    "evidence_path": f"{parsed.source_path}#parsed",
                    "event_time": _first_str(fields, ("datatime", "time", "atime")) or _first_str(parsed.header, ("timestamp", "event_time")),
                    "host_name": _first_str(fields, ("host_name",)),
                    "parent_process_id": _intish(_first_str(fields, _PARENT_ID_ALIASES)),
                    "nodes": nodes,
                }
            )
        )
    return observations


def build_hids_file_observations(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for parsed in parsed_messages:
        fields = parsed.fields
        event_type = _event_type(fields)
        if event_type not in _FILE_EVENT_TYPES:
            continue
        file_path = _first_str(fields, ("file_path",))
        md5 = _validated_digest(_first_str(fields, ("md5",)), expected_length=32)
        sha1 = _validated_digest(_first_str(fields, ("sha1",)), expected_length=40)
        sha256 = _validated_digest(_first_str(fields, ("sha256",)), expected_length=64)
        if not any((file_path, md5, sha1, sha256)):
            continue
        observations.append(
            _drop_none(
                {
                    "observation_id": f"file:{parsed.message_hash[:16]}",
                    "evidence_path": f"{parsed.source_path}#parsed",
                    "relation": "observed_artifact",
                    "event_time": _first_str(fields, ("datatime", "discovery_time", "first_discovery_time")),
                    "process_id": _intish(_first_str(fields, _PROCESS_ID_ALIASES)),
                    "file_name": _basename(file_path),
                    "file_path": file_path,
                    "md5": md5,
                    "sha1": sha1,
                    "sha256": sha256,
                }
            )
        )
    return observations


def build_hids_network_observations(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> list[dict[str, Any]]:
    """Map only event types whose source contract gives endpoint direction."""

    observations: list[dict[str, Any]] = []
    for parsed in parsed_messages:
        fields = parsed.fields
        event_type = _event_type(fields)
        endpoint = next(iter(hids_endpoint_addresses(fields)), None)
        source_ip: str | None = None
        destination_ip: str | None = None
        src_port: int | None = None
        dst_port: int | None = None
        direction: str | None = None
        if event_type == "bounce_shell":
            source_ip = endpoint
            destination_ip = _first_ip(fields, ("dst_ip",))
            dst_port = _port(fields.get("port"))
            direction = "outbound"
        elif event_type == "honeypot":
            source_ip = _first_ip(fields, ("src_ip",))
            destination_ip = endpoint
            src_port = _port(fields.get("src_port"))
            dst_port = _port(fields.get("port"))
            direction = "inbound"
        elif event_type == "malic_opera":
            source_ip = _first_ip(fields, ("src_ip",))
            destination_ip = endpoint
            direction = "inbound"
        if source_ip is None and destination_ip is None:
            continue
        observations.append(
            _drop_none(
                {
                    "observation_id": f"network:{parsed.message_hash[:16]}",
                    "evidence_path": f"{parsed.source_path}#parsed",
                    "event_time": _first_str(fields, ("datatime", "time")),
                    "source_ip": source_ip,
                    "destination_ip": destination_ip,
                    "src_port": src_port,
                    "dst_port": dst_port,
                    "direction": direction,
                }
            )
        )
    return observations


def hids_field_importance_rules() -> list[dict[str, Any]]:
    definitions = (
        ("pingan.hids.host_ip", ["parsed.internal_ip", "parsed.agent_ip", "parsed.external_ip"], "entities.host.ip_addresses", "critical"),
        ("pingan.hids.host_name", ["parsed.host_name"], "entities.host.host_name", "high"),
        ("pingan.hids.host_id", ["parsed.agent_id"], "entities.host.host_id", "high"),
        (
            "pingan.hids.process_observation",
            [
                "parsed.process_tree",
                "parsed.process_chain",
                "parsed.process_name",
                "parsed.pname",
                "parsed.pid",
                "parsed.cmd",
                "parsed.event_content",
                "parsed.ppname",
                "parsed.ppid",
                "parsed.pcmd",
            ],
            "entities.process.observations",
            "critical",
        ),
        (
            "pingan.hids.file_observation",
            ["parsed.file_path", "parsed.md5", "parsed.sha1", "parsed.sha256"],
            "entities.file.observations",
            "high",
        ),
        ("pingan.hids.user", ["parsed.uname"], "entities.user.username", "high"),
        (
            "pingan.hids.rule_name",
            ["parsed.hit_rule_name", "parsed.hit_rule_names", "parsed.rule"],
            "detection.rule_name",
            "high",
        ),
        (
            "pingan.hids.event_network",
            ["parsed.src_ip", "parsed.dst_ip"],
            "entities.network.observations",
            "high",
        ),
    )
    return [
        {
            "rule_id": rule_id,
            "source_patterns": source_patterns,
            "expected_target": expected_target,
            "importance": importance,
            "source_types": [AlertSourceType.HIDS.value],
            "reason": f"PingAn HIDS evidence should populate {expected_target}",
        }
        for rule_id, source_patterns, expected_target, importance in definitions
    ]


def build_hids_source_field_semantics(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for parsed in parsed_messages:
        fields = parsed.fields
        base_path = f"{parsed.source_path}#parsed"
        if _first_str(fields, ("external_ip",)) == _DEFAULT_EXTERNAL_IP:
            result.append(
                _semantic(
                    f"{base_path}.external_ip",
                    "source_placeholder",
                    "vendor_default_value_not_observed_external_ip",
                    entities=False,
                    reasoning=False,
                )
            )
        event_type_source = _first_present_path(fields, ("event_type", "datatype"))
        if event_type_source:
            result.append(
                _semantic(
                    f"{base_path}.{event_type_source}",
                    "host_event_taxonomy",
                    "vendor_event_type_routes_typed_host_evidence_but_does_not_prove_maliciousness",
                )
            )
        if any(_first_str(fields, (name,)) for name in (*_PROCESS_NAME_ALIASES, *_TREE_ALIASES, "process_chain")):
            result.append(
                _semantic(
                    base_path,
                    "endpoint_process_observation",
                    "process_fields_describe_endpoint_execution_context_not_detection_truth",
                    entities=True,
                )
            )
        if any(_first_str(fields, (name,)) for name in ("file_path", "md5", "sha1", "sha256")):
            result.append(
                _semantic(
                    base_path,
                    "endpoint_artifact_observation",
                    "artifact_paths_and_hashes_are_observed_context_not_proof_of_maliciousness",
                    entities=True,
                )
            )
        if _first_str(fields, ("src_ip", "dst_ip")):
            result.append(
                _semantic(
                    base_path,
                    "event_scoped_network_observation",
                    "network_fields_receive_direction_only_for_the_explicit_HIDS_event_contract",
                    entities=True,
                )
            )
        if _first_str(fields, ("action",)):
            result.append(
                _semantic(
                    f"{base_path}.action",
                    "sensor_record_action",
                    "sensor_add_or_update_action_is_record_lifecycle_not_response_or_attack_success",
                )
            )
        if _first_str(fields, ("event_level",)):
            result.append(
                _semantic(
                    f"{base_path}.event_level",
                    "vendor_event_severity",
                    "vendor_event_level_is_severity_context_not_calibrated_confidence",
                )
            )
        exact_semantics = (
            (
                "datatime",
                "sensor_event_time",
                "sensor_event_time_orders_observations_but_does_not_establish_attack_success",
                False,
            ),
            (
                "time",
                "sensor_event_time",
                "sensor_event_time_orders_observations_but_does_not_establish_attack_success",
                False,
            ),
            (
                "discovery_time",
                "sensor_discovery_time",
                "sensor_discovery_time_supports_timeline_ordering_not_attack_success",
                False,
            ),
            (
                "first_discovery_time",
                "sensor_first_discovery_time",
                "sensor_first_discovery_time_supports_timeline_ordering_not_attack_success",
                False,
            ),
            (
                "datatype",
                "host_event_taxonomy",
                "vendor_event_type_routes_typed_host_evidence_but_does_not_prove_maliciousness",
                False,
            ),
            (
                "event_name",
                "host_event_name",
                "vendor_event_name_is_scenario_context_not_independent_confirmation",
                False,
            ),
            (
                "event_content",
                "host_event_detail",
                "event_detail_may_contain_process_or_behavior_context_but_is_not_independent_confirmation",
                False,
            ),
            (
                "agent_id",
                "endpoint_agent_identifier",
                "endpoint_agent_identifier_supports_asset_resolution_not_human_attribution",
                True,
            ),
            (
                "host_name",
                "endpoint_host_identity",
                "host_name_identifies_the_observed_endpoint_not_a_human_actor",
                True,
            ),
            (
                "agent_ip",
                "endpoint_identity_candidate",
                "endpoint_address_identifies_the_observed_host_not_a_wire_source_or_confirmed_victim",
                True,
            ),
            (
                "external_ip",
                "endpoint_external_identity_candidate",
                "non_placeholder_external_address_is_endpoint_identity_context_not_wire_direction",
                True,
            ),
            (
                "rule",
                "vendor_detection_rule_description",
                "vendor_rule_description_is_detection_context_not_detection_truth",
                False,
            ),
            (
                "hit_rule_name",
                "vendor_detection_rule_name",
                "vendor_rule_name_is_detection_context_not_detection_truth",
                False,
            ),
            (
                "hit_rule_names",
                "vendor_detection_rule_name",
                "vendor_rule_name_is_detection_context_not_detection_truth",
                False,
            ),
            (
                "process_chain",
                "endpoint_process_lineage",
                "process_lineage_is_observed_execution_context_not_proof_of_maliciousness",
                True,
            ),
            (
                "uname",
                "endpoint_process_user",
                "user_running_the_observed_process_not_asset_owner_or_authenticated_human_identity",
                True,
            ),
            (
                _SESSION_USER_ALIASES[0],
                "host_login_session_user",
                "login_session_user_is_separate_from_process_user_and_parent_process_user",
                False,
            ),
            (
                "puname",
                "parent_process_user",
                "user_running_the_observed_parent_process_not_the_child_process_user",
                True,
            ),
            (
                "execute_pname",
                "session_entry_process",
                "entry_or_login_process_context_not_an_automatic_parent_process_relationship",
                False,
            ),
            (
                "backdoor_type",
                "vendor_backdoor_subtype",
                "vendor_behavior_subtype_is_investigation_context_not_detection_truth",
                False,
            ),
            (
                "access_permission",
                "file_permission_observation",
                "observed_file_permission_context_does_not_prove_maliciousness",
                False,
            ),
            (
                "file_operation",
                "file_operation_observation",
                "observed_file_operation_context_does_not_prove_attack_success",
                False,
            ),
            (
                "file_rules",
                "vendor_file_rule_identifier",
                "vendor_file_rule_identifier_is_not_a_portable_detection_rule_code",
                False,
            ),
            (
                "group_name",
                "vendor_asset_group",
                "asset_group_context_may_support_ownership_lookup_but_is_not_event_actor_identity",
                False,
            ),
            (
                "url",
                "sensor_console_link",
                "sensor_console_or_evidence_link_is_not_an_observed_network_destination",
                False,
            ),
            (
                "comment",
                "vendor_event_description",
                "vendor_event_description_is_context_not_independent_confirmation",
                False,
            ),
            (
                "comid",
                "sensor_collector_identifier",
                "collector_identifier_supports_traceability_but_is_not_an_event_actor_or_detection_truth",
                False,
            ),
            (
                "group",
                "vendor_asset_group_identifier",
                "asset_group_identifier_supports_ownership_lookup_but_is_not_event_actor_identity",
                False,
            ),
            (
                "type",
                "sensor_collection_mode",
                "monitor_or_scan_mode_describes_sensor_collection_context_not_attack_outcome",
                False,
            ),
            (
                "name",
                "vendor_behavior_name",
                "vendor_behavior_name_is_investigation_context_not_independent_confirmation",
                False,
            ),
            (
                "file_type",
                "observed_file_type",
                "observed_file_type_is_artifact_context_not_proof_of_maliciousness",
                False,
            ),
            (
                "log_type",
                "vendor_log_subtype",
                "vendor_log_subtype_requires_source_specific_interpretation",
                False,
            ),
            (
                "item",
                "sensor_match_detail",
                "sensor_match_detail_is_high_value_detection_context_not_independent_outcome_proof",
                False,
            ),
            (
                "gname",
                "observed_file_group",
                "file_group_ownership_is_artifact_context_not_event_actor_identity",
                False,
            ),
            (
                "tty",
                "host_session_terminal",
                "terminal_session_context_may_support_investigation_but_not_human_attribution",
                False,
            ),
            (
                "size",
                "observed_file_size",
                "file_size_is_artifact_context_not_proof_of_maliciousness",
                False,
            ),
            (
                "atime",
                "observed_file_access_time",
                "file_access_time_is_source_metadata_and_may_be_zero_or_sensor_encoded",
                False,
            ),
            (
                "ctime",
                "observed_file_change_time",
                "file_change_time_is_source_metadata_and_not_attack_outcome_by_itself",
                False,
            ),
            (
                "mtime",
                "observed_file_modify_time",
                "file_modify_time_is_source_metadata_and_not_attack_outcome_by_itself",
                False,
            ),
        )
        for alias, semantic_type, meaning, entities in exact_semantics:
            if not _has_value(fields.get(alias)):
                continue
            if alias == "external_ip" and _first_str(fields, (alias,)) == _DEFAULT_EXTERNAL_IP:
                continue
            result.append(
                _semantic(
                    f"{base_path}.{alias}",
                    semantic_type,
                    meaning,
                    entities=entities,
                )
            )
    return _dedupe_semantics(result)


def build_hids_canonical_field_provenance(
    alert: AlertInput,
    *,
    parsed_messages: Sequence[ParsedRawMessageEvidence],
    primary_parsed: ParsedRawMessageEvidence | None,
) -> list[dict[str, Any]]:
    provenance: list[dict[str, Any]] = []
    if primary_parsed is not None:
        fields = primary_parsed.fields
        direct = {
            "event.event_time": _first_present_path(fields, ("datatime", "time", "atime")),
            "classification.severity": _first_present_path(fields, ("event_level",)),
            "detection.rule_name": _first_present_path(fields, ("hit_rule_name", "hit_rule_names", "rule")),
            "classification.category": _first_present_path(fields, ("event_name", "event_type")),
            "entities.host.host_name": _first_present_path(fields, ("host_name",)),
            "entities.host.host_id": _first_present_path(fields, ("agent_id",)),
            "entities.host.asset_id": _first_present_path(fields, ("agent_id",)),
            "entities.user.username": _first_present_path(fields, _PROCESS_USER_ALIASES),
            "entities.process.process_name": _process_source_path(fields, alert.entities.process.process_name),
            "entities.process.process_id": _first_present_path(fields, _PROCESS_ID_ALIASES),
            "entities.process.process_path": _first_present_path(fields, _PROCESS_PATH_ALIASES),
            "entities.process.command_line": _process_command_source_path(
                fields,
                alert.entities.process.command_line,
            ),
            "entities.process.parent_process_name": _process_source_path(
                fields,
                alert.entities.process.parent_process_name,
            ),
            "entities.process.parent_process_id": _first_present_path(
                fields,
                _PARENT_ID_ALIASES,
            ),
            "entities.process.parent_command_line": _first_present_path(fields, _PARENT_COMMAND_ALIASES),
            "entities.process.md5": _first_present_path(fields, ("md5",)),
            "entities.process.sha256": _first_present_path(fields, ("sha256",)),
            "entities.file.file_path": _first_present_path(fields, ("file_path",)),
            "entities.file.md5": _first_present_path(fields, ("md5",)),
            "entities.file.sha1": _first_present_path(fields, ("sha1",)),
            "entities.file.sha256": _first_present_path(fields, ("sha256",)),
        }
        for canonical_path, source_path in direct.items():
            _append_provenance(
                provenance,
                canonical_path=canonical_path,
                selected_value=_resolve_alert_path(alert, canonical_path),
                parsed=primary_parsed,
                source_path=source_path,
            )
    for index, value in enumerate(alert.entities.host.ip_addresses):
        source = _source_for_value(parsed_messages, _ENDPOINT_IP_ALIASES, value)
        if source is not None:
            parsed, source_path = source
            _append_provenance(
                provenance,
                canonical_path=f"entities.host.ip_addresses[{index}]",
                selected_value=value,
                parsed=parsed,
                source_path=source_path,
            )
    for canonical_path, selected_value, aliases, compare_basename in (
        (
            "entities.user.username",
            alert.entities.user.username,
            _PROCESS_USER_ALIASES,
            False,
        ),
        (
            "entities.file.file_name",
            alert.entities.file.file_name,
            ("file_path",),
            True,
        ),
        (
            "entities.file.file_path",
            alert.entities.file.file_path,
            ("file_path",),
            False,
        ),
        ("entities.file.md5", alert.entities.file.md5, ("md5",), False),
        ("entities.file.sha1", alert.entities.file.sha1, ("sha1",), False),
        ("entities.file.sha256", alert.entities.file.sha256, ("sha256",), False),
    ):
        source = _source_for_scalar_value(
            parsed_messages,
            aliases,
            selected_value,
            compare_basename=compare_basename,
        )
        if source is None:
            continue
        parsed, source_path = source
        _append_provenance(
            provenance,
            canonical_path=canonical_path,
            selected_value=selected_value,
            parsed=parsed,
            source_path=source_path,
        )
    _append_observation_provenance(provenance, alert, parsed_messages)
    return _dedupe_provenance(provenance)


def _append_observation_provenance(
    provenance: list[dict[str, Any]],
    alert: AlertInput,
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> None:
    parsed_by_evidence = {f"{item.source_path}#parsed": item for item in parsed_messages}
    for index, observation in enumerate(alert.entities.process.observations):
        parsed = parsed_by_evidence.get(observation.evidence_path)
        if parsed is None:
            continue
        for field_name, aliases in (
            ("event_time", ("datatime", "time", "atime")),
            ("host_name", ("host_name",)),
            ("parent_process_id", _PARENT_ID_ALIASES),
        ):
            _append_provenance(
                provenance,
                canonical_path=f"entities.process.observations[{index}].{field_name}",
                selected_value=getattr(observation, field_name),
                parsed=parsed,
                source_path=_first_present_path(parsed.fields, aliases),
            )
        for node_index, node in enumerate(observation.nodes):
            for field_name in (
                "process_name",
                "process_id",
                "process_path",
                "command_line",
                "username",
                "md5",
                "sha256",
            ):
                _append_provenance(
                    provenance,
                    canonical_path=(f"entities.process.observations[{index}].nodes[{node_index}].{field_name}"),
                    selected_value=getattr(node, field_name),
                    parsed=parsed,
                    source_path=_process_node_source_path(
                        parsed.fields,
                        node,
                        field_name=field_name,
                    ),
                )
    for index, observation in enumerate(alert.entities.file.observations):
        parsed = parsed_by_evidence.get(observation.evidence_path)
        if parsed is None:
            continue
        for field_name, aliases in (
            ("event_time", ("datatime", "discovery_time", "first_discovery_time")),
            ("process_id", _PROCESS_ID_ALIASES),
            ("file_name", ("file_path",)),
            ("file_path", ("file_path",)),
            ("md5", ("md5",)),
            ("sha1", ("sha1",)),
            ("sha256", ("sha256",)),
        ):
            _append_provenance(
                provenance,
                canonical_path=f"entities.file.observations[{index}].{field_name}",
                selected_value=getattr(observation, field_name),
                parsed=parsed,
                source_path=_first_present_path(parsed.fields, aliases),
            )
    for index, observation in enumerate(alert.entities.network.observations):
        parsed = parsed_by_evidence.get(observation.evidence_path)
        if parsed is None:
            continue
        event_type = _event_type(parsed.fields)
        source_map = _network_source_paths(event_type, parsed.fields)
        source_map["event_time"] = _first_present_path(
            parsed.fields,
            ("datatime", "time"),
        )
        for field_name in (
            "event_time",
            "source_ip",
            "destination_ip",
            "src_port",
            "dst_port",
            "direction",
        ):
            _append_provenance(
                provenance,
                canonical_path=f"entities.network.observations[{index}].{field_name}",
                selected_value=getattr(observation, field_name),
                parsed=parsed,
                source_path=source_map.get(field_name),
            )


def _network_source_paths(event_type: str, fields: Mapping[str, Any]) -> dict[str, str]:
    endpoint_path = hids_endpoint_sources(fields)[0][1] if hids_endpoint_sources(fields) else "internal_ip"
    event_type_path = _first_present_path(fields, ("event_type", "datatype")) or "event_type"
    if event_type == "bounce_shell":
        return {
            "source_ip": endpoint_path,
            "destination_ip": "dst_ip",
            "dst_port": "port",
            "direction": event_type_path,
        }
    if event_type == "honeypot":
        return {
            "source_ip": "src_ip",
            "destination_ip": endpoint_path,
            "src_port": "src_port",
            "dst_port": "port",
            "direction": event_type_path,
        }
    if event_type == "malic_opera":
        return {
            "source_ip": "src_ip",
            "destination_ip": endpoint_path,
            "direction": event_type_path,
        }
    return {}


def _tree_nodes(fields: Mapping[str, Any]) -> list[dict[str, Any]]:
    tree = _first_str(fields, _TREE_ALIASES) or ""
    nodes = [{"process_name": name, "process_id": int(process_id)} for name, process_id in re.findall(r"([A-Za-z0-9_.-]+)\((\d+)\)", tree)]
    if nodes:
        return nodes
    process_chain = _first_str(fields, ("process_chain",))
    if not process_chain:
        return []
    parts = [item.strip() for item in re.split(r"\s*(?:-&gt;|->|→)\s*", process_chain) if item.strip()]
    if len(parts) == 1 and process_chain.count("-") == 1:
        parts = [item.strip() for item in process_chain.split("-") if item.strip()]
    return [{"process_name": item} for item in parts[:32]]


def _explicit_process_node(fields: Mapping[str, Any]) -> dict[str, Any] | None:
    name = _first_str(fields, _PROCESS_NAME_ALIASES)
    if not name:
        return None
    return _drop_none(
        {
            "process_name": name,
            "process_id": _intish(_first_str(fields, _PROCESS_ID_ALIASES)),
            "process_path": _first_str(fields, _PROCESS_PATH_ALIASES),
            "command_line": _process_command_line(fields),
            "username": _first_str(fields, _PROCESS_USER_ALIASES),
            "md5": _validated_digest(_first_str(fields, ("md5",)), expected_length=32),
            "sha256": _validated_digest(_first_str(fields, ("sha256",)), expected_length=64),
        }
    )


def _explicit_parent_node(fields: Mapping[str, Any]) -> dict[str, Any] | None:
    name = _first_str(fields, _PARENT_NAME_ALIASES)
    if not name:
        return None
    return _drop_none(
        {
            "process_name": name,
            "process_id": _intish(_first_str(fields, _PARENT_ID_ALIASES)),
            "process_path": _first_str(fields, _PARENT_PATH_ALIASES),
            "command_line": _first_str(fields, _PARENT_COMMAND_ALIASES),
            "username": _first_str(fields, _PARENT_USER_ALIASES),
        }
    )


def _enrich_nodes(
    nodes: list[dict[str, Any]],
    *,
    explicit: dict[str, Any] | None,
    parent: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    result = [dict(node) for node in nodes]
    for extra in (parent, explicit):
        if not extra:
            continue
        index = _matching_node_index(result, extra)
        if index is None:
            result.append(extra)
        else:
            result[index] = {**result[index], **extra}
    return result


def _matching_node_index(nodes: Sequence[Mapping[str, Any]], candidate: Mapping[str, Any]) -> int | None:
    candidate_pid = candidate.get("process_id")
    candidate_name = str(candidate.get("process_name") or "").lower()
    for index, node in enumerate(nodes):
        if candidate_pid is not None and node.get("process_id") == candidate_pid:
            return index
        if candidate_pid is None and candidate_name and str(node.get("process_name") or "").lower() == candidate_name:
            return index
    return None


def _matching_node(
    nodes: Sequence[Mapping[str, Any]],
    *,
    process_id: int | None,
    process_name: str | None,
) -> Mapping[str, Any] | None:
    index = _matching_node_index(
        nodes,
        _drop_none({"process_name": process_name, "process_id": process_id}),
    )
    return nodes[index] if index is not None else None


def _parent_for_node(
    nodes: Sequence[Mapping[str, Any]],
    selected: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    if selected is None:
        return None
    for index, node in enumerate(nodes):
        if node is selected or node == selected:
            return nodes[index - 1] if index > 0 else None
    return None


def _dedupe_nodes(nodes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, Any]] = set()
    for node in nodes:
        name = str(node.get("process_name") or "").strip()
        if not name:
            continue
        key = (name.lower(), node.get("process_id"))
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(node))
    return result


def _process_source_path(fields: Mapping[str, Any], value: str | None) -> str | None:
    if value:
        for alias in (*_PROCESS_NAME_ALIASES, *_PARENT_NAME_ALIASES):
            if _first_str(fields, (alias,)) == value:
                return alias
        for alias in (*_TREE_ALIASES, "process_chain"):
            text = _first_str(fields, (alias,))
            if text and value in text:
                return alias
    return _first_present_path(fields, (*_PROCESS_NAME_ALIASES, *_TREE_ALIASES, "process_chain"))


def _process_node_source_path(
    fields: Mapping[str, Any],
    node: Any,
    *,
    field_name: str,
) -> str | None:
    process_aliases = {
        "process_name": _PROCESS_NAME_ALIASES,
        "process_id": _PROCESS_ID_ALIASES,
        "process_path": _PROCESS_PATH_ALIASES,
        "command_line": _PROCESS_COMMAND_ALIASES,
        "username": _PROCESS_USER_ALIASES,
        "md5": ("md5",),
        "sha256": ("sha256",),
    }
    parent_aliases = {
        "process_name": _PARENT_NAME_ALIASES,
        "process_id": _PARENT_ID_ALIASES,
        "process_path": _PARENT_PATH_ALIASES,
        "command_line": _PARENT_COMMAND_ALIASES,
        "username": _PARENT_USER_ALIASES,
    }
    if _node_matches_aliases(
        fields,
        node,
        name_aliases=_PROCESS_NAME_ALIASES,
        id_aliases=_PROCESS_ID_ALIASES,
    ):
        source = _process_command_source_path(fields, node.command_line) if field_name == "command_line" else _first_present_path(fields, process_aliases.get(field_name, ()))
        if source:
            return source
    if _node_matches_aliases(
        fields,
        node,
        name_aliases=_PARENT_NAME_ALIASES,
        id_aliases=_PARENT_ID_ALIASES,
    ):
        source = _first_present_path(fields, parent_aliases.get(field_name, ()))
        if source:
            return source
    if field_name in {"process_name", "process_id"}:
        tree_source = _first_present_path(fields, (*_TREE_ALIASES, "process_chain"))
        tree_text = _first_str(fields, (*_TREE_ALIASES, "process_chain")) or ""
        if node.process_name in tree_text:
            return tree_source
    return None


def _process_command_line(fields: Mapping[str, Any]) -> str | None:
    explicit = _first_str(fields, _PROCESS_COMMAND_ALIASES)
    if explicit:
        return explicit
    event_content = _first_str(fields, ("event_content",))
    if not event_content:
        return None
    match = re.search(r"(?:其)?执行命令(?:为)?[：:]\s*(.+)$", event_content)
    if match is None:
        return None
    command = match.group(1).strip()
    return command or None


def _process_command_source_path(
    fields: Mapping[str, Any],
    value: str | None,
) -> str | None:
    if not value:
        return None
    for alias in _PROCESS_COMMAND_ALIASES:
        if _first_str(fields, (alias,)) == value:
            return alias
    if _process_command_line(fields) == value:
        return "event_content"
    return None


def _node_matches_aliases(
    fields: Mapping[str, Any],
    node: Any,
    *,
    name_aliases: Sequence[str],
    id_aliases: Sequence[str],
) -> bool:
    expected_name = _first_str(fields, name_aliases)
    expected_id = _intish(_first_str(fields, id_aliases))
    if expected_id is not None:
        return node.process_id == expected_id
    return bool(expected_name and node.process_name == expected_name)


def _source_for_value(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
    aliases: Sequence[str],
    value: str,
) -> tuple[ParsedRawMessageEvidence, str] | None:
    for parsed in parsed_messages:
        for alias in aliases:
            if value in _ip_values(parsed.fields.get(alias)):
                return parsed, alias
    return None


def _source_for_scalar_value(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
    aliases: Sequence[str],
    selected_value: Any,
    *,
    compare_basename: bool = False,
) -> tuple[ParsedRawMessageEvidence, str] | None:
    if selected_value is None:
        return None
    selected = str(selected_value).strip()
    for parsed in parsed_messages:
        for alias in aliases:
            candidate = _first_str(parsed.fields, (alias,))
            comparable = _basename(candidate) if compare_basename else candidate
            if comparable == selected:
                return parsed, alias
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
            "selection_reason": "pingan_hids_raw_message_mapping",
        }
    )


def _resolve_alert_path(alert: AlertInput, path: str) -> Any:
    current: Any = alert
    for segment in path.split("."):
        current = getattr(current, segment, None)
        if current is None:
            return None
    return current


def _first_present_path(fields: Mapping[str, Any], aliases: Sequence[str]) -> str | None:
    for alias in aliases:
        if _first_str(fields, (alias,)) is not None:
            return alias
    return None


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


def _dedupe_semantics(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return list({(str(item.get("field_path")), str(item.get("semantic_type"))): dict(item) for item in values}.values())


def _dedupe_provenance(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return list({(str(item.get("canonical_path")), str(item.get("selected_from"))): dict(item) for item in values}.values())


def _event_type(fields: Mapping[str, Any]) -> str:
    return str(fields.get("event_type") or fields.get("datatype") or "").strip().lower()


def _first_str(value: Mapping[str, Any], aliases: Sequence[str]) -> str | None:
    for alias in aliases:
        item = value.get(alias)
        if item is None or isinstance(item, bool):
            continue
        text = str(item).strip()
        if text:
            return text
    return None


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _first_ip(fields: Mapping[str, Any], aliases: Sequence[str]) -> str | None:
    for alias in aliases:
        values = _ip_values(fields.get(alias))
        if values:
            return values[0]
    return None


def _ip_values(value: Any) -> list[str]:
    candidates = value if isinstance(value, list) else re.split(r"[,;\s]+", str(value or ""))
    result: list[str] = []
    for candidate in candidates:
        try:
            normalized = str(ipaddress.ip_address(str(candidate).strip()))
        except ValueError:
            continue
        if normalized not in result:
            result.append(normalized)
    return result


def _port(value: Any) -> int | None:
    result = _intish(value)
    return result if result is not None and 1 <= result <= 65535 else None


def _intish(value: Any) -> int | None:
    try:
        return int(str(value).strip()) if value is not None else None
    except (TypeError, ValueError):
        return None


def _validated_digest(value: str | None, *, expected_length: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized if re.fullmatch(rf"[0-9A-Fa-f]{{{expected_length}}}", normalized) else None


def _basename(value: str | None) -> str | None:
    if not value:
        return None
    return PurePath(value.replace("\\", "/")).name or None


def _drop_none(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None and item != []}


__all__ = [
    "build_hids_canonical_field_provenance",
    "build_hids_file_observations",
    "build_hids_network_observations",
    "build_hids_process_observations",
    "build_hids_source_field_semantics",
    "hids_endpoint_addresses",
    "hids_endpoint_sources",
    "hids_field_importance_rules",
    "hids_primary_file",
    "hids_primary_process",
    "hids_primary_username",
]
