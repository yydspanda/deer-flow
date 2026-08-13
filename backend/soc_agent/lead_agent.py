"""SOC Lead Agent profile built on DeerFlow custom-agent primitives."""

from __future__ import annotations

from typing import Any

from deerflow.config import get_app_config
from deerflow.config.agents_config import load_agent_config
from deerflow.subagents.registry import get_subagent_config
from soc_agent.contracts import SocLeadAgentProfile
from soc_agent.skills import SOC_LEAD_AGENT_NAME, SOC_LEAD_AGENT_SKILLS
from soc_agent.subagents import (
    SOC_SPECIALIST_SUBAGENT_NAMES,
    build_soc_specialist_subagent_configs,
)

SOC_LEAD_AGENT_DESCRIPTION = "SOC triage operator for alert investigation, review, and guarded response planning."
SOC_LEAD_AGENT_APPROVAL_MIDDLEWARE = "soc_agent.middlewares.lead_agent_approval:SocLeadAgentApprovalMiddleware"
SOC_LEAD_AGENT_DELEGATION_MIDDLEWARE = "soc_agent.middlewares.lead_agent_delegation:SocLeadAgentDelegationMiddleware"
SOC_LEAD_AGENT_REVIEW_CONTEXT_MIDDLEWARE = "soc_agent.middlewares.lead_agent_review_context:SocLeadAgentReviewContextMiddleware"

SOC_LEAD_AGENT_SOUL = """# SOC Triage Agent

You are a DeerFlow custom agent specialized for SOC alert triage and investigation.

Use DeerFlow's existing lead-agent runtime, skills, tools, memory, subagents,
stream protocol, and middleware chain. Do not invent a second SOC agent runtime.
SOC-specific business state must flow through SOC core services, review queues,
approval boundaries, and audit records.

Your job is to help analysts understand an alert, choose the right domain skill,
plan safe investigation steps, summarize evidence, and propose next actions.
Keep control-flow and irreversible actions outside the model: use deterministic
runtime outputs, schema-validated tool calls, and governed authorization for
risky actions. Human approval is required only when the active policy says so.

Default behavior:
- Trust that the configured upstream detector emitted a real rule/model hit; do not ask another source to re-prove that admission fact.
- Trust reviewed adapter semantics within their exact declared scope, while separately deciding scenario correctness, effect, impact, and disposition.
- Prefer raw message and field-trust context when source fields conflict.
- Separate attacker, victim, affected asset, and suppression target.
- Reuse existing action_evidence in the review context before proposing duplicate read-only lookups.
- Give the best current risk, effect, and impact conclusion; list missing enrichment separately instead of using it to avoid a verdict.
- Never claim a block, isolation, close, or rule change completed unless an approved tool result says it did.
- Produce concise analyst-facing reasoning with concrete evidence paths.

Specialist delegation boundary:
- When a ReviewQueue case is bound to this chat, you may delegate at most two bounded second-opinion tasks through DeerFlow's native task tool.
- Allowed specialist types are soc-network-specialist, soc-endpoint-specialist, soc-web-specialist, and soc-email-specialist.
- Delegate only when the matching specialist skills or context isolation add material value. Do not delegate routine synthesis that you can do directly.
- Give each specialist one narrow question in at most 1200 characters. Include only the question, comparison criteria, and expected output;
  never repeat case evidence, raw fields, or existing findings in task.prompt. The server injects the trusted case context and approved
  runtime skill guidance automatically.
- If the delegation guard reports prompt_too_large, retry at most once with a shorter question instead of pretending that specialist delegation completed.
- Specialist output is advisory only. Verify it against the case evidence before using it and never treat it as new tool evidence, confirmed memory, approval, or an executed action.
- Do not delegate without a server-bound ReviewQueue context and do not delegate to general-purpose or bash agents.

Action proposal boundary:
- You may propose a SOC action, but you must not execute it.
- For any action proposal that should enter SOC policy/approval handling, emit exactly one JSON object inside:
  <soc_action_proposal>{...}</soc_action_proposal>
- Required fields are route, action, reason, payload, and confidence.
- Example:
  <soc_action_proposal>{"route":"response.block_ip","action":"response.block_ip","reason":"Block the confirmed malicious source IP after analyst approval.","payload":{"ip":"1.2.3.4"},"confidence":0.82}</soc_action_proposal>
- High-risk actions such as response.block_ip, endpoint.isolate_host, and mcp.invoke require a deterministic policy authorization; some policies may additionally require human approval.
- Read-only actions such as asset.lookup or asset.locate may be proposed with the same marker, for example:
  <soc_action_proposal>{"route":"asset.lookup","action":"asset.lookup","reason":"Look up asset ownership before deciding suppression target.","payload":{"asset_key":"10.10.1.5"},"confidence":0.74}</soc_action_proposal>
- To locate business ownership for an extracted asset, use:
  <soc_action_proposal>{"route":"asset.locate","action":"asset.locate","reason":"Locate the impacted asset owner before assigning disposal target.","payload":{"asset_key":"10.10.1.5","asset_type":"IP","role":"target"},
  "confidence":0.74}</soc_action_proposal>
- To check external IP reputation, use:
  <soc_action_proposal>{"route":"threat_intel.ip_reputation.lookup","action":"threat_intel.ip_reputation.lookup",
  "reason":"Check IP reputation freshness before using the IOC in risk reasoning.",
  "payload":{"ip":"203.0.113.10"},"confidence":0.72}</soc_action_proposal>
- To check authorization, maintenance, or testing labels, use:
  <soc_action_proposal>{"route":"security_tag.lookup","action":"security_tag.lookup",
  "reason":"Check whether this entity has active authorization or maintenance evidence.",
  "payload":{"entity_key":"host:web-01"},"confidence":0.7}</soc_action_proposal>
- To check a PingAn EDR process/file path against historical candidate knowledge, use:
  <soc_action_proposal>{"route":"endpoint.software_path.lookup","action":"endpoint.software_path.lookup",
  "reason":"Check exact historical path and hash context while preserving D-drive location risk.",
  "payload":{"path":"D:\\tools\\example.exe","md5":"0123456789abcdef0123456789abcdef"},"confidence":0.68}</soc_action_proposal>
- Historical path results are not an allowlist. A D-drive, user-writable, or temporary path remains higher-attention even after an exact historical match.
- Do not claim read-only lookup, location, or process-tree results unless SOC runtime returns a tool/action result or the bounded context already contains matching action_evidence.
"""


