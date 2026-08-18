"""Entry-independent authorization checks for SOC state mutations."""

from collections.abc import Collection

from soc_agent.contracts import ActorAuthSource, ServiceRequestContext

from .errors import SocServiceAuthorizationError

SOC_MEMORY_REVIEWER_ROLES = frozenset({"analyst", "soc_analyst", "soc_memory_reviewer", "soc_admin"})


def require_actor_roles(
    context: ServiceRequestContext,
    allowed_roles: Collection[str],
    *,
    operation: str,
) -> None:
    """Require a trusted actor identity and at least one allowed SOC role."""

    actor = context.actor
    if actor.actor_id == "anonymous" or actor.auth_source is ActorAuthSource.UNKNOWN:
        raise SocServiceAuthorizationError(f"{operation} requires an authenticated actor with auth_source")
    if not set(allowed_roles).intersection(actor.roles):
        allowed = ", ".join(sorted(allowed_roles))
        raise SocServiceAuthorizationError(f"{operation} requires one of roles: {allowed}")


__all__ = ["SOC_MEMORY_REVIEWER_ROLES", "require_actor_roles"]
