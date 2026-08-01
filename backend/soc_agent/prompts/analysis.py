"""Prompt builder for the bounded SOC analysis node."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from soc_agent.contracts import LLMAnalysisRequest, Verdict
from soc_agent.pipeline.analysis_context import project_analysis_context

ANALYSIS_PROMPT_VERSION = "soc-analysis-v8"
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
            "Treat upstream or deterministic scenario hypotheses as hints, not truth. Confirm, revise, or reject them from bounded evidence.",
            "Scenario names are open vocabulary: infer a more accurate scenario when the provided hints do not fit, without inventing a closed taxonomy.",
            "For each scenario assessment, classify the observed activity as detection_hit, attempt_observed, effect_observed, impact_confirmed, or indeterminate.",
            "Use detection_hit when only a detector assertion exists; attempt_observed when attack-like input or behavior is visible without a resulting system effect.",
            "Use effect_observed for a directly observed response or state change such as an issued session token, command output, new process, or file write; this still does not by itself prove material impact.",
            "Use impact_confirmed only when independent bounded evidence confirms asset, account, data, or business impact. Use indeterminate only when evidence cannot place the activity reliably.",
            "The first scenario assessment marked is_primary=true is the current primary explanation. If scenarios are present, exactly one must be primary.",
            "Scenario evidence_indices are zero-based indexes into the response evidence array; every scenario must cite at least one evidence item.",
            "Use evidence coverage warnings to identify parser degradation, sanitized fields, truncation, and high-value canonical gaps.",
            "Use evidence.highlights as compact, adapter-governed values retained from messages outside the full supplementary-evidence budget; cite a representative highlight path and do not infer omitted sibling fields.",
            "Obey source_field_semantics from the adapter. Fields marked participates_in_reasoning=false are preserved for audit but must not support entities, facts, verdicts, or confidence.",
            (
                "Treat reviewed provider detection classifications and outcomes declared by source_field_semantics as trusted upstream assertions. "
                "Cite the exact source value and preserve that upstream origin; do not dismiss them as mere workflow noise or silently recast them as independently observed telemetry."
            ),
            "A provider_detection_outcome_assertion may support effect_observed when its exact high-trust value is visible and cited. It does not by itself establish impact_confirmed.",
            "When fields conflict, explain the uncertainty instead of silently choosing one side.",
            "Every evidence item must quote a value present in the bounded context and use an exact dotted context path "
            "or one of these source sections: alert_id, source, detection, classification, entities, canonical_entities, "
            "extracted_entities, fact_reconstruction, primary_evidence, supplementary_evidence, evidence_coverage, "
            "skill_context.",
            "Each evidence source must identify exactly one source section or path; do not combine multiple paths into a comma-separated source.",
            "A BoundedAnalysisEvidence source_path may be extended with an exact projected_field_paths entry using #parsed, #decoded, or #repaired only when the quoted value is visible inside that bounded evidence content.",
            "An evidence description may interpret its quoted value, but it must not introduce additional sibling-field facts; cite each additional fact as a separate evidence item with its own exact source.",
            "The evidence description must be supported by that item's source and value alone. Never use one item to describe a token, header, rule label, role, or asset fact that is absent from its quoted value.",
            "Never serialize a whole object or array as one evidence value. Cite the smallest relevant scalar leaf with its exact #parsed, #decoded, or #repaired path.",
            "Copy evidence.value verbatim from that scalar leaf. Do not prepend a field name, path, label, or key= text unless those characters are part of the source scalar itself.",
            "When a statement needs multiple scalar facts, such as an IP address and a port, emit one evidence item per fact and cite each exact source path.",
            (
                "An exact visible <ENCODED:...:OMITTED> marker may establish only that the named source value existed, matched the stated encoding shape, "
                "and was omitted at the model boundary. Quote the marker-bearing scalar and exact source path."
            ),
            "An encoded-omission marker does not reveal the hidden bytes and cannot prove token validity, identity, privileges, or security outcome. Never invent or cite its private full hash or omitted content.",
            "Redaction or projection metadata alone does not prove that a username, password, token, or other secret existed in the source.",
            "Claim a hidden field only when the quoted value itself contains an explicit source redaction indicator.",
            "Separate an observed attempt from a confirmed outcome. HTTP status 200 proves only that an HTTP response was observed; it does not prove exploit, command, file-write, or compromise success.",
            "Workflow fields such as blocked, banned, ignored, transferred, or closed describe handling state and do not by themselves prove attack success or failure.",
            (
                "Claim a successful security outcome only when bounded evidence contains an explicit outcome artifact such as execution output, a created file, "
                "a resulting process, endpoint telemetry, or an exact high-trust provider outcome assertion declared by source_field_semantics."
            ),
            "Do not fabricate correlation, memory, authorization, asset, threat-intelligence, or tool results that are absent from the bounded context.",
            "Always provide a current verdict even when evidence is incomplete. State the remaining evidence gaps and executable manual checks separately.",
            "recommended_action is a safe routing suggestion only; manual_checks are concrete analyst verification steps. Neither may claim an action was executed.",
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
            "Produce open-vocabulary scenario assessments, distinguish behavior stage from verdict, and preserve competing benign explanations.",
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
        "schema_version": "soc.analysis_result.v2",
        "verdict": f"one of: {', '.join(item.value for item in Verdict)}",
        "confidence": "number from 0.0 to 1.0",
        "summary": "short analyst-facing Chinese summary, non-empty",
        "evidence": [
            {
                "source": "exact bounded-context path/section, or bounded source_path#parsed.field.path",
                "description": "why this exact quoted value matters; no sibling-field facts",
                "value": "verbatim scalar leaf: string, number, boolean, or null; never synthesized key=value text",
            }
        ],
        "scenario_assessments": [
            {
                "schema_version": "soc.triage_scenario_assessment.v1",
                "scenario_name": "open-vocabulary scenario name",
                "scenario_key": "optional stable generic key, or null",
                "is_primary": "boolean; exactly one true when this array is non-empty",
                "origin": "one of: upstream_hint, inferred, hybrid",
                "confidence": "number from 0.0 to 1.0",
                "activity_stage": ("one of: detection_hit, attempt_observed, effect_observed, impact_confirmed, indeterminate"),
                "evidence_indices": ["zero-based indexes into evidence; at least one"],
                "rationale": "bounded-evidence reasoning for this scenario",
                "competing_explanations": ["plausible benign or alternative explanation; may be empty"],
            }
        ],
        "evidence_gaps": ["missing evidence that would materially change or strengthen the conclusion"],
        "manual_checks": ["concrete analyst verification step; at least one is required"],
        "reason": "Chinese reasoning summary, non-empty; include uncertainty when conflicts or fallback evidence exist",
        "recommended_action": "short action string, non-empty; no direct destructive action",
        "knowledge_candidates": ["optional candidate knowledge strings; candidates are pending review only and must not be treated as confirmed facts"],
    }


def _to_pretty_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
