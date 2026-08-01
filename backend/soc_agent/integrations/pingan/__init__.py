"""PingAn-specific provider integrations.

Vendor field names, authentication mechanisms, and fallback workflows stop in
this package. Generic SOC Runtime and action services consume their typed
results through vendor-neutral routes such as ``asset.locate``.
"""

from soc_agent.integrations.pingan.asset_location import (
    CallablePingAnAssetWorkflowPort,
    HttpPingAnZeusAssetSearchPort,
    PingAnAssetLocationAttempt,
    PingAnAssetLocationCandidate,
    PingAnAssetLocationQuery,
    PingAnAssetLocationResult,
    PingAnAssetLocatorService,
    PingAnAssetOwnershipOverride,
    PingAnAssetProviderConfigurationError,
    PingAnAssetProviderError,
    PingAnAssetProviderUnavailableError,
    PingAnAssetType,
    PingAnAssetWorkflowConfig,
    StaticPingAnAssetSearchPort,
    StaticPingAnAssetWorkflowPort,
    build_pingan_asset_locator_from_env,
)

__all__ = [
    "CallablePingAnAssetWorkflowPort",
    "HttpPingAnZeusAssetSearchPort",
    "PingAnAssetLocationAttempt",
    "PingAnAssetLocationCandidate",
    "PingAnAssetLocationQuery",
    "PingAnAssetLocationResult",
    "PingAnAssetLocatorService",
    "PingAnAssetOwnershipOverride",
    "PingAnAssetProviderConfigurationError",
    "PingAnAssetProviderError",
    "PingAnAssetProviderUnavailableError",
    "PingAnAssetType",
    "PingAnAssetWorkflowConfig",
    "StaticPingAnAssetSearchPort",
    "StaticPingAnAssetWorkflowPort",
    "build_pingan_asset_locator_from_env",
]
