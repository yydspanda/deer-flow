"""SOC Lead Agent chat entry backed by DeerFlow's embedded client."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, Protocol
from uuid import uuid4

from deerflow.client import DeerFlowClient
from deerflow.config.agents_config import load_agent_config
from soc_agent.actions.proposals import (
    SocLeadAgentActionProposalBoundary,
    action_proposal_error_event,
    action_proposal_event,
    action_result_event,
    approval_request_event,
    extract_action_proposals_from_text,
    permission_decision_event,
    route_decision_event,
)
from soc_agent.context_bridge import build_lead_agent_review_context_artifact, render_lead_agent_review_context_message
from soc_agent.contracts import InvestigationContext, ServiceRequestContext, SocAgentChatRequest, SocAgentStreamEvent
from soc_agent.skills import SOC_LEAD_AGENT_NAME


class SocLeadAgentProfileNotInstalledError(RuntimeError):
    """Raised when the DeerFlow SOC custom-agent profile is not installed."""


class SocLeadAgentReviewContextError(RuntimeError):
    """Raised when a ReviewQueue context cannot be bridged to the lead agent."""


class DeerFlowClientLike(Protocol):
    def stream(
        self,
        message: str,
        *,
        thread_id: str | None = None,
        **kwargs: Any,
    ) -> Iterator[Any]: ...


class ReviewContextProvider(Protocol):
    def get_investigation_context(self, queue_id: str) -> InvestigationContext: ...


class SocLeadAgentChatService:
    """Thin stream adapter into DeerFlow ``lead_agent`` with ``agent_name=soc-triage``."""

    def __init__(
        self,
        *,
        agent_name: str = SOC_LEAD_AGENT_NAME,
        model_name: str | None = None,
        client_factory: Callable[[], DeerFlowClientLike] | None = None,
        require_profile: bool = True,
        review_service: ReviewContextProvider | None = None,
        action_proposal_boundary: SocLeadAgentActionProposalBoundary | None = None,
    ) -> None:
        self._agent_name = agent_name
        self._model_name = model_name.strip() if model_name and model_name.strip() else None
        self._client_factory = client_factory or (lambda: DeerFlowClient(agent_name=agent_name))
        self._require_profile = require_profile
        self._review_service = review_service
        self._action_proposal_boundary = action_proposal_boundary or SocLeadAgentActionProposalBoundary()

    def stream(
        self,
        request: SocAgentChatRequest | str,
        *,
        context: ServiceRequestContext | None = None,
    ) -> Iterator[SocAgentStreamEvent]:
        chat_request = request if isinstance(request, SocAgentChatRequest) else SocAgentChatRequest(message=request)
        request_context = context or ServiceRequestContext()
        thread_id = chat_request.thread_id or f"SOC-LEAD-{uuid4().hex[:12].upper()}"
        if self._require_profile:
            _ensure_profile_installed(self._agent_name)

        yield SocAgentStreamEvent(
            type="custom",
            data={
                "kind": "soc.lead_agent_entry",
                "agent_name": self._agent_name,
                "thread_id": thread_id,
                "actor_surface": request_context.actor.surface.value,
            },
        )
        message = _operator_message(chat_request)
        proposal_defaults: dict[str, Any] = {
            "thread_id": thread_id,
            "queue_id": chat_request.queue_id,
            "run_id": chat_request.run_id,
            "proposed_by": request_context.actor.model_dump(mode="json"),
        }
        if chat_request.queue_id:
            if self._review_service is None:
                raise SocLeadAgentReviewContextError("SOC Lead Agent review context bridge requires SocReviewService")
            artifact = build_lead_agent_review_context_artifact(
                self._review_service.get_investigation_context(chat_request.queue_id),
                request_context=request_context,
            )
            proposal_defaults.update(
                {
                    "run_id": artifact.run_id,
                    "alert_id": artifact.alert_id,
                    "context_hash": artifact.context_hash,
                }
            )
            yield SocAgentStreamEvent(
                type="custom",
                data={
                    "kind": "soc.lead_agent_review_context",
                    "artifact_id": artifact.artifact_id,
                    "queue_id": artifact.queue_id,
                    "run_id": artifact.run_id,
                    "alert_id": artifact.alert_id,
                    "context_hash": artifact.context_hash,
                    "skill_context_hash": artifact.skill_context_hash,
                    "artifact": artifact.model_dump(mode="json", exclude_none=True),
                },
            )
            message = render_lead_agent_review_context_message(message=message, artifact=artifact)
        client = self._client_factory()
        stream_options = {"model_name": self._model_name} if self._model_name else {}
        for event in client.stream(message, thread_id=thread_id, **stream_options):
            stream_event = _coerce_stream_event(event, thread_id=thread_id)
            yield from _review_action_proposals(
                stream_event,
                boundary=self._action_proposal_boundary,
                context=request_context,
                proposal_defaults=proposal_defaults,
            )


def _ensure_profile_installed(agent_name: str) -> None:
    try:
        load_agent_config(agent_name)
    except FileNotFoundError as exc:
        raise SocLeadAgentProfileNotInstalledError(f"SOC Lead Agent profile '{agent_name}' is not installed. Run `soc agent install-profile` first.") from exc


def _coerce_stream_event(event: Any, *, thread_id: str) -> SocAgentStreamEvent:
    event_type = getattr(event, "type", None)
    data = getattr(event, "data", None) or {}
    payload = dict(data)
    if event_type in {"values", "end"} and "thread_id" not in payload:
        payload["thread_id"] = thread_id
    return SocAgentStreamEvent(type=event_type, data=payload)


def _review_action_proposals(
    event: SocAgentStreamEvent,
    *,
    boundary: SocLeadAgentActionProposalBoundary,
    context: ServiceRequestContext,
    proposal_defaults: dict[str, Any],
) -> Iterator[SocAgentStreamEvent]:
    if event.type != "messages-tuple":
        yield event
        return
    content = event.data.get("content")
    if not isinstance(content, str) or "<soc_action_proposal>" not in content:
        yield event
        return

    parse_result = extract_action_proposals_from_text(content, defaults=proposal_defaults)
    if parse_result.clean_text:
        data = dict(event.data)
        data["content"] = parse_result.clean_text
        yield SocAgentStreamEvent(type=event.type, data=data)
    for error in parse_result.errors:
        yield action_proposal_error_event(error)
    for proposal in parse_result.proposals:
        yield action_proposal_event(proposal)
        result = boundary.review(proposal, context=context)
        if result.read_only_tool_result is not None:
            yield route_decision_event(result.read_only_tool_result.route_decision)
        yield permission_decision_event(result.permission_decision)
        if result.approval_request is not None:
            yield approval_request_event(result.approval_request)
        if result.read_only_tool_result is not None and result.read_only_tool_result.action_result is not None:
            yield action_result_event(result.read_only_tool_result.action_result)


def _operator_message(request: SocAgentChatRequest) -> str:
    message = request.message.strip()
    if request.queue_id and message.startswith("/open"):
        return "Open and investigate this SOC review context."
    return message
