"""Daemon adapter helpers for SOC Agent background ingestion."""

from soc_agent.daemon.kafka_mapper import KafkaMapperError, KafkaRecord, map_kafka_record_to_daemon_message

__all__ = [
    "KafkaMapperError",
    "KafkaRecord",
    "map_kafka_record_to_daemon_message",
]
