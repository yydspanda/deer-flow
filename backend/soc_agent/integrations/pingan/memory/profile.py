"""PingAn same-class, occurrence and applicability semantics.

This module consumes canonical SOC facets produced after normalization. It does
not parse PingAn raw field aliases and it does not change Runtime decisions.
"""

from __future__ import annotations

import ipaddress
import ntpath
import re
from datetime import UTC, datetime

from soc_agent.contracts import (
    AnalysisRun,
    LLMAnalysisRequest,
    MemoryPatternDimension,
    MemoryPatternSignature,
    SocMemoryApplicabilitySpec,
)
from soc_agent.memory.facets import (
    memory_facets_from_analysis_request,
    memory_facets_from_analysis_run,
)
from soc_agent.memory.profiles import SocMemoryProfileIdentity
from soc_agent.normalizers import normalize_alert_payload
from soc_agent.utils.hashing import stable_hash

_PINGAN_PATTERN_FACET_KEYS = (
    "source_type",
    "source_system",
    "product",
    "detection_key",
    "rule_code",
    "rule_name",
    "detection_signature",
    "category",
    "environment",
    "scenario_key",
    "network_service",
    "vulnerability_id",
    "attack_behavior_family",
    "behavior_component",
    "behavior_component_core",
    "behavior_component_strong",
    "behavior_fingerprint",
    "behavior_strength",
    "role_entity",
    "entity",
)


