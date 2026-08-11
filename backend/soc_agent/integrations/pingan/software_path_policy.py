"""PingAn EDR safe-software path signals for the generic tenant policy layer."""

from __future__ import annotations

from dataclasses import dataclass, field

from soc_agent.contracts import (
    AlertSourceType,
    AnalysisRun,
    TenantDispositionPolicy,
    TenantPolicySignal,
    TenantPolicySignalProviderStatus,
    TenantPolicySignalResolution,
)
from soc_agent.integrations.pingan.software_path_catalog import (
    PingAnSoftwarePathCatalog,
    PingAnSoftwarePathMatchType,
    is_executable_like_path,
    normalize_windows_path,
)
from soc_agent.utils.hashing import stable_hash

PINGAN_SOFTWARE_PATH_POLICY_SIGNAL_PROVIDER_ID = "pingan-edr-safe-software-path"
PINGAN_SOFTWARE_PATH_POLICY_SIGNAL_PROVIDER_VERSION = "pingan-safe-software-path-signal-v1"
PINGAN_SOFTWARE_PATH_FAST_DISPOSITION_SIGNAL = "endpoint.software_path.fast_disposition"
PINGAN_SOFTWARE_PATH_MATCH_SIGNAL = "endpoint.software_path.match"
PINGAN_SOFTWARE_PATH_ALL_SAFE_VALUE = "all_relevant_paths_safe"
_MAX_RELEVANT_PATHS = 50


@dataclass
class _PathCandidate:
    path: str
    normalized_path: str
    evidence_paths: set[str] = field(default_factory=set)
    md5s: set[str] = field(default_factory=set)


