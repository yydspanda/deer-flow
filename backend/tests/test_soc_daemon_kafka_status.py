from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine

from soc_agent.daemon.kafka_config import KafkaConsumerSettings
from soc_agent.daemon.kafka_status import build_kafka_daemon_status
from soc_agent.db import create_soc_tables


def test_kafka_daemon_status_reports_missing_database() -> None:
    status = build_kafka_daemon_status(database_url=None, kafka_settings=KafkaConsumerSettings(), check_database=True)

    assert status.ready is False
    assert status.database.configured is False
    assert status.database.reachable is False
    assert "database URL required" in (status.database.error or "")
    assert status.kafka.enabled is False
    assert status.kafka.adapter_configured is True


def test_kafka_daemon_status_reports_sqlite_reachable(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'soc.db'}"
    create_soc_tables(create_engine(database_url))

    status = build_kafka_daemon_status(database_url=database_url, kafka_settings=KafkaConsumerSettings(), check_database=True)

    assert status.ready is True
    assert status.database.configured is True
    assert status.database.reachable is True
    assert status.database.url == database_url
    assert status.kafka.enabled is False
    assert status.kafka.reachable is None


def test_kafka_daemon_status_skips_database_check(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'missing.db'}"

    status = build_kafka_daemon_status(database_url=database_url, kafka_settings=KafkaConsumerSettings(), check_database=False)

    assert status.ready is False
    assert status.database.configured is True
    assert status.database.reachable is False
    assert status.database.error == "database check skipped"


def test_kafka_daemon_status_reports_enabled_broker_unchecked(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'soc.db'}"
    create_soc_tables(create_engine(database_url))

    status = build_kafka_daemon_status(
        database_url=database_url,
        kafka_settings=KafkaConsumerSettings(enabled=True),
        check_database=True,
        check_broker=False,
    )

    assert status.ready is True
    assert status.kafka.enabled is True
    assert status.kafka.adapter_configured is True
    assert status.kafka.checked is False
    assert status.kafka.reachable is None


def test_kafka_daemon_status_reports_broker_checker_failure(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'soc.db'}"
    create_soc_tables(create_engine(database_url))

    def fail(_settings: KafkaConsumerSettings) -> None:
        raise RuntimeError("broker down")

    status = build_kafka_daemon_status(
        database_url=database_url,
        kafka_settings=KafkaConsumerSettings(enabled=True),
        check_database=True,
        check_broker=True,
        broker_checker=fail,
    )

    assert status.ready is False
    assert status.kafka.checked is True
    assert status.kafka.reachable is False
    assert status.kafka.error == "broker down"


def test_kafka_daemon_status_reports_broker_checker_success(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'soc.db'}"
    create_soc_tables(create_engine(database_url))

    status = build_kafka_daemon_status(
        database_url=database_url,
        kafka_settings=KafkaConsumerSettings(enabled=True),
        check_database=True,
        check_broker=True,
        broker_checker=lambda _settings: None,
    )

    assert status.ready is True
    assert status.kafka.checked is True
    assert status.kafka.reachable is True
