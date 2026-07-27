"""Typed projections for PingAn trusted structured SIEM/model alerts."""

from __future__ import annotations

import ast
import hashlib
import ipaddress
import json
from collections.abc import Mapping, Sequence
from typing import Any

from soc_agent.contracts import (
    AlertInput,
    AlertSourceType,
    EvidenceLayer,
    EvidenceTrustLevel,
    ScenarioSignal,
)

_SUSPICIOUS_EMAIL = "suspicious_email"
_STANDARD_MACHINE_COPY = "standard_machine_copy"
_MAX_LITERAL_CHARS = 100_000


def build_siem_entities(
    fields: Mapping[str, Any],
    *,
    evidence_path: str,
) -> dict[str, Any]:
    """Project only fields whose subtype semantics are explicitly known."""

    subtype = siem_subtype(fields)
    if subtype == _SUSPICIOUS_EMAIL:
        email = _email_projection(fields, evidence_path=evidence_path)
        return {"email": email} if email else {}
    if subtype == _STANDARD_MACHINE_COPY:
        return {
            "host": _drop_none(
                {
                    "host_name": _first_str(fields, ("computername",)),
                    "ip_addresses": siem_machine_copy_ip_addresses(fields),
                }
            )
        }
    return {}


def siem_subtype(fields: Mapping[str, Any]) -> str | None:
    value = _first_str(fields, ("subtype",))
    return value.lower() if value else None


def siem_machine_copy_ip_addresses(fields: Mapping[str, Any]) -> list[str]:
    if siem_subtype(fields) != _STANDARD_MACHINE_COPY:
        return []
    candidates = [
        *_string_list(fields.get("agg_ip"), max_items=500),
        *_string_list(
            fields.get("winlogbeat_event_data_ipaddress"),
            max_items=10,
        ),
    ]
    return _dedupe([normalized for candidate in candidates if (normalized := _valid_ip(candidate)) is not None])


def siem_machine_copy_impacted_asset(fields: Mapping[str, Any]) -> str | None:
    if siem_subtype(fields) != _STANDARD_MACHINE_COPY:
        return None
    return _first_str(fields, ("computername",)) or next(
        iter(siem_machine_copy_ip_addresses(fields)),
        None,
    )


def build_siem_scenario_signals(
    fields: Mapping[str, Any],
    *,
    evidence_path: str,
    trust: EvidenceTrustLevel,
) -> list[ScenarioSignal]:
    subtype = siem_subtype(fields)
    candidates: list[tuple[str, str | None]] = [
        ("subtype", subtype),
    ]
    if subtype == _SUSPICIOUS_EMAIL:
        candidates.extend(
            (
                ("Phishing_type", _first_str(fields, ("Phishing_type",))),
                ("subject", _first_str(fields, ("subject",))),
            )
        )
        candidates.extend(("attachment", name) for name in _attachment_names(fields.get("attachment")))
    return [
        ScenarioSignal(
            text=value,
            evidence_path=f"{evidence_path}.{field_name}",
            source_layer=EvidenceLayer.RAW_STRUCTURED,
            evidence_trust=trust,
        )
        for field_name, value in candidates
        if value
    ]


def build_siem_source_field_semantics(
    fields: Mapping[str, Any],
    *,
    evidence_path: str,
) -> list[dict[str, Any]]:
    subtype = siem_subtype(fields)
    observations: list[dict[str, Any]] = []
    if subtype == _SUSPICIOUS_EMAIL:
        definitions = (
            (
                "llm_ans",
                "upstream_model_narrative",
                "upstream model analysis may support investigation but is not analyst truth, human confirmation, or Runtime output",
                False,
                True,
            ),
            (
                "llm_score",
                "upstream_uncalibrated_model_score",
                "source model score is not calibrated SOC Runtime confidence",
                False,
                True,
            ),
            (
                "Phishing_type",
                "provider_scenario_taxonomy",
                "provider phishing class is a source assertion and remains separate from the vendor-neutral scenario taxonomy",
                False,
                True,
            ),
            (
                "User",
                "pipeline_service_identity",
                "pipeline identity is not the sender, recipient, affected user, or event actor",
                False,
                False,
            ),
            (
                "text",
                "email_body_content",
                "email body remains bounded evidence and is not duplicated into canonical entities",
                False,
                True,
            ),
        )
        for field_name, semantic_type, meaning, entities, reasoning in definitions:
            if fields.get(field_name) not in (None, "", [], {}):
                observations.append(
                    _semantic(
                        f"{evidence_path}.{field_name}",
                        semantic_type,
                        meaning,
                        entities=entities,
                        reasoning=reasoning,
                    )
                )
    elif subtype == _STANDARD_MACHINE_COPY:
        for field_name, semantic_type, meaning, entities, reasoning in (
            (
                "agg_ip",
                "aggregated_host_ip_candidates",
                "aggregate IPs are host identity candidates from the model window, not network source/destination observations",
                True,
                True,
            ),
            (
                "winlogbeat_event_data_ipaddress",
                "observed_host_ip_candidate",
                "source event IP identifies a host candidate and does not establish network direction",
                True,
                True,
            ),
            (
                "computername",
                "aggregated_host_identity",
                "computer name identifies the modeled host candidate",
                True,
                True,
            ),
            (
                "if_cross",
                "upstream_model_feature",
                "crossing flag is an upstream aggregate-model feature, not detection truth",
                False,
                True,
            ),
            (
                "sorted_timestamp_str",
                "upstream_model_feature_series",
                "timestamp series is aggregate-model context and not a set of independent Runtime alerts",
                False,
                True,
            ),
        ):
            if fields.get(field_name) not in (None, "", [], {}):
                observations.append(
                    _semantic(
                        f"{evidence_path}.{field_name}",
                        semantic_type,
                        meaning,
                        entities=entities,
                        reasoning=reasoning,
                    )
                )
    elif subtype:
        observations.append(
            _semantic(
                f"{evidence_path}.subtype",
                "unsupported_siem_subtype",
                "unknown SIEM subtype remains bounded source evidence; the adapter does not infer entities or roles",
                reasoning=True,
            )
        )
    return observations


