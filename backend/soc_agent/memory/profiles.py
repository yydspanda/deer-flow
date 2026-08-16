"""Pluggable feature and cohort semantics for SOC operational memory.

The generic Memory Kernel owns lifecycle, persistence, retrieval and audit. A
profile owns only tenant/source-specific feature projection and same-class
semantics. Generic services receive this protocol through composition and never
import a tenant integration directly.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

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
from soc_agent.utils.hashing import stable_hash


@dataclass(frozen=True)
class SocMemoryProfileIdentity:
    """Stable identity recorded on queries, observations and candidates."""

    profile_id: str
    profile_version: str
    feature_schema_version: str


class SocMemoryProfile(Protocol):
    """Tenant/source plug-in used by the generic Memory Kernel."""

    identity: SocMemoryProfileIdentity

    def matches_request(self, request: LLMAnalysisRequest) -> bool: ...

    def project_query_facets(
        self,
        request: LLMAnalysisRequest,
    ) -> dict[str, list[str]]: ...

    def project_run_facets(
        self,
        run: AnalysisRun,
    ) -> dict[str, list[str]]: ...

    def build_pattern_signature(
        self,
        run: AnalysisRun,
        *,
        facets: dict[str, list[str]],
    ) -> MemoryPatternSignature: ...

    def build_occurrence_key(
        self,
        run: AnalysisRun,
        *,
        signature: MemoryPatternSignature,
        facets: dict[str, list[str]],
        observed_at: datetime,
    ) -> str: ...

    def build_applicability(
        self,
        *,
        consensus_facets: dict[str, list[str]],
        strong_anchor_facets: dict[str, list[str]],
    ) -> SocMemoryApplicabilitySpec | None: ...


class GenericSocMemoryProfile:
    """Portable fallback with conservative, vendor-neutral semantics."""

    identity = SocMemoryProfileIdentity(
        profile_id="soc.generic",
        profile_version="1",
        feature_schema_version="soc.memory_features.generic.v1",
    )

    def matches_request(self, request: LLMAnalysisRequest) -> bool:
        return True

    def project_query_facets(
        self,
        request: LLMAnalysisRequest,
    ) -> dict[str, list[str]]:
        return memory_facets_from_analysis_request(request)

    def project_run_facets(
        self,
        run: AnalysisRun,
    ) -> dict[str, list[str]]:
        return memory_facets_from_analysis_run(run)

    def build_pattern_signature(
        self,
        run: AnalysisRun,
        *,
        facets: dict[str, list[str]],
    ) -> MemoryPatternSignature:
        request = run.llm_analysis_request
        if request is None:
            raise ValueError("bounded analysis request is required")
        if request.detection.detection_key:
            return MemoryPatternSignature(
                dimension=MemoryPatternDimension.DETECTION,
                value=_normalize_pattern_value(request.detection.detection_key),
                label=request.detection.rule_name or request.detection.detection_key,
                origin="canonical_detection",
                facets=facets,
            )
        behavior = facets.get("behavior_fingerprint", [])
        if behavior:
            return MemoryPatternSignature(
                dimension=MemoryPatternDimension.BEHAVIOR,
                value=_normalize_pattern_value(behavior[0]),
                label="Canonical behavior fingerprint",
                origin=self.identity.feature_schema_version,
                facets=facets,
            )
        primary = next(
            (item for item in (run.analysis.scenario_assessments if run.analysis else []) if item.is_primary),
            None,
        )
        if primary is not None:
            return MemoryPatternSignature(
                dimension=MemoryPatternDimension.SCENARIO,
                value=_normalize_pattern_value(primary.scenario_key or primary.scenario_name),
                label=primary.scenario_name,
                origin=f"analysis:{primary.origin.value}",
                facets={
                    **facets,
                    "scenario_origin": [primary.origin.value],
                    "activity_stage": [primary.activity_stage.value],
                },
            )
        if request.classification.category:
            return MemoryPatternSignature(
                dimension=MemoryPatternDimension.CATEGORY,
                value=_normalize_pattern_value(request.classification.category),
                label=request.classification.category,
                origin="canonical_category",
                facets=facets,
            )
        raise ValueError("Runtime result has no detection, behavior, primary scenario, or category")

    def build_occurrence_key(
        self,
        run: AnalysisRun,
        *,
        signature: MemoryPatternSignature,
        facets: dict[str, list[str]],
        observed_at: datetime,
    ) -> str:
        return stable_hash(
            {
                "profile": self.identity.profile_id,
                "profile_version": self.identity.profile_version,
                "source_run": run.run_id,
                "source_alert": run.alert_id,
                "signature": signature.model_dump(mode="json"),
            }
        )

    def build_applicability(
        self,
        *,
        consensus_facets: dict[str, list[str]],
        strong_anchor_facets: dict[str, list[str]],
    ) -> SocMemoryApplicabilitySpec | None:
        if not strong_anchor_facets:
            return None
        required = _first_facet_group(
            strong_anchor_facets,
            (
                "behavior_fingerprint",
                "detection_key",
                "rule_code",
                "scenario_key",
                "conflict_type",
                "skill",
            ),
        )
        if not required:
            return None
        optional = _selected_facets(
            consensus_facets,
            (
                "source_type",
                "source_system",
                "product",
                "scenario_key",
                "role_entity",
                "entity",
                "environment",
            ),
            exclude=required,
        )
        return SocMemoryApplicabilitySpec(
            profile_id=self.identity.profile_id,
            profile_version=self.identity.profile_version,
            feature_schema_version=self.identity.feature_schema_version,
            required_facets=required,
            optional_facets=optional,
            minimum_optional_matches=0,
            minimum_strong_anchor_matches=1,
        )


class SocMemoryProfileRegistry:
    """Resolve one server-owned profile; caller input cannot select it."""

    def __init__(
        self,
        profiles: Iterable[SocMemoryProfile] = (),
        *,
        fallback: SocMemoryProfile | None = None,
    ) -> None:
        self._fallback = fallback or GenericSocMemoryProfile()
        ordered = list(profiles)
        identities = [profile.identity.profile_id for profile in ordered]
        if len(identities) != len(set(identities)):
            raise ValueError("SOC memory profile IDs must be unique")
        if self._fallback.identity.profile_id in identities:
            raise ValueError("fallback SOC memory profile must not be registered twice")
        self._profiles = tuple(ordered)
        self._by_id = {profile.identity.profile_id: profile for profile in (*self._profiles, self._fallback)}

    def resolve_request(self, request: LLMAnalysisRequest) -> SocMemoryProfile:
        return next(
            (profile for profile in self._profiles if profile.matches_request(request)),
            self._fallback,
        )

    def resolve_run(self, run: AnalysisRun) -> SocMemoryProfile:
        if run.llm_analysis_request is None:
            return self._fallback
        return self.resolve_request(run.llm_analysis_request)

    def get(self, profile_id: str) -> SocMemoryProfile | None:
        return self._by_id.get(profile_id)


def _normalize_pattern_value(value: str) -> str:
    normalized = " ".join(value.split()).casefold()
    if not normalized:
        raise ValueError("memory pattern value is blank")
    if len(normalized) <= 256:
        return normalized
    return f"sha256:{stable_hash(normalized)}"


def _first_facet_group(
    facets: dict[str, list[str]],
    keys: Iterable[str],
) -> dict[str, list[str]]:
    for key in keys:
        if facets.get(key):
            return {key: list(facets[key])}
    return {}


def _selected_facets(
    facets: dict[str, list[str]],
    keys: Iterable[str],
    *,
    exclude: dict[str, list[str]],
) -> dict[str, list[str]]:
    return {key: list(facets[key]) for key in keys if key not in exclude and facets.get(key)}


__all__ = [
    "GenericSocMemoryProfile",
    "SocMemoryProfile",
    "SocMemoryProfileIdentity",
    "SocMemoryProfileRegistry",
]
