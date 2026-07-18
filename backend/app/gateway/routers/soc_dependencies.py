"""Shared dependencies for SOC Gateway routers."""

from __future__ import annotations

from fastapi import HTTPException, Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.contracts import ActorAuthSource, ActorContext, EntrySurface, ServiceRequestContext
from soc_agent.db import SqlAlchemyAlertRepository, resolve_database_url, to_sync_database_url

_ALLOWED_HEADER_SURFACES = {
    EntrySurface.API.value: EntrySurface.API,
    EntrySurface.WEB.value: EntrySurface.WEB,
}


def get_or_create_soc_repository(request: Request) -> SqlAlchemyAlertRepository:
    repository = getattr(request.app.state, "soc_alert_repository", None)
    if repository is not None:
        return repository

    try:
        database_url = resolve_database_url()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    engine = create_engine(to_sync_database_url(database_url), pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository = SqlAlchemyAlertRepository(session_factory)
    request.app.state.soc_alert_repository = repository
    return repository


def soc_service_context_from_request(
    request: Request,
    *,
    include_soc_roles: bool = False,
) -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id=actor_id_from_request(request),
            surface=surface_from_request(request),
            roles=soc_roles_from_request(request) if include_soc_roles else [],
            auth_source=auth_source_from_request(request),
        ),
        trace_id=request.headers.get("x-trace-id"),
        idempotency_key=request.headers.get("idempotency-key"),
    )


def actor_id_from_request(request: Request) -> str:
    user = getattr(getattr(request, "state", None), "user", None)
    user_id = getattr(user, "id", None)
    if user_id is not None:
        return str(user_id)
    return request.headers.get("x-soc-actor-id") or "api"


def surface_from_request(request: Request) -> EntrySurface:
    surface = request.headers.get("x-soc-surface", EntrySurface.API.value).lower()
    return _ALLOWED_HEADER_SURFACES.get(surface, EntrySurface.API)


def soc_roles_from_request(request: Request) -> list[str]:
    user = getattr(getattr(request, "state", None), "user", None)
    system_role = getattr(user, "system_role", None)
    if system_role == "admin":
        return ["soc_admin"]
    if user is not None:
        return ["soc_analyst"]
    return []


def auth_source_from_request(request: Request) -> ActorAuthSource:
    value = getattr(getattr(request, "state", None), "auth_source", None)
    try:
        return ActorAuthSource(value)
    except (TypeError, ValueError):
        return ActorAuthSource.UNKNOWN
