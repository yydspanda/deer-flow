"""PingAn same-class, occurrence and applicability semantics.

This module consumes canonical SOC facets produced after normalization. It does
not parse PingAn raw field aliases and it does not change Runtime decisions.
"""

from __future__ import annotations

import ipaddress
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


class PingAnSocMemoryProfile:
    """Conservative PingAn profile layered on the generic Memory Kernel."""

    identity = SocMemoryProfileIdentity(
        profile_id="pingan.soc",
        profile_version="3",
        feature_schema_version="pingan.soc.memory_features.v3",
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
        if detection_keys and behavior_fingerprints:
            detection_key = detection_keys[0]
            detection_signature = detection_signatures[0] if detection_signatures else None
            behavior_fingerprint = behavior_fingerprints[0]
            return MemoryPatternSignature(
                dimension=MemoryPatternDimension.COMPOUND,
                value=f"compound:{stable_hash({'detection_key': detection_key, 'detection_signature': detection_signature, 'behavior_fingerprint': behavior_fingerprint})}",
                label=f"{request.detection.rule_name or detection_key} + canonical behavior",
                origin=self.identity.feature_schema_version,
                facets=facets,
            )

        if detection_keys:
            value = detection_keys[0]
            signature = detection_signatures[0] if detection_signatures else None
            return MemoryPatternSignature(
                dimension=MemoryPatternDimension.DETECTION,
                value=_bounded_value(f"detection:{stable_hash({'detection_key': value, 'detection_signature': signature})}"),
                label=request.detection.rule_name or value,
                origin="pingan_adapter:canonical_detection",
                facets=facets,
            )

        if behavior_fingerprints:
            return MemoryPatternSignature(
                dimension=MemoryPatternDimension.BEHAVIOR,
                value=_bounded_value(behavior_fingerprints[0]),
                label="PingAn canonical behavior fingerprint",
                origin=self.identity.feature_schema_version,
                facets=facets,
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
        event_id = _canonical_event_id(run)
        if event_id:
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
                "behavior_component_strong",
                "behavior_component_weak",
                "behavior_fingerprint",
                "behavior_strength",
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


def _project_pingan_facets(
    facets: dict[str, list[str]],
    *,
    request: LLMAnalysisRequest,
) -> dict[str, list[str]]:
    projected = {key: list(values) for key, values in facets.items()}
    signature = _detection_signature(request)
    if signature:
        _add_facet(projected, "detection_signature", signature)

    components = projected.get("behavior_component", [])
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
    if normalized.startswith(("protocol:", "http_method:")):
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
