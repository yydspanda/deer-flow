"""Bridge SOC Lead Agent proposals into the shared approval boundary."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langgraph.errors import GraphBubbleUp
from langgraph.runtime import Runtime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from deerflow.utils.custom_events import aemit_custom_event, emit_custom_event
from soc_agent.actions.proposals import (
    SocLeadAgentActionProposalBoundary,
    action_proposal_error_event,
    action_proposal_event,
    action_result_event,
    approval_request_event,
    extract_action_proposals_from_text,
    permission_decision_event,
    proposal_service_context,
    route_decision_event,
)
from soc_agent.context_bridge import SOC_LEAD_AGENT_REVIEW_CONTEXT_ARTIFACT_RUNTIME_KEY
from soc_agent.contracts import (
    ActorAuthSource,
    ActorContext,
    ActorType,
    EntrySurface,
    ServiceRequestContext,
    SocAgentStreamEvent,
    SocLeadAgentReviewContextArtifact,
)
from soc_agent.core import SocAgentApprovalService
from soc_agent.db import SqlAlchemyAlertRepository, resolve_database_url, to_sync_database_url
from soc_agent.skills import SOC_LEAD_AGENT_NAME

logger = logging.getLogger(__name__)

BoundaryFactory = Callable[[], SocLeadAgentActionProposalBoundary]
EventSink = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class _ProposalIntervention:
    update: dict[str, Any]
    event_payloads: list[dict[str, Any]]


class SocLeadAgentApprovalMiddleware(AgentMiddleware[AgentState]):
    """Persist structured SOC action proposals without executing them.

    The middleware is loaded only from the operator-owned ``soc-triage``
    custom-agent profile. It parses complete model messages at ``after_model``,
    applies ``SocAgentActionPolicy`` through the shared proposal boundary, and
    writes high-risk requests to the same Approval Inbox used by API, Web, TUI,
    and daemon entry points. It never executes a high-risk action.
    """

    def __init__(
        self,
        *,
        boundary_factory: BoundaryFactory | None = None,
        event_sink: EventSink | None = None,
        agent_name: str = SOC_LEAD_AGENT_NAME,
    ) -> None:
        super().__init__()
        self._boundary_factory = boundary_factory or _default_boundary
        self._event_sink = event_sink
        self._agent_name = agent_name

    def _prepare(
        self,
        state: AgentState,
        runtime_context: Mapping[str, Any],
    ) -> _ProposalIntervention | None:
        configured_agent = runtime_context.get("agent_name")
        if configured_agent is not None and configured_agent != self._agent_name:
            return None

        messages = state.get("messages", [])
        if not messages:
            return None
        message = messages[-1]
        if not isinstance(message, AIMessage):
            return None
        content_text = _visible_content_text(message.content)
        if "<soc_action_proposal>" not in content_text:
            return None

        proposed_by = _requested_actor(runtime_context)
        review_artifact = _review_context_artifact(runtime_context)
        defaults = {
            "thread_id": _optional_string(runtime_context.get("thread_id")),
            "queue_id": review_artifact.queue_id if review_artifact is not None else None,
            "run_id": (review_artifact.run_id if review_artifact is not None else _optional_string(runtime_context.get("run_id"))),
            "alert_id": review_artifact.alert_id if review_artifact is not None else None,
            "context_hash": (review_artifact.context_hash if review_artifact is not None else None),
            "proposed_by": proposed_by.model_dump(mode="json"),
        }
        seed = ":".join(
            str(value)
            for value in (
                self._agent_name,
                defaults["thread_id"],
                defaults["run_id"],
                message.id,
                len(messages),
            )
            if value is not None
        )
        parse_result = extract_action_proposals_from_text(
            content_text,
            defaults=defaults,
            proposal_id_seed=seed,
        )
        event_payloads = [_event_payload(action_proposal_error_event(error)) for error in parse_result.errors]
        approval_request_ids: list[str] = []
        permission_decision_ids: list[str] = []
        processing_error_count = len(parse_result.errors)
        boundary: SocLeadAgentActionProposalBoundary | None = None
        service_context = _submission_context(runtime_context)

        for proposal in parse_result.proposals:
            event_payloads.append(_event_payload(action_proposal_event(proposal)))
            try:
                boundary = boundary or self._boundary_factory()
                result = boundary.review(
                    proposal,
                    context=proposal_service_context(service_context, proposal),
                )
            except Exception:  # noqa: BLE001 - fail closed at the middleware boundary
                processing_error_count += 1
                logger.exception(
                    "SOC Lead Agent proposal could not enter the governed action boundary",
                    extra={
                        "proposal_id": proposal.proposal_id,
                        "route": proposal.route,
                        "action": proposal.action,
                    },
                )
                event_payloads.append(_event_payload(action_proposal_error_event("SOC action proposal could not be persisted; no action was executed")))
                continue

            permission_decision_ids.append(result.permission_decision.decision_id)
            if result.read_only_tool_result is not None:
                event_payloads.append(_event_payload(route_decision_event(result.read_only_tool_result.route_decision)))
            event_payloads.append(_event_payload(permission_decision_event(result.permission_decision)))
            if result.approval_request is not None:
                if result.submitted_approval_request:
                    approval_request_ids.append(result.approval_request.approval_request_id)
                    event_payloads.append(_event_payload(approval_request_event(result.approval_request)))
                else:
                    processing_error_count += 1
                    event_payloads.append(_event_payload(action_proposal_error_event("SOC approval service did not persist the request; no action was executed")))
            if result.read_only_tool_result is not None and result.read_only_tool_result.action_result is not None:
                event_payloads.append(_event_payload(action_result_event(result.read_only_tool_result.action_result)))

        clean_text = parse_result.clean_text
        if not clean_text:
            clean_text = (
                "SOC action proposal was rejected by the governed policy boundary. No action was executed."
                if processing_error_count
                else "SOC action proposal was processed by the governed policy boundary. No action was executed automatically."
            )
        additional_kwargs = dict(message.additional_kwargs or {})
        additional_kwargs["soc_action_boundary"] = {
            "proposal_ids": [proposal.proposal_id for proposal in parse_result.proposals],
            "permission_decision_ids": permission_decision_ids,
            "approval_request_ids": approval_request_ids,
            "error_count": processing_error_count,
            "automatic_execution": False,
        }
        patched = message.model_copy(
            update={
                "content": _replace_visible_content(message.content, clean_text),
                "additional_kwargs": additional_kwargs,
            }
        )
        return _ProposalIntervention(
            update={"messages": [patched]},
            event_payloads=event_payloads,
        )

    def _emit(self, event_payloads: list[dict[str, Any]]) -> None:
        if self._event_sink is not None:
            for payload in event_payloads:
                self._event_sink(payload)
            return
        try:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()
            for payload in event_payloads:
                emit_custom_event(payload, writer=writer)
        except GraphBubbleUp:
            raise
        except Exception:  # noqa: BLE001 - observability must not break the run
            logger.debug("Failed to emit SOC approval middleware events", exc_info=True)

    async def _aemit(self, event_payloads: list[dict[str, Any]]) -> None:
        if self._event_sink is not None:
            for payload in event_payloads:
                self._event_sink(payload)
            return
        try:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()
            for payload in event_payloads:
                await aemit_custom_event(payload, writer=writer)
        except GraphBubbleUp:
            raise
        except Exception:  # noqa: BLE001 - observability must not break the run
            logger.debug("Failed to emit async SOC approval middleware events", exc_info=True)

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        intervention = self._prepare(state, _runtime_context(runtime))
        if intervention is None:
            return None
        self._emit(intervention.event_payloads)
        return intervention.update

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        intervention = await asyncio.to_thread(self._prepare, state, _runtime_context(runtime))
        if intervention is None:
            return None
        await self._aemit(intervention.event_payloads)
        return intervention.update


@lru_cache(maxsize=4)
def _default_boundary_for_database(database_url: str) -> SocLeadAgentActionProposalBoundary:
    engine = create_engine(to_sync_database_url(database_url), pool_pre_ping=True)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    return SocLeadAgentActionProposalBoundary(
        approval_service=SocAgentApprovalService(
            grant_repository=repository,
            request_repository=repository,
        )
    )


def _default_boundary() -> SocLeadAgentActionProposalBoundary:
    return _default_boundary_for_database(resolve_database_url())


def _runtime_context(runtime: Runtime) -> Mapping[str, Any]:
    context = getattr(runtime, "context", None)
    return context if isinstance(context, Mapping) else {}


def _review_context_artifact(
    context: Mapping[str, Any],
) -> SocLeadAgentReviewContextArtifact | None:
    payload = context.get(SOC_LEAD_AGENT_REVIEW_CONTEXT_ARTIFACT_RUNTIME_KEY)
    if payload is None:
        return None
    return SocLeadAgentReviewContextArtifact.model_validate(payload)


def _requested_actor(context: Mapping[str, Any]) -> ActorContext:
    actor_id = _optional_string(context.get("user_id")) or _optional_string(context.get("channel_user_id")) or "anonymous"
    surface = _entry_surface(context)
    auth_source = ActorAuthSource.UNKNOWN
    if context.get("is_internal") is True:
        auth_source = ActorAuthSource.INTERNAL
    elif context.get("oauth_provider") or context.get("oauth_id"):
        auth_source = ActorAuthSource.SESSION
    elif actor_id != "anonymous":
        auth_source = ActorAuthSource.AUTH_DISABLED
    return ActorContext(
        actor_id=actor_id,
        actor_type=ActorType.USER,
        surface=surface,
        auth_source=auth_source,
    )


def _submission_context(context: Mapping[str, Any]) -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id="soc-lead-agent",
            actor_type=ActorType.SERVICE,
            surface=_entry_surface(context),
            roles=["soc_agent"],
            auth_source=ActorAuthSource.INTERNAL,
        ),
        trace_id=_optional_string(context.get("deerflow_trace_id")) or _optional_string(context.get("trace_id")),
    )


def _entry_surface(context: Mapping[str, Any]) -> EntrySurface:
    if context.get("channel_user_id") or context.get("channel_name"):
        return EntrySurface.CHANNEL
    return EntrySurface.WEB


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _visible_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif _is_visible_text_block(block):
            parts.append(block["text"])
    return "\n".join(parts)


def _replace_visible_content(content: Any, text: str) -> str | list[Any]:
    if isinstance(content, str):
        return text
    if not isinstance(content, list):
        return text
    replaced = False
    patched: list[Any] = []
    for block in content:
        is_string_text = isinstance(block, str)
        is_mapping_text = _is_visible_text_block(block)
        if not is_string_text and not is_mapping_text:
            patched.append(block)
            continue
        if replaced:
            continue
        patched.append({**block, "text": text} if is_mapping_text else text)
        replaced = True
    if not replaced:
        patched.append({"type": "text", "text": text})
    return patched


def _is_visible_text_block(value: Any) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("text"), str):
        return False
    return value.get("type") in {None, "text", "output_text"}


def _event_payload(event: SocAgentStreamEvent) -> dict[str, Any]:
    payload = dict(event.data)
    kind = str(payload.get("kind") or "soc.action_event")
    payload.setdefault("type", kind.replace(".", "_"))
    return payload


__all__ = ["SocLeadAgentApprovalMiddleware"]
