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


class SocEnrichmentWorkflowError(SocServiceError):
    """Base failure raised at the persistent investigation boundary."""

    retryable = False


class SocEnrichmentWorkflowConflictError(SocEnrichmentWorkflowError):
    """The same durable identity was reused for different semantics."""


class SocEnrichmentWorkflowBusyError(SocEnrichmentWorkflowError):
    """Another process owns a non-stale execution claim."""

    retryable = True


class SocEnrichmentWorkflowPersistenceError(SocEnrichmentWorkflowError):
    """Optimistic persistence state changed unexpectedly."""

    retryable = True


__all__ = [
    "SocEnrichmentWorkflowBusyError",
    "SocEnrichmentWorkflowConflictError",
    "SocEnrichmentWorkflowError",
    "SocEnrichmentWorkflowPersistenceError",
    "SocServiceAuthorizationError",
    "SocServiceConflictError",
    "SocServiceError",
    "SocServiceNotFoundError",
    "SocServiceNotImplementedError",
]
