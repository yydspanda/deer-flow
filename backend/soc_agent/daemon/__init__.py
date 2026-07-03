"""Daemon adapter helpers for SOC Agent background ingestion."""

from soc_agent.daemon.kafka_mapper import KafkaMapperError, KafkaRecord, map_kafka_record_to_daemon_message
from soc_agent.daemon.kafka_runner import KafkaConsumerPort, KafkaRunnerProcessResult, SocKafkaConsumerRunner

__all__ = [
    "KafkaConsumerPort",
    "KafkaMapperError",
    "KafkaRecord",
    "KafkaRunnerProcessResult",
    "SocKafkaConsumerRunner",
    "map_kafka_record_to_daemon_message",
]
