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
from soc_agent.normalizers.pingan_edr import (
    edr_attacker_candidates,
    edr_endpoint_addresses,
    edr_ip_addresses,
)
from soc_agent.normalizers.pingan_hids import hids_endpoint_sources
from soc_agent.normalizers.pingan_siem import (
    build_siem_scenario_signals,
    siem_machine_copy_impacted_asset,
)
from soc_agent.normalizers.pingan_threat_intel import (
    build_threat_intel_scenario_signals,
    threat_intel_session,
)

_SCENARIO_SIGNAL_FIELDS = frozenset(
    {
        "attack_type",
        "alert_describe",
        "detail_info",
        "description",
        "event_content",
        "event_name",
        "event_type",
        "finding__desc",
        "finding__title",
        "host_state",
        "hit_rule_name",
        "hit_rule_names",
        "ioc",
        "rule_name",
        "rule_desc",
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
    structured_fallback_trust: EvidenceTrustLevel = EvidenceTrustLevel.LOW,
) -> tuple[list[RoleClaim], list[ScenarioSignal]]:
    """Translate PingAn aliases into generic claims without leaking them into Runtime."""

    claims: list[RoleClaim] = []
    signals: list[ScenarioSignal] = []
    raw_events = _iter_raw_events(alert)
    edr_endpoints_by_scope = (
        _edr_endpoint_addresses_by_scope(
            parsed_messages,
            () if parsed_messages else raw_events,
        )
        if source_type is AlertSourceType.EDR
        else {}
    )
    for parsed in parsed_messages:
        base_path = f"{parsed.source_path}#parsed"
        claims.extend(
            _claims_for_fields(
                parsed.fields,
                base_path=base_path,
                layer=EvidenceLayer.RAW_MESSAGE,
                trust=EvidenceTrustLevel.HIGH,
                source_type=source_type,
                known_edr_endpoints=edr_endpoints_by_scope.get(
                    _observation_scope(base_path),
                    (),
                ),
            )
        )
        if source_type is not AlertSourceType.THREAT_INTEL:
            signals.extend(
                _scenario_signals(
                    parsed.fields,
                    base_path=f"{parsed.source_path}#parsed",
                    layer=EvidenceLayer.RAW_MESSAGE,
                    trust=EvidenceTrustLevel.HIGH,
                    source_type=source_type,
                )
            )
    if source_type is AlertSourceType.THREAT_INTEL:
        signals.extend(build_threat_intel_scenario_signals(parsed_messages))

    if parsed_messages:
        # Parsed messages are authoritative for analysis. Matching Zeus
        # processed fields remain preserved in AlertInput.raw, but cannot
        # become claims, scenarios, conflicts, or analyzer evidence.
        return _dedupe_claims(claims), _dedupe_signals(signals)

    for raw_position, (hit_log_index, raw_event_index, raw_event) in enumerate(raw_events):
        base_path = f"alert.hitLog[{hit_log_index}].zeusRawLogs[{raw_event_index}]"
        if raw_position > 0:
            # Message-less fallback selects only the first raw event. Later
            # events remain in AlertInput.raw for audit and replay.
            continue
        claims.extend(
            _claims_for_fields(
                raw_event,
                base_path=base_path,
                layer=EvidenceLayer.RAW_STRUCTURED,
                trust=structured_fallback_trust,
                source_type=source_type,
                known_edr_endpoints=edr_endpoints_by_scope.get(base_path, ()),
            )
        )
        if source_type is AlertSourceType.SIEM:
            signals.extend(
                build_siem_scenario_signals(
                    raw_event,
                    evidence_path=base_path,
                    trust=structured_fallback_trust,
                )
            )
        else:
            signals.extend(
                _scenario_signals(
                    raw_event,
                    base_path=base_path,
                    layer=EvidenceLayer.RAW_STRUCTURED,
                    trust=structured_fallback_trust,
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
    known_edr_endpoints: Sequence[str] = (),
) -> list[RoleClaim]:
    claims: list[RoleClaim] = []
    observation_confidence = 0.9 if layer is EvidenceLayer.RAW_MESSAGE else 0.55
    assertion_confidence = 0.5 if layer is EvidenceLayer.RAW_MESSAGE else 0.3
    endpoint_confidence = 0.8 if layer is EvidenceLayer.RAW_MESSAGE else 0.5

    if source_type is AlertSourceType.EDR:
        _append_edr_role_claims(
            claims,
            fields,
            base_path=base_path,
            layer=layer,
            trust=trust,
            endpoint_confidence=endpoint_confidence,
            attacker_confidence=assertion_confidence,
            known_endpoint_addresses=known_edr_endpoints,
        )
        return claims

    if source_type is AlertSourceType.HIDS:
        _append_hids_role_claims(
            claims,
            fields,
            base_path=base_path,
            layer=layer,
            trust=trust,
            observation_confidence=observation_confidence,
            endpoint_confidence=endpoint_confidence,
        )
        return claims

    if source_type is AlertSourceType.THREAT_INTEL:
        _append_threat_intel_role_claims(
            claims,
            fields,
            base_path=base_path,
            layer=layer,
            trust=trust,
            observation_confidence=observation_confidence,
            assertion_confidence=assertion_confidence,
            endpoint_confidence=endpoint_confidence,
        )
        return claims

    if source_type is AlertSourceType.SIEM:
        impacted_asset = siem_machine_copy_impacted_asset(fields)
        if impacted_asset:
            _append_direct_claim(
                claims,
                role="impacted_asset",
                value=impacted_asset,
                claim_type=RoleClaimType.DERIVED_HYPOTHESIS,
                evidence_path=f"{base_path}.computername",
                base_path=base_path,
                layer=layer,
                trust=trust,
                semantic_confidence=0.65,
                rationale=("the SIEM aggregate model identifies a host candidate; asset ownership and maliciousness still require corroboration"),
            )
        return claims

    if source_type is AlertSourceType.HIDS:
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


def _append_threat_intel_role_claims(
    claims: list[RoleClaim],
    fields: Mapping[str, Any],
    *,
    base_path: str,
    layer: EvidenceLayer,
    trust: EvidenceTrustLevel,
    observation_confidence: float,
    assertion_confidence: float,
    endpoint_confidence: float,
) -> None:
    """Keep wire endpoints independent from provider attacker/victim labels."""

    session = threat_intel_session(fields)
    for role, field_name in (
        ("source", "source_ip"),
        ("destination", "destination_ip"),
    ):
        value = session.get(field_name)
        if not isinstance(value, str) or not value:
            continue
        source_name = "src_ip" if role == "source" else "dest_ip"
        _append_direct_claim(
            claims,
            role=role,
            value=value,
            claim_type=RoleClaimType.OBSERVATION,
            evidence_path=f"{base_path}.net.{source_name}",
            base_path=base_path,
            layer=layer,
            trust=trust,
            semantic_confidence=observation_confidence,
            rationale="ThreatBook net fields describe the observed network session endpoint",
        )

    for role, field_name in (("attacker", "attacker"), ("victim", "victim")):
        value = _claim_value(fields.get(field_name))
        if value is None:
            continue
        _append_direct_claim(
            claims,
            role=role,
            value=value,
            claim_type=RoleClaimType.VENDOR_ASSERTION,
            evidence_path=f"{base_path}.{field_name}",
            base_path=base_path,
            layer=layer,
            trust=trust,
            semantic_confidence=assertion_confidence,
            rationale=(f"ThreatBook asserted the {role} security role; the assertion does not redefine observed wire direction"),
        )

    impacted_asset = _claim_value(fields.get("machine")) or _claim_value(fields.get("victim"))
    if impacted_asset:
        source_name = "machine" if _claim_value(fields.get("machine")) else "victim"
        _append_direct_claim(
            claims,
            role="impacted_asset",
            value=impacted_asset,
            claim_type=RoleClaimType.DERIVED_HYPOTHESIS,
            evidence_path=f"{base_path}.{source_name}",
            base_path=base_path,
            layer=layer,
            trust=trust,
            semantic_confidence=endpoint_confidence,
            rationale=("ThreatBook identifies the monitored machine as an impacted asset candidate; ownership still requires corroboration"),
        )


def _append_direct_claim(
    claims: list[RoleClaim],
    *,
    role: str,
    value: str,
    claim_type: RoleClaimType,
    evidence_path: str,
    base_path: str,
    layer: EvidenceLayer,
    trust: EvidenceTrustLevel,
    semantic_confidence: float,
    rationale: str,
) -> None:
    claims.append(
        RoleClaim(
            claim_id=_claim_id(role, value, evidence_path, claim_type),
            role=role,  # type: ignore[arg-type]
            value=value,
            claim_type=claim_type,
            evidence_path=evidence_path,
            observation_scope=_observation_scope(base_path),
            source_layer=layer,
            evidence_trust=trust,
            semantic_confidence=semantic_confidence,
            rationale=rationale,
        )
    )


def _append_edr_role_claims(
    claims: list[RoleClaim],
    fields: Mapping[str, Any],
    *,
    base_path: str,
    layer: EvidenceLayer,
    trust: EvidenceTrustLevel,
    endpoint_confidence: float,
    attacker_confidence: float,
    known_endpoint_addresses: Sequence[str],
) -> None:
    """Emit endpoint and vendor-role claims without inventing wire direction."""

    observation_scope = _observation_scope(base_path)
    for alias in (
        "str_source_ip",
        "device__ip",
        "agent_ip",
        "internal_ip",
        "iplist",
    ):
        evidence_path = f"{base_path}.{alias}"
        for value in edr_ip_addresses(fields.get(alias)):
            for role, claim_type, confidence, rationale in (
                (
                    "victim",
                    RoleClaimType.VENDOR_ASSERTION,
                    endpoint_confidence - 0.05,
                    "endpoint telemetry identifies the host as a provisional victim candidate",
                ),
                (
                    "impacted_asset",
                    RoleClaimType.DERIVED_HYPOTHESIS,
                    endpoint_confidence,
                    "endpoint telemetry identifies an impacted asset candidate; ownership still requires corroboration",
                ),
            ):
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
                        semantic_confidence=confidence,
                        rationale=rationale,
                    )
                )

    evidence_path = f"{base_path}.str_attack_ip"
    for value in edr_attacker_candidates(
        fields,
        known_endpoint_addresses=known_endpoint_addresses,
    ):
        claim_type = RoleClaimType.VENDOR_ASSERTION
        claims.append(
            RoleClaim(
                claim_id=_claim_id("attacker", value, evidence_path, claim_type),
                role="attacker",
                value=value,
                claim_type=claim_type,
                evidence_path=evidence_path,
                observation_scope=observation_scope,
                source_layer=layer,
                evidence_trust=trust,
                semantic_confidence=attacker_confidence,
                rationale=("source product labeled a non-endpoint IP as an attack candidate; wire direction and attacker identity remain unconfirmed"),
            )
        )


