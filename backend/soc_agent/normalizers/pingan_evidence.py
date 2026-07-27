"""PingAn-specific evidence claims emitted for vendor-neutral fact reconstruction."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from soc_agent.contracts import (
    AlertSourceType,
    EvidenceLayer,
    EvidenceTrustLevel,
    ParsedRawMessageEvidence,
    RoleClaim,
    RoleClaimType,
    ScenarioSignal,
)

_SCENARIO_SIGNAL_FIELDS = frozenset(
    {
        "attack_type",
        "detail_info",
        "description",
        "event_content",
        "event_name",
        "finding__desc",
        "finding__title",
        "host_state",
        "ioc",
        "rule_name",
        "str_desc",
        "str_title",
        "vuln_name",
        "vuln_type",
    }
)


def build_pingan_fact_inputs(
    alert: Mapping[str, Any],
    *,
    parsed_messages: Sequence[ParsedRawMessageEvidence],
    source_type: AlertSourceType,
) -> tuple[list[RoleClaim], list[ScenarioSignal]]:
    """Translate PingAn aliases into generic claims without leaking them into Runtime."""

    claims: list[RoleClaim] = []
    signals: list[ScenarioSignal] = []
    for parsed in parsed_messages:
        claims.extend(
            _claims_for_fields(
                parsed.fields,
                base_path=f"{parsed.source_path}#parsed",
                layer=EvidenceLayer.RAW_MESSAGE,
                trust=EvidenceTrustLevel.HIGH,
                source_type=source_type,
            )
        )
        signals.extend(
            _scenario_signals(
                parsed.fields,
                base_path=f"{parsed.source_path}#parsed",
                layer=EvidenceLayer.RAW_MESSAGE,
                trust=EvidenceTrustLevel.HIGH,
                source_type=source_type,
            )
        )

    for hit_log_index, raw_event_index, raw_event in _iter_raw_events(alert):
        base_path = f"alert.hitLog[{hit_log_index}].zeusRawLogs[{raw_event_index}]"
        claims.extend(
            _claims_for_fields(
                raw_event,
                base_path=base_path,
                layer=EvidenceLayer.RAW_STRUCTURED,
                trust=EvidenceTrustLevel.MEDIUM,
                source_type=source_type,
            )
        )
        signals.extend(
            _scenario_signals(
                raw_event,
                base_path=base_path,
                layer=EvidenceLayer.RAW_STRUCTURED,
                trust=EvidenceTrustLevel.MEDIUM,
                source_type=source_type,
            )
        )
    return _dedupe_claims(claims), _dedupe_signals(signals)


def _claims_for_fields(
    fields: Mapping[str, Any],
    *,
    base_path: str,
    layer: EvidenceLayer,
    trust: EvidenceTrustLevel,
    source_type: AlertSourceType,
) -> list[RoleClaim]:
    claims: list[RoleClaim] = []
    observation_confidence = 0.9 if layer is EvidenceLayer.RAW_MESSAGE else 0.55
    assertion_confidence = 0.5 if layer is EvidenceLayer.RAW_MESSAGE else 0.3
    endpoint_confidence = 0.8 if layer is EvidenceLayer.RAW_MESSAGE else 0.5

    if source_type is AlertSourceType.EDR:
        source_aliases = ("str_source_ip", "device__ip")
        destination_aliases = ("str_attack_ip", "str_threat_value", "str_activity_id")
        impacted_aliases = ("str_source_ip", "device__ip")
        attacker_aliases = ("str_attack_ip", "str_threat_value", "str_activity_id")
        victim_aliases = impacted_aliases
    elif source_type is AlertSourceType.HIDS:
        source_aliases = ()
        destination_aliases = ()
        impacted_aliases = ("internal_ip", "agent_ip", "device__ip", "str_source_ip")
        attacker_aliases = ()
        victim_aliases = impacted_aliases
    else:
        # Zeus processed ``source_ip``/``src_addr`` may represent the original
        # client recovered from a proxy chain rather than this session's
        # observed source. Prefer the sensor's sip/dip pair when available;
        # processed aliases remain fallback-only for message-less events.
        source_aliases = ("sip",) if _claim_value(fields.get("sip")) else ("source_ip", "src_addr")
        destination_aliases = ("dip",) if _claim_value(fields.get("dip")) else ("dst_addr",)
        impacted_aliases = ("victim", "alarm_sip")
        attacker_aliases = ("attacker", "attack_sip")
        victim_aliases = ("victim", "alarm_sip")

    _append_alias_claims(
        claims,
        fields,
        aliases=source_aliases,
        role="source",
        claim_type=RoleClaimType.OBSERVATION,
        base_path=base_path,
        layer=layer,
        trust=trust,
        semantic_confidence=observation_confidence,
        rationale="source adapter observed the network source field",
    )
    _append_alias_claims(
        claims,
        fields,
        aliases=destination_aliases,
        role="destination",
        claim_type=RoleClaimType.OBSERVATION,
        base_path=base_path,
        layer=layer,
        trust=trust,
        semantic_confidence=observation_confidence,
        rationale="source adapter observed the network destination field",
    )
    _append_alias_claims(
        claims,
        fields,
        aliases=attacker_aliases,
        role="attacker",
        claim_type=(RoleClaimType.DERIVED_HYPOTHESIS if source_type in {AlertSourceType.EDR, AlertSourceType.HIDS} else RoleClaimType.VENDOR_ASSERTION),
        base_path=base_path,
        layer=layer,
        trust=trust,
        semantic_confidence=assertion_confidence,
        rationale="source product asserted an attacker role; semantic correctness remains unconfirmed",
    )
    _append_alias_claims(
        claims,
        fields,
        aliases=victim_aliases,
        role="victim",
        claim_type=RoleClaimType.VENDOR_ASSERTION,
        base_path=base_path,
        layer=layer,
        trust=trust,
        semantic_confidence=(endpoint_confidence - 0.05 if source_type in {AlertSourceType.EDR, AlertSourceType.HIDS} else assertion_confidence),
        rationale=(
            "endpoint telemetry identifies the host as a provisional victim candidate" if source_type in {AlertSourceType.EDR, AlertSourceType.HIDS} else "source product asserted a victim role; semantic correctness remains unconfirmed"
        ),
    )
    _append_alias_claims(
        claims,
        fields,
        aliases=impacted_aliases,
        role="impacted_asset",
        claim_type=RoleClaimType.DERIVED_HYPOTHESIS,
        base_path=base_path,
        layer=layer,
        trust=trust,
        semantic_confidence=endpoint_confidence if source_type in {AlertSourceType.EDR, AlertSourceType.HIDS} else assertion_confidence,
        rationale="source adapter proposed an impacted asset candidate; asset ownership still requires corroboration",
    )
    return claims


def _append_alias_claims(
    claims: list[RoleClaim],
    fields: Mapping[str, Any],
    *,
    aliases: Sequence[str],
    role: str,
    claim_type: RoleClaimType,
    base_path: str,
    layer: EvidenceLayer,
    trust: EvidenceTrustLevel,
    semantic_confidence: float,
    rationale: str,
) -> None:
    observation_scope = _observation_scope(base_path)
    for alias in aliases:
        value = _claim_value(fields.get(alias))
        if value is None:
            continue
        evidence_path = f"{base_path}.{alias}"
        claims.append(
            RoleClaim(
                claim_id=_claim_id(role, value, evidence_path, claim_type),
                role=role,  # type: ignore[arg-type]
                value=value,
                claim_type=claim_type,
                evidence_path=evidence_path,
                observation_scope=observation_scope,
                source_layer=layer,
                evidence_trust=trust,
                semantic_confidence=semantic_confidence,
                rationale=rationale,
            )
        )


def _observation_scope(base_path: str) -> str:
    source_path = base_path.split("#", 1)[0]
    return source_path.removesuffix(".message")


def _scenario_signals(
    fields: Mapping[str, Any],
    *,
    base_path: str,
    layer: EvidenceLayer,
    trust: EvidenceTrustLevel,
    source_type: AlertSourceType,
) -> list[ScenarioSignal]:
    signals: list[ScenarioSignal] = []

    def visit(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                visit(item, (*path, str(key)))
            return
        if not path or path[-1].lower() not in _SCENARIO_SIGNAL_FIELDS:
            return
        text = _claim_value(value)
        if text is None:
            return
        signals.append(
            ScenarioSignal(
                text=text,
                evidence_path=f"{base_path}.{'.'.join(path)}",
                source_layer=layer,
                evidence_trust=trust,
            )
        )

    visit(fields, ())
    if source_type is AlertSourceType.NIDS:
        for path in (
            ("alert", "signature"),
            ("alert", "category"),
        ):
            text = _claim_value(_nested_value(fields, path))
            if text is None:
                continue
            signals.append(
                ScenarioSignal(
                    text=text,
                    evidence_path=f"{base_path}.{'.'.join(path)}",
                    source_layer=layer,
                    evidence_trust=trust,
                )
            )
    return signals


def _iter_raw_events(alert: Mapping[str, Any]) -> list[tuple[int, int, Mapping[str, Any]]]:
    result: list[tuple[int, int, Mapping[str, Any]]] = []
    hit_logs = alert.get("hitLog")
    if not isinstance(hit_logs, list):
        return result
    for hit_log_index, hit_log in enumerate(hit_logs):
        if not isinstance(hit_log, Mapping):
            continue
        raw_logs = hit_log.get("zeusRawLogs")
        if not isinstance(raw_logs, list):
            continue
        for raw_event_index, raw_event in enumerate(raw_logs):
            if isinstance(raw_event, Mapping):
                result.append((hit_log_index, raw_event_index, raw_event))
    return result


def _claim_value(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _nested_value(
    value: Mapping[str, Any],
    path: tuple[str, ...],
) -> Any:
    current: Any = value
    for segment in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(segment)
    return current


def _claim_id(role: str, value: str, path: str, claim_type: RoleClaimType) -> str:
    digest = hashlib.sha256(f"{role}|{value}|{path}|{claim_type.value}".encode()).hexdigest()[:16]
    return f"claim:{digest}"


def _dedupe_claims(claims: Sequence[RoleClaim]) -> list[RoleClaim]:
    return list({claim.claim_id: claim for claim in claims}.values())


def _dedupe_signals(signals: Sequence[ScenarioSignal]) -> list[ScenarioSignal]:
    result: list[ScenarioSignal] = []
    seen: set[tuple[str, str]] = set()
    for signal in signals:
        key = (signal.text, signal.evidence_path)
        if key not in seen:
            seen.add(key)
            result.append(signal)
    return result


__all__ = ["build_pingan_fact_inputs"]
