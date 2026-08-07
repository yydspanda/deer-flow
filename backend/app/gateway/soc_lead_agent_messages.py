"""Resolve SOC Lead Agent messages from server-owned thread state."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from pydantic import ValidationError

from app.gateway.deps import get_thread_store
from app.gateway.services import build_thread_checkpoint_state_accessor
from deerflow.utils.messages import message_to_text
from soc_agent.context_bridge import (
    SOC_LEAD_AGENT_REVIEW_CONTEXT_PROVENANCE_MESSAGE_KEY,
    SOC_LEAD_AGENT_REVIEW_THREAD_BINDING_METADATA_KEY,
)
from soc_agent.contracts import (
    SocLeadAgentReviewContextProvenance,
    SocLeadAgentReviewThreadBinding,
)
from soc_agent.skills import SOC_LEAD_AGENT_NAME

_MAX_ACCEPTED_MESSAGE_LENGTH = 12_000


class SocLeadAgentMessageResolutionError(RuntimeError):
    """Base class for safe Gateway message-resolution failures."""


class SocLeadAgentMessageNotFoundError(SocLeadAgentMessageResolutionError):
    """The thread or message is absent from the caller's current branch."""


class SocLeadAgentMessageConflictError(SocLeadAgentMessageResolutionError):
    """The referenced message is not an eligible completed SOC conclusion."""


class SocLeadAgentMessageUnavailableError(SocLeadAgentMessageResolutionError):
    """The Gateway cannot currently read the authoritative thread state."""


@dataclass(frozen=True)
class ResolvedSocLeadAgentMessage:
    """One assistant conclusion resolved from the latest materialized checkpoint."""

    thread_id: str
    message_id: str
    agent_name: str
    text: str
    text_sha256: str
    checkpoint_id: str | None
    context_provenance: SocLeadAgentReviewContextProvenance


async def resolve_soc_lead_agent_message(
    request: Request,
    *,
    thread_id: str,
    message_id: str,
    queue_id: str,
) -> ResolvedSocLeadAgentMessage:
    """Resolve the latest terminal assistant message for a SOC custom-agent thread.

    The caller supplies identity only. Text is read from the current materialized
    checkpoint so stale, regenerated, hidden, tool-calling, or client-forged
    message bodies cannot cross the memory-candidate boundary.
    """

    try:
        record = await get_thread_store(request).get(thread_id)
    except Exception as exc:  # noqa: BLE001 - fail closed at the transport boundary
        raise SocLeadAgentMessageUnavailableError("SOC Lead Agent thread metadata is unavailable") from exc
    if record is None:
        raise SocLeadAgentMessageNotFoundError(f"thread {thread_id} not found")

    metadata = record.get("metadata")
    agent_name = metadata.get("agent_name") if isinstance(metadata, dict) else None
    if agent_name != SOC_LEAD_AGENT_NAME:
        raise SocLeadAgentMessageConflictError("thread is not associated with the SOC Lead Agent profile")
    binding = _thread_review_binding(metadata)
    if binding.queue_id != queue_id:
        raise SocLeadAgentMessageConflictError(f"thread is bound to review queue {binding.queue_id}, not {queue_id}")

    try:
        accessor, config = await build_thread_checkpoint_state_accessor(
            request,
            thread_id=thread_id,
            fail_closed=True,
        )
        snapshot = await accessor.aget(config)
    except SocLeadAgentMessageResolutionError:
        raise
    except Exception as exc:  # noqa: BLE001 - checkpoint details stay server-side
        raise SocLeadAgentMessageUnavailableError("SOC Lead Agent thread state is unavailable") from exc

    messages = _checkpoint_messages(snapshot)
    matches = [message for message in messages if _message_id(message) == message_id]
    if not matches:
        raise SocLeadAgentMessageNotFoundError(f"message {message_id} not found in the current thread branch")
    if len(matches) != 1:
        raise SocLeadAgentMessageConflictError("message identity is ambiguous in the current thread branch")

    latest_assistant = next(
        (message for message in reversed(messages) if _is_visible_assistant(message)),
        None,
    )
    target = matches[0]
    if latest_assistant is not target:
        raise SocLeadAgentMessageConflictError("only the latest visible assistant message can be accepted")
    if _message_tool_calls(target):
        raise SocLeadAgentMessageConflictError("assistant tool-call messages cannot be accepted as conclusions")

    text = message_to_text(target).strip()
    if not text:
        raise SocLeadAgentMessageConflictError("assistant conclusion is empty")
    if len(text) > _MAX_ACCEPTED_MESSAGE_LENGTH:
        raise SocLeadAgentMessageConflictError(f"assistant conclusion exceeds the {_MAX_ACCEPTED_MESSAGE_LENGTH}-character review-note limit")
    provenance = _message_review_context_provenance(target)
    if provenance.queue_id != binding.queue_id or provenance.run_id != binding.run_id or provenance.alert_id != binding.alert_id or provenance.chat_thread_id != thread_id:
        raise SocLeadAgentMessageConflictError("assistant conclusion review-context provenance does not match the thread binding")

    return ResolvedSocLeadAgentMessage(
        thread_id=thread_id,
        message_id=message_id,
        agent_name=agent_name,
        text=text,
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        checkpoint_id=_checkpoint_id(snapshot),
        context_provenance=provenance,
    )