class PingAnSocMemoryProfile:
    """Conservative PingAn profile layered on the generic Memory Kernel."""

    identity = SocMemoryProfileIdentity(
        profile_id="pingan.soc",
        profile_version="7",
        feature_schema_version="pingan.soc.memory_features.v5",
        aggregation_window_seconds=30 * 24 * 60 * 60,
    )

    def matches_request(self, request: LLMAnalysisRequest) -> bool:
        integration = (request.source.integration_name or "").strip().casefold()
        return integration == "pingan_legacy_alert_platform"

    def project_query_facets(
        self,
        request: LLMAnalysisRequest,
    ) -> dict[str, list[str]]:
        # The PingAn Adapter has already converted raw aliases into canonical
        # fields. Keeping the feature vocabulary canonical is what makes the
        # shared retrieval kernel portable.
        return _project_pingan_facets(
            memory_facets_from_analysis_request(request),
            request=request,
        )

    def project_run_facets(
        self,
        run: AnalysisRun,
    ) -> dict[str, list[str]]:
        request = run.llm_analysis_request
        if request is None:
            return memory_facets_from_analysis_run(run)
        return _project_pingan_facets(
            memory_facets_from_analysis_run(run),
            request=request,
        )

    def build_pattern_signature(
        self,
        run: AnalysisRun,
        *,
        facets: dict[str, list[str]],
    ) -> MemoryPatternSignature:
        request = run.llm_analysis_request
        if request is None:
            raise ValueError("bounded analysis request is required")

        detection_keys = facets.get("detection_key", [])
        detection_signatures = facets.get("detection_signature", [])
        behavior_fingerprints = facets.get("behavior_fingerprint", [])
        signature_facets = _pingan_pattern_facets(facets)
        if detection_keys and behavior_fingerprints:
            detection_key = detection_keys[0]
            detection_signature = detection_signatures[0] if detection_signatures else None
            behavior_fingerprint = behavior_fingerprints[0]
            return MemoryPatternSignature(
                dimension=MemoryPatternDimension.COMPOUND,
                value=f"compound:{stable_hash({'detection_key': detection_key, 'detection_signature': detection_signature, 'behavior_fingerprint': behavior_fingerprint})}",
                label=_pingan_behavior_label(request, facets),
                origin=self.identity.feature_schema_version,
                facets=signature_facets,
            )

        if detection_keys:
            value = detection_keys[0]
            signature = detection_signatures[0] if detection_signatures else None
            return MemoryPatternSignature(
                dimension=MemoryPatternDimension.DETECTION,
                value=_bounded_value(f"detection:{stable_hash({'detection_key': value, 'detection_signature': signature})}"),
                label=request.detection.rule_name or value,
                origin="pingan_adapter:canonical_detection",
                facets=signature_facets,
            )

        if behavior_fingerprints:
            return MemoryPatternSignature(
                dimension=MemoryPatternDimension.BEHAVIOR,
                value=_bounded_value(behavior_fingerprints[0]),
                label=_pingan_behavior_label(request, facets),
                origin=self.identity.feature_schema_version,
                facets=signature_facets,
            )

        # A model-only scenario label is not stable enough to own a PingAn
        # cohort. It remains an optional applicability facet but cannot create
        # an expert-review candidate by itself.
        raise ValueError("PingAn memory profile requires a canonical detection key or behavior fingerprint")

    def build_occurrence_key(
        self,
        run: AnalysisRun,
        *,
        signature: MemoryPatternSignature,
        facets: dict[str, list[str]],
        observed_at: datetime,
    ) -> str:
        alert_id = _canonical_alert_id(run)
        event_id = _canonical_event_id(run)
        if alert_id:
            # The Runtime processes one ZEUS alert as one operational occurrence.
            # Re-delivery may change transport or other mutable payload fields, but
            # the operator-visible alert identity remains stable.
            identity: dict[str, object] = {"alert_id": alert_id}
        elif event_id:
            identity: dict[str, object] = {"event_id": event_id}
        elif run.input_hash:
            # Exact replays can arrive under another transport offset even
            # when the upstream contract has no stable event identifier.
            identity = {"input_hash": run.input_hash}
        else:
            role_scope = sorted(facets.get("role_entity", []))[:6]
            entity_scope = sorted(facets.get("entity", []))[:6]
            if role_scope or entity_scope:
                utc = observed_at.astimezone(UTC)
                identity = {
                    "five_minute_bucket": utc.replace(
                        minute=(utc.minute // 5) * 5,
                        second=0,
                        microsecond=0,
                    ).isoformat(),
                    "role_scope": role_scope,
                    "entity_scope": entity_scope,
                }
            else:
                identity = {
                    "run_id": run.run_id,
                    "alert_id": run.alert_id,
                }
        return stable_hash(
            {
                "profile_id": self.identity.profile_id,
                "profile_version": self.identity.profile_version,
                "signature_dimension": signature.dimension.value,
                "signature_value": signature.value,
                "identity": identity,
            }
        )

    def build_applicability(
        self,
        *,
        consensus_facets: dict[str, list[str]],
        strong_anchor_facets: dict[str, list[str]],
    ) -> SocMemoryApplicabilitySpec | None:
        detection_key = strong_anchor_facets.get("detection_key")
        detection_signature = strong_anchor_facets.get("detection_signature")
        behavior_fingerprint = strong_anchor_facets.get("behavior_fingerprint")
        behavior_strength = consensus_facets.get("behavior_strength")
        strong_behavior = behavior_strength == ["strong"]

        required: dict[str, list[str]] = {}
        decision_eligible = False
        if detection_key and detection_signature and behavior_fingerprint and strong_behavior:
            required.update(
                {
                    "detection_key": list(detection_key),
                    "detection_signature": list(detection_signature),
                    "behavior_fingerprint": list(behavior_fingerprint),
                    "behavior_strength": ["strong"],
                }
            )
            decision_eligible = True
        elif not detection_key and behavior_fingerprint and strong_behavior:
            required.update(
                {
                    "behavior_fingerprint": list(behavior_fingerprint),
                    "behavior_strength": ["strong"],
                }
            )
            decision_eligible = True
        elif detection_key:
            required["detection_key"] = list(detection_key)
            if detection_signature:
                required["detection_signature"] = list(detection_signature)
        else:
            return None

        # PingAn operational conclusions vary materially between DEV/STG/PRD.
        # The environment is supplied by the server-owned ingestion command,
        # not inferred from a topic or a vendor field.
        environment = consensus_facets.get("environment")
        if not environment:
            return None
        required["environment"] = list(environment)

        optional = {
            key: list(consensus_facets[key])
            for key in (
                "source_type",
                "source_system",
                "product",
                "scenario_key",
                "behavior_component",
                "behavior_component_core",
                "behavior_component_strong",
                "behavior_component_weak",
                "behavior_fingerprint",
                "behavior_strength",
                "network_service",
                "vulnerability_id",
                "attack_behavior_family",
                "role_entity",
                "entity",
                "environment",
            )
            if key not in required and consensus_facets.get(key)
        }
        context_only_required = sorted(set(required) - {"behavior_fingerprint"}) if decision_eligible and detection_key and optional.get("behavior_component_strong") else []
        context_only_missing = ["behavior_fingerprint"] if context_only_required else []
        context_only_similarity = ["behavior_component_strong"] if context_only_required else []
        return SocMemoryApplicabilitySpec(
            profile_id=self.identity.profile_id,
            profile_version=self.identity.profile_version,
            feature_schema_version=self.identity.feature_schema_version,
            required_facets=required,
            optional_facets=optional,
            minimum_optional_matches=0,
            minimum_strong_anchor_matches=len(
                set(required)
                & {
                    "detection_key",
                    "detection_signature",
                    "behavior_fingerprint",
                }
            ),
            context_only_required_facet_keys=context_only_required,
            context_only_missing_facet_keys=context_only_missing,
            context_only_similarity_facet_keys=context_only_similarity,
        )

    def retrieval_conflict_reasons(
        self,
        *,
        record_facets: dict[str, list[str]],
        query_facets: dict[str, list[str]],
    ) -> list[str]:
        """Reject same-rule lessons whose canonical behavior scope conflicts."""

        record = _normalized_facet_sets(record_facets)
        query = _normalized_facet_sets(query_facets)
        reasons: list[str] = []

        record_services = _semantic_scope_values(
            record,
            "network_service",
            component_prefix="network_service:",
        )
        query_services = _semantic_scope_values(
            query,
            "network_service",
            component_prefix="network_service:",
        )
        if record_services and query_services and record_services.isdisjoint(query_services):
            reasons.append("network_service_mismatch")

        record_vulnerabilities = _semantic_scope_values(
            record,
            "vulnerability_id",
            component_prefix="vulnerability:",
        )
        query_vulnerabilities = _semantic_scope_values(
            query,
            "vulnerability_id",
            component_prefix="vulnerability:",
        )
        if record_vulnerabilities and not query_vulnerabilities:
            reasons.append("vulnerability_scope_missing")
        elif record_vulnerabilities.isdisjoint(query_vulnerabilities) and query_vulnerabilities:
            reasons.append("vulnerability_scope_mismatch")

        record_families = _semantic_scope_values(
            record,
            "attack_behavior_family",
            component_prefix="attack_family:",
        )
        query_families = _semantic_scope_values(
            query,
            "attack_behavior_family",
            component_prefix="attack_family:",
        )
        if record_families and query_families and record_families.isdisjoint(query_families):
            reasons.append("attack_behavior_family_mismatch")
        return reasons


def _canonical_alert_id(run: AnalysisRun) -> str | None:
    payload = run.input_payload
    if not isinstance(payload, dict):
        return None

    alert = payload.get("alert")
    if isinstance(alert, dict):
        for key in ("alertId", "alertCode", "alert_id"):
            value = alert.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()

    for key in ("alert_id", "alertId"):
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _normalized_facet_sets(
    facets: dict[str, list[str]],
) -> dict[str, set[str]]:
    return {str(key).strip().casefold(): {str(value).strip().casefold() for value in values if str(value).strip()} for key, values in facets.items() if str(key).strip()}


def _semantic_scope_values(
    facets: dict[str, set[str]],
    key: str,
    *,
    component_prefix: str,
) -> set[str]:
    """Read one canonical scope from both current and legacy component facets."""

    values = set(facets.get(key, set()))
    for component_key in (
        "behavior_component",
        "behavior_component_core",
        "behavior_component_strong",
        "behavior_component_weak",
    ):
        for component in facets.get(component_key, set()):
            if component.startswith(component_prefix):
                scoped_value = component.removeprefix(component_prefix).strip()
                if scoped_value:
                    values.add(scoped_value)
    return values


def _canonical_event_id(run: AnalysisRun) -> str | None:
    if run.input_payload is None:
        return None
    try:
        return normalize_alert_payload(run.input_payload).event.event_id
    except Exception:  # noqa: BLE001 - recurrence remains best-effort
        return None


def _bounded_value(value: str) -> str:
    normalized = " ".join(value.split()).casefold()
    if not normalized:
        raise ValueError("PingAn memory signature must not be blank")
    if len(normalized) <= 256:
        return normalized
    return f"sha256:{stable_hash(normalized)}"


def _pingan_pattern_facets(
    facets: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Freeze only decision-relevant and reviewer-useful facets on a cohort."""

    return {key: list(facets[key]) for key in _PINGAN_PATTERN_FACET_KEYS if facets.get(key)}


_ATTACK_FAMILY_LABELS = {
    "command_and_control": "命令与控制",
    "proxy_tunnel_activity": "代理 / 隧道",
    "denial_of_service": "拒绝服务",
    "malicious_outbound_connection": "恶意外联",
    "reverse_shell": "反弹 Shell",
    "webshell": "WebShell",
    "command_execution": "命令执行",
    "privilege_escalation": "权限提升",
    "lateral_movement": "横向移动",
    "vulnerability_exploitation": "漏洞利用",
}


def _pingan_behavior_label(
    request: LLMAnalysisRequest,
    facets: dict[str, list[str]],
) -> str:
    """Render canonical facets as an analyst-readable same-behavior name."""

    services = facets.get("network_service", [])
    vulnerabilities = facets.get("vulnerability_id", [])
    families = facets.get("attack_behavior_family", [])
    core_components = facets.get("behavior_component_core", [])
    parts: list[str] = []

    if "udp/1194" in {value.casefold() for value in services}:
        parts.append("OpenVPN")
    parts.extend(vulnerabilities[:2])
    for family in families:
        if family == "vulnerability_exploitation" and vulnerabilities:
            continue
        label = _ATTACK_FAMILY_LABELS.get(family)
        if label and not (label == "代理 / 隧道" and "OpenVPN" in parts):
            parts.append(label)
    parts.extend(_endpoint_behavior_labels(core_components))
    parts.extend(_network_service_label(value) for value in services[:2])

    unique = list(dict.fromkeys(part for part in parts if part))
    if not unique:
        fallback = request.detection.rule_name or request.classification.category or request.detection.detection_key or "未命名同类行为"
        unique.append(" ".join(fallback.split()))
    return " / ".join(unique)[:512]


def _endpoint_behavior_labels(components: list[str]) -> list[str]:
    by_prefix: dict[str, list[str]] = {}
    for component in components:
        prefix, separator, value = component.partition(":")
        if separator and value:
            by_prefix.setdefault(prefix, []).append(value)
    labels: list[str] = []
    if by_prefix.get("process_image"):
        labels.append(by_prefix["process_image"][0])
    if by_prefix.get("parent_service"):
        labels.append(f"{by_prefix['parent_service'][0]} 服务")
    if "windows_protected_registry_hive" in by_prefix.get("target_class", []):
        labels.append("Windows 受保护注册表配置单元")
    return labels


def _network_service_label(value: str) -> str:
    protocol, separator, port = value.partition("/")
    if separator and port:
        return f"{protocol.upper()} {port}"
    return value.upper()


def _project_pingan_facets(
    facets: dict[str, list[str]],
    *,
    request: LLMAnalysisRequest,
) -> dict[str, list[str]]:
    projected = {key: list(values) for key, values in facets.items()}
    signature = _detection_signature(request)
    if signature:
        _add_facet(projected, "detection_signature", signature)

    base_components = list(projected.get("behavior_component", []))
    tenant_components, tenant_core_components = _pingan_canonical_behavior_components(request)
    for family in _pingan_attack_behavior_families(request, projected):
        _add_facet(projected, "attack_behavior_family", family)
        _append_component(tenant_components, f"attack_family:{family}")
        _append_component(tenant_core_components, f"attack_family:{family}")
    for component in tenant_components:
        _add_facet(projected, "behavior_component", component)
    components = sorted(projected.get("behavior_component", []))
    if components:
        projected["behavior_component"] = components
    fingerprint_components = sorted(dict.fromkeys([*base_components, *tenant_core_components]))
    if fingerprint_components:
        projected["behavior_component_core"] = fingerprint_components
    if len(fingerprint_components) >= 2:
        projected["behavior_fingerprint"] = [
            stable_hash(
                {
                    "schema_version": "pingan.soc.memory_behavior_fingerprint.v5",
                    "components": fingerprint_components,
                }
            )
        ]
    else:
        projected.pop("behavior_fingerprint", None)

    strong_components = [value for value in components if _is_strong_behavior_component(value)]
    weak_components = [value for value in components if not _is_strong_behavior_component(value)]
    for component in strong_components:
        _add_facet(projected, "behavior_component_strong", component)
    for component in weak_components:
        _add_facet(projected, "behavior_component_weak", component)
    if components:
        projected["behavior_strength"] = ["strong" if strong_components else "weak_only"]

    _remove_duplicate_role_ips(projected)
    return projected


def _pingan_canonical_behavior_components(
    request: LLMAnalysisRequest,
) -> tuple[list[str], list[str]]:
    """Project stable endpoint behavior from canonical entities only."""

    entities = request.canonical_entities
    process = entities.process
    components: list[str] = []
    core_components: list[str] = []

    def add_core(value: str) -> None:
        _append_component(components, value)
        _append_component(core_components, value)

    process_image = _windows_leaf(process.process_path)
    if process_image:
        add_core(f"process_image:{process_image}")
    process_path = _normalized_windows_path_suffix(process.process_path)
    if process_path:
        add_core(f"process_path:{process_path}")

    for module in _command_modules(process.command_line):
        add_core(f"command_module:{module}")
    for switch in _command_switches(process.command_line):
        add_core(f"command_switch:{switch}")

    parent_service = _windows_service_name(process.parent_command_line)
    if parent_service:
        add_core(f"parent_service:{parent_service}")

    target_names: set[str] = set()
    for observation in entities.file.observations:
        if observation.relation.value != "endpoint_action_target":
            continue
        target_name = _windows_leaf(observation.file_path or observation.file_name)
        if target_name:
            target_names.add(target_name)
            _append_component(components, f"target_file:{target_name}")
    if target_names & {"sam", "security", "system"}:
        add_core("target_class:windows_protected_registry_hive")
    return sorted(components), sorted(core_components)


_PINGAN_ATTACK_CATEGORY_FAMILIES = {
    "command_and_control": "command_and_control",
    "c2": "command_and_control",
    "命令与控制": "command_and_control",
    "代理工具": "proxy_tunnel_activity",
    "代理隧道": "proxy_tunnel_activity",
    "vpn": "proxy_tunnel_activity",
    "拒绝服务": "denial_of_service",
    "dos": "denial_of_service",
    "ddos": "denial_of_service",
    "恶意外联": "malicious_outbound_connection",
    "反弹shell": "reverse_shell",
    "反弹 shell": "reverse_shell",
    "webshell": "webshell",
    "命令执行": "command_execution",
    "提权": "privilege_escalation",
    "横向移动": "lateral_movement",
}


def _pingan_attack_behavior_families(
    request: LLMAnalysisRequest,
    facets: dict[str, list[str]],
) -> list[str]:
    """Normalize PingAn classifications without importing raw field aliases."""

    families: list[str] = []
    category = " ".join((request.classification.category or "").split()).casefold()
    if category:
        family = _PINGAN_ATTACK_CATEGORY_FAMILIES.get(category)
        if family is None:
            token = re.sub(r"[^\w.-]+", "_", category, flags=re.UNICODE).strip("_")
            family = f"source_category:{token}" if token else None
        if family:
            families.append(family)
    if facets.get("vulnerability_id"):
        families.append("vulnerability_exploitation")
    return sorted(dict.fromkeys(families))


def _normalized_windows_path_suffix(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = ntpath.normpath(value.strip().replace("/", "\\")).casefold()
    _, suffix = ntpath.splitdrive(normalized)
    suffix = suffix.lstrip("\\").replace("\\", "/")
    return suffix if suffix and suffix != "." else None


def _windows_leaf(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    leaf = ntpath.basename(value.strip().replace("/", "\\")).casefold()
    return leaf or None


def _command_modules(value: str | None) -> list[str]:
    if value is None:
        return []
    return sorted(
        {
            match.group("module").casefold()
            for match in re.finditer(
                r"(?i)(?P<module>[a-z0-9_.-]+\.dll)\b",
                value,
            )
        }
    )


def _command_switches(value: str | None) -> list[str]:
    if value is None:
        return []
    return sorted(
        {
            match.group("switch").casefold()
            for match in re.finditer(
                r"(?:^|\s)/(?P<switch>[a-z][a-z0-9_-]*)\b",
                value,
                flags=re.IGNORECASE,
            )
        }
    )


def _windows_service_name(value: str | None) -> str | None:
    if value is None:
        return None
    match = re.search(
        r"(?:^|\s)-s\s+(?P<service>[^\s\"']+)",
        value,
        flags=re.IGNORECASE,
    )
    return match.group("service").casefold() if match else None


def _append_component(values: list[str], value: str) -> None:
    normalized = value.strip().casefold()
    if normalized and normalized not in values:
        values.append(normalized)


def _detection_signature(request: LLMAnalysisRequest) -> str | None:
    rule_name = " ".join((request.detection.rule_name or "").split()).casefold()
    if not rule_name:
        return None
    return stable_hash(
        {
            "schema_version": "pingan.soc.detection_signature.v1",
            "source_system": (request.source.source_system or "").strip().casefold(),
            "product": (request.source.product or "").strip().casefold(),
            "rule_name": rule_name,
        }
    )


def _is_strong_behavior_component(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized.startswith(("protocol:", "http_method:", "network_service:", "attack_family:")):
        return False
    return normalized != "scenario:web_attack"


def _remove_duplicate_role_ips(facets: dict[str, list[str]]) -> None:
    role_ips = {ip for value in facets.get("role_entity", []) if (ip := _role_ip(value)) is not None}
    if not role_ips:
        return
    entities = facets.get("entity")
    if not entities:
        return
    retained = [value for value in entities if not (value.strip().casefold().startswith("ip:") and value.partition(":")[2].strip().casefold() in role_ips)]
    if retained:
        facets["entity"] = retained
    else:
        facets.pop("entity", None)


def _role_ip(value: str) -> str | None:
    _, separator, candidate = value.partition(":")
    if not separator:
        return None
    normalized = candidate.strip().casefold()
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        return None
    return normalized


def _add_facet(
    facets: dict[str, list[str]],
    key: str,
    value: str,
) -> None:
    normalized = value.strip()
    if not normalized:
        return
    values = facets.setdefault(key, [])
    if normalized not in values:
        values.append(normalized)


__all__ = ["PingAnSocMemoryProfile"]
