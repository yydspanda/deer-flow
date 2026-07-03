"""Readiness/status contracts for SOC Kafka daemon wiring."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from soc_agent.daemon.kafka_config import KafkaConsumerSettings
from soc_agent.db import resolve_database_url, to_sync_database_url


class KafkaDaemonDatabaseStatus(BaseModel):
    """Database readiness summary for the SOC Kafka daemon."""

    configured: bool
    reachable: bool
    url: str | None = None
    error: str | None = None


class KafkaDaemonBrokerStatus(BaseModel):
    """Kafka readiness summary for the SOC Kafka daemon."""

    enabled: bool
    adapter_configured: bool
    bootstrap_servers: list[str] = Field(default_factory=list)
    alert_topics: list[str] = Field(default_factory=list)
    approval_request_topics: list[str] = Field(default_factory=list)
    dead_letter_topic: str | None = None
    checked: bool = False
    reachable: bool | None = None
    error: str | None = None


class KafkaDaemonStatus(BaseModel):
    """Top-level daemon readiness/status response."""

    schema_version: str = "soc.kafka_daemon_status.v1"
    ready: bool
    database: KafkaDaemonDatabaseStatus
    kafka: KafkaDaemonBrokerStatus


def build_kafka_daemon_status(
    *,
    database_url: str | None,
    kafka_settings: KafkaConsumerSettings,
    check_database: bool = True,
    check_broker: bool = False,
    broker_checker: Callable[[KafkaConsumerSettings], None] | None = None,
) -> KafkaDaemonStatus:
    """Build a lightweight readiness snapshot for SOC Kafka daemon wiring."""

    database = _database_status(database_url, check_database=check_database)
    kafka = _broker_status(kafka_settings, check_broker=check_broker, broker_checker=broker_checker)
    return KafkaDaemonStatus(
        ready=database.reachable and kafka.adapter_configured and (kafka.reachable is not False),
        database=database,
        kafka=kafka,
    )


def _database_status(database_url: str | None, *, check_database: bool) -> KafkaDaemonDatabaseStatus:
    try:
        resolved_url = resolve_database_url(database_url)
    except ValueError as exc:
        return KafkaDaemonDatabaseStatus(configured=False, reachable=False, error=str(exc))

    if not check_database:
        return KafkaDaemonDatabaseStatus(configured=True, reachable=False, url=_redacted_database_url(resolved_url), error="database check skipped")

    try:
        engine = create_engine(to_sync_database_url(resolved_url), pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        return KafkaDaemonDatabaseStatus(
            configured=True,
            reachable=False,
            url=_redacted_database_url(resolved_url),
            error=str(exc),
        )
    return KafkaDaemonDatabaseStatus(configured=True, reachable=True, url=_redacted_database_url(resolved_url))


def _broker_status(
    settings: KafkaConsumerSettings,
    *,
    check_broker: bool,
    broker_checker: Callable[[KafkaConsumerSettings], None] | None,
) -> KafkaDaemonBrokerStatus:
    status = KafkaDaemonBrokerStatus(
        enabled=settings.enabled,
        adapter_configured=not settings.enabled,
        bootstrap_servers=settings.bootstrap_servers,
        alert_topics=settings.alert_topics,
        approval_request_topics=settings.approval_request_topics,
        dead_letter_topic=settings.dead_letter_topic,
        checked=check_broker,
    )
    if not settings.enabled:
        status.reachable = None
        return status

    status.adapter_configured = True
    if not check_broker:
        status.reachable = None
        return status

    checker = broker_checker or _default_broker_checker
    try:
        checker(settings)
    except Exception as exc:  # noqa: BLE001 - status boundary reports any adapter failure
        status.reachable = False
        status.error = str(exc)
    else:
        status.reachable = True
    return status


def _default_broker_checker(settings: KafkaConsumerSettings) -> None:
    from soc_agent.daemon.kafka_adapter import build_kafka_consumer_port

    port = build_kafka_consumer_port(settings)
    try:
        port.poll()
    finally:
        port.close()


def _redacted_database_url(database_url: str) -> str:
    if "@" not in database_url or "://" not in database_url:
        return database_url
    scheme, rest = database_url.split("://", 1)
    credentials, host = rest.split("@", 1)
    if ":" not in credentials:
        return database_url
    username, _password = credentials.split(":", 1)
    return f"{scheme}://{username}:***@{host}"