class PingAnSoftwarePathPolicySignalProvider:
    """Turn exact/path-family catalog matches into an auditable policy signal."""

    provider_id = PINGAN_SOFTWARE_PATH_POLICY_SIGNAL_PROVIDER_ID
    provider_version = PINGAN_SOFTWARE_PATH_POLICY_SIGNAL_PROVIDER_VERSION

    def __init__(self, catalog: PingAnSoftwarePathCatalog) -> None:
        self._catalog = catalog

    @classmethod
    def from_env(cls) -> PingAnSoftwarePathPolicySignalProvider:
        return cls(PingAnSoftwarePathCatalog.from_env())

    def resolve(
        self,
        policy: TenantDispositionPolicy,
        run: AnalysisRun,
        *,
        environment: str,
    ) -> TenantPolicySignalResolution:
        request = run.llm_analysis_request
        source_ref = f"catalog:{self._catalog.catalog_id}"
        if request is None or policy.tenant_id.casefold() != "pingan" or request.tenant_id != policy.tenant_id:
            return self._not_applicable(source_ref=source_ref)
        if request.source.source_type is not AlertSourceType.EDR:
            return self._not_applicable(source_ref=source_ref)
        if environment.casefold() not in {item.casefold() for item in policy.applicable_environments}:
            return self._not_applicable(source_ref=source_ref)

        candidates, extraction_warnings = _path_candidates(run)
        if not candidates:
            return self._completed(
                source_ref=source_ref,
                warnings=[*extraction_warnings, "No canonical EDR executable/process path was available for the fast disposition policy."],
            )
        if len(candidates) > _MAX_RELEVANT_PATHS:
            return self._completed(
                source_ref=source_ref,
                warnings=[*extraction_warnings, f"Relevant path count {len(candidates)} exceeds the governed limit {_MAX_RELEVANT_PATHS}; fast disposition was not emitted."],
            )

        path_signals: list[TenantPolicySignal] = []
        all_safe = True
        for candidate in candidates:
            signal, eligible = self._resolve_candidate(
                candidate,
                run=run,
                source_ref=source_ref,
            )
            path_signals.append(signal)
            all_safe = all_safe and eligible

        signals = list(path_signals)
        if all_safe:
            evidence_paths = sorted({path for candidate in candidates for path in candidate.evidence_paths})[:50]
            exact_count = sum(signal.signal_value == "exact_safe_path" for signal in path_signals)
            family_count = sum(signal.signal_value == "safe_path_family" for signal in path_signals)
            signals.append(
                TenantPolicySignal(
                    signal_id=_signal_id(run, PINGAN_SOFTWARE_PATH_FAST_DISPOSITION_SIGNAL, PINGAN_SOFTWARE_PATH_ALL_SAFE_VALUE, self._catalog.source_sha256),
                    signal_key=PINGAN_SOFTWARE_PATH_FAST_DISPOSITION_SIGNAL,
                    signal_value=PINGAN_SOFTWARE_PATH_ALL_SAFE_VALUE,
                    provider_id=self.provider_id,
                    provider_version=self.provider_version,
                    source_ref=source_ref,
                    source_hash=self._catalog.source_sha256,
                    evidence_paths=evidence_paths,
                    attributes={
                        "relevant_path_count": len(candidates),
                        "exact_path_match_count": exact_count,
                        "path_family_match_count": family_count,
                        "catalog_id": self._catalog.catalog_id,
                    },
                )
            )
        else:
            extraction_warnings.append("At least one relevant EDR path was unmatched or hash-conflicted; fast disposition was not emitted.")
        return self._completed(
            source_ref=source_ref,
            signals=signals,
            warnings=extraction_warnings,
        )

    def _resolve_candidate(
        self,
        candidate: _PathCandidate,
        *,
        run: AnalysisRun,
        source_ref: str,
    ) -> tuple[TenantPolicySignal, bool]:
        if len(candidate.md5s) > 1:
            return self._path_signal(
                candidate,
                run=run,
                value="hash_conflict",
                source_ref=source_ref,
                attributes={"observed_md5_count": len(candidate.md5s)},
            ), False

        query_md5 = next(iter(candidate.md5s), None)
        result = self._catalog.lookup(candidate.path, md5=query_md5)
        exact_hash_conflict = result.exact_safe_path_candidate and result.match_type is PingAnSoftwarePathMatchType.EXACT_PATH_HASH_MISMATCH
        exact_other_only = result.historical_context is not None and not result.exact_safe_path_candidate
        exact_eligible = result.exact_safe_path_candidate and not exact_hash_conflict
        family = result.path_family_context
        family_hash_eligible = family is not None and not exact_hash_conflict and not exact_other_only and (not query_md5 or not family.known_md5s or query_md5 in family.known_md5s)
        if exact_eligible:
            return self._path_signal(
                candidate,
                run=run,
                value="exact_safe_path",
                source_ref=source_ref,
                attributes={
                    "catalog_match_type": result.match_type.value,
                    "location_attention": result.location_attention.value,
                },
            ), True
        if family_hash_eligible:
            assert family is not None
            return self._path_signal(
                candidate,
                run=run,
                value="safe_path_family",
                source_ref=source_ref,
                attributes={
                    "family_id": family.family_id,
                    "family_pattern": family.pattern_path,
                    "family_member_count": family.member_path_count,
                    "location_attention": result.location_attention.value,
                },
            ), True
        if exact_other_only:
            value = "other_paths_only"
        elif query_md5 and (result.matched or family is not None):
            value = "hash_mismatch"
        else:
            value = "unmatched"
        return self._path_signal(
            candidate,
            run=run,
            value=value,
            source_ref=source_ref,
            attributes={
                "catalog_match_type": result.match_type.value,
                "location_attention": result.location_attention.value,
            },
        ), False

    def _path_signal(
        self,
        candidate: _PathCandidate,
        *,
        run: AnalysisRun,
        value: str,
        source_ref: str,
        attributes: dict[str, str | int | float | bool],
    ) -> TenantPolicySignal:
        return TenantPolicySignal(
            signal_id=_signal_id(
                run=run,
                key=PINGAN_SOFTWARE_PATH_MATCH_SIGNAL,
                value=value,
                discriminator=candidate.normalized_path,
            ),
            signal_key=PINGAN_SOFTWARE_PATH_MATCH_SIGNAL,
            signal_value=value,
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            source_ref=source_ref,
            source_hash=self._catalog.source_sha256,
            subject=candidate.path,
            evidence_paths=sorted(candidate.evidence_paths)[:50],
            attributes=attributes,
        )

    def _completed(
        self,
        *,
        source_ref: str,
        signals: list[TenantPolicySignal] | None = None,
        warnings: list[str] | None = None,
    ) -> TenantPolicySignalResolution:
        return TenantPolicySignalResolution(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            status=TenantPolicySignalProviderStatus.COMPLETED,
            source_ref=source_ref,
            source_hash=self._catalog.source_sha256,
            signals=signals or [],
            warnings=warnings or [],
        )

    def _not_applicable(self, *, source_ref: str) -> TenantPolicySignalResolution:
        return TenantPolicySignalResolution(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            status=TenantPolicySignalProviderStatus.NOT_APPLICABLE,
            source_ref=source_ref,
            source_hash=self._catalog.source_sha256,
        )


