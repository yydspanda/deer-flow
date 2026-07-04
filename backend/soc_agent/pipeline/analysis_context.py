"""Build bounded analysis context for stub or future LLM nodes."""

from __future__ import annotations

from soc_agent.contracts import AlertInput, ExtractedEntities, FactReconstructionResult, LLMAnalysisRequest
from soc_agent.skills import SocSkillResolver, build_soc_skill_context


def build_llm_analysis_request(
    alert: AlertInput,
    entities: ExtractedEntities,
    fact_reconstruction: FactReconstructionResult,
) -> LLMAnalysisRequest:
    """Convert runtime state into the only input shape analysis nodes consume."""

    conflict_types = sorted({report.conflict_type for report in fact_reconstruction.conflict_reports})
    warnings = [
        *fact_reconstruction.warnings,
        *entities.warnings,
    ]
    request = LLMAnalysisRequest(
        alert_id=alert.alert_id,
        source=alert.source,
        detection=alert.detection,
        classification=alert.classification,
        canonical_entities=alert.entities,
        extracted_entities=entities,
        fact_reconstruction=fact_reconstruction,
        primary_evidence_path=fact_reconstruction.selected_input_path,
        conflict_count=len(fact_reconstruction.conflict_reports),
        conflict_types=conflict_types,
        warnings=_dedupe(warnings),
    )
    skill_resolution = SocSkillResolver().resolve_for_analysis_request(request)
    request.skill_context = build_soc_skill_context(skill_resolution)
    return request


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
