"""Shared vendor-neutral facets for SOC memory admission and retrieval."""

from __future__ import annotations

from typing import TYPE_CHECKING

from soc_agent.contracts import (
    AdjudicatedRoleStatus,
    AlertInput,
    AnalysisRun,
    LLMAnalysisRequest,
    RoleResolutionStatus,
)
from soc_agent.utils.hashing import stable_hash

if TYPE_CHECKING:
    from collections.abc import Iterable


_RESOLVED_FACT_ROLE_STATUSES = frozenset(
    {
        RoleResolutionStatus.OBSERVED,
        RoleResolutionStatus.CONFIRMED,
    }
)
_RESOLVED_MODEL_ROLE_STATUSES = frozenset(
    {
        AdjudicatedRoleStatus.RESOLVED_FROM_EVIDENCE,
    }
)


def memory_facets_from_analysis_request(
    request: LLMAnalysisRequest,
) -> dict[str, list[str]]:
    """Build replay-stable pre-LLM facets from the canonical analysis request."""

    facets: dict[str, list[str]] = {}
    _add(facets, "source_type", request.source.source_type.value)
    _add(facets, "source_system", request.source.source_system)
    _add(facets, "vendor", request.source.vendor)
    _add(facets, "product", request.source.product)
    _add(facets, "integration_name", request.source.integration_name)
    _add(facets, "detection_key", request.detection.detection_key)
    _add(facets, "rule_code", request.detection.rule_code)
    _add(facets, "rule_name", request.detection.rule_name)
    _add(facets, "category", request.classification.category)
    _add(facets, "severity", request.classification.severity)
    _add(facets, "environment", request.environment)

    for mention in request.extracted_entities.mentions[:80]:
        _add(facets, "entity", mention.key)
    for conflict_type in request.conflict_types:
        _add(facets, "conflict_type", conflict_type)
    for skill in request.skill_context.selected_skills:
        _add(facets, "skill", skill.skill_name)

    hypotheses = sorted(
        request.fact_reconstruction.scenario_hypotheses,
        key=lambda item: (item.status == "confirmed", item.confidence, item.scenario_type),
        reverse=True,
    )
    for hypothesis in hypotheses[:8]:
        _add(facets, "scenario_key", hypothesis.scenario_type)

    for resolution in request.fact_reconstruction.role_resolutions:
        if resolution.status in _RESOLVED_FACT_ROLE_STATUSES and resolution.selected_value:
            _add(
                facets,
                "role_entity",
                _role_entity(resolution.role, resolution.selected_value),
            )

    behavior_components = _behavior_components(request)
    for component in behavior_components:
        _add(facets, "behavior_component", component)
    if len(behavior_components) >= 2:
        _add(
            facets,
            "behavior_fingerprint",
            stable_hash(
                {
                    "schema_version": "soc.memory_behavior_fingerprint.v1",
                    "components": behavior_components,
                }
            ),
        )
    return facets


def memory_facets_from_analysis_run(
    run: AnalysisRun,
    *,
    alert: AlertInput | None = None,
) -> dict[str, list[str]]:
    """Build reusable facets for a reviewed artifact without lineage IDs."""

    if run.llm_analysis_request is not None:
        facets = memory_facets_from_analysis_request(run.llm_analysis_request)
    else:
        facets = _facets_from_alert_or_report(run, alert=alert)

    if run.analysis is not None:
        for assessment in run.analysis.scenario_assessments:
            _add(facets, "scenario_key", assessment.scenario_key)
        for role in run.analysis.role_adjudication.roles:
            if role.status in _RESOLVED_MODEL_ROLE_STATUSES and role.value is not None:
                _add(
                    facets,
                    "role_entity",
                    _role_entity(role.role.value, role.value),
                )

    if run.role_adjudication_revisions:
        latest = max(
            run.role_adjudication_revisions,
            key=lambda item: item.revision,
        )
        for role in latest.roles:
            _add(
                facets,
                "role_entity",
                _role_entity(role.role.value, role.value),
            )
        _add(facets, "role_confirmation", "human_confirmed")
    return facets