def siem_field_importance_rules() -> list[dict[str, Any]]:
    """Known structured mappings; validation also audits these until generic structured drift exists."""

    definitions = (
        (
            "pingan.siem.email.sender",
            ["structured.from"],
            "entities.email.sender_addresses",
            "critical",
        ),
        (
            "pingan.siem.email.recipient",
            ["structured.to"],
            "entities.email.recipient_addresses",
            "critical",
        ),
        (
            "pingan.siem.email.subject",
            ["structured.subject"],
            "entities.email.subject",
            "high",
        ),
        (
            "pingan.siem.machine.host",
            ["structured.computername"],
            "entities.host.host_name",
            "critical",
        ),
        (
            "pingan.siem.machine.ips",
            ["structured.agg_ip", "structured.winlogbeat_event_data_ipaddress"],
            "entities.host.ip_addresses",
            "critical",
        ),
    )
    return [
        {
            "rule_id": rule_id,
            "source_patterns": source_patterns,
            "expected_target": expected_target,
            "importance": importance,
            "source_types": [AlertSourceType.SIEM.value],
            "reason": f"PingAn SIEM evidence should populate {expected_target}",
        }
        for rule_id, source_patterns, expected_target, importance in definitions
    ]


def build_siem_canonical_field_provenance(
    alert: AlertInput,
    *,
    fields: Mapping[str, Any],
    evidence_path: str,
    trust: EvidenceTrustLevel,
) -> list[dict[str, Any]]:
    subtype = siem_subtype(fields)
    provenance: list[dict[str, Any]] = []
    if subtype == _SUSPICIOUS_EMAIL and alert.entities.email is not None:
        email = alert.entities.email
        _append_provenance(
            provenance,
            canonical_path="entities.email.message_id",
            selected_value=email.message_id,
            selected_from=f"{evidence_path}.email_id",
            trust=trust,
        )
        _append_provenance(
            provenance,
            canonical_path="entities.email.subject",
            selected_value=email.subject,
            selected_from=f"{evidence_path}.subject",
            trust=trust,
        )
        for attribute, source_name in (
            ("sender_addresses", "from"),
            ("recipient_addresses", "to"),
            ("cc_addresses", "cc"),
            ("links", "url"),
            ("attachment_names", "attachment"),
        ):
            for index, value in enumerate(getattr(email, attribute)):
                _append_provenance(
                    provenance,
                    canonical_path=f"entities.email.{attribute}[{index}]",
                    selected_value=value,
                    selected_from=f"{evidence_path}.{source_name}",
                    trust=trust,
                )
        for observation_index, observation in enumerate(email.observations):
            observation_prefix = f"entities.email.observations[{observation_index}]"
            for attribute, source_name in (
                ("message_id", "email_id"),
                ("subject", "subject"),
            ):
                _append_provenance(
                    provenance,
                    canonical_path=f"{observation_prefix}.{attribute}",
                    selected_value=getattr(observation, attribute),
                    selected_from=f"{evidence_path}.{source_name}",
                    trust=trust,
                )
            for attribute, source_name in (
                ("sender_addresses", "from"),
                ("recipient_addresses", "to"),
                ("cc_addresses", "cc"),
                ("links", "url"),
                ("attachment_names", "attachment"),
            ):
                for index, value in enumerate(getattr(observation, attribute)):
                    _append_provenance(
                        provenance,
                        canonical_path=f"{observation_prefix}.{attribute}[{index}]",
                        selected_value=value,
                        selected_from=f"{evidence_path}.{source_name}",
                        trust=trust,
                    )
    elif subtype == _STANDARD_MACHINE_COPY:
        _append_provenance(
            provenance,
            canonical_path="entities.host.host_name",
            selected_value=alert.entities.host.host_name,
            selected_from=f"{evidence_path}.computername",
            trust=trust,
        )
        aggregate_ips = set(_string_list(fields.get("agg_ip"), max_items=500))
        for index, value in enumerate(alert.entities.host.ip_addresses):
            source_name = "agg_ip" if value in aggregate_ips else "winlogbeat_event_data_ipaddress"
            _append_provenance(
                provenance,
                canonical_path=f"entities.host.ip_addresses[{index}]",
                selected_value=value,
                selected_from=f"{evidence_path}.{source_name}",
                trust=trust,
            )
    return provenance


