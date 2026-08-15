"""Short model-facing aliases for stable SOC reference catalogs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from soc_agent.contracts import (
    AnalysisContextCatalogItem,
    AnalysisEvidenceCatalogItem,
)

MAX_MODEL_VISIBLE_REFERENCE_ITEMS = 100


@dataclass(frozen=True)
class ModelReferenceAliases:
    """Bidirectional aliases for one frozen model request."""

    alias_to_stable: dict[str, str]
    stable_to_alias: dict[str, str]


def build_model_reference_aliases(
    evidence_catalog: Sequence[AnalysisEvidenceCatalogItem],
    context_catalog: Sequence[AnalysisContextCatalogItem],
) -> ModelReferenceAliases:
    """Create deterministic aliases for references visible in the prompt."""

    alias_to_stable: dict[str, str] = {}
    stable_to_alias: dict[str, str] = {}
    for index, item in enumerate(
        evidence_catalog[:MAX_MODEL_VISIBLE_REFERENCE_ITEMS],
        start=1,
    ):
        alias = f"E-{index:03d}"
        alias_to_stable[alias] = item.evidence_ref
        stable_to_alias[item.evidence_ref] = alias

    namespace_counts: dict[str, int] = defaultdict(int)
    for item in context_catalog[:MAX_MODEL_VISIBLE_REFERENCE_ITEMS]:
        prefix = item.context_ref[0]
        namespace_counts[prefix] += 1
        alias = f"{prefix}-{namespace_counts[prefix]:03d}"
        alias_to_stable[alias] = item.context_ref
        stable_to_alias[item.context_ref] = alias
    return ModelReferenceAliases(
        alias_to_stable=alias_to_stable,
        stable_to_alias=stable_to_alias,
    )


def project_model_reference_aliases(
    context: Mapping[str, Any],
    aliases: ModelReferenceAliases,
) -> dict[str, Any]:
    """Replace stable catalog IDs only in the model-visible projection."""

    projected = dict(context)
    catalogs = dict(projected.get("reference_catalogs") or {})
    evidence = catalogs.get("current_alert_evidence")
    if isinstance(evidence, list):
        catalogs["current_alert_evidence"] = [_alias_catalog_item(item, field="evidence_ref", aliases=aliases) for item in evidence]
    role_entities = catalogs.get("role_entities")
    if isinstance(role_entities, list):
        catalogs["role_entities"] = [_alias_catalog_item(item, field="evidence_ref", aliases=aliases) for item in role_entities]
    reasoning_context = catalogs.get("reasoning_context")
    if isinstance(reasoning_context, list):
        catalogs["reasoning_context"] = [_alias_catalog_item(item, field="context_ref", aliases=aliases) for item in reasoning_context]
    projected["reference_catalogs"] = catalogs
    projected["model_reference_protocol"] = {
        "schema_version": "soc.model_reference_aliases.v1",
        "evidence_alias_pattern": "E-001",
        "context_alias_patterns": ["S-001", "A-001", "M-001", "C-001", "T-001"],
        "role_entity_refs_come_from": "reference_catalogs.role_entities",
        "runtime_restores_stable_references": True,
    }
    return projected


def _alias_catalog_item(
    item: Any,
    *,
    field: str,
    aliases: ModelReferenceAliases,
) -> Any:
    if not isinstance(item, Mapping):
        return item
    stable = item.get(field)
    if not isinstance(stable, str):
        return dict(item)
    alias = aliases.stable_to_alias.get(stable)
    if alias is None:
        return dict(item)
    return {**item, field: alias}


__all__ = [
    "MAX_MODEL_VISIBLE_REFERENCE_ITEMS",
    "ModelReferenceAliases",
    "build_model_reference_aliases",
    "project_model_reference_aliases",
]