def _facets_from_alert_or_report(
    run: AnalysisRun,
    *,
    alert: AlertInput | None,
) -> dict[str, list[str]]:
    facets: dict[str, list[str]] = {}
    if alert is not None:
        _add(facets, "source_type", alert.source.source_type.value)
        _add(facets, "source_system", alert.source.source_system)
        _add(facets, "vendor", alert.source.vendor)
        _add(facets, "product", alert.source.product)
        _add(facets, "integration_name", alert.source.integration_name)
        _add(facets, "detection_key", alert.detection.detection_key)
        _add(facets, "rule_code", alert.detection.rule_code)
        _add(facets, "rule_name", alert.detection.rule_name)
        _add(facets, "category", alert.classification.category)
        _add(facets, "severity", alert.classification.severity)
        environment = alert.extensions.get("environment")
        _add(
            facets,
            "environment",
            environment if isinstance(environment, str) else None,
        )
    elif run.normalization_report is not None:
        _add(facets, "source_type", run.normalization_report.source_type.value)
        _add(facets, "source_system", run.normalization_report.source_system)

    if run.entities is not None:
        for mention in run.entities.mentions[:80]:
            _add(facets, "entity", mention.key)
        for value in run.entities.rule_codes:
            _add(facets, "rule_code", value)
        for value in run.entities.rule_names:
            _add(facets, "rule_name", value)
    return facets


def _behavior_components(request: LLMAnalysisRequest) -> list[str]:
    entities = request.canonical_entities
    components: list[str] = []
    for hypothesis in request.fact_reconstruction.scenario_hypotheses[:8]:
        _append(components, f"scenario:{hypothesis.scenario_type}")
    for value in (
        entities.process.process_name,
        entities.process.parent_process_name,
    ):
        if value:
            _append(components, f"process:{_leaf_name(value)}")
    for value in (
        entities.network.protocol,
        entities.network.application_protocol,
        entities.http.protocol,
    ):
        if value:
            _append(components, f"protocol:{value}")
    if entities.http.method:
        _append(components, f"http_method:{entities.http.method}")
    for technique in request.classification.technique[:20]:
        _append(components, f"technique:{technique}")
    for mention in request.extracted_entities.mentions:
        if mention.kind.value == "behavior":
            _append(components, f"behavior:{mention.value}")
        if len(components) >= 40:
            break
    return sorted(components)


def _role_entity(role: str, value: str) -> str:
    return f"{str(role).strip().casefold()}:{str(value).strip().casefold()}"


def _leaf_name(value: str) -> str:
    return value.strip().replace("\\", "/").rsplit("/", 1)[-1].casefold()


def _add(
    facets: dict[str, list[str]],
    key: str,
    value: str | None,
) -> None:
    normalized = str(value).strip() if value is not None else ""
    if not normalized:
        return
    values = facets.setdefault(key, [])
    if normalized not in values:
        values.append(normalized)


def _append(values: list[str], value: str) -> None:
    normalized = value.strip().casefold()
    if normalized and normalized not in values:
        values.append(normalized)


def merge_memory_facets(
    *facet_sets: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Merge facet sets while retaining deterministic insertion order."""

    merged: dict[str, list[str]] = {}
    for facets in facet_sets:
        for key, values in facets.items():
            for value in values:
                _add(merged, key, value)
    return merged


def reusable_facet_values(
    facets: dict[str, list[str]],
    keys: Iterable[str],
) -> dict[str, list[str]]:
    """Select reusable facets without copying event lineage identifiers."""

    allowed = set(keys)
    return {key: list(values) for key, values in facets.items() if key in allowed and values}


__all__ = [
    "memory_facets_from_analysis_request",
    "memory_facets_from_analysis_run",
    "merge_memory_facets",
    "reusable_facet_values",
]
