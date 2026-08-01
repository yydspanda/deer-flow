"""SOC operational visibility adapters and shared wiring."""

from soc_agent.operations.kafka_probe import KafkaOperationsProbe
from soc_agent.operations.wiring import build_soc_operations_service

__all__ = ["KafkaOperationsProbe", "build_soc_operations_service"]
