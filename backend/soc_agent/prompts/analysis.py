"""Prompt builder for the bounded SOC analysis node."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from soc_agent.contracts import LLMAnalysisRequest, Verdict
from soc_agent.pipeline.analysis_context import project_analysis_context

ANALYSIS_PROMPT_VERSION = "soc-analysis-v17"
MAX_ANALYSIS_CONTEXT_CHARS = 180_000


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
            "Assess network direction at three separate layers: observed wire flow, organization-boundary direction, and attacker/victim semantic direction.",
            "Do not equate source with attacker or destination with victim. Reverse connections, C2 callbacks, proxies, relays, CDN, NAT, and F5 SNAT may separate those roles.",
            "Use network_direction for the direction assessment and role_adjudication for semantic roles. Both must cite selected E-* facts and R-* reasoning from this response.",
            "Direction, role, and response-target context_refs may directly cite exact S/A/M/C/T items from the supplied context catalog; they do not need to duplicate the context_refs of a referenced R-* item.",
            (
                "A role may be resolved_from_evidence by the analyzer, but that is not human confirmation. "
                "Use tentative or conflicted for a concrete candidate entity; use unresolved with value=null "
                "only when no concrete entity can be assigned."
            ),
            "Response target proposals are action-specific suggestions only.",
            (
                "Every proposed target entity must exactly match one adjudicated entity by entity type and value. "
                "The action-specific target_role may differ from that entity's global semantic role; for example, "
                "a victim host may be the impacted_asset for isolation."
            ),
            "Propose the victim for host isolation, the attacker/C2 for network blocking, or the relevant account for disablement only when the cited evidence supports that target.",
            "Every response target must keep policy_review_required=true and automation_allowed=false; policy and authorization are evaluated after Runtime analysis.",
            "Treat upstream or deterministic scenario hypotheses as hints, not truth. Confirm, revise, or reject them from bounded evidence.",
            "Separate current-alert facts from reasoning. evidence[] contains only exact E-* catalog facts; reasoning[] contains interpretation and inference.",
            "Security expertise is expected: you may infer from general security knowledge or reviewed Skill guidance when the inference is explicitly labeled in reasoning.basis.",
            "A valid inference does not need to appear verbatim in the alert. Its cited current-alert facts and any required S/A/M/C/T context references must exist.",
            "Scenario names are open vocabulary: infer a more accurate scenario when the provided hints do not fit, without inventing a closed taxonomy.",
            "For each scenario assessment, classify the observed activity as detection_hit, attempt_observed, effect_observed, impact_confirmed, or indeterminate.",
            "Use detection_hit when only a detector assertion exists; attempt_observed when attack-like input or behavior is visible without a resulting system effect.",
            "Use effect_observed for a directly observed response or state change such as an issued session token, command output, new process, or file write; this still does not by itself prove material impact.",
            "Use impact_confirmed only when independent bounded evidence confirms asset, account, data, or business impact. Use indeterminate only when evidence cannot place the activity reliably.",
            "The first scenario assessment marked is_primary=true is the current primary explanation. If scenarios are present, exactly one must be primary.",
            "Every scenario cites E-* evidence_refs and R-* reasoning_refs from the same response.",
            "Use evidence coverage warnings to identify parser degradation, sanitized fields, truncation, and high-value canonical gaps.",
            "Use evidence.highlights as compact, adapter-governed values retained from messages outside the full supplementary-evidence budget; cite a representative highlight path and do not infer omitted sibling fields.",
            "Obey source_field_semantics from the adapter. Fields marked participates_in_reasoning=false are preserved for audit but must not support entities, facts, verdicts, or confidence.",
            (
                "Trust an upstream field within the exact meaning declared by its reviewed adapter contract. "
                "When that contract explicitly identifies a provider-reported session initiator or responder, "
                "accept that session role without demanding an independent SYN, flow record, or PCAP; require "
                "those only when the contract is ambiguous or the current alert contains an explicit "
                "proxy/NAT/forwarding caveat or same-observation contradiction."
            ),
            "A provider-reported session initiator is a network-session fact only; it does not by itself establish attacker, victim, compromise, or action authority.",
            (
                "Treat reviewed provider detection classifications and outcomes declared by source_field_semantics as trusted upstream assertions. "
                "Cite the exact source value and preserve that upstream origin; do not dismiss them as mere workflow noise or silently recast them as independently observed telemetry."
            ),
            "A provider_detection_outcome_assertion may support effect_observed when its exact high-trust value is visible and cited. It does not by itself establish impact_confirmed.",
            "When fields conflict, explain the uncertainty instead of silently choosing one side.",
            "Select every evidence item from reference_catalogs.current_alert_evidence. Copy its evidence_ref, source_path, and scalar value exactly.",
            "Select at most 40 evidence items. Prefer facts actually used by reasoning, scenario assessments, or knowledge candidates.",
            "evidence.description is only a short observation label. Do not use it as the place for security interpretation; put interpretation in reasoning[].",
            "Each distinct current-alert scalar fact needs its own E-* item. Do not serialize or synthesize arrays, objects, key=value text, or comma-joined facts.",
            "Each reasoning item must cite at least one selected E-* fact. Use basis=current_evidence for direct synthesis and basis=general_security_knowledge for security-domain inference.",
            "basis=skill requires an S-* reference; adapter_contract requires A-*; confirmed_memory requires M-*; governed_context requires C-*; tool_result requires T-*.",
            "The converse is also required: every S/A/M/C/T context_ref must have its matching skill/adapter_contract/confirmed_memory/governed_context/tool_result basis label.",
            "Use context_refs=[] when no governed context is needed. Never write none, null, prose, or E-* IDs in context_refs.",
            "Every E-* evidence_ref must appear at most once in evidence[].",
            "Never treat Skill, memory, adapter semantics, governed context, or tool output as proof that an uncited event occurred in this alert.",
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
            "knowledge_candidates are inert review suggestions. Link each candidate to E-* and R-* support, suggest a destination and scope, and never treat it as confirmed memory.",
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
        "schema_version": "soc.analysis_result.v4",
        "verdict": f"one of: {', '.join(item.value for item in Verdict)}",
        "confidence": "number from 0.0 to 1.0",
        "summary": "short analyst-facing Chinese summary, non-empty",
        "evidence": [
            {
                "evidence_ref": "exact E-* ID from current_alert_evidence",
                "source": "exact source_path paired with that E-* ID",
                "description": "short observation label only; put interpretation in reasoning",
                "value": "exact scalar paired with that E-* ID",
            }
        ],
        "reasoning": [
            {
                "schema_version": "soc.analysis_reasoning_item.v1",
                "reasoning_id": "R-01, R-02, ...; unique",
                "statement": "explicit security interpretation or inference",
                "basis": ["one or more of: current_evidence, general_security_knowledge, skill, adapter_contract, confirmed_memory, governed_context, tool_result"],
                "evidence_refs": ["selected E-* evidence IDs supporting this inference"],
                "context_refs": ["required S/A/M/C/T IDs, or empty when not needed"],
                "confidence": "number from 0.0 to 1.0",
            }
        ],
        "scenario_assessments": [
            {
                "schema_version": "soc.triage_scenario_assessment.v2",
                "scenario_name": "open-vocabulary scenario name",
                "scenario_key": "optional stable generic key, or null",
                "is_primary": "boolean; exactly one true when this array is non-empty",
                "origin": "one of: upstream_hint, inferred, hybrid",
                "confidence": "number from 0.0 to 1.0",
                "activity_stage": ("one of: detection_hit, attempt_observed, effect_observed, impact_confirmed, indeterminate"),
                "evidence_refs": ["E-* IDs selected in evidence; at least one"],
                "reasoning_refs": ["R-* IDs selected in reasoning; at least one"],
                "rationale": "bounded-evidence reasoning for this scenario",
                "competing_explanations": ["plausible benign or alternative explanation; may be empty"],
            }
        ],
        "network_direction": {
            "schema_version": "soc.network_direction_assessment.v1",
            "status": "one of: observed, inferred, conflicted, indeterminate",
            "observed_flow": "one of: source_to_destination, multiple_flows, not_available",
            "boundary_direction": "one of: external_to_internal, internal_to_external, internal_to_internal, external_to_external, proxy_mediated, indeterminate, not_applicable",
            "semantic_direction": "short open-vocabulary semantic direction, or null",
            "connection_initiator": "entity value when supported, or null",
            "intermediaries": ["proxy, relay, CDN, F5, NAT, or other intermediary entity values"],
            "confidence": "number from 0.0 to 1.0",
            "evidence_refs": ["selected E-* IDs; at least one"],
            "reasoning_refs": ["R-* IDs; at least one"],
            "context_refs": ["exact S/A/M/C/T IDs directly used for this assessment, or empty"],
            "rationale": "why the three direction layers were assigned",
            "evidence_gaps": ["missing facts that prevent stronger direction resolution"],
        },
        "role_adjudication": {
            "schema_version": "soc.role_adjudication_result.v1",
            "status": "one of: tentative, resolved_from_evidence, conflicted",
            "roles": [
                {
                    "role": "one of: initiator, responder, attacker, victim, impacted_asset, proxy, relay, scanner, c2",
                    "entity_type": "ip, domain, host, user, process, file, url, or another explicit type",
                    "value": "exact entity value for tentative/resolved/conflicted; null only when status=unresolved",
                    "status": "one of: tentative, resolved_from_evidence, conflicted, unresolved",
                    "confidence": "number from 0.0 to 1.0",
                    "evidence_refs": ["selected E-* IDs"],
                    "reasoning_refs": ["R-* IDs"],
                    "context_refs": ["exact S/A/M/C/T IDs directly used for this role, or empty"],
                    "rationale": "role-specific rationale",
                }
            ],
            "response_target_proposals": [
                {
                    "proposal_id": "RT-01, RT-02, ...; unique",
                    "action_kind": "action-specific suggestion such as isolate_host or block_ip",
                    "target_type": "ip, domain, host, user, process, file, url, or another explicit type",
                    "target_value": "exact proposed target entity",
                    "target_role": "action-specific role for this target; one of: initiator, responder, attacker, victim, impacted_asset, proxy, relay, scanner, c2",
                    "confidence": "number from 0.0 to 1.0",
                    "evidence_refs": ["selected E-* IDs"],
                    "reasoning_refs": ["R-* IDs"],
                    "context_refs": ["exact S/A/M/C/T IDs directly used for this target proposal, or empty"],
                    "rationale": "why this target fits this action",
                    "policy_review_required": True,
                    "automation_allowed": False,
                }
            ],
            "conflicts": ["role conflict retained for audit"],
            "evidence_gaps": ["missing evidence needed to improve role assignment"],
            "rationale": "overall semantic role adjudication",
        },
        "evidence_gaps": ["missing evidence that would materially change or strengthen the conclusion"],
        "manual_checks": ["concrete analyst verification step; at least one is required"],
        "reason": "Chinese reasoning summary, non-empty; include uncertainty when conflicts or fallback evidence exist",
        "recommended_action": "short action string, non-empty; no direct destructive action",
        "knowledge_candidates": [
            {
                "schema_version": "soc.analysis_knowledge_candidate.v1",
                "candidate_id": "K-01, K-02, ...; unique",
                "statement": "one reusable candidate statement",
                "destination_hint": "one of: general_skill, tenant_memory, governed_context, provider_requirement, adapter_mapping, tenant_policy, evaluation_fixture, reject_or_verify",
                "scope_hint": "one of: global, tenant, provider, source, detection, scenario, event",
                "evidence_refs": ["supporting selected E-* IDs"],
                "reasoning_refs": ["supporting R-* IDs"],
                "rationale": "why this may be reusable and what still requires review",
            }
        ],
    }


def analysis_response_schema() -> dict[str, Any]:
    """Return the public bounded-output schema used by correction prompts."""

    return _analysis_response_schema()


__all__ = [
    "ANALYSIS_PROMPT_VERSION",
    "AnalysisPrompt",
    "AnalysisPromptSizeError",
    "analysis_response_schema",
    "build_analysis_prompt",
]


def _to_pretty_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
