"""Separate reviewed detection metadata from visible event-field candidates.

This is a source-role contract, not an engine field catalog or a filter evaluator.
Roles below come from the reviewed legacy source snapshot; field-name similarity,
the model's opinion, or presence in a raw log never establishes a role. Unclassified
visible fields remain candidates with engine availability explicitly unverified.
"""

import copy

POLICY_VERSION = "event-fields-not-detection-results-v1"
FIELD_USAGE_POLICY_VERSION = POLICY_VERSION
_SCHEMA = "my_workflows/zeus/flows/alert_schema.py"
_TOPIC_MAP_SOURCE = "my_workflows/zeus/utils/attack_chain_data_preparer.py:32-41"

# Exact topic/field roles reviewed against validation/sec_know_model.zip. The
# deployable module deliberately does not import or load that validation source.
# Each tuple is (field, schema line, source description, additional evidence).
_HIDS_FIELDS = (
    ("detail_hit_rule_name", 751, "命中的青藤云的规则名", None),
    ("detail_rule", 763, "触发的告警规则", None),
    # HIDS message keys retain dots; parse_message_to_dict explicitly changes
    # dots to underscores. These two named origins are reviewed equivalents,
    # not a general dot/underscore alias-normalization rule.
    (
        "detail.hit_rule_name",
        751,
        "命中的青藤云的规则名",
        "my_workflows/zeus/tools/pingan_msgs_tools.py:332-339",
    ),
    (
        "detail.rule",
        763,
        "触发的告警规则",
        "my_workflows/zeus/tools/pingan_msgs_tools.py:332-339",
    ),
)
_APT_FIELDS = (
    ("rule_name", 307, "规则名词", None),
    ("rule_id", 312, "规则ID", None),
)
_REVIEWED_RESULT_FIELDS = {
    "security_qthids": _HIDS_FIELDS,
    "security-qthids-stg": _HIDS_FIELDS,
    "sec_guard_apt": _APT_FIELDS,
    "sec_guard_apt_detail": _APT_FIELDS,
    "leagsoft-edr": (("str_rule_id", 549, "规则ID", None),),
    "edr-core-xc": (
        ("rule_name", 651, "规则名称", None),
        ("rule_id", 658, "规则ID", None),
        ("rule_desc", 662, "规则描述", None),
    ),
    "ptp-nids": (
        ("alert_rule", 892, "规则内容", None),
        ("alert_signature", 899, "告警名称", None),
        ("alert_signature_id", 901, "规则ID", None),
    ),
}


def _evidence(topic, role):
    field, line, description, normalization_source = role
    result = {
        "topic": topic,
        "declared_field": field,
        "source": f"{_SCHEMA}:{line}",
        "source_description": description,
        "topic_mapping_source": _TOPIC_MAP_SOURCE,
    }
    if normalization_source:
        result["normalization_source"] = normalization_source
    return result


def build_field_usage(rule, visible_fields):
    """Build a deterministic contract from trusted source roles and actual fields.

    Original rule conditions do not define the entire set of possible event
    fields; an unfamiliar but visible field is therefore not rejected simply
    because the original rule has never used it. Conversely, an old condition
    cannot override a reviewed detection-metadata role.
    """
    if not isinstance(rule, dict) or not isinstance(rule.get("topic"), str):
        raise ValueError("field_usage_topic_required")
    topic = rule["topic"]
    if not topic:
        raise ValueError("field_usage_topic_required")
    if not isinstance(visible_fields, (list, tuple, set, frozenset)) or any(
        not isinstance(field, str) or not field for field in visible_fields
    ):
        raise ValueError("field_usage_visible_fields_invalid")
    fields = sorted(set(visible_fields))
    roles = _REVIEWED_RESULT_FIELDS.get(topic, ())
    # Only a parent that actually contains a visible metadata path is excluded.
    # A heterogeneous log's plain "detail" object is not rejected because a
    # different log schema could theoretically place metadata beneath it.
    visible_metadata = {
        field
        for field in fields
        if any(field == role[0] or field.startswith(role[0] + ".") for role in roles)
    }
    context_only = []
    candidates = []
    for field in fields:
        exact = [role for role in roles if field == role[0]]
        inside = [role for role in roles if field.startswith(role[0] + ".")]
        containing = [
            role
            for role in roles
            if role[0].startswith(field + ".")
            and any(
                path == role[0] or path.startswith(role[0] + ".")
                for path in visible_metadata
            )
        ]
        applicable = exact or inside or containing
        if not applicable:
            candidates.append(field)
            continue
        context_only.append(
            {
                "name": field,
                "role": "detection_result_metadata",
                "relation": "exact"
                if exact
                else "within_metadata"
                if inside
                else "contains_metadata",
                "evidence": [_evidence(topic, role) for role in applicable],
            }
        )
    return {
        "field_usage_policy_version": FIELD_USAGE_POLICY_VERSION,
        "topic": topic,
        "candidate_field_names": candidates,
        "context_only_fields": context_only,
        "candidate_engine_availability_verified": False,
        "classification_scope": "reviewed_source_roles_only",
        "unclassified_fields_are_verified_event_fields": False,
    }


def validate_field_usage(contract, rule, visible_fields):
    """Reject a forged or stale frozen contract instead of accepting its lists."""
    expected = build_field_usage(rule, visible_fields)
    if not isinstance(contract, dict) or contract != expected:
        raise ValueError("field_usage_contract_mismatch")
    return expected


def issue_for_field(field, contract):
    """Describe an ineligible known field; visibility checks belong to the caller.

    Callers must create the contract with build_field_usage, or validate a frozen
    contract with validate_field_usage. Neither model output nor an optional rule
    attribute may supply an alternative role map.
    """
    if (
        not isinstance(contract, dict)
        or contract.get("field_usage_policy_version") != FIELD_USAGE_POLICY_VERSION
    ):
        raise ValueError("field_usage_policy_version_unsupported")
    for item in contract["context_only_fields"]:
        if item["name"] == field:
            return {
                "code": "condition_uses_detection_metadata",
                "field": field,
                "reason": item["relation"],
                "evidence": copy.deepcopy(item["evidence"]),
            }
    return None
