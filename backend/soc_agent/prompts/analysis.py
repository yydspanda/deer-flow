"""Prompt builder for the bounded SOC analysis node."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from soc_agent.contracts import LLMAnalysisRequest, Verdict

ANALYSIS_PROMPT_VERSION = "soc-analysis-v1"


@dataclass(frozen=True)
class AnalysisPrompt:
    """Versioned prompt payload passed to a future LLM client."""

    prompt_version: str
    system: str
    user: str
    context: Mapping[str, Any]
    response_schema: Mapping[str, Any]

    def messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.user},
        ]


def build_analysis_prompt(request: LLMAnalysisRequest) -> AnalysisPrompt:
    """Build the only prompt shape allowed for SOC alert analysis.

    The builder intentionally consumes ``LLMAnalysisRequest`` instead of raw
    vendor payloads. If later analysis needs raw evidence excerpts, add a
    bounded, sanitized field to that contract rather than bypassing it here.
    """

    response_schema = _analysis_response_schema()
    context = _analysis_context(request)
    return AnalysisPrompt(
        prompt_version=ANALYSIS_PROMPT_VERSION,
        system=_system_prompt(response_schema),
        user=_user_prompt(context, response_schema),
        context=context,
        response_schema=response_schema,
    )


def _system_prompt(response_schema: Mapping[str, Any]) -> str:
    verdict_values = ", ".join(item.value for item in Verdict)
    return "\n".join(
        [
            "You are a SOC alert triage analysis node inside a deterministic runtime.",
            "The runtime owns control flow, validation, persistence, and final routing.",
            "Analyze only the bounded analysis context provided by the user message.",
            "Do not assume missing facts, do not execute actions, and do not change the workflow.",
            "Treat field-trust, role candidates, conflict reports, and warnings as first-class evidence.",
            "When fields conflict, explain the uncertainty instead of silently choosing one side.",
            f"Allowed verdict values: {verdict_values}.",
            "Return JSON only. Do not include markdown, code fences, or explanatory text outside JSON.",
            "The JSON object must match this shape:",
            _to_pretty_json(response_schema),
        ]
    )


def _user_prompt(context: Mapping[str, Any], response_schema: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "Analyze this SOC alert using the bounded context below.",
            "Focus on whether the alert is likely true positive, false positive, suspicious, unknown, or needs review.",
            "Use the primary evidence path and field-trust data to avoid trusting processed fields blindly.",
            "",
            "Bounded analysis context:",
            _to_pretty_json(context),
            "",
            "Required JSON response schema:",
            _to_pretty_json(response_schema),
        ]
    )


def _analysis_context(request: LLMAnalysisRequest) -> dict[str, Any]:
    fact = request.fact_reconstruction
    return {
        "schema_version": request.schema_version,
        "prompt_version": ANALYSIS_PROMPT_VERSION,
        "alert_id": request.alert_id,
        "source": request.source.model_dump(mode="json", exclude_none=True),
        "detection": request.detection.model_dump(mode="json", exclude_none=True),
        "classification": request.classification.model_dump(mode="json", exclude_none=True),
        "canonical_entities": request.canonical_entities.model_dump(mode="json", exclude_none=True),
        "extracted_entities": request.extracted_entities.model_dump(mode="json", exclude_none=True),
        "evidence": {
            "primary_evidence_path": request.primary_evidence_path,
            "selected_input_path": fact.selected_input_path,
            "selected_input_available": fact.selected_input_available,
            "evidence_policy": fact.evidence_policy.model_dump(mode="json", exclude_none=True) if fact.evidence_policy is not None else None,
            "field_trusts": [item.model_dump(mode="json", exclude_none=True) for item in fact.field_trusts],
        },
        "fact_reconstruction": {
            "role_assignments": [item.model_dump(mode="json", exclude_none=True) for item in fact.role_assignments],
            "conflict_count": request.conflict_count,
            "conflict_types": request.conflict_types,
            "conflict_reports": [item.model_dump(mode="json", exclude_none=True) for item in fact.conflict_reports],
            "warnings": request.warnings,
        },
    }


def _analysis_response_schema() -> dict[str, Any]:
    return {
        "verdict": [item.value for item in Verdict],
        "confidence": "number from 0.0 to 1.0",
        "summary": "short analyst-facing Chinese summary, non-empty",
        "evidence": [
            {
                "source": "string, evidence source or context section",
                "description": "string, why this evidence matters",
                "value": "string, number, boolean, or null",
            }
        ],
        "reason": "Chinese reasoning summary, non-empty; include uncertainty when conflicts or fallback evidence exist",
        "recommended_action": "short action string, non-empty; no direct destructive action",
        "knowledge_candidates": ["optional candidate knowledge strings; candidates are pending review only and must not be treated as confirmed facts"],
    }


def _to_pretty_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
