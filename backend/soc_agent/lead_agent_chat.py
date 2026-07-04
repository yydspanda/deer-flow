"""SOC Lead Agent chat entry backed by DeerFlow's embedded client."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, Protocol
from uuid import uuid4

from deerflow.client import DeerFlowClient
from deerflow.config.agents_config import load_agent_config
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
        client_factory: Callable[[], DeerFlowClientLike] | None = None,
        require_profile: bool = True,
        review_service: ReviewContextProvider | None = None,
    ) -> None:
        self._agent_name = agent_name
        self._client_factory = client_factory or (lambda: DeerFlowClient(agent_name=agent_name))
        self._require_profile = require_profile
        self._review_service = review_service

    def stream(
        self,
        request: SocAgentChatRequest | str,
        *,
        context: ServiceRequestContext | None = None,
    ) -> Iterator[SocAgentStreamEvent]:
        chat_request = request if isinstance(request, SocAgentChatRequest) else SocAgentChatRequest(message=request)
        thread_id = chat_request.thread_id or f"SOC-LEAD-{uuid4().hex[:12].upper()}"
        if self._require_profile:
            _ensure_profile_installed(self._agent_name)

        yield SocAgentStreamEvent(
            type="custom",
            data={
                "kind": "soc.lead_agent_entry",
                "agent_name": self._agent_name,
                "thread_id": thread_id,
                "actor_surface": context.actor.surface.value if context is not None else None,
            },
        )
        message = _operator_message(chat_request)
        if chat_request.queue_id:
            if self._review_service is None:
                raise SocLeadAgentReviewContextError("SOC Lead Agent review context bridge requires SocReviewService")
            artifact = build_lead_agent_review_context_artifact(
                self._review_service.get_investigation_context(chat_request.queue_id),
                request_context=context,
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
        for event in client.stream(message, thread_id=thread_id):
            yield _coerce_stream_event(event, thread_id=thread_id)


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


def _operator_message(request: SocAgentChatRequest) -> str:
    message = request.message.strip()
    if request.queue_id and message.startswith("/open"):
        return "Open and investigate this SOC review context."
    return message
