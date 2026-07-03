"""Daemon adapter helpers for SOC Agent background ingestion."""

from soc_agent.daemon.kafka_adapter import KafkaAdapterNotConfiguredError, NullKafkaConsumerPort
from soc_agent.daemon.kafka_config import KafkaConsumerSettings, KafkaSecurityProtocol
from soc_agent.daemon.kafka_mapper import KafkaMapperError, KafkaRecord, map_kafka_record_to_daemon_message
from soc_agent.daemon.kafka_runner import KafkaConsumerPort, KafkaRunnerProcessResult, SocKafkaConsumerRunner

__all__ = [
    "KafkaAdapterNotConfiguredError",
    "KafkaConsumerPort",
    "KafkaConsumerSettings",
    "KafkaMapperError",
    "KafkaRecord",
    "KafkaRunnerProcessResult",
    "KafkaSecurityProtocol",
    "NullKafkaConsumerPort",
    "SocKafkaConsumerRunner",
    "map_kafka_record_to_daemon_message",
]
