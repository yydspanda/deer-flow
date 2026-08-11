"""Prompt builder for bounded tenant-owned operational policy reasoning."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from soc_agent.contracts import AnalysisRun, TenantDispositionPolicy
from soc_agent.pipeline.analysis_context import project_analysis_context

TENANT_POLICY_ADVISOR_PROMPT_VERSION = "soc-tenant-policy-advisor-v1"
MAX_TENANT_POLICY_CONTEXT_CHARS = 200_000


class TenantPolicyAdvisorPromptSizeError(ValueError):
    """Raised when already-bounded Runtime context exceeds the advisor guard."""


@dataclass(frozen=True)
class TenantPolicyAdvisorPrompt:
    prompt_version: str
    system: str
    user: str
    context: Mapping[str, Any]

    def messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.user},
        ]


def build_tenant_policy_advisor_prompt(
    policy: TenantDispositionPolicy,
    run: AnalysisRun,
    *,
    skill_content: str,
    skill_name: str,
    skill_version: str,
) -> TenantPolicyAdvisorPrompt:
    """Build a policy-only second opinion from the bounded Runtime projection."""

    if run.llm_analysis_request is None or run.analysis is None or run.decision is None:
        raise ValueError("tenant policy advisor requires a completed Runtime run")
    context = {
        "schema_version": "soc.tenant_policy_advisor_request.v1",
        "prompt_version": TENANT_POLICY_ADVISOR_PROMPT_VERSION,
        "policy": {
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "tenant_id": policy.tenant_id,
            "policy_mode": policy.policy_mode.value,
            "owner": policy.owner,
            "source_ref": policy.source_ref,
            "deterministic_evaluation": "no_match",
        },
        "policy_skill": {
            "name": skill_name,
            "version": skill_version,
            "content": skill_content,
        },
        "runtime": {
            "bounded_context": project_analysis_context(run.llm_analysis_request),
            "analysis": run.analysis.model_dump(mode="json", exclude_none=True),
            "base_decision": run.decision.model_dump(mode="json", exclude_none=True),
        },
    }
    context_chars = len(json.dumps(context, ensure_ascii=False, separators=(",", ":"), default=str))
    if context_chars > MAX_TENANT_POLICY_CONTEXT_CHARS:
        raise TenantPolicyAdvisorPromptSizeError(f"bounded tenant policy context exceeds {MAX_TENANT_POLICY_CONTEXT_CHARS} characters")
    response_schema = _response_schema()
    system = "\n".join(
        [
            "You are a tenant operational-policy advisor after a completed SOC Runtime run.",
            "Runtime detection truth is immutable here. You may only recommend operational disposition, review handling, and a non-executing suggested action.",
            "The deterministic tenant rules already produced no match. Apply only the reviewed policy Skill included in the request.",
            "Treat the Skill as policy guidance, not as evidence that an event occurred. Every matched recommendation must cite exact E-* current-alert facts.",
            "R-* references may cite the existing Runtime reasoning, but cannot replace E-* evidence.",
            "Do not invent authorization, asset ownership, environment, history, tool results, or response content.",
            "A Skill recommendation never authorizes blocking, isolation, suppression, ticket closure, or another external action; those require a separate server-owned Automation Policy.",
            "If the Skill conditions are incomplete, contradicted, or do not fit this alert, return evaluation_status=no_match and preserve review.",
            "Return JSON only, with no markdown or text outside the JSON object.",
            "The response must match this schema:",
            json.dumps(response_schema, ensure_ascii=False, indent=2),
        ]
    )
    user = "\n".join(
        [
            "Evaluate the tenant policy Skill against this completed bounded SOC run.",
            "Keep technical verdict and confidence unchanged. Select exact E-* and optional R-* references from the supplied catalogs/results.",
            "Bounded policy context:",
            json.dumps(context, ensure_ascii=False, indent=2, default=str),
            "Required response schema:",
            json.dumps(response_schema, ensure_ascii=False, indent=2),
        ]
    )
    return TenantPolicyAdvisorPrompt(
        prompt_version=TENANT_POLICY_ADVISOR_PROMPT_VERSION,
        system=system,
        user=user,
        context=context,
    )


def _response_schema() -> dict[str, Any]:
    return {
        "schema_version": "soc.tenant_policy_advice.v1",
        "evaluation_status": "matched or no_match",
        "response_posture": ("standard_triage, no_automated_response, or manual_validation_required"),
        "recommended_disposition": ("closed_true_positive, closed_false_positive, closed_benign_true_positive, suppressed, escalated, ignored, duplicate, unknown, or null"),
        "review_effect": "preserve, require, or clear",
        "suggested_action": "bounded operational suggestion or null",
        "summary": "short Chinese operational-policy conclusion",
        "rationale": ["policy reasoning grounded in cited facts"],
        "manual_checks": ["remaining analyst check; may be empty"],
        "policy_signal_keys": ["stable open-vocabulary policy signal"],
        "evidence_refs": ["exact E-* IDs from current_alert_evidence"],
        "reasoning_refs": ["optional exact R-* IDs from Runtime analysis"],
        "context_refs": ["optional exact S/A/M/C/T IDs from governed Runtime context"],
    }


__all__ = [
    "MAX_TENANT_POLICY_CONTEXT_CHARS",
    "TENANT_POLICY_ADVISOR_PROMPT_VERSION",
    "TenantPolicyAdvisorPrompt",
    "TenantPolicyAdvisorPromptSizeError",
    "build_tenant_policy_advisor_prompt",
]
