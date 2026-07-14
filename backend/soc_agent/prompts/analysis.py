"""Prompt builder for the bounded SOC analysis node."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from soc_agent.contracts import LLMAnalysisRequest, Verdict
from soc_agent.pipeline.analysis_context import project_analysis_context

ANALYSIS_PROMPT_VERSION = "soc-analysis-v2"
MAX_ANALYSIS_CONTEXT_CHARS = 120_000


class AnalysisPromptSizeError(ValueError):
    """Raised when bounded projections still exceed the model context guard."""


@dataclass(frozen=True)
class AnalysisPrompt:
    """Versioned prompt payload passed to the configured LLM client."""

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
    context = project_analysis_context(request)
    context["prompt_version"] = ANALYSIS_PROMPT_VERSION
    context_chars = len(json.dumps(context, ensure_ascii=False, separators=(",", ":"), default=str))
    if context_chars > MAX_ANALYSIS_CONTEXT_CHARS:
        raise AnalysisPromptSizeError(f"bounded analysis context exceeds {MAX_ANALYSIS_CONTEXT_CHARS} characters")
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
            "Keep evidence trust separate from semantic confidence: a faithfully parsed vendor field may still assert the wrong attacker or victim role.",
            "Treat tentative or conflicted role resolutions as provisional and cite their evidence gaps.",
            "Use evidence coverage warnings to identify parser degradation, sanitized fields, truncation, and high-value canonical gaps.",
            "When fields conflict, explain the uncertainty instead of silently choosing one side.",
            "Every evidence item must quote a value present in the bounded context and use an exact dotted context path "
            "or one of these source sections: alert_id, source, detection, classification, entities, canonical_entities, "
            "extracted_entities, fact_reconstruction, primary_evidence, supplementary_evidence, evidence_coverage, "
            "skill_context.",
            "A BoundedAnalysisEvidence source_path may be extended as source_path#parsed.field.path only when that parsed field and quoted value are visible inside the corresponding bounded evidence content.",
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


def _analysis_response_schema() -> dict[str, Any]:
    return {
        "verdict": [item.value for item in Verdict],
        "confidence": "number from 0.0 to 1.0",
        "summary": "short analyst-facing Chinese summary, non-empty",
        "evidence": [
            {
                "source": "exact bounded-context path/section, or bounded source_path#parsed.field.path",
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