def _append_hids_role_claims(
    claims: list[RoleClaim],
    fields: Mapping[str, Any],
    *,
    base_path: str,
    layer: EvidenceLayer,
    trust: EvidenceTrustLevel,
    observation_confidence: float,
    endpoint_confidence: float,
) -> None:
    """Emit host identity and only event-contract network observations."""

    endpoints = hids_endpoint_sources(fields)
    for value, alias in endpoints:
        evidence_path = f"{base_path}.{alias}"
        for role, claim_type, confidence, rationale in (
            (
                "victim",
                RoleClaimType.VENDOR_ASSERTION,
                endpoint_confidence - 0.05,
                "HIDS endpoint identity is a provisional victim candidate, not detection truth",
            ),
            (
                "impacted_asset",
                RoleClaimType.DERIVED_HYPOTHESIS,
                endpoint_confidence,
                "HIDS endpoint identity is an impacted asset candidate; ownership still requires corroboration",
            ),
        ):
            _append_direct_claim(
                claims,
                role=role,
                value=value,
                claim_type=claim_type,
                evidence_path=evidence_path,
                base_path=base_path,
                layer=layer,
                trust=trust,
                semantic_confidence=confidence,
                rationale=rationale,
            )

    event_type = str(fields.get("event_type") or fields.get("datatype") or "").strip().lower()
    endpoint_value, endpoint_alias = endpoints[0] if endpoints else (None, None)
    observed_roles: list[tuple[str, str | None, str | None]] = []
    if event_type == "bounce_shell":
        observed_roles.extend(
            [
                ("source", endpoint_value, endpoint_alias),
                ("destination", _first_ip_value(fields.get("dst_ip")), "dst_ip"),
            ]
        )
    elif event_type in {"honeypot", "malic_opera"}:
        observed_roles.extend(
            [
                ("source", _first_ip_value(fields.get("src_ip")), "src_ip"),
                ("destination", endpoint_value, endpoint_alias),
            ]
        )
    for role, value, alias in observed_roles:
        if value is None or alias is None:
            continue
        _append_direct_claim(
            claims,
            role=role,
            value=value,
            claim_type=RoleClaimType.OBSERVATION,
            evidence_path=f"{base_path}.{alias}",
            base_path=base_path,
            layer=layer,
            trust=trust,
            semantic_confidence=observation_confidence,
            rationale=(f"HIDS {event_type} event contract identifies the observed network {role}"),
        )


def _first_ip_value(value: Any) -> str | None:
    values = edr_ip_addresses(value)
    return values[0] if values else None


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


def _edr_endpoint_addresses_by_scope(
    parsed_messages: Sequence[ParsedRawMessageEvidence],
    raw_events: Sequence[tuple[int, int, Mapping[str, Any]]],
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for parsed in parsed_messages:
        scope = _observation_scope(f"{parsed.source_path}#parsed")
        result.setdefault(scope, []).extend(edr_endpoint_addresses(parsed.fields))
    for hit_log_index, raw_event_index, raw_event in raw_events:
        scope = f"alert.hitLog[{hit_log_index}].zeusRawLogs[{raw_event_index}]"
        result.setdefault(scope, []).extend(edr_endpoint_addresses(raw_event))
    return {scope: list(dict.fromkeys(addresses)) for scope, addresses in result.items()}


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
