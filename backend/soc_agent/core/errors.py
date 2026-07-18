"""Shared SOC service-layer errors."""


class SocServiceError(RuntimeError):
    """Base error for service-layer failures."""


class SocServiceNotImplementedError(SocServiceError):
    """Raised when a planned service operation has no current implementation."""


class SocServiceNotFoundError(SocServiceError):
    """Raised when a requested SOC resource does not exist."""


class SocServiceAuthorizationError(SocServiceError):
    """Raised when an actor lacks trusted identity or a required SOC role."""


class SocServiceConflictError(SocServiceError):
    """Raised when a state transition conflicts with persisted SOC state."""


__all__ = [
    "SocServiceAuthorizationError",
    "SocServiceConflictError",
    "SocServiceError",
    "SocServiceNotFoundError",
    "SocServiceNotImplementedError",
]
