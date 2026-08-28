"""Shared CLI/Gateway wiring for the SOC operations service."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from soc_agent.core.effectiveness import SocEffectivenessService
from soc_agent.core.operations import SocOperationsService
from soc_agent.daemon import KafkaConsumerSettings
from soc_agent.db import resolve_database_url, to_sync_database_url
from soc_agent.db.effectiveness import SqlAlchemySocEffectivenessRepository
from soc_agent.db.operations import SqlAlchemySocOperationsRepository
from soc_agent.operations.kafka_probe import KafkaOperationsProbe


def build_soc_operations_service(
    *,
    database_url: str | None = None,
    kafka_settings: KafkaConsumerSettings | None = None,
    broker_checker: Callable[[KafkaConsumerSettings], None] | None = None,
) -> SocOperationsService:
    """Build the same read-only service for CLI and Gateway entry surfaces."""

    repository = None
    database_backend = None
    database_error_code = None
    try:
        resolved_database_url = resolve_database_url(database_url)
    except ValueError:
        database_error_code = "soc.database.not_configured"
    else:
        try:
            sync_database_url = to_sync_database_url(resolved_database_url)
            database_backend = make_url(sync_database_url).get_backend_name()
            engine = create_engine(sync_database_url, pool_pre_ping=True)
            session_factory = sessionmaker(bind=engine, expire_on_commit=False)
            repository = SqlAlchemySocOperationsRepository(session_factory)
        except (SQLAlchemyError, TypeError, ValueError):
            database_error_code = "soc.database.invalid_configuration"

    kafka_probe = KafkaOperationsProbe(kafka_settings, broker_checker=broker_checker) if kafka_settings is not None else KafkaOperationsProbe.from_env(broker_checker=broker_checker)
    return SocOperationsService(
        repository=repository,
        kafka_probe=kafka_probe,
        database_backend=database_backend,
        database_error_code=database_error_code,
    )


def build_soc_effectiveness_service(
    *,
    database_url: str | None = None,
) -> SocEffectivenessService:
    """Build the read-only effectiveness service from the configured SOC store."""

    repository = None
    database_error_code = None
    try:
        resolved_database_url = resolve_database_url(database_url)
    except ValueError:
        database_error_code = "soc.effectiveness.database_not_configured"
    else:
        try:
            sync_database_url = to_sync_database_url(resolved_database_url)
            engine = create_engine(sync_database_url, pool_pre_ping=True)
            session_factory = sessionmaker(bind=engine, expire_on_commit=False)
            repository = SqlAlchemySocEffectivenessRepository(session_factory)
        except (SQLAlchemyError, TypeError, ValueError):
            database_error_code = "soc.effectiveness.invalid_configuration"
    return SocEffectivenessService(
        repository=repository,
        database_error_code=database_error_code,
    )


__all__ = ["build_soc_effectiveness_service", "build_soc_operations_service"]