def _path_candidates(run: AnalysisRun) -> tuple[list[_PathCandidate], list[str]]:
    request = run.llm_analysis_request
    if request is None:
        return [], []
    by_path: dict[str, _PathCandidate] = {}
    warnings: list[str] = []
    process = request.canonical_entities.process
    _add_path_candidate(
        by_path,
        process.process_path,
        md5=process.md5,
        evidence_path="llm_analysis_request.canonical_entities.process.process_path",
        executable_required=False,
        warnings=warnings,
    )
    for observation_index, observation in enumerate(process.observations):
        for node_index, node in enumerate(observation.nodes):
            _add_path_candidate(
                by_path,
                node.process_path,
                md5=node.md5,
                evidence_path=(observation.evidence_path or f"llm_analysis_request.canonical_entities.process.observations[{observation_index}].nodes[{node_index}].process_path"),
                executable_required=False,
                warnings=warnings,
            )

    file_entity = request.canonical_entities.file
    _add_path_candidate(
        by_path,
        file_entity.file_path,
        md5=file_entity.md5,
        evidence_path="llm_analysis_request.canonical_entities.file.file_path",
        executable_required=True,
        warnings=warnings,
    )
    for observation_index, observation in enumerate(file_entity.observations):
        _add_path_candidate(
            by_path,
            observation.file_path,
            md5=observation.md5,
            evidence_path=(observation.evidence_path or f"llm_analysis_request.canonical_entities.file.observations[{observation_index}].file_path"),
            executable_required=True,
            warnings=warnings,
        )
    return sorted(by_path.values(), key=lambda item: item.normalized_path), warnings


def _add_path_candidate(
    by_path: dict[str, _PathCandidate],
    path: str | None,
    *,
    md5: str | None,
    evidence_path: str,
    executable_required: bool,
    warnings: list[str],
) -> None:
    if not path or (executable_required and not is_executable_like_path(path)):
        return
    try:
        normalized = normalize_windows_path(path)
    except ValueError:
        warnings.append(f"Invalid canonical path was excluded from fast disposition: {evidence_path}")
        return
    candidate = by_path.setdefault(
        normalized,
        _PathCandidate(path=path.strip(), normalized_path=normalized),
    )
    candidate.evidence_paths.add(evidence_path)
    if md5:
        candidate.md5s.add(md5.strip().casefold())


def _signal_id(
    run: AnalysisRun | None,
    key: str,
    value: str,
    discriminator: str,
) -> str:
    digest = stable_hash(
        {
            "run_id": run.run_id if run is not None else None,
            "key": key,
            "value": value,
            "discriminator": discriminator,
        }
    )
    return f"TPS-{digest[:20].upper()}"


__all__ = [
    "PINGAN_SOFTWARE_PATH_ALL_SAFE_VALUE",
    "PINGAN_SOFTWARE_PATH_FAST_DISPOSITION_SIGNAL",
    "PINGAN_SOFTWARE_PATH_MATCH_SIGNAL",
    "PingAnSoftwarePathPolicySignalProvider",
]