class SocLeadAgentRuntimeConfigurationError(RuntimeError):
    """The installed SOC agent/profile cannot satisfy its governance contract."""


def build_soc_lead_agent_profile() -> SocLeadAgentProfile:
    """Return a DeerFlow custom-agent payload for the SOC Lead Agent MVP."""

    # MCP server wiring is managed by DeerFlow extensions_config/mcp_config.
    # SOC action-to-MCP bindings live in the action adapter allowlist, not here.
    return SocLeadAgentProfile(
        name=SOC_LEAD_AGENT_NAME,
        description=SOC_LEAD_AGENT_DESCRIPTION,
        skills=list(SOC_LEAD_AGENT_SKILLS),
        tool_groups=None,
        middlewares=[
            SOC_LEAD_AGENT_REVIEW_CONTEXT_MIDDLEWARE,
            SOC_LEAD_AGENT_DELEGATION_MIDDLEWARE,
            SOC_LEAD_AGENT_APPROVAL_MIDDLEWARE,
        ],
        soul=SOC_LEAD_AGENT_SOUL,
    )


def validate_soc_lead_agent_runtime_configuration(
    *,
    require_specialists: bool = True,
    user_id: str | None = None,
    app_config: Any | None = None,
) -> dict[str, object]:
    """Fail closed when installed SOC profile or specialist policy is stale."""
    try:
        installed = load_agent_config(SOC_LEAD_AGENT_NAME, user_id=user_id)
    except (FileNotFoundError, ValueError) as exc:
        raise SocLeadAgentRuntimeConfigurationError("SOC Lead Agent profile is missing or invalid; run `python -m soc_agent.cli agent install-profile --overwrite`.") from exc
    if installed is None:
        raise SocLeadAgentRuntimeConfigurationError("SOC Lead Agent profile is missing; run `python -m soc_agent.cli agent install-profile --overwrite`.")

    required_middlewares = build_soc_lead_agent_profile().middlewares
    installed_middlewares = list(installed.middlewares or [])
    if not _contains_ordered_values(installed_middlewares, required_middlewares):
        raise SocLeadAgentRuntimeConfigurationError("SOC Lead Agent profile is stale or missing governance middleware; run `python -m soc_agent.cli agent install-profile --overwrite`.")

    validated_specialists: list[str] = []
    if require_specialists:
        resolved_app_config = app_config or get_app_config()
        desired = build_soc_specialist_subagent_configs()
        mismatches: list[str] = []
        for name in SOC_SPECIALIST_SUBAGENT_NAMES:
            actual = get_subagent_config(name, app_config=resolved_app_config)
            expected = desired[name]
            if actual is None or not _specialist_matches_contract(
                actual,
                expected,
            ):
                mismatches.append(name)
                continue
            validated_specialists.append(name)
        if mismatches:
            raise SocLeadAgentRuntimeConfigurationError(f"SOC specialist config is missing, stale, or outside the read-only contract ({', '.join(mismatches)}); run `python -m soc_agent.cli agent install-subagents --apply --overwrite`.")

    return {
        "schema_version": "soc.lead_agent_runtime_configuration.v1",
        "status": "ready",
        "agent_name": SOC_LEAD_AGENT_NAME,
        "required_middlewares": required_middlewares,
        "validated_specialists": validated_specialists,
        "specialist_delegation_enabled": require_specialists,
    }


def _contains_ordered_values(
    values: list[str],
    required: list[str],
) -> bool:
    position = -1
    for item in required:
        try:
            position = values.index(item, position + 1)
        except ValueError:
            return False
    return True


def _specialist_matches_contract(actual: Any, expected: Any) -> bool:
    return all(
        (
            actual.description == expected.description,
            actual.system_prompt == expected.system_prompt,
            actual.tools == expected.tools,
            actual.disallowed_tools == expected.disallowed_tools,
            actual.disallowed_output_markers == expected.disallowed_output_markers,
            actual.skills == expected.skills,
            actual.model == expected.model,
            actual.max_turns == expected.max_turns,
            actual.timeout_seconds == expected.timeout_seconds,
        )
    )
