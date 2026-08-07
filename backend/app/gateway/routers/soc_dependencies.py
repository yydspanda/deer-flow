"""Compatibility exports for SOC dependencies now owned by the Gateway."""

from app.gateway.soc_dependencies import (
    actor_id_from_request,
    auth_source_from_request,
    get_or_create_soc_repository,
    get_soc_review_service,
    soc_roles_from_request,
    soc_service_context_from_request,
    surface_from_request,
)

__all__ = [
    "actor_id_from_request",
    "auth_source_from_request",
    "get_or_create_soc_repository",
    "get_soc_review_service",
    "soc_roles_from_request",
    "soc_service_context_from_request",
    "surface_from_request",
]
