"""Secret-free Kafka projection for SOC operational visibility."""

from __future__ import annotations

from collections.abc import Callable

from soc_agent.contracts import SocOperationsAvailability, SocOperationsKafkaSnapshot
from soc_agent.daemon import KafkaConsumerSettings, build_kafka_broker_status


class KafkaOperationsProbe:
    """Adapt existing daemon readiness checks to the operations contract."""

    def __init__(
        self,
        settings: KafkaConsumerSettings | None,
        *,
        broker_checker: Callable[[KafkaConsumerSettings], None] | None = None,
        configuration_error_code: str | None = None,
    ) -> None:
        self._settings = settings
        self._broker_checker = broker_checker
        self._configuration_error_code = configuration_error_code

    @classmethod
    def from_env(
        cls,
        *,
        broker_checker: Callable[[KafkaConsumerSettings], None] | None = None,
    ) -> KafkaOperationsProbe:
        try:
            settings = KafkaConsumerSettings.from_env()
        except (TypeError, ValueError):
            return cls(
                None,
                broker_checker=broker_checker,
                configuration_error_code="soc.kafka.invalid_configuration",
            )
        return cls(settings, broker_checker=broker_checker)

    def snapshot(self, *, check_connectivity: bool = False) -> SocOperationsKafkaSnapshot:
        if self._settings is None:
            return SocOperationsKafkaSnapshot(
                availability=SocOperationsAvailability.UNAVAILABLE,
                enabled=False,
                settings_valid=False,
                error_code=self._configuration_error_code or "soc.kafka.invalid_configuration",
            )

        status = build_kafka_broker_status(
            self._settings,
            check_broker=check_connectivity,
            broker_checker=self._broker_checker,
        )
        availability = _availability_from_status(
            enabled=status.enabled,
            checked=status.checked,
            reachable=status.reachable,
        )
        error_code = "soc.kafka.broker_unreachable" if status.reachable is False else None
        return SocOperationsKafkaSnapshot(
            availability=availability,
            enabled=status.enabled,
            settings_valid=status.adapter_configured,
            checked=status.checked,
            reachable=status.reachable,
            bootstrap_server_count=len(status.bootstrap_servers),
            alert_topic_count=len(status.alert_topics),
            approval_request_topic_count=len(status.approval_request_topics),
            dead_letter_configured=bool(status.dead_letter_topic),
            error_code=error_code,
        )


def _availability_from_status(
    *,
    enabled: bool,
    checked: bool,
    reachable: bool | None,
) -> SocOperationsAvailability:
    if not enabled:
        return SocOperationsAvailability.NOT_CONFIGURED
    if not checked:
        return SocOperationsAvailability.NOT_MEASURED
    if reachable is True:
        return SocOperationsAvailability.AVAILABLE
    return SocOperationsAvailability.UNAVAILABLE


__all__ = ["KafkaOperationsProbe"]
