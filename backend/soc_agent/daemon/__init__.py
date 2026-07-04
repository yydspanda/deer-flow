"""Daemon adapter helpers for SOC Agent background ingestion."""

from soc_agent.daemon.kafka_adapter import (
    ConfluentKafkaConsumerPort,
    KafkaAdapterError,
    KafkaAdapterNotConfiguredError,
    NullKafkaConsumerPort,
    build_kafka_consumer_port,
)
from soc_agent.daemon.kafka_commit_tracker import (
    KafkaCommitAdvance,
    KafkaPartitionRef,
    PartitionCommitStateSnapshot,
    PartitionCommitTracker,
)
from soc_agent.daemon.kafka_config import KafkaConsumerSettings, KafkaSecurityProtocol
from soc_agent.daemon.kafka_daemon import (
    JsonLineKafkaDaemonMetricSink,
    KafkaDaemonMetricSink,
    KafkaDaemonRunResult,
    KafkaDaemonStopSignal,
    SocKafkaDaemonRunner,
)
from soc_agent.daemon.kafka_mapper import KafkaMapperError, KafkaRecord, map_kafka_record_to_daemon_message
from soc_agent.daemon.kafka_runner import KafkaConsumerPort, KafkaRunnerLoopResult, KafkaRunnerProcessResult, SocKafkaConsumerRunner
from soc_agent.daemon.kafka_status import KafkaDaemonBrokerStatus, KafkaDaemonDatabaseStatus, KafkaDaemonStatus, build_kafka_daemon_status
from soc_agent.daemon.kafka_worker import KafkaWorkerError, KafkaWorkerResult, KafkaWorkerResultStatus, SocKafkaWorker

__all__ = [
    "ConfluentKafkaConsumerPort",
    "KafkaAdapterError",
    "KafkaAdapterNotConfiguredError",
    "KafkaConsumerPort",
    "KafkaConsumerSettings",
    "KafkaCommitAdvance",
    "KafkaDaemonBrokerStatus",
    "KafkaDaemonDatabaseStatus",
    "KafkaDaemonMetricSink",
    "KafkaDaemonRunResult",
    "KafkaDaemonStatus",
    "KafkaDaemonStopSignal",
    "KafkaMapperError",
    "KafkaPartitionRef",
    "KafkaRecord",
    "KafkaRunnerLoopResult",
    "KafkaRunnerProcessResult",
    "KafkaSecurityProtocol",
    "KafkaWorkerError",
    "KafkaWorkerResult",
    "KafkaWorkerResultStatus",
    "JsonLineKafkaDaemonMetricSink",
    "NullKafkaConsumerPort",
    "PartitionCommitStateSnapshot",
    "PartitionCommitTracker",
    "SocKafkaWorker",
    "SocKafkaDaemonRunner",
    "SocKafkaConsumerRunner",
    "build_kafka_consumer_port",
    "build_kafka_daemon_status",
    "map_kafka_record_to_daemon_message",
]
