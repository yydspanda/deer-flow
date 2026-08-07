"""Authenticated ReviewQueue context binding for direct SOC Web runs."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, Request
from pydantic import ValidationError

from app.gateway.soc_dependencies import (
    get_soc_review_service,
    soc_service_context_from_request,
)
from soc_agent.context_bridge import (
    SOC_LEAD_AGENT_REVIEW_CONTEXT_ARTIFACT_RUNTIME_KEY,
    SOC_LEAD_AGENT_REVIEW_THREAD_BINDING_METADATA_KEY,
    SocLeadAgentReviewContextTooLargeError,
    build_lead_agent_review_context_artifact,
    render_lead_agent_review_context_data,
)
from soc_agent.contracts import (
    ActorAuthSource,
    InvestigationContext,
    SocLeadAgentReviewContextArtifact,
    SocLeadAgentReviewThreadBinding,
)
from soc_agent.core import SocServiceNotFoundError, SocServiceNotImplementedError
from soc_agent.lead_agent import (
    SocLeadAgentRuntimeConfigurationError,
    validate_soc_lead_agent_runtime_configuration,
)
from soc_agent.skills import SOC_LEAD_AGENT_NAME

SOC_REVIEW_QUEUE_ID_REQUEST_CONTEXT_KEY = "soc_review_queue_id"


async def inject_soc_lead_agent_review_context(
    *,
    config: dict[str, Any],
    request_context: Mapping[str, Any] | None,
    assistant_id: str,
    thread_id: str,
    request: Request,
    thread_store: Any,
) -> SocLeadAgentReviewContextArtifact | None:
    """Bind, rebuild, and inject one trusted ReviewQueue context artifact.

    The caller-provided context contributes only a queue identity hint. The
    artifact, hashes, run/alert lineage, actor, and immutable thread binding are
    derived server-side. Existing bindings are reused when a later turn omits
    the URL hint and cannot be switched to a different queue.
    """
    _clear_server_owned_runtime_keys(config)
    requested_queue_id = _requested_queue_id(request_context)
    effective_agent_name = _effective_agent_name(config)
    if effective_agent_name != SOC_LEAD_AGENT_NAME:
        if requested_queue_id is not None:
            raise HTTPException(
                status_code=400,
                detail="SOC review queue context requires agent_name='soc-triage'",
            )
        return None
    if assistant_id != "lead_agent":
        raise HTTPException(
            status_code=400,
            detail="SOC review queue context requires assistant_id='lead_agent'",
        )

    service_context = soc_service_context_from_request(
        request,
        include_soc_roles=True,
    )
    if service_context.actor.auth_source is ActorAuthSource.UNKNOWN:
        raise HTTPException(
            status_code=403,
            detail="authenticated Gateway identity is required for SOC review context",
        )
    authenticated_actor_id = service_context.actor.actor_id

    try:
        thread_record = await thread_store.get(
            thread_id,
            user_id=authenticated_actor_id,
        )
    except Exception as exc:  # noqa: BLE001 - storage details stay server-side
        raise HTTPException(
            status_code=503,
            detail="SOC Lead Agent thread metadata is unavailable",
        ) from exc
    if thread_record is None:
        if requested_queue_id is None:
            return None
    metadata = thread_record.get("metadata") if thread_record is not None else None
    metadata = metadata if isinstance(metadata, dict) else {}
    recorded_assistant_id = thread_record.get("assistant_id") if thread_record is not None else None
    if recorded_assistant_id not in (None, "lead_agent"):
        raise HTTPException(
            status_code=409,
            detail="thread is associated with a different assistant",
        )
    recorded_agent_name = metadata.get("agent_name")
    if recorded_agent_name not in (None, SOC_LEAD_AGENT_NAME):
        raise HTTPException(
            status_code=409,
            detail="thread is associated with a different custom agent",
        )
    existing_binding = _parse_existing_binding(metadata.get(SOC_LEAD_AGENT_REVIEW_THREAD_BINDING_METADATA_KEY))
    if existing_binding is not None:
        if requested_queue_id is not None and requested_queue_id != existing_binding.queue_id:
            raise HTTPException(
                status_code=409,
                detail=(f"thread is already bound to review queue {existing_binding.queue_id}; open a new thread for a different queue item"),
            )
        queue_id = existing_binding.queue_id
    else:
        queue_id = requested_queue_id
    if queue_id is None:
        return None

    try:
        await asyncio.to_thread(
            validate_soc_lead_agent_runtime_configuration,
            require_specialists=True,
            user_id=authenticated_actor_id,
        )
    except SocLeadAgentRuntimeConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        investigation_context = await asyncio.to_thread(
            get_soc_review_service(request).get_investigation_context,
            queue_id,
        )
        _validate_investigation_lineage(investigation_context, queue_id=queue_id)
        artifact = await asyncio.to_thread(
            build_lead_agent_review_context_artifact,
            investigation_context,
            request_context=service_context,
        )
        # Fail before run admission instead of discovering an oversized model
        # projection inside the background worker.
        render_lead_agent_review_context_data(artifact)
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocLeadAgentReviewContextTooLargeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    proposed_binding = existing_binding or SocLeadAgentReviewThreadBinding(
        queue_id=artifact.queue_id,
        run_id=artifact.run_id,
        alert_id=artifact.alert_id,
        bound_by_actor_id=service_context.actor.actor_id,
    )
    _assert_binding_lineage(proposed_binding, artifact)
    try:
        if thread_record is None:
            thread_record = await thread_store.get_or_create(
                thread_id,
                assistant_id=assistant_id,
                user_id=authenticated_actor_id,
                metadata={"agent_name": SOC_LEAD_AGENT_NAME},
            )
            if thread_record is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Thread {thread_id} not found",
                )
            stored_metadata = thread_record.get("metadata")
            stored_metadata = stored_metadata if isinstance(stored_metadata, dict) else {}
            stored_agent_name = stored_metadata.get("agent_name")
            if stored_agent_name not in (None, SOC_LEAD_AGENT_NAME):
                raise HTTPException(
                    status_code=409,
                    detail="thread is associated with a different custom agent",
                )
        stored_payload = await thread_store.bind_metadata_once(
            thread_id,
            SOC_LEAD_AGENT_REVIEW_THREAD_BINDING_METADATA_KEY,
            proposed_binding.model_dump(mode="json"),
            user_id=authenticated_actor_id,
        )
        if stored_payload is None:
            raise HTTPException(
                status_code=404,
                detail=f"Thread {thread_id} not found",
            )
        stored_binding = SocLeadAgentReviewThreadBinding.model_validate(stored_payload)
        _assert_binding_lineage(stored_binding, artifact)
        await thread_store.update_metadata(
            thread_id,
            {"agent_name": SOC_LEAD_AGENT_NAME},
            touch=False,
            user_id=authenticated_actor_id,
        )
    except HTTPException:
        raise
    except (ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail="thread has an invalid or conflicting SOC review binding",
        ) from exc
    except Exception as exc:  # noqa: BLE001 - storage details stay server-side
        raise HTTPException(
            status_code=503,
            detail="SOC review thread binding could not be persisted",
        ) from exc

    runtime_context = config.setdefault("context", {})
    if not isinstance(runtime_context, dict):
        raise HTTPException(status_code=400, detail="run context must be an object")
    runtime_context[SOC_LEAD_AGENT_REVIEW_CONTEXT_ARTIFACT_RUNTIME_KEY] = artifact.model_dump(mode="json", exclude_none=True)
    return artifact


def _requested_queue_id(
    request_context: Mapping[str, Any] | None,
) -> str | None:
    if not request_context or SOC_REVIEW_QUEUE_ID_REQUEST_CONTEXT_KEY not in request_context:
        return None
    value = request_context.get(SOC_REVIEW_QUEUE_ID_REQUEST_CONTEXT_KEY)
    if not isinstance(value, str):
        raise HTTPException(
            status_code=422,
            detail=f"context.{SOC_REVIEW_QUEUE_ID_REQUEST_CONTEXT_KEY} must be a string",
        )
    normalized = value.strip()
    if not normalized or len(normalized) > 64:
        raise HTTPException(
            status_code=422,
            detail=f"context.{SOC_REVIEW_QUEUE_ID_REQUEST_CONTEXT_KEY} must contain 1-64 characters",
        )
    return normalized


def _effective_agent_name(config: Mapping[str, Any]) -> str | None:
    for section in ("context", "configurable"):
        values = config.get(section)
        if isinstance(values, Mapping):
            value = values.get("agent_name")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _clear_server_owned_runtime_keys(config: dict[str, Any]) -> None:
    for section in ("context", "configurable"):
        values = config.get(section)
        if isinstance(values, dict):
            values.pop(SOC_LEAD_AGENT_REVIEW_CONTEXT_ARTIFACT_RUNTIME_KEY, None)


def _parse_existing_binding(value: Any) -> SocLeadAgentReviewThreadBinding | None:
    if value is None:
        return None
    try:
        return SocLeadAgentReviewThreadBinding.model_validate(value)
    except ValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail="thread contains an invalid SOC review binding",
        ) from exc


def _validate_investigation_lineage(
    context: InvestigationContext,
    *,
    queue_id: str,
) -> None:
    item = context.queue_item
    run = context.run
    if item.queue_id != queue_id or item.run_id != run.run_id or item.alert_id != run.alert_id:
        raise HTTPException(
            status_code=409,
            detail="ReviewQueue, AnalysisRun, and alert lineage are inconsistent",
        )
    tenant_ids = {
        value
        for value in (
            item.tenant_id,
            context.summary.tenant_id if context.summary is not None else None,
            (run.llm_analysis_request.tenant_id if run.llm_analysis_request is not None else None),
        )
        if value is not None
    }
    if len(tenant_ids) > 1:
        raise HTTPException(
            status_code=409,
            detail="ReviewQueue context contains conflicting tenant lineage",
        )
    if context.summary is not None and (context.summary.run_id != run.run_id or context.summary.alert_id != run.alert_id):
        raise HTTPException(
            status_code=409,
            detail="ReviewQueue summary lineage is inconsistent",
        )


def _assert_binding_lineage(
    binding: SocLeadAgentReviewThreadBinding,
    artifact: SocLeadAgentReviewContextArtifact,
) -> None:
    if binding.queue_id != artifact.queue_id or binding.run_id != artifact.run_id or binding.alert_id != artifact.alert_id:
        raise HTTPException(
            status_code=409,
            detail=(f"thread is already bound to review queue {binding.queue_id}; open a new thread for a different queue item"),
        )


__all__ = [
    "SOC_REVIEW_QUEUE_ID_REQUEST_CONTEXT_KEY",
    "inject_soc_lead_agent_review_context",
]
