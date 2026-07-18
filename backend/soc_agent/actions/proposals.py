"""Structured SOC Lead Agent action proposal boundary."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from soc_agent.contracts import (
    ServiceRequestContext,
    SocAgentActionProposal,
    SocAgentActionResult,
    SocAgentApprovalRequest,
    SocAgentChatRequest,
    SocAgentPermissionDecision,
    SocAgentRiskLevel,
    SocAgentRouteDecision,
    SocAgentStreamEvent,
)
from soc_agent.core import SocAgentActionDispatcher, SocAgentActionPolicy, SocAgentApprovalService, SocAgentCapabilityRouter

ACTION_PROPOSAL_START = "<soc_action_proposal>"
ACTION_PROPOSAL_END = "</soc_action_proposal>"
_ACTION_PROPOSAL_RE = re.compile(rf"{re.escape(ACTION_PROPOSAL_START)}\s*(.*?)\s*{re.escape(ACTION_PROPOSAL_END)}", re.DOTALL)


@dataclass(frozen=True)
class ActionProposalParseResult:
    clean_text: str
    proposals: list[SocAgentActionProposal]
    errors: list[str]


@dataclass(frozen=True)
class ReadOnlyToolBridgeResult:
    route_decision: SocAgentRouteDecision
    action_result: SocAgentActionResult | None = None


@dataclass(frozen=True)
class ActionProposalBoundaryResult:
    proposal: SocAgentActionProposal
    permission_decision: SocAgentPermissionDecision
    approval_request: SocAgentApprovalRequest | None = None
    submitted_approval_request: bool = False
    read_only_tool_result: ReadOnlyToolBridgeResult | None = None


class SocLeadAgentActionProposalBoundary:
    """Validate lead-agent action candidates without executing them."""

    def __init__(
        self,
        *,
        action_policy: SocAgentActionPolicy | None = None,
        approval_service: SocAgentApprovalService | None = None,
        read_only_capability_router: SocAgentCapabilityRouter | None = None,
        read_only_action_dispatcher: SocAgentActionDispatcher | None = None,
    ) -> None:
        self._action_policy = action_policy or SocAgentActionPolicy()
        self._approval_service = approval_service
        self._read_only_capability_router = read_only_capability_router
        self._read_only_action_dispatcher = read_only_action_dispatcher

    def review(
        self,
        proposal: SocAgentActionProposal,
        *,
        context: ServiceRequestContext,
    ) -> ActionProposalBoundaryResult:
        chat_request = SocAgentChatRequest(
            message=proposal.reason,
            thread_id=proposal.thread_id,
            queue_id=proposal.queue_id,
            run_id=proposal.run_id,
        )
        decision = self._action_policy.check(action=proposal.action, route=proposal.route, request=chat_request, context=context)
        approval_request: SocAgentApprovalRequest | None = None
        submitted = False
        read_only_tool_result = self._review_read_only_tool_proposal(proposal, decision=decision, context=context)
        if decision.requires_human_approval:
            approval_request = approval_request_from_action_proposal(proposal, decision=decision, context=context)
            if self._approval_service is not None:
                self._approval_service.submit_request(approval_request, context=context)
                submitted = True
        return ActionProposalBoundaryResult(
            proposal=proposal,
            permission_decision=decision,
            approval_request=approval_request,
            submitted_approval_request=submitted,
            read_only_tool_result=read_only_tool_result,
        )

    def _review_read_only_tool_proposal(
        self,
        proposal: SocAgentActionProposal,
        *,
        decision: SocAgentPermissionDecision,
        context: ServiceRequestContext,
    ) -> ReadOnlyToolBridgeResult | None:
        if decision.risk_level != SocAgentRiskLevel.READ_ONLY or not decision.allowed:
            return None
        if self._read_only_capability_router is None or self._read_only_action_dispatcher is None:
            return None
        chat_request = _read_only_tool_chat_request(proposal)
        route_decision = self._read_only_capability_router.route(chat_request)
        action_result: SocAgentActionResult | None = None
        if route_decision.allowed:
            action_result = self._read_only_action_dispatcher.dispatch(
                chat_request,
                route_decision,
                context=context,
                permission_decision=decision,
            )
        return ReadOnlyToolBridgeResult(route_decision=route_decision, action_result=action_result)


def extract_action_proposals_from_text(
    text: str,
    *,
    defaults: dict[str, Any] | None = None,
) -> ActionProposalParseResult:
    """Extract explicit SOC action proposals from a lead-agent text message."""

    metadata = dict(defaults or {})
    proposals: list[SocAgentActionProposal] = []
    errors: list[str] = []
    for index, match in enumerate(_ACTION_PROPOSAL_RE.finditer(text), start=1):
        raw_block = match.group(1).strip()
        try:
            parsed = json.loads(raw_block)
        except json.JSONDecodeError as exc:
            errors.append(f"proposal block {index} is not valid JSON: {exc.msg}")
            continue
        items = parsed if isinstance(parsed, list) else [parsed]
        if not isinstance(items, list):
            errors.append(f"proposal block {index} must be a JSON object or list")
            continue
        for item_index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                errors.append(f"proposal block {index}.{item_index} must be a JSON object")
                continue
            payload = _proposal_payload(item, metadata)
            try:
                proposals.append(SocAgentActionProposal.model_validate(payload))
            except ValidationError as exc:
                errors.append(f"proposal block {index}.{item_index} failed schema validation: {exc.errors()[0]['msg']}")
    clean_text = _ACTION_PROPOSAL_RE.sub("", text).strip()
    return ActionProposalParseResult(clean_text=clean_text, proposals=proposals, errors=errors)


def approval_request_from_action_proposal(
    proposal: SocAgentActionProposal,
    *,
    decision: SocAgentPermissionDecision,
    context: ServiceRequestContext,
) -> SocAgentApprovalRequest:
    """Convert a high-risk proposal into a pending approval request."""

    return SocAgentApprovalRequest(
        approval_request_id=decision.approval_request_id or f"APR-{uuid4().hex[:12].upper()}",
        permission_decision_id=decision.decision_id,
        route=decision.route,
        action=decision.action,
        risk_level=decision.risk_level,
        reason=f"Lead Agent proposed action {proposal.action}: {proposal.reason}",
        requested_by=decision.actor or context.actor,
        source_proposal_id=proposal.proposal_id,
        action_payload=proposal.payload,
        context_refs={
            "source": proposal.source,
            "thread_id": proposal.thread_id,
            "queue_id": proposal.queue_id,
            "run_id": proposal.run_id,
            "alert_id": proposal.alert_id,
            "context_hash": proposal.context_hash,
        },
    )


def route_decision_event(decision: SocAgentRouteDecision) -> SocAgentStreamEvent:
    return SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.route_decision",
            "route": decision.route,
            "allowed": decision.allowed,
            "reason": decision.reason,
            "input_text": decision.input_text,
        },
    )


def action_proposal_event(proposal: SocAgentActionProposal) -> SocAgentStreamEvent:
    return SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.action_proposal",
            "proposal": proposal.model_dump(mode="json", exclude_none=True),
            "proposal_id": proposal.proposal_id,
            "source": proposal.source,
            "route": proposal.route,
            "action": proposal.action,
            "confidence": proposal.confidence,
            "reason": proposal.reason,
        },
    )


def action_proposal_error_event(error: str) -> SocAgentStreamEvent:
    return SocAgentStreamEvent(type="custom", data={"kind": "soc.action_proposal_error", "error": error})


def permission_decision_event(decision: SocAgentPermissionDecision) -> SocAgentStreamEvent:
    return SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.permission_decision",
            "decision_id": decision.decision_id,
            "route": decision.route,
            "action": decision.action,
            "allowed": decision.allowed,
            "risk_level": decision.risk_level.value,
            "reason": decision.reason,
            "requires_human_approval": decision.requires_human_approval,
            "approval_request_id": decision.approval_request_id,
            "policy_version": decision.policy_version,
        },
    )


def approval_request_event(request: SocAgentApprovalRequest) -> SocAgentStreamEvent:
    return SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.approval_request",
            "approval_request_id": request.approval_request_id,
            "permission_decision_id": request.permission_decision_id,
            "route": request.route,
            "action": request.action,
            "risk_level": request.risk_level.value,
            "reason": request.reason,
            "requested_by": request.requested_by.model_dump(mode="json"),
            "source_proposal_id": request.source_proposal_id,
            "action_payload": request.action_payload,
            "context_refs": request.context_refs,
            "status": request.status,
            "created_at": request.created_at.isoformat(),
        },
    )


def action_result_event(result: SocAgentActionResult) -> SocAgentStreamEvent:
    return SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.action_result",
            "route": result.route,
            "action": result.action,
            "status": result.status,
            "message": result.message,
            "requires_human_approval": result.requires_human_approval,
            "payload": result.payload,
        },
    )


def _proposal_payload(item: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    payload = dict(item)
    if "route" not in payload and isinstance(payload.get("action"), str):
        payload["route"] = payload["action"]
    if "action" not in payload and isinstance(payload.get("route"), str):
        payload["action"] = payload["route"]
    for key, value in metadata.items():
        if value is not None:
            payload[key] = value
    payload["source"] = "lead_agent"
    return payload


def _read_only_tool_chat_request(proposal: SocAgentActionProposal) -> SocAgentChatRequest:
    payload = dict(proposal.payload)
    context_refs = dict(payload.get("context_refs")) if isinstance(payload.get("context_refs"), dict) else {}
    _set_context_ref(context_refs, "source", proposal.source)
    _set_context_ref(context_refs, "proposal_id", proposal.proposal_id)
    _set_context_ref(context_refs, "thread_id", proposal.thread_id)
    _set_context_ref(context_refs, "queue_id", proposal.queue_id)
    _set_context_ref(context_refs, "run_id", proposal.run_id)
    _set_context_ref(context_refs, "alert_id", proposal.alert_id)
    _set_context_ref(context_refs, "context_hash", proposal.context_hash)
    if context_refs:
        payload["context_refs"] = context_refs
    return SocAgentChatRequest(
        message=proposal.reason,
        thread_id=proposal.thread_id,
        queue_id=proposal.queue_id,
        run_id=proposal.run_id,
        metadata={
            "soc_route": proposal.route,
            "action_payload": payload,
        },
        allowed_routes=[proposal.route],
    )


def _set_context_ref(context_refs: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        context_refs.setdefault(key, value)


__all__ = [
    "ACTION_PROPOSAL_END",
    "ACTION_PROPOSAL_START",
    "ActionProposalBoundaryResult",
    "ActionProposalParseResult",
    "ReadOnlyToolBridgeResult",
    "SocLeadAgentActionProposalBoundary",
    "action_proposal_error_event",
    "action_proposal_event",
    "action_result_event",
    "approval_request_event",
    "approval_request_from_action_proposal",
    "extract_action_proposals_from_text",
    "permission_decision_event",
    "route_decision_event",
]