def _email_projection(
    fields: Mapping[str, Any],
    *,
    evidence_path: str,
) -> dict[str, Any]:
    sender_addresses = _string_list(fields.get("from"), max_items=100)
    recipient_addresses = _string_list(fields.get("to"), max_items=500)
    cc_addresses = _string_list(fields.get("cc"), max_items=500)
    links = _string_list(fields.get("url"), max_items=200)
    attachment_names = _attachment_names(fields.get("attachment"))[:200]
    message_id = _first_str(fields, ("email_id", "logcloud_msgid"))
    subject = _bounded_str(fields.get("subject"), max_chars=2000)
    if not any(
        (
            message_id,
            sender_addresses,
            recipient_addresses,
            cc_addresses,
            subject,
            links,
            attachment_names,
        )
    ):
        return {}
    observation = {
        "observation_id": "email:" + hashlib.sha256(f"{evidence_path}|{message_id or ''}".encode()).hexdigest()[:16],
        "evidence_path": evidence_path,
        "event_time": _first_str(fields, ("modeltime",)),
        "message_id": message_id,
        "sender_addresses": sender_addresses,
        "recipient_addresses": recipient_addresses,
        "cc_addresses": cc_addresses,
        "subject": subject,
        "links": links,
        "attachment_names": attachment_names,
    }
    return {
        "message_id": message_id,
        "sender_addresses": sender_addresses,
        "recipient_addresses": recipient_addresses,
        "cc_addresses": cc_addresses,
        "subject": subject,
        "links": links,
        "attachment_names": attachment_names,
        "observations": [_drop_none(observation)],
    }


def _attachment_names(value: Any) -> list[str]:
    parsed = _parse_literal(value)
    if isinstance(parsed, Mapping):
        return _dedupe([text for key in parsed if (text := _bounded_str(key, max_chars=1000)) is not None])
    if isinstance(parsed, list):
        names: list[str] = []
        for item in parsed:
            if isinstance(item, Mapping):
                for key in ("name", "filename", "file_name"):
                    text = _bounded_str(item.get(key), max_chars=1000)
                    if text:
                        names.append(text)
                        break
            elif (text := _bounded_str(item, max_chars=1000)) is not None:
                names.append(text)
        return _dedupe(names)
    text = _bounded_str(value, max_chars=1000)
    return [text] if text and text not in {"{}", "[]"} else []


def _string_list(value: Any, *, max_items: int) -> list[str]:
    parsed = _parse_literal(value)
    candidates = parsed if isinstance(parsed, (list, tuple, set)) else [parsed]
    values: list[str] = []
    for candidate in candidates:
        text = _bounded_str(candidate, max_chars=4000)
        if text:
            values.append(text)
        if len(values) >= max_items:
            break
    return _dedupe(values)


def _parse_literal(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or len(text) > _MAX_LITERAL_CHARS:
        return value
    if text[:1] not in {"[", "{"}:
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(text)
        except (SyntaxError, ValueError):
            return value


def _append_provenance(
    target: list[dict[str, Any]],
    *,
    canonical_path: str,
    selected_value: Any,
    selected_from: str,
    trust: EvidenceTrustLevel,
) -> None:
    if selected_value is None or selected_value == "":
        return
    target.append(
        {
            "canonical_path": canonical_path,
            "selected_value": str(selected_value),
            "selected_from": selected_from,
            "source_layer": EvidenceLayer.RAW_STRUCTURED.value,
            "trust_level": trust.value,
            "selection_reason": "PingAn SIEM adapter selected typed structured evidence",
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


def _valid_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _first_str(value: Mapping[str, Any], aliases: Sequence[str]) -> str | None:
    for alias in aliases:
        text = _bounded_str(value.get(alias), max_chars=4000)
        if text:
            return text
    return None


def _bounded_str(value: Any, *, max_chars: int) -> str | None:
    if value is None or isinstance(value, (dict, list, tuple, set, bool)):
        return None
    text = str(value).strip()
    return text[:max_chars] if text else None


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


__all__ = [
    "build_siem_canonical_field_provenance",
    "build_siem_entities",
    "build_siem_scenario_signals",
    "build_siem_source_field_semantics",
    "siem_field_importance_rules",
    "siem_machine_copy_impacted_asset",
    "siem_machine_copy_ip_addresses",
    "siem_subtype",
]
