"""Build stable references for current-alert evidence and governed context."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

from soc_agent.contracts import (
    AnalysisContextCatalogItem,
    AnalysisContextReferenceKind,
    AnalysisEvidenceCatalogItem,
    BoundedAnalysisEvidence,
    EvidenceItem,
    EvidenceTrustLevel,
    LLMAnalysisRequest,
)
from soc_agent.pipeline.analysis_context import project_analysis_context
from soc_agent.utils.hashing import stable_hash

MAX_ANALYSIS_EVIDENCE_CATALOG_ITEMS = 1200
MAX_CATALOG_SCALAR_CHARS = 4000

_DIRECT_FACT_PREFIXES = (
    "source.",
    "detection.",
    "classification.",
    "canonical_entities.",
)
_DIRECT_ENTITY_COLLECTIONS = frozenset(
    {
        "ips",
        "domains",
        "urls",
        "emails",
        "processes",
        "users",
        "hosts",
        "assets",
        "rule_codes",
        "rule_names",
        "rules",
    }
)
_FACT_RECONSTRUCTION_LEAVES = frozenset(
    {
        "canonical_path",
        "selected_from",
        "selected_value",
        "claim_type",
        "evidence_path",
        "observation_scope",
        "role",
        "value",
        "status",
        "conflict_type",
        "field_path",
        "left_value",
        "right_value",
        "candidate_value",
    }
)


def finalize_analysis_reference_catalogs(
    request: LLMAnalysisRequest,
) -> LLMAnalysisRequest:
    """Attach deterministic E-* plus governed S/A/M/C/T references.

    Existing M/C/T references supplied by a future retrieval/provider stage are
    preserved. Skill and adapter references are rebuilt from the exact bounded
    request so a replay cannot inherit stale prompt context.
    """

    base = request.model_copy(update={"evidence_catalog": []})
    evidence_catalog = build_analysis_evidence_catalog(base)
    external_context = [
        item
        for item in request.context_catalog
        if item.kind
        not in {
            AnalysisContextReferenceKind.SKILL,
            AnalysisContextReferenceKind.ADAPTER_CONTRACT,
        }
    ]
    context_catalog = _dedupe_context_items(
        [
            *external_context,
            *_skill_context_items(request),
            *_adapter_context_items(request),
        ]
    )
    return request.model_copy(
        update={
            "evidence_catalog": evidence_catalog,
            "context_catalog": context_catalog,
        }
    )


def build_analysis_evidence_catalog(
    request: LLMAnalysisRequest,
) -> list[AnalysisEvidenceCatalogItem]:
    """Index exact scalar facts in the model-visible current-alert projection."""

    context = project_analysis_context(request.model_copy(update={"evidence_catalog": [], "context_catalog": []}))
    candidates: list[tuple[str, Any, EvidenceTrustLevel]] = []
    for path, value in _iter_scalars(context):
        if not _projection_scalar_allowed(path):
            continue
        candidates.append((path, value, _trust_for_context_path(path, request)))
    candidates.extend(_parsed_evidence_scalars(request))

    unique: dict[tuple[str, str], tuple[str, Any, EvidenceTrustLevel]] = {}
    for path, value, trust in candidates:
        if not _catalog_scalar_allowed(value):
            continue
        key = (path, _stable_scalar_key(value))
        unique.setdefault(key, (path, value, trust))
    ordered = sorted(unique.values(), key=lambda item: (item[0], _stable_scalar_key(item[1])))
    if len(ordered) > MAX_ANALYSIS_EVIDENCE_CATALOG_ITEMS:
        raise ValueError(f"bounded analysis projection produced {len(ordered)} evidence facts; maximum is {MAX_ANALYSIS_EVIDENCE_CATALOG_ITEMS}")
    return [
        AnalysisEvidenceCatalogItem(
            evidence_ref=evidence_ref_for(path, value),
            source_path=path,
            value=value,
            value_type=_value_type(value),
            trust_level=trust,
        )
        for path, value, trust in ordered
    ]


def evidence_ref_for(path: str, value: Any) -> str:
    """Return a replay-stable reference for one exact path/value pair."""

    digest = stable_hash(
        {
            "namespace": "soc.current_alert_evidence.v1",
            "path": path,
            "value": value,
            "value_type": _value_type(value),
        }
    )
    return f"E-{digest[:12].upper()}"


def evidence_item_from_catalog(
    request: LLMAnalysisRequest,
    *,
    description: str,
    preferred_paths: tuple[str, ...] = (),
    preferred_value: Any | None = None,
) -> EvidenceItem:
    """Select one exact catalog item for deterministic analyzers and tests."""

    selected = next(
        (item for path in preferred_paths for item in request.evidence_catalog if item.source_path == path and (preferred_value is None or _same_scalar(item.value, preferred_value))),
        None,
    )
    if selected is None and preferred_value is not None:
        selected = next(
            (item for item in request.evidence_catalog if _same_scalar(item.value, preferred_value)),
            None,
        )
    if selected is None:
        selected = next(iter(request.evidence_catalog), None)
    if selected is None:
        raise ValueError("analysis request has no current-alert evidence catalog")
    return EvidenceItem(
        evidence_ref=selected.evidence_ref,
        source=selected.source_path,
        description=description,
        value=selected.value,
    )


def _parsed_evidence_scalars(
    request: LLMAnalysisRequest,
) -> list[tuple[str, Any, EvidenceTrustLevel]]:
    result: list[tuple[str, Any, EvidenceTrustLevel]] = []
    evidence_items: list[tuple[str, BoundedAnalysisEvidence]] = []
    if request.primary_evidence is not None:
        evidence_items.append(("evidence.primary_evidence.content", request.primary_evidence))
    evidence_items.extend((f"evidence.supplementary_evidence[{index}].content", item) for index, item in enumerate(request.supplementary_evidence))
    for context_path, evidence in evidence_items:
        try:
            parsed = json.loads(evidence.content)
        except (json.JSONDecodeError, TypeError):
            if _catalog_scalar_allowed(evidence.content):
                result.append((context_path, evidence.content, evidence.trust_level))
            continue
        result.extend(
            (path, value, evidence.trust_level)
            for path, value in _iter_scalars(
                parsed,
                path=f"{context_path}#parsed",
            )
        )
    return result


def _skill_context_items(
    request: LLMAnalysisRequest,
) -> list[AnalysisContextCatalogItem]:
    result: list[AnalysisContextCatalogItem] = []
    for skill in request.skill_context.selected_skills:
        digest = stable_hash(
            {
                "kind": "skill",
                "skill_name": skill.skill_name,
                "guidance_hash": skill.guidance_hash,
                "package_hash": skill.package_hash,
            }
        )
        result.append(
            AnalysisContextCatalogItem(
                context_ref=f"S-{digest[:12].upper()}",
                kind=AnalysisContextReferenceKind.SKILL,
                label=skill.skill_name,
                source_id=skill.guidance_source,
                summary=skill.guidance,
                content_hash=skill.guidance_hash,
            )
        )
    return result


def _adapter_context_items(
    request: LLMAnalysisRequest,
) -> list[AnalysisContextCatalogItem]:
    result: list[AnalysisContextCatalogItem] = []
    for semantic in request.source_field_semantics:
        if not semantic.participates_in_reasoning:
            continue
        digest = stable_hash(
            {
                "kind": "adapter_contract",
                "field_path": semantic.field_path,
                "semantic_type": semantic.semantic_type,
                "meaning": semantic.meaning,
            }
        )
        result.append(
            AnalysisContextCatalogItem(
                context_ref=f"A-{digest[:12].upper()}",
                kind=AnalysisContextReferenceKind.ADAPTER_CONTRACT,
                label=semantic.semantic_type,
                source_id=semantic.field_path,
                summary=semantic.meaning,
            )
        )
    return result


def _dedupe_context_items(
    items: list[AnalysisContextCatalogItem],
) -> list[AnalysisContextCatalogItem]:
    unique: dict[str, AnalysisContextCatalogItem] = {}
    for item in items:
        unique.setdefault(item.context_ref, item)
    return sorted(unique.values(), key=lambda item: item.context_ref)


def _iter_scalars(value: Any, *, path: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            yield from _iter_scalars(item, path=child)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_scalars(item, path=f"{path}[{index}]")
        return
    yield path, value


def _projection_scalar_allowed(path: str) -> bool:
    leaf = path.rsplit(".", 1)[-1].split("[", 1)[0]
    if leaf in {
        "schema_version",
        "parser_version",
        "schema_fingerprint",
        "guidance_hash",
        "package_hash",
        "estimated_token_count",
        "token_budget",
        "original_length",
    }:
        return False
    if path in {"alert_id", "tenant_id"}:
        return True
    if path.startswith(_DIRECT_FACT_PREFIXES):
        return True
    if path.startswith("extracted_entities."):
        collection = path.removeprefix("extracted_entities.").split("[", 1)[0]
        return collection in _DIRECT_ENTITY_COLLECTIONS
    if path.startswith("fact_reconstruction."):
        if path == "fact_reconstruction.conflict_count":
            return True
        if path.startswith("fact_reconstruction.conflict_types["):
            return True
        return leaf in _FACT_RECONSTRUCTION_LEAVES
    if path.startswith("evidence.highlights["):
        return leaf == "value"
    return False


def _trust_for_context_path(
    path: str,
    request: LLMAnalysisRequest,
) -> EvidenceTrustLevel:
    if path.startswith("evidence.primary_evidence") and request.primary_evidence is not None:
        return request.primary_evidence.trust_level
    for index, item in enumerate(request.supplementary_evidence):
        if path.startswith(f"evidence.supplementary_evidence[{index}]"):
            return item.trust_level
    return EvidenceTrustLevel.UNKNOWN


def _catalog_scalar_allowed(value: Any) -> bool:
    if value is None:
        return False
    if not isinstance(value, (str, int, float, bool)):
        return False
    if isinstance(value, str):
        return bool(value.strip()) and len(value) <= MAX_CATALOG_SCALAR_CHARS
    return True


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _stable_scalar_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _same_scalar(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


__all__ = [
    "MAX_ANALYSIS_EVIDENCE_CATALOG_ITEMS",
    "build_analysis_evidence_catalog",
    "evidence_item_from_catalog",
    "evidence_ref_for",
    "finalize_analysis_reference_catalogs",
]
