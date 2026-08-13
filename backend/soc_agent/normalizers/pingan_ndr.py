"""PingAn NDR/APT projections for message-first network and HTTP evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import PurePath
from typing import Any
from urllib.parse import urlsplit

from soc_agent.contracts import (
    AlertInput,
    AlertSourceType,
    EvidenceLayer,
    EvidenceTrustLevel,
    ParsedRawMessageEvidence,
)


def ndr_primary_file(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> dict[str, Any]:
    """Select one compact summary while retaining every message observation."""

    observations = build_ndr_file_observations(parsed_messages)
    if not observations:
        return {}
    item = observations[0]
    return _drop_none(
        {
            "file_name": item.get("file_name"),
            "md5": item.get("md5"),
        }
    )


def ndr_primary_http(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> dict[str, Any]:
    """Select one compact HTTP summary while retaining every observation."""

    observations = build_ndr_http_observations(parsed_messages)
    if not observations:
        return {}
    item = observations[0]
    return {
        key: value
        for key, value in item.items()
        if key
        in {
            "method",
            "host",
            "path",
            "url",
            "protocol",
            "port",
            "status_code",
            "user_agent",
            "referer",
            "x_forwarded_for",
        }
    }


def build_ndr_file_observations(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> list[dict[str, Any]]:
    """Preserve network-content metadata without claiming an endpoint file write."""

    observations: list[dict[str, Any]] = []
    for parsed in parsed_messages:
        file_name = _first_str(parsed.fields, ("file_name",))
        file_md5 = _validated_digest(
            _first_str(parsed.fields, ("file_md5",)),
            expected_length=32,
        )
        if not file_name and not file_md5:
            continue
        observations.append(
            _drop_none(
                {
                    "observation_id": f"file:{parsed.message_hash[:16]}",
                    "evidence_path": f"{parsed.source_path}#parsed",
                    "relation": "observed_artifact",
                    "event_time": _first_str(
                        parsed.fields,
                        ("access_time", "first_access_time", "write_date"),
                    )
                    or _first_str(parsed.header, ("timestamp", "event_time")),
                    "file_name": _basename(file_name) or file_name,
                    "md5": file_md5,
                }
            )
        )
    return observations


def build_ndr_http_observations(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for parsed in parsed_messages:
        projection, _ = _http_projection(
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
                    "event_time": _first_str(
                        parsed.fields,
                        ("access_time", "first_access_time", "write_date"),
                    )
                    or _first_str(parsed.header, ("timestamp", "event_time")),
                    **projection,
                }
            )
        )
    return observations


def ndr_field_importance_rules() -> list[dict[str, Any]]:
    definitions = (
        ("pingan.ndr.source_ip", ["parsed.sip"], "entities.network.source_ip", "critical"),
        ("pingan.ndr.destination_ip", ["parsed.dip"], "entities.network.destination_ip", "critical"),
        ("pingan.ndr.src_port", ["parsed.sport"], "entities.network.src_port", "high"),
        ("pingan.ndr.dst_port", ["parsed.dport"], "entities.network.dst_port", "high"),
        ("pingan.ndr.protocol", ["parsed.proto"], "entities.network.protocol", "high"),
        (
            "pingan.ndr.rule_name",
            ["parsed.rule_name", "parsed.rule_desc"],
            "detection.rule_name",
            "high",
        ),
        (
            "pingan.ndr.category",
            ["parsed.attack_type", "parsed.vuln_type"],
            "classification.category",
            "high",
        ),
        (
            "pingan.ndr.http_host",
            ["parsed.host"],
            "entities.http.observations",
            "high",
        ),
        (
            "pingan.ndr.http_path",
            ["parsed.uri", "parsed._origin.uri"],
            "entities.http.observations",
            "high",
        ),
        (
            "pingan.ndr.http_xff",
            ["parsed.x_forwarded_for", "parsed.xff", "parsed._origin.xff"],
            "entities.http.observations",
            "critical",
        ),
        (
            "pingan.ndr.file_name",
            ["parsed.file_name"],
            "entities.file.observations",
            "medium",
        ),
        (
            "pingan.ndr.file_md5",
            ["parsed.file_md5"],
            "entities.file.observations",
            "medium",
        ),
    )
    return [
        {
            "rule_id": rule_id,
            "source_patterns": source_patterns,
            "expected_target": expected_target,
            "importance": importance,
            "source_types": [AlertSourceType.NDR.value],
            "reason": f"PingAn NDR evidence should populate {expected_target}",
        }
        for rule_id, source_patterns, expected_target, importance in definitions
    ]


def build_ndr_source_field_semantics(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for parsed in parsed_messages:
        fields = parsed.fields
        base_path = f"{parsed.source_path}#parsed"
        for alias, semantic_type, meaning in (
            (
                "sip",
                "provider_reported_session_initiator",
                "reviewed_pingan_NDR_sip_is_the_upstream_reported_session_initiator_for_this_observation;_an_independent_SYN_or_PCAP_is_not_required_unless_the_alert_explicitly_marks_direction_unknown,_proxy/NAT/forwarding,_or_a_same-observation_conflict",
            ),
            (
                "dip",
                "provider_reported_session_responder",
                "reviewed_pingan_NDR_dip_is_the_upstream_reported_session_responder_for_this_observation;_this_session_role_does_not_by_itself_assign_attacker_or_victim_semantics",
            ),
        ):
            if _first_str(fields, (alias,)):
                result.append(
                    _semantic(
                        f"{base_path}.{alias}",
                        semantic_type,
                        meaning,
                        entities=True,
                    )
                )
        if _first_str(fields, ("host_md5",)):
            result.append(
                _semantic(
                    f"{base_path}.host_md5",
                    "host_identity_digest",
                    "host_identity_digest_not_file_hash",
                    entities=False,
                    reasoning=False,
                )
            )
        for alias, role in (("attack_sip", "attacker"), ("alarm_sip", "victim"), ("attacker", "attacker"), ("victim", "victim")):
            if _first_str(fields, (alias,)):
                result.append(
                    _semantic(
                        f"{base_path}.{alias}",
                        "vendor_security_role_assertion",
                        f"vendor_{role}_label_is_separate_from_observed_wire_direction",
                        entities=False,
                    )
                )
        if _first_str(fields, ("attack_result",)):
            result.append(
                _semantic(
                    f"{base_path}.attack_result",
                    "vendor_attack_result",
                    "vendor_result_code_is_not_runtime_detection_truth_or_exploit_success_proof",
                )
            )
        if _first_str(fields, ("confidence",)):
            result.append(
                _semantic(
                    f"{base_path}.confidence",
                    "vendor_uncalibrated_confidence",
                    "vendor_confidence_is_source_metadata_not_runtime_calibrated_probability",
                )
            )
        reasoning_semantics = (
            (
                "access_time",
                "sensor_observed_time",
                "sensor_observed_time_orders_network_evidence_but_does_not_establish_attack_success",
            ),
            (
                "first_access_time",
                "sensor_first_observed_time",
                "first_observed_time_orders_sensor_evidence_but_does_not_establish_attack_success",
            ),
            (
                "rule_name",
                "provider_detection_rule_name_assertion",
                "reviewed_provider_rule_name_is_a_trusted_upstream_detection_assertion_and_supports_alert_classification",
            ),
            (
                "rule_desc",
                "provider_detection_rule_description_assertion",
                "reviewed_provider_rule_description_is_a_trusted_upstream_detection_assertion_and_supports_alert_classification",
            ),
            (
                "attack_type",
                "provider_detection_classification_assertion",
                "reviewed_provider_attack_type_is_a_trusted_upstream_detection_classification_and_may_support_scenario_reasoning",
            ),
            (
                "rule_id",
                "sensor_rule_identifier",
                "sensor_rule_identifier_is_separate_from_the_platform_rule_code_namespace",
            ),
            (
                "alarm_id",
                "sensor_event_identifier",
                "sensor_event_identifier_supports_traceability_but_does_not_establish_detection_truth",
            ),
            (
                "device_ip",
                "network_sensor_identity",
                "sensor_address_identifies_the_observing_device_not_a_session_endpoint_or_impacted_asset",
            ),
            (
                "attack_chain",
                "vendor_attack_chain_code",
                "opaque_vendor_attack_chain_code_requires_a_versioned_vendor_taxonomy_lookup",
            ),
            (
                "super_attack_chain",
                "vendor_attack_chain_parent_code",
                "opaque_vendor_parent_chain_code_requires_a_versioned_vendor_taxonomy_lookup",
            ),
            (
                "type_chain",
                "vendor_detection_type_code",
                "opaque_vendor_detection_type_code_must_not_be_guessed_as_a_portable_scenario",
            ),
            (
                "kill_chain",
                "vendor_kill_chain_code",
                "opaque_vendor_kill_chain_code_requires_a_versioned_vendor_taxonomy_lookup",
            ),
            (
                "att_ck",
                "vendor_mitre_mapping",
                "vendor_MITRE_mapping_is_classification_context_not_observed_attack_progress",
            ),
            (
                "att_ck_all",
                "vendor_mitre_mapping",
                "vendor_MITRE_mapping_is_classification_context_not_observed_attack_progress",
            ),
            (
                "victim_type",
                "vendor_victim_endpoint_class",
                "client_or_server_classification_does_not_establish_attacker_or_victim_identity",
            ),
            (
                "pcap_url",
                "packet_capture_evidence_link",
                "packet_capture_link_is_an_investigation_artifact_not_an_observed_request_url",
            ),
            (
                "hit_content",
                "sensor_match_excerpt",
                "sensor_match_excerpt_is_high_value_detection_context_not_independent_outcome_proof",
            ),
            (
                "alarm_sample",
                "sensor_alarm_sample",
                "sensor_alarm_sample_is_investigation_context_not_independent_confirmation",
            ),
            (
                "vuln_desc",
                "vendor_vulnerability_description",
                "vendor_vulnerability_description_is_context_not_exploitation_proof",
            ),
            (
                "vuln_harm",
                "vendor_vulnerability_impact_description",
                "vendor_impact_description_is_context_not_observed_impact",
            ),
            (
                "solution",
                "vendor_remediation_guidance",
                "vendor_remediation_text_is_guidance_not_an_executed_response_action",
            ),
            (
                "severity",
                "vendor_event_severity",
                "vendor_severity_is_source_context_not_runtime_calibrated_confidence",
            ),
            (
                "hazard_level",
                "vendor_hazard_level",
                "vendor_hazard_level_is_source_context_not_runtime_detection_truth",
            ),
            (
                "hazard_rating",
                "vendor_hazard_rating",
                "vendor_hazard_rating_is_source_context_not_runtime_calibrated_probability",
            ),
            (
                "is_white",
                "vendor_workflow_whitelist_state",
                "vendor_whitelist_state_is_workflow_context_not_detection_truth",
            ),
            (
                "host_state",
                "provider_detection_outcome_assertion",
                "reviewed_provider_host_state_is_a_trusted_upstream_detection_outcome_and_may_support_effect_stage_reasoning",
            ),
            (
                "rule_state",
                "vendor_rule_workflow_state",
                "vendor_rule_state_is_workflow_context_not_detection_truth",
            ),
            (
                "sip_group",
                "source_asset_group_context",
                "source_group_is_ownership_context_not_network_direction_evidence",
            ),
            (
                "dip_group",
                "destination_asset_group_context",
                "destination_group_is_ownership_context_not_victim_confirmation",
            ),
            (
                "asset_group",
                "vendor_asset_group_context",
                "asset_group_may_support_ownership_lookup_but_is_not_event_actor_identity",
            ),
            (
                "serial_num",
                "network_sensor_identifier",
                "sensor_identifier_supports_traceability_but_is_not_a_session_endpoint",
            ),
            (
                "skyeye_serial_num",
                "network_sensor_identifier",
                "sensor_identifier_supports_traceability_but_is_not_a_session_endpoint",
            ),
            (
                "alarm_source",
                "vendor_alarm_source",
                "alarm_source_describes_sensor_provenance_not_attack_origin",
            ),
            (
                "vlan_id",
                "network_segment_identifier",
                "VLAN_identifier_is_network_context_not_asset_ownership_or_attack_direction",
            ),
            (
                "vxlan_id",
                "network_segment_identifier",
                "VXLAN_identifier_is_network_context_not_asset_ownership_or_attack_direction",
            ),
            (
                "src_mac",
                "observed_source_mac",
                "source_MAC_is_session_context_and_requires_asset_resolution_before_attribution",
            ),
            (
                "dst_mac",
                "observed_destination_mac",
                "destination_MAC_is_session_context_and_does_not_confirm_victim_identity",
            ),
            (
                "sip_addr",
                "source_address_enrichment",
                "source_address_enrichment_is_provider_context_not_observed_network_direction",
            ),
            (
                "dip_addr",
                "destination_address_enrichment",
                "destination_address_enrichment_is_provider_context_not_victim_confirmation",
            ),
            (
                "attack_addr",
                "vendor_attack_address_enrichment",
                "attack_address_enrichment_is_provider_context_not_attacker_identity_proof",
            ),
            (
                "attack_org",
                "vendor_attack_organization_enrichment",
                "organization_enrichment_is_provider_context_not_actor_attribution_proof",
            ),
            (
                "device_area",
                "sensor_location_context",
                "sensor_location_describes_collection_context_not_attack_location",
            ),
            (
                "attack_method",
                "vendor_attack_method",
                "vendor_attack_method_is_classification_context_not_observed_success",
            ),
            (
                "attack_flag",
                "vendor_attack_flag",
                "vendor_attack_flag_requires_source_specific_interpretation_and_is_not_runtime_truth",
            ),
            (
                "attack_type_all",
                "vendor_attack_taxonomy_set",
                "vendor_attack_taxonomy_set_is_context_not_observed_attack_progress",
            ),
            (
                "kill_chain_all",
                "vendor_kill_chain_set",
                "vendor_kill_chain_set_is_context_not_observed_attack_progress",
            ),
            (
                "webrules_tag",
                "vendor_web_detection_tags",
                "vendor_web_detection_tags_are_context_not_independent_confirmation",
            ),
            (
                "packet_data",
                "bounded_packet_excerpt",
                "packet_excerpt_is_high_value_raw_evidence_and_must_remain_bounded",
            ),
            (
                "parameter",
                "observed_request_parameter",
                "request_parameter_is_high_value_HTTP_context_not_execution_proof",
            ),
            (
                "api",
                "observed_HTTP_endpoint",
                "API_URL_is_observed_request_context_not_exploitation_success_proof",
            ),
            (
                "detail_info",
                "vendor_detection_detail",
                "vendor_detection_detail_is_high_value_investigation_context_not_independent_confirmation",
            ),
            (
                "description",
                "vendor_detection_description",
                "vendor_description_is_investigation_context_not_independent_confirmation",
            ),
            (
                "bulletin",
                "vendor_security_bulletin",
                "vendor_bulletin_is_reference_context_not_observed_exploitation_proof",
            ),
            (
                "vuln_name",
                "vendor_vulnerability_name",
                "vendor_vulnerability_name_is_classification_context_not_exploitation_proof",
            ),
            (
                "vuln_type",
                "vendor_vulnerability_type",
                "vendor_vulnerability_type_is_scenario_context_not_exploitation_proof",
            ),
            (
                "malicious_family",
                "vendor_malware_family",
                "vendor_malware_family_is_a_source_assertion_requiring_evidence_corroboration",
            ),
            (
                "hit_field",
                "sensor_match_field",
                "sensor_match_field_identifies_where_the_rule_matched_not_whether_the_attack_succeeded",
            ),
            (
                "is_web_attack",
                "vendor_web_attack_flag",
                "vendor_web_attack_flag_is_scenario_context_not_runtime_detection_truth",
            ),
            (
                "repeat_count",
                "sensor_aggregation_count",
                "sensor_repeat_count_describes_aggregation_volume_not_distinct_attack_successes",
            ),
            (
                "rsp_status",
                "observed_HTTP_response_status",
                "HTTP_response_status_is_transaction_evidence_not_exploitation_success_by_itself",
            ),
            (
                "packet_size",
                "observed_packet_size",
                "packet_size_is_network_context_not_attack_outcome",
            ),
            (
                "appid",
                "vendor_application_protocol_identifier",
                "vendor_application_identifier_requires_source_taxonomy_context",
            ),
            (
                "protocol_id",
                "vendor_protocol_identifier",
                "vendor_protocol_identifier_requires_source_taxonomy_context",
            ),
            (
                "tproto",
                "vendor_transport_protocol",
                "vendor_transport_protocol_is_observed_network_context",
            ),
            (
                "dns_type",
                "observed_DNS_record_type",
                "DNS_record_type_is_transaction_context_not_detection_truth",
            ),
            (
                "code_language",
                "vendor_code_language_classification",
                "code_language_is_request_or_payload_context_not_execution_proof",
            ),
            (
                "site_app",
                "vendor_web_application_classification",
                "web_application_classification_is_context_not_exploitation_proof",
            ),
            (
                "req_header",
                "observed_HTTP_request_headers",
                "request_headers_are_sensitive_bounded_transaction_evidence",
            ),
            (
                "rsp_header",
                "observed_HTTP_response_headers",
                "response_headers_are_sensitive_bounded_transaction_evidence",
            ),
            (
                "req_body",
                "observed_HTTP_request_body",
                "request_body_is_sensitive_bounded_transaction_evidence_not_execution_proof",
            ),
            (
                "rsp_body",
                "observed_HTTP_response_body",
                "response_body_is_sensitive_bounded_transaction_evidence_not_success_proof",
            ),
            (
                "rule_labels",
                "provider_detection_rule_label_assertion",
                "reviewed_provider_rule_labels_are_trusted_upstream_detection_classification_evidence",
            ),
            (
                "cookie",
                "observed_HTTP_cookie",
                "HTTP_cookie_is_sensitive_session_context_not_actor_identity_proof",
            ),
            (
                "rsp_body_len",
                "observed_HTTP_response_body_length",
                "response_body_length_is_transaction_context_not_attack_outcome",
            ),
            (
                "rsp_content_length",
                "observed_HTTP_response_content_length",
                "response_content_length_is_transaction_context_not_attack_outcome",
            ),
            (
                "rsp_content_type",
                "observed_HTTP_response_content_type",
                "response_content_type_is_transaction_context_not_attack_outcome",
            ),
            (
                "write_date",
                "sensor_record_time",
                "sensor_record_time_supports_timeline_ordering_not_attack_success",
            ),
            (
                "update_time",
                "sensor_record_update_time",
                "sensor_update_time_is_record_lifecycle_context_not_event_occurrence_time",
            ),
            (
                "dolog_count",
                "sensor_log_count",
                "sensor_log_count_is_aggregation_context_not_distinct_attack_count",
            ),
            (
                "victim_ip",
                "vendor_victim_address_assertion",
                "vendor_victim_address_is_a_role_assertion_separate_from_wire_direction",
            ),
            (
                "xml_confidence",
                "vendor_uncalibrated_confidence",
                "vendor_XML_confidence_is_source_metadata_not_runtime_calibrated_probability",
            ),
            (
                "rule_version",
                "sensor_rule_version",
                "sensor_rule_version_supports_detection_traceability_and_drift_review",
            ),
            (
                "rule_version_str",
                "sensor_rule_version",
                "sensor_rule_version_supports_detection_traceability_and_drift_review",
            ),
            (
                "sig_id",
                "sensor_signature_identifier",
                "sensor_signature_identifier_supports_detection_traceability_not_detection_truth",
            ),
            (
                "public_date",
                "sensor_rule_publication_time",
                "rule_publication_time_is_detection_metadata_not_event_occurrence_time",
            ),
        )
        for alias, semantic_type, meaning in reasoning_semantics:
            if not _has_value(fields.get(alias)):
                continue
            result.append(
                _semantic(
                    f"{base_path}.{alias}",
                    semantic_type,
                    meaning,
                )
            )
        nested_semantics = {alias: (semantic_type, meaning) for alias, semantic_type, meaning in reasoning_semantics}
        nested_semantics.update(
            {
                "sip": (
                    "observed_network_source",
                    "nested_source_address_is_wire_evidence_within_its_raw_message_observation",
                ),
                "dip": (
                    "observed_network_destination",
                    "nested_destination_address_is_wire_evidence_within_its_raw_message_observation",
                ),
                "sport": (
                    "observed_network_source_port",
                    "nested_source_port_is_wire_evidence_within_its_raw_message_observation",
                ),
                "dport": (
                    "observed_network_destination_port",
                    "nested_destination_port_is_wire_evidence_within_its_raw_message_observation",
                ),
                "proto": (
                    "observed_network_protocol",
                    "nested_protocol_is_wire_evidence_within_its_raw_message_observation",
                ),
                "xff": (
                    "observed_forwarded_address_chain",
                    "nested_forwarded_chain_is_HTTP_evidence_not_automatic_attacker_identity",
                ),
            }
        )
        excluded_semantics = {
            "branch_id": (
                "source_routing_identifier",
                "source_branch_identifier_is_traceability_metadata_not_analysis_evidence",
            ),
            "dimension": (
                "opaque_vendor_dimension",
                "opaque_vendor_dimension_requires_a_versioned_taxonomy_before_reasoning",
            ),
            "hit_start": (
                "sensor_match_offset",
                "match_start_offset_is_parser_metadata_not_security_evidence",
            ),
            "hit_end": (
                "sensor_match_offset",
                "match_end_offset_is_parser_metadata_not_security_evidence",
            ),
            "is_delete": (
                "sensor_record_lifecycle_state",
                "delete_state_is_record_lifecycle_metadata_not_response_or_attack_outcome",
            ),
            "host_md5": (
                "host_identity_digest",
                "host_identity_digest_not_file_hash",
            ),
            "nid": (
                "sensor_internal_identifier",
                "sensor_internal_identifier_is_traceability_metadata_not_network_or_role_evidence",
            ),
            "rule_key": (
                "sensor_rule_storage_key",
                "sensor_rule_storage_key_is_traceability_metadata_not_a_portable_rule_identifier",
            ),
            "sip_ioc_dip": (
                "vendor_composite_correlation_key",
                "opaque_composite_key_must_not_be_parsed_into_network_or_role_facts",
            ),
            "skyeye_type": (
                "opaque_vendor_event_type",
                "opaque_vendor_event_type_requires_a_versioned_taxonomy_before_reasoning",
            ),
            "super_type": (
                "opaque_vendor_parent_type",
                "opaque_vendor_parent_type_requires_a_versioned_taxonomy_before_reasoning",
            ),
            "type": (
                "opaque_vendor_record_type",
                "opaque_vendor_record_type_requires_a_versioned_taxonomy_before_reasoning",
            ),
        }
        for alias, (semantic_type, meaning) in excluded_semantics.items():
            if not _has_value(fields.get(alias)):
                continue
            result.append(
                _semantic(
                    f"{base_path}.{alias}",
                    semantic_type,
                    meaning,
                    reasoning=False,
                )
            )
        for relative_path, value in _flatten_leaf_paths(fields):
            if "." not in relative_path or not _has_value(value):
                continue
            alias = re.sub(r"\[\d+\]$", "", relative_path.rsplit(".", maxsplit=1)[-1])
            definition = nested_semantics.get(alias)
            reasoning = True
            if definition is None:
                definition = excluded_semantics.get(alias)
                reasoning = False
            if definition is None:
                continue
            semantic_type, meaning = definition
            result.append(
                _semantic(
                    f"{base_path}.{relative_path}",
                    semantic_type,
                    meaning,
                    reasoning=reasoning,
                )
            )
        if _has_value(parsed.decoded_fields.get("rule_labels")):
            result.append(
                _semantic(
                    f"{base_path.replace('#parsed', '#decoded')}.rule_labels",
                    "provider_detection_rule_label_assertion",
                    "decoded_reviewed_provider_rule_labels_are_trusted_upstream_detection_classification_evidence",
                )
            )
        if _first_str(fields, ("ioc",)):
            result.append(
                _semantic(
                    f"{base_path}.ioc",
                    "vendor_detection_descriptor",
                    "vendor_ioc_field_contains_rule_or_detection_descriptors_not_a_typed_indicator",
                    entities=False,
                )
            )
        if _first_str(fields, ("file_name", "file_md5")):
            result.append(
                _semantic(
                    base_path,
                    "network_transaction_file_metadata",
                    "file_metadata_describes_observed_network_content_not_an_endpoint_file_write",
                    entities=True,
                )
            )
    return _dedupe(result)


def build_ndr_canonical_field_provenance(
    alert: AlertInput,
    *,
    parsed_messages: Sequence[ParsedRawMessageEvidence],
    primary_parsed: ParsedRawMessageEvidence | None,
) -> list[dict[str, Any]]:
    provenance: list[dict[str, Any]] = []
    if primary_parsed is not None:
        fields = primary_parsed.fields
        network_sources = {
            "source_ip": _first_present_path(fields, ("sip", "source_ip", "src_addr")),
            "destination_ip": _first_present_path(fields, ("dip", "dst_addr")),
            "src_port": _first_present_path(fields, ("sport", "src_port")),
            "dst_port": _first_present_path(fields, ("dport", "dst_port")),
            "protocol": _first_present_path(fields, ("proto", "protocol")),
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
        http_projection, http_sources = _http_projection(
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
        if http_projection.get("host"):
            _append_provenance(
                provenance,
                canonical_path="entities.network.domain",
                selected_value=alert.entities.network.domain,
                parsed=primary_parsed,
                source_path=http_sources.get("host"),
            )
        direct_sources = {
            "event.event_id": _first_present_path(fields, ("alarm_id",)),
            "event.event_time": _first_present_path(
                fields,
                ("access_time", "first_access_time", "write_date"),
            ),
            "classification.severity": _first_present_path(
                fields,
                ("severity", "hazard_rating", "hazard_level"),
            ),
            "detection.rule_name": _first_present_path(
                fields,
                (
                    "finding__title",
                    "str_title",
                    "rule_name",
                    "alert_describe",
                    "hit_rule_name",
                    "hit_rule_names",
                    "rule",
                ),
            ),
            "detection.rule_category": _first_present_path(fields, ("attack_type", "vuln_type")),
            "classification.category": _first_present_path(fields, ("attack_type", "vuln_type")),
            "entities.file.file_name": _first_present_path(fields, ("file_name",)),
            "entities.file.md5": _first_present_path(fields, ("file_md5",)),
        }
        for canonical_path, source_path in direct_sources.items():
            _append_provenance(
                provenance,
                canonical_path=canonical_path,
                selected_value=_resolve_alert_path(alert, canonical_path),
                parsed=primary_parsed,
                source_path=source_path,
            )
        _append_mitre_provenance(provenance, alert, primary_parsed)
    for canonical_path, selected_value, aliases, compare_basename in (
        (
            "entities.file.file_name",
            alert.entities.file.file_name,
            ("file_name",),
            True,
        ),
        ("entities.file.md5", alert.entities.file.md5, ("file_md5",), False),
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
    for index, observation in enumerate(alert.entities.network.observations):
        parsed = parsed_by_evidence.get(observation.evidence_path)
        if parsed is None:
            continue
        sources = {
            "event_time": _first_present_path(
                parsed.fields,
                ("access_time", "first_access_time", "write_date"),
            ),
            "source_ip": _first_present_path(parsed.fields, ("sip", "source_ip", "src_addr")),
            "destination_ip": _first_present_path(parsed.fields, ("dip", "dst_addr")),
            "src_port": _first_present_path(parsed.fields, ("sport", "src_port")),
            "dst_port": _first_present_path(parsed.fields, ("dport", "dst_port")),
            "protocol": _first_present_path(parsed.fields, ("proto", "protocol")),
            "application_protocol": _first_present_path(parsed.fields, ("app_proto",)),
            "direction": _first_present_path(parsed.fields, ("direction",)),
        }
        for field_name, source_path in sources.items():
            _append_provenance(
                provenance,
                canonical_path=f"entities.network.observations[{index}].{field_name}",
                selected_value=getattr(observation, field_name),
                parsed=parsed,
                source_path=source_path,
            )
    for index, observation in enumerate(alert.entities.http.observations):
        parsed = parsed_by_evidence.get(observation.evidence_path)
        if parsed is None:
            continue
        _, sources = _http_projection(
            parsed.fields,
            decoded_fields=parsed.decoded_fields,
        )
        sources["event_time"] = _first_present_path(
            parsed.fields,
            ("access_time", "first_access_time", "write_date"),
        )
        for field_name, source_path in sources.items():
            _append_provenance(
                provenance,
                canonical_path=f"entities.http.observations[{index}].{field_name}",
                selected_value=getattr(observation, field_name),
                parsed=parsed,
                source_path=source_path,
            )
    for index, observation in enumerate(alert.entities.file.observations):
        parsed = parsed_by_evidence.get(observation.evidence_path)
        if parsed is None:
            continue
        for field_name, alias in (
            ("event_time", "access_time"),
            ("file_name", "file_name"),
            ("md5", "file_md5"),
        ):
            _append_provenance(
                provenance,
                canonical_path=f"entities.file.observations[{index}].{field_name}",
                selected_value=getattr(observation, field_name),
                parsed=parsed,
                source_path=_first_present_path(parsed.fields, (alias,)),
            )


def _append_mitre_provenance(
    provenance: list[dict[str, Any]],
    alert: AlertInput,
    parsed: ParsedRawMessageEvidence,
) -> None:
    fields = parsed.fields
    for canonical_name, values, prefix in (
        ("tactic", alert.classification.tactic, "TA"),
        ("technique", alert.classification.technique, "T"),
    ):
        for index, value in enumerate(values):
            source_path = next(
                (alias for alias in ("att_ck", "att_ck_all") if value in (_first_str(fields, (alias,)) or "")),
                None,
            )
            if source_path is None or not value.startswith(prefix):
                continue
            _append_provenance(
                provenance,
                canonical_path=f"classification.{canonical_name}[{index}]",
                selected_value=value,
                parsed=parsed,
                source_path=source_path,
            )


def _http_projection(
    fields: Mapping[str, Any],
    *,
    decoded_fields: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    origin = _as_dict(fields.get("_origin"))
    decoded_payload = _as_dict(decoded_fields.get("payload"))
    decoded_request = _as_dict(decoded_payload.get("req_header"))
    if not decoded_request:
        decoded_request = _as_dict(decoded_fields.get("req_header"))
    headers = _as_dict(decoded_request.get("headers"))
    start_line = _first_str(decoded_request, ("start_line",))
    method, request_path = _parse_request_line(start_line)

    direct_method = _first_str(fields, ("method",))
    direct_path = _first_str(fields, ("uri",))
    origin_path = _first_str(origin, ("uri",))
    api_url = _first_str(fields, ("api",))
    api_projection = _url_projection(api_url)
    host = _first_str(fields, ("host",)) or _first_header(headers, "host") or api_projection.get("host")
    xff = _first_str(fields, ("x_forwarded_for", "xff")) or _first_str(origin, ("xff",)) or _first_forwarded(decoded_request.get("forwarded_chain"))
    values = {
        "method": direct_method or method,
        "host": host,
        "path": direct_path or origin_path or request_path or api_projection.get("path"),
        "url": direct_path or origin_path or request_path or api_url,
        "protocol": api_projection.get("protocol"),
        "port": api_projection.get("port"),
        "status_code": _intish(_first_str(fields, ("rsp_status",)) or _first_str(origin, ("rsp_status",))),
        "user_agent": _first_str(fields, ("agent",)) or _first_header(headers, "user-agent"),
        "referer": _first_str(fields, ("referer",)) or _first_header(headers, "referer"),
        "x_forwarded_for": _first_forwarded(xff),
    }
    sources = {
        "method": "method" if direct_method else "decoded.payload.req_header.start_line",
        "host": ("host" if _first_str(fields, ("host",)) else ("decoded.payload.req_header.headers.host" if _first_header(headers, "host") else "api")),
        "path": ("uri" if direct_path else ("_origin.uri" if origin_path else ("decoded.payload.req_header.start_line" if request_path else "api"))),
        "url": ("uri" if direct_path else ("_origin.uri" if origin_path else ("decoded.payload.req_header.start_line" if request_path else "api"))),
        "protocol": "api",
        "port": "api",
        "status_code": "rsp_status" if _first_str(fields, ("rsp_status",)) else "_origin.rsp_status",
        "user_agent": "agent" if _first_str(fields, ("agent",)) else "decoded.payload.req_header.headers.user-agent",
        "referer": "referer" if _first_str(fields, ("referer",)) else "decoded.payload.req_header.headers.referer",
        "x_forwarded_for": ("x_forwarded_for" if _first_str(fields, ("x_forwarded_for",)) else ("xff" if _first_str(fields, ("xff",)) else ("_origin.xff" if _first_str(origin, ("xff",)) else "decoded.payload.req_header.forwarded_chain"))),
    }
    projection = _drop_none(values)
    return projection, {key: value for key, value in sources.items() if key in projection}


def _url_projection(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return {"path": value}
    host = parsed.hostname
    if host and any(character.isspace() or character in ";/" for character in host):
        host = None
    path = parsed.path or None
    if path and parsed.query:
        path = f"{path}?{parsed.query}"
    return _drop_none(
        {
            "protocol": parsed.scheme or None,
            "host": host,
            "path": path or value,
            "port": port,
        }
    )


def _parse_request_line(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    match = re.match(r"^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(\S+)", value, re.IGNORECASE)
    return (match.group(1).upper(), match.group(2)) if match else (None, None)


def _first_header(headers: Mapping[str, Any], name: str) -> str | None:
    for key, value in headers.items():
        if str(key).strip().lower() != name.lower():
            continue
        if isinstance(value, list):
            return next((str(item).strip() for item in value if str(item).strip()), None)
        text = str(value).strip()
        return text or None
    return None


def _first_forwarded(value: Any) -> str | None:
    if isinstance(value, list):
        value = next((item for item in value if str(item).strip()), None)
    if value is None:
        return None
    return str(value).split(",", 1)[0].strip() or None


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
            if candidate is None:
                continue
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
    suffix = source_path if source_path.startswith(("decoded.", "repaired.")) else f"parsed.{source_path}"
    target.append(
        {
            "canonical_path": canonical_path,
            "selected_value": str(selected_value),
            "selected_from": f"{parsed.source_path}#{suffix}",
            "source_layer": EvidenceLayer.RAW_MESSAGE.value,
            "trust_level": EvidenceTrustLevel.HIGH.value,
            "selection_reason": "pingan_ndr_raw_message_mapping",
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
        value: Any = fields
        for segment in alias.split("."):
            if not isinstance(value, Mapping):
                value = None
                break
            value = value.get(segment)
        if _has_value(value):
            return alias
    return None


def _first_str(value: Mapping[str, Any], aliases: Sequence[str]) -> str | None:
    for alias in aliases:
        item = value.get(alias)
        if item is None or isinstance(item, bool):
            continue
        text = str(item).strip()
        if text:
            return text
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


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


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _flatten_leaf_paths(value: Any, path: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, Mapping):
        result: list[tuple[str, Any]] = []
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            result.extend(_flatten_leaf_paths(item, child))
        return result
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value):
            result.extend(_flatten_leaf_paths(item, f"{path}[{index}]"))
        return result
    return [(path, value)] if path else []


def _drop_none(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None and item != []}


def _dedupe(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return list({(str(item.get("field_path")), str(item.get("semantic_type"))): dict(item) for item in values}.values())


def _dedupe_provenance(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return list({(str(item.get("canonical_path")), str(item.get("selected_from"))): dict(item) for item in values}.values())


__all__ = [
    "build_ndr_canonical_field_provenance",
    "build_ndr_file_observations",
    "build_ndr_http_observations",
    "build_ndr_source_field_semantics",
    "ndr_field_importance_rules",
    "ndr_primary_file",
]