def _thread_review_binding(metadata: Any) -> SocLeadAgentReviewThreadBinding:
    payload = metadata.get(SOC_LEAD_AGENT_REVIEW_THREAD_BINDING_METADATA_KEY) if isinstance(metadata, dict) else None
    if payload is None:
        raise SocLeadAgentMessageConflictError("thread has no server-owned SOC review queue binding")
    try:
        return SocLeadAgentReviewThreadBinding.model_validate(payload)
    except ValidationError as exc:
        raise SocLeadAgentMessageConflictError("thread has invalid SOC review queue binding") from exc


def _message_review_context_provenance(
    message: Any,
) -> SocLeadAgentReviewContextProvenance:
    payload = _message_additional_kwargs(message).get(SOC_LEAD_AGENT_REVIEW_CONTEXT_PROVENANCE_MESSAGE_KEY)
    if payload is None:
        raise SocLeadAgentMessageConflictError("assistant conclusion was not produced with server-built SOC review context")
    try:
        return SocLeadAgentReviewContextProvenance.model_validate(payload)
    except ValidationError as exc:
        raise SocLeadAgentMessageConflictError("assistant conclusion has invalid SOC review-context provenance") from exc


def _checkpoint_messages(snapshot: Any) -> list[Any]:
    values = getattr(snapshot, "values", None)
    messages = values.get("messages") if isinstance(values, dict) else None
    return list(messages) if isinstance(messages, (list, tuple)) else []


def _checkpoint_id(snapshot: Any) -> str | None:
    config = getattr(snapshot, "config", None)
    configurable = config.get("configurable") if isinstance(config, dict) else None
    checkpoint_id = configurable.get("checkpoint_id") if isinstance(configurable, dict) else None
    return str(checkpoint_id) if checkpoint_id else None


def _message_id(message: Any) -> str | None:
    value = message.get("id") if isinstance(message, dict) else getattr(message, "id", None)
    return str(value) if value else None


def _message_type(message: Any) -> str | None:
    value = message.get("type") or message.get("role") if isinstance(message, dict) else getattr(message, "type", None)
    return "ai" if value == "assistant" else str(value) if value else None


def _message_name(message: Any) -> str | None:
    value = message.get("name") if isinstance(message, dict) else getattr(message, "name", None)
    return str(value) if value else None


def _message_additional_kwargs(message: Any) -> dict[str, Any]:
    value = message.get("additional_kwargs") if isinstance(message, dict) else getattr(message, "additional_kwargs", None)
    return dict(value) if isinstance(value, dict) else {}


def _message_tool_calls(message: Any) -> list[Any]:
    value = message.get("tool_calls") if isinstance(message, dict) else getattr(message, "tool_calls", None)
    if value is None:
        value = _message_additional_kwargs(message).get("tool_calls")
    return list(value) if isinstance(value, list) else []


def _is_visible_assistant(message: Any) -> bool:
    additional_kwargs = _message_additional_kwargs(message)
    return _message_type(message) == "ai" and _message_name(message) != "summary" and additional_kwargs.get("hide_from_ui") is not True


__all__ = [
    "ResolvedSocLeadAgentMessage",
    "SocLeadAgentMessageConflictError",
    "SocLeadAgentMessageNotFoundError",
    "SocLeadAgentMessageResolutionError",
    "SocLeadAgentMessageUnavailableError",
    "resolve_soc_lead_agent_message",
]
