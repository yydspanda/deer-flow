"""PingAn same-class, occurrence and applicability semantics.

This module consumes canonical SOC facets produced after normalization. It does
not parse PingAn raw field aliases and it does not change Runtime decisions.
"""

from __future__ import annotations

from datetime import UTC, datetime

from soc_agent.contracts import (
    AnalysisRun,
    LLMAnalysisRequest,
    MemoryPatternDimension,
    MemoryPatternSignature,
    SocMemoryApplicabilitySpec,
)
from soc_agent.memory.facets import memory_facets_from_analysis_request
from soc_agent.memory.profiles import SocMemoryProfileIdentity
from soc_agent.normalizers import normalize_alert_payload
from soc_agent.utils.hashing import stable_hash


class PingAnSocMemoryProfile:
    """Conservative PingAn profile layered on the generic Memory Kernel."""

    identity = SocMemoryProfileIdentity(
        profile_id="pingan.soc",
        profile_version="2",
        feature_schema_version="pingan.soc.memory_features.v2",
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
        return memory_facets_from_analysis_request(request)

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
        behavior_fingerprints = facets.get("behavior_fingerprint", [])
        if detection_keys and behavior_fingerprints:
            detection_key = detection_keys[0]
            behavior_fingerprint = behavior_fingerprints[0]
            return MemoryPatternSignature(
                dimension=MemoryPatternDimension.COMPOUND,
                value=f"compound:{stable_hash({'detection_key': detection_key, 'behavior_fingerprint': behavior_fingerprint})}",
                label=f"{request.detection.rule_name or detection_key} + canonical behavior",
                origin=self.identity.feature_schema_version,
                facets=facets,
            )

        if detection_keys:
            value = detection_keys[0]
            return MemoryPatternSignature(
                dimension=MemoryPatternDimension.DETECTION,
                value=_bounded_value(value),
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
        required: dict[str, list[str]] = {}
        for key in ("detection_key", "behavior_fingerprint"):
            values = strong_anchor_facets.get(key)
            if values:
                required[key] = list(values)
        if not required:
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
                "role_entity",
                "entity",
                "environment",
            )
            if key not in required and consensus_facets.get(key)
        }
        is_compound = {"detection_key", "behavior_fingerprint"} <= set(required)
        context_only_required = ["detection_key", "environment"] if is_compound and optional.get("behavior_component") else []
        context_only_missing = ["behavior_fingerprint"] if context_only_required else []
        context_only_similarity = ["behavior_component"] if context_only_required else []
        return SocMemoryApplicabilitySpec(
            profile_id=self.identity.profile_id,
            profile_version=self.identity.profile_version,
            feature_schema_version=self.identity.feature_schema_version,
            required_facets=required,
            optional_facets=optional,
            minimum_optional_matches=0,
            minimum_strong_anchor_matches=len(set(required) & {"detection_key", "behavior_fingerprint"}),
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


__all__ = ["PingAnSocMemoryProfile"]
