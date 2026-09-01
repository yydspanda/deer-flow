"""Legacy ZEUS protocol adapter backed by the new SOC execution plane."""

from soc_agent.integrations.pingan.legacy_compat.callback import (
    PINGAN_ALERT_CALLBACK_DESTINATION,
    HttpPingAnZeusAlertCallbackPort,
    PingAnAlertCallbackConfigurationError,
    PingAnAlertCallbackResponseError,
    PingAnLegacyCallbackDispatcher,
    StaticPingAnZeusAlertCallbackPort,
)
from soc_agent.integrations.pingan.legacy_compat.contracts import (
    PINGAN_LEGACY_MODEL_NAME,
    PINGAN_LEGACY_QUEUE_NAME,
    PingAnAlertLifecycleCheck,
    PingAnAlertLifecycleState,
    PingAnLegacyTaskMetadata,
    PingAnLegacyTaskRequest,
    PingAnLegacyTaskResponse,
    extract_pingan_legacy_task_metadata,
    project_legacy_task_status,
)
from soc_agent.integrations.pingan.legacy_compat.execution import (
    PingAnLegacyExecutionSupervisor,
)
from soc_agent.integrations.pingan.legacy_compat.live_acceptance import (
    PingAnLegacyLiveAcceptanceReport,
    run_pingan_legacy_live_acceptance,
)
from soc_agent.integrations.pingan.legacy_compat.result_mapper import (
    PingAnLegacyResultMapper,
)
from soc_agent.integrations.pingan.legacy_compat.service import (
    PingAnLegacyTaskNotFoundError,
    PingAnLegacyTaskService,
)
from soc_agent.integrations.pingan.legacy_compat.wiring import (
    PingAnLegacyApiSettings,
    PingAnLegacyProviderMode,
    PingAnLegacyWorkerSettings,
    build_pingan_callback_port,
    build_pingan_lifecycle_service,
)
from soc_agent.integrations.pingan.legacy_compat.worker import (
    PingAnLegacyJobWorker,
)
from soc_agent.integrations.pingan.legacy_compat.zeus_lifecycle import (
    HttpPingAnZeusAlertLifecyclePort,
    PingAnAlertLifecycleConfigurationError,
    PingAnAlertLifecycleResponseError,
    PingAnAlertLifecycleService,
    StaticPingAnZeusAlertLifecyclePort,
)

__all__ = [
    "PINGAN_LEGACY_MODEL_NAME",
    "PINGAN_LEGACY_QUEUE_NAME",
    "PINGAN_ALERT_CALLBACK_DESTINATION",
    "HttpPingAnZeusAlertCallbackPort",
    "PingAnAlertLifecycleCheck",
    "PingAnAlertLifecycleState",
    "PingAnLegacyTaskMetadata",
    "PingAnLegacyTaskNotFoundError",
    "PingAnLegacyTaskRequest",
    "PingAnLegacyTaskResponse",
    "PingAnLegacyTaskService",
    "PingAnLegacyApiSettings",
    "PingAnLegacyExecutionSupervisor",
    "PingAnLegacyProviderMode",
    "PingAnLegacyWorkerSettings",
    "PingAnLegacyJobWorker",
    "PingAnLegacyLiveAcceptanceReport",
    "PingAnLegacyCallbackDispatcher",
    "PingAnLegacyResultMapper",
    "HttpPingAnZeusAlertLifecyclePort",
    "PingAnAlertLifecycleConfigurationError",
    "PingAnAlertLifecycleResponseError",
    "PingAnAlertLifecycleService",
    "StaticPingAnZeusAlertLifecyclePort",
    "StaticPingAnZeusAlertCallbackPort",
    "PingAnAlertCallbackConfigurationError",
    "PingAnAlertCallbackResponseError",
    "extract_pingan_legacy_task_metadata",
    "project_legacy_task_status",
    "run_pingan_legacy_live_acceptance",
    "build_pingan_callback_port",
    "build_pingan_lifecycle_service",
]
