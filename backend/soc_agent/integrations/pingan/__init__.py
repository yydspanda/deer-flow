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
from soc_agent.integrations.pingan.software_path_catalog import (
    PINGAN_SOFTWARE_PATH_LOOKUP_ACTION,
    PingAnSoftwarePathCatalog,
    PingAnSoftwarePathCatalogBuildReport,
    PingAnSoftwarePathCatalogError,
    PingAnSoftwarePathContextResult,
    PingAnSoftwarePathLookupActionAdapter,
    classify_pingan_path,
    compile_pingan_software_path_catalog,
    normalize_windows_path,
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
    "PINGAN_SOFTWARE_PATH_LOOKUP_ACTION",
    "PingAnSoftwarePathCatalog",
    "PingAnSoftwarePathCatalogBuildReport",
    "PingAnSoftwarePathCatalogError",
    "PingAnSoftwarePathContextResult",
    "PingAnSoftwarePathLookupActionAdapter",
    "StaticPingAnAssetSearchPort",
    "StaticPingAnAssetWorkflowPort",
    "build_pingan_asset_locator_from_env",
    "classify_pingan_path",
    "compile_pingan_software_path_catalog",
    "normalize_windows_path",
]
