"""Source-bound HIDS command facts; never execute commands or fabricate flows."""

from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic import ValidationError

from soc_agent.contracts import AlertInput, CanonicalFieldProvenance, ParsedRawMessageEvidence
from soc_agent.contracts.normalization import DetectionIdentifier, DetectionObservationRef, ProcessBehaviorDescriptor, SupplementaryFactRef
from soc_agent.normalizers.detection_identity import resolve_detection_identity
from soc_agent.normalizers.pingan_hids import _event_type, hids_detection_rule_name
from soc_agent.utils.hashing import stable_hash

_BASH = re.compile(r"(?:/[^\s'\"]+/)?bash[ \t]+-c[ \t]+(.+)")
_REDIRECTION = re.compile(r"exec[ \t]+[0-9]{1,3}<>[ \t]*/dev/tcp/([0-9a-fA-F:.]+)/([0-9]{1,5})")


def _literal_target(command: str) -> ProcessBehaviorDescriptor | None:
    # Accept the exact bounded command idiom, including sensor display strings
    # which omit argv quotes. Strings, compound scripts and expansions stay gaps.
    shell = _BASH.fullmatch(command.strip())
    if shell is None:
        return None
    body = shell[1]
    if len(body) >= 2 and body[0] in {"'", '"'} and body[-1] == body[0]:
        body = body[1:-1]
    redirection = _REDIRECTION.fullmatch(body)
    if redirection is None:
        return None
    try:
        return ProcessBehaviorDescriptor(kind="shell_tcp_connection_target", protocol="tcp", destination_ip=redirection[1], dst_port=int(redirection[2]))
    except ValidationError:
        return None


def complete_hids_process_facts(alert: AlertInput, parsed_messages: Sequence[ParsedRawMessageEvidence]) -> AlertInput:
    """Complete a new Adapter snapshot before review; old saved inputs are untouched."""
    parsed_by_source = {item.source_path: item for item in parsed_messages}
    process = alert.entities.process
    provenance = alert.extensions.setdefault("canonical_field_provenance", [])
    for index, observation in enumerate(process.observations):
        source = observation.evidence_path.split("#", 1)[0]
        parsed = parsed_by_source.get(source)
        binding = observation.subject_binding
        if parsed is None or binding is None:
            continue
        ref = f"entities.process.observations[{index}]"
        # This source-level boundary survives an ambiguous process binding.
        # Ambiguity is never permission to turn a command into observed traffic.
        has_command_target = any("/dev/tcp/" in (node.command_line or "") for node in observation.nodes)
        has_network = any(item.evidence_path.split("#", 1)[0] == source for item in alert.entities.network.observations)
        if _event_type(parsed.fields) == "web_command" and observation.field_sources and has_command_target and not has_network:
            alert.extensions.setdefault("source_field_semantics", []).insert(
                0,
                {
                    "field_path": source + "#parsed",
                    "semantic_type": "process_command_only_network_context",
                    "meaning": "network_targets_in_this_process_command_record_are_command_intent_not_observed_wire_traffic",
                    "participates_in_entities": True,
                    "participates_in_reasoning": True,
                },
            )
        if binding.status != "resolved":
            continue
        node_index = binding.node_index
        if node_index is None or node_index >= len(observation.nodes):
            continue
        node = observation.nodes[node_index]
        subject = f"{ref}.nodes[{node_index}]"
        if parsed_messages and source == parsed_messages[0].source_path:
            process.primary_ref = subject

        rule = hids_detection_rule_name(parsed.fields)
        if rule:
            declared = [DetectionIdentifier.model_validate(value) for value in alert.extensions.get("source_detection_identifiers", {}).get(source, [])]
            detection = DetectionObservationRef(
                observation_id="HIDS-" + stable_hash([source, subject, rule])[:16],
                evidence_path=source + "#parsed",
                event_scope_id=source,
                kind="process_execution",
                subject_refs=[subject],
                name=rule,
                **resolve_detection_identity([], declared, None),
            )
            alert.entities.detections.append(detection)

        command = node.command_line or ""
        if "/dev/tcp/" not in command:
            continue
        command_source = observation.field_sources.get(f"nodes[{node_index}].command_line")
        target = _literal_target(command) if command_source else None
        if target is None:
            observation.subject_binding = binding.model_copy(update={"gaps": list(dict.fromkeys([*binding.gaps, "command_target_not_proven"]))})
            continue
        fact = SupplementaryFactRef(
            observation_id="HIDS-TARGET-" + stable_hash([source, subject, target.model_dump()])[:16],
            evidence_path=command_source,
            event_scope_id=source,
            subject_ref=subject,
            name="process_command_target",
            value=command,
            meaning="命令中的 TCP 连接目标；不证明连接成功、实际流量方向或反弹 Shell 成立。",
            process_behavior=target,
            process_behavior_verification="source_bound_v1",
        )
        fact_index = len(alert.entities.supplementary_facts)
        alert.entities.supplementary_facts.append(fact)
        for key, value in target.model_dump().items():
            provenance.append(
                CanonicalFieldProvenance(
                    canonical_path=f"entities.supplementary_facts[{fact_index}].process_behavior.{key}",
                    selected_value=str(value),
                    selected_from=command_source,
                    source_layer="raw_message",
                    trust_level="high",
                    selection_reason="pingan_hids_literal_command_target",
                ).model_dump(mode="json")
            )
    return alert
